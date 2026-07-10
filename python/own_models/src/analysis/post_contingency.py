from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from own_models.src.analysis.contingencies import (
    Contingency,
    apply_contingency,
    generate_contingencies,
    generate_generator_contingencies,
)
from own_models.src.analysis.balancing_policy import apply_balancing_policy
from own_models.src.analysis.security_checks import (
    designated_slack_generator,
    evaluate_dc_security_checks,
    evaluate_security_checks,
)
from own_models.src.analysis.security_labels import (
    ContingencyScreeningResult,
    result_from_checks,
    result_from_generation_imbalance,
    result_from_islanding,
    result_from_nonconvergence,
)
from own_models.src.analysis.topology import island_diagnostics
from own_models.src.data.case import Generator, PowerCase
from own_models.src.pf.ac_power_flow import solve_ac_power_flow_newton


def case_with_base_dispatch(case: PowerCase, ac_opf_result) -> PowerCase:
    generators: list[Generator] = []
    for gen in case.generators:
        if gen.status != 1:
            generators.append(gen)
            continue
        pg = ac_opf_result.pg.get(gen.id, gen.pg0)
        qg = ac_opf_result.qg.get(gen.id, gen.qg0)
        generators.append(replace(gen, pg0=pg, qg0=qg))
    buses = [
        replace(
            bus,
            vm0=ac_opf_result.vm.get(bus.id, bus.vm0),
            va0=ac_opf_result.va.get(bus.id, bus.va0),
        )
        for bus in case.buses
    ]
    return replace(case, generators=generators, buses=buses)


def _base_pg(case: PowerCase) -> dict[str, float]:
    return {gen.id: gen.pg0 for gen in case.generators if gen.status == 1}


def run_post_contingency_ac_pf(
    base_dispatch_case: PowerCase,
    contingency: Contingency,
    *,
    operating_point_id: str,
    pf_tol: float = 1e-8,
    pf_max_iter: int = 30,
) -> ContingencyScreeningResult:
    contingency_case = apply_contingency(base_dispatch_case, contingency)
    diagnostics = island_diagnostics(contingency_case)
    slack_gen = designated_slack_generator(contingency_case)

    if diagnostics.islanded:
        return result_from_islanding(
            case_name=base_dispatch_case.name,
            operating_point_id=operating_point_id,
            contingency=contingency,
            diagnostics=diagnostics,
            slack_generator_id=slack_gen.id,
            slack_pg_min=slack_gen.pmin,
            slack_pg_max=slack_gen.pmax,
        )

    dc_checks = evaluate_dc_security_checks(contingency_case)

    try:
        pf_result = solve_ac_power_flow_newton(
            contingency_case,
            tol=pf_tol,
            max_iter=pf_max_iter,
        )
    except Exception:
        pf_result = SimpleNamespace(converged=False, mismatch_norm=float("inf"), iterations=0)

    if not pf_result.converged:
        return result_from_nonconvergence(
            case_name=base_dispatch_case.name,
            operating_point_id=operating_point_id,
            contingency=contingency,
            diagnostics=diagnostics,
            pf_result=pf_result,
            dc_checks=dc_checks,
            slack_generator_id=slack_gen.id,
            slack_pg_min=slack_gen.pmin,
            slack_pg_max=slack_gen.pmax,
        )

    checks = evaluate_security_checks(
        contingency_case,
        pf_result,
        _base_pg(base_dispatch_case),
    )
    return result_from_checks(
        case_name=base_dispatch_case.name,
        operating_point_id=operating_point_id,
        contingency=contingency,
        diagnostics=diagnostics,
        pf_result=pf_result,
        checks=checks,
        dc_checks=dc_checks,
    )


def run_contingency_screening(
    case: PowerCase,
    ac_opf_result,
    *,
    operating_point_id: str = "base_ac_opf",
    k: int = 1,
    include_branches: bool = True,
    include_generators: bool = False,
    pf_tol: float = 1e-8,
    pf_max_iter: int = 30,
) -> list[ContingencyScreeningResult]:
    base_dispatch_case = case_with_base_dispatch(case, ac_opf_result)
    contingencies = generate_contingencies(
        base_dispatch_case,
        k=k,
        include_branches=include_branches,
        include_generators=include_generators,
    )
    return [
        run_post_contingency_ac_pf(
            base_dispatch_case,
            contingency,
            operating_point_id=operating_point_id,
            pf_tol=pf_tol,
            pf_max_iter=pf_max_iter,
        )
        for contingency in contingencies
    ]


def run_post_generator_contingency_ac_pf(
    base_dispatch_case: PowerCase,
    base_ac_opf_result,
    contingency: Contingency,
    *,
    operating_point_id: str,
    balancing_policy: str = "proportional_reserve",
    pf_tol: float = 1e-8,
    pf_max_iter: int = 30,
) -> ContingencyScreeningResult:
    contingency_case = apply_contingency(base_dispatch_case, contingency)
    diagnostics = island_diagnostics(contingency_case)

    if diagnostics.islanded:
        slack_gen = designated_slack_generator(contingency_case)
        return result_from_islanding(
            case_name=base_dispatch_case.name,
            operating_point_id=operating_point_id,
            contingency=contingency,
            diagnostics=diagnostics,
            slack_generator_id=slack_gen.id,
            slack_pg_min=slack_gen.pmin,
            slack_pg_max=slack_gen.pmax,
        )

    balanced_case, balancing_result = apply_balancing_policy(
        contingency_case,
        base_ac_opf_result,
        contingency,
        policy=balancing_policy,
    )
    slack_gen = next(
        gen for gen in balanced_case.generators if gen.id == balancing_result.new_slack_generator_id
    )

    if not balancing_result.success:
        return result_from_generation_imbalance(
            case_name=base_dispatch_case.name,
            operating_point_id=operating_point_id,
            contingency=contingency,
            diagnostics=diagnostics,
            balancing_result=balancing_result,
            slack_pg_min=slack_gen.pmin,
            slack_pg_max=slack_gen.pmax,
        )

    dc_checks = evaluate_dc_security_checks(balanced_case)

    try:
        pf_result = solve_ac_power_flow_newton(
            balanced_case,
            tol=pf_tol,
            max_iter=pf_max_iter,
        )
    except Exception:
        pf_result = SimpleNamespace(converged=False, mismatch_norm=float("inf"), iterations=0)

    if not pf_result.converged:
        return result_from_nonconvergence(
            case_name=base_dispatch_case.name,
            operating_point_id=operating_point_id,
            contingency=contingency,
            diagnostics=diagnostics,
            pf_result=pf_result,
            dc_checks=dc_checks,
            balancing_result=balancing_result,
            slack_generator_id=slack_gen.id,
            slack_pg_min=slack_gen.pmin,
            slack_pg_max=slack_gen.pmax,
        )

    checks = evaluate_security_checks(
        balanced_case,
        pf_result,
        balancing_result.scheduled_pg_by_generator,
    )
    return result_from_checks(
        case_name=base_dispatch_case.name,
        operating_point_id=operating_point_id,
        contingency=contingency,
        diagnostics=diagnostics,
        pf_result=pf_result,
        checks=checks,
        dc_checks=dc_checks,
        balancing_result=balancing_result,
    )


def run_generator_contingency_screening(
    case: PowerCase,
    ac_opf_result,
    *,
    operating_point_id: str = "base_ac_opf",
    k: int = 1,
    balancing_policy: str = "proportional_reserve",
    pf_tol: float = 1e-8,
    pf_max_iter: int = 30,
) -> list[ContingencyScreeningResult]:
    base_dispatch_case = case_with_base_dispatch(case, ac_opf_result)
    contingencies = generate_generator_contingencies(base_dispatch_case, k=k)
    return [
        run_post_generator_contingency_ac_pf(
            base_dispatch_case,
            ac_opf_result,
            contingency,
            operating_point_id=operating_point_id,
            balancing_policy=balancing_policy,
            pf_tol=pf_tol,
            pf_max_iter=pf_max_iter,
        )
        for contingency in contingencies
    ]
