"""Scenario validation suite for the matrix-form DC OPF implementation."""

from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path

import pandas as pd

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat
from own_models.src.analysis.violations import (
    dc_opf_branch_rows,
    dc_opf_generator_rows,
    dc_opf_summary_row,
)
from own_models.src.data.case import Branch, Generator, Load, PowerCase
from own_models.src.opf.dc.solve import solve_dc_opf_pyomo


def run_dc_opf_validation_suite(
    case: PowerCase,
    *,
    cost_power_units: str = "mw",
    include_branch_limits: bool = True,
    include_angle_limits: bool = True,
) -> dict[str, pd.DataFrame]:
    """Run the planned DC OPF validation scenarios for one case."""
    base = _solve_scenario(
        "01_base",
        case,
        cost_power_units=cost_power_units,
        include_branch_limits=include_branch_limits,
        include_angle_limits=include_angle_limits,
    )

    scenarios = _build_scenarios_from_base(case, base["result"])
    scenario_results = [base]

    for name, scenario_case, branch_limits, angle_limits in scenarios:
        scenario_results.append(
            _solve_scenario(
                name,
                scenario_case,
                cost_power_units=cost_power_units,
                include_branch_limits=branch_limits,
                include_angle_limits=angle_limits,
                base_result=base["result"],
            )
        )

    summary = pd.concat([item["summary"] for item in scenario_results], ignore_index=True)
    generators = pd.concat(
        [item["generators"] for item in scenario_results if not item["generators"].empty],
        ignore_index=True,
    )
    branches = pd.concat(
        [item["branches"] for item in scenario_results if not item["branches"].empty],
        ignore_index=True,
    )

    return {
        "summary": summary,
        "generators": generators,
        "branches": branches,
    }


def run_dc_opf_validation_suite_from_matpower_mat(
    matpower_path: str | Path,
    *,
    cost_power_units: str = "mw",
) -> dict[str, pd.DataFrame]:
    """Load a PGLib/MATPOWER ``.mat`` file and run the scenario suite."""
    case = load_powercase_from_matpower_mat(matpower_path)
    return run_dc_opf_validation_suite(case, cost_power_units=cost_power_units)


def _build_scenarios_from_base(case: PowerCase, base_result):
    return [
        ("02_relaxed_limits", _relaxed_limits_case(case), False, False),
        ("03_branch_congestion", _branch_congestion_case(case, base_result), True, True),
        ("04_gen_reduction", _gen_reduction_case(case, base_result), True, True),
        ("05_gen_expansion", _gen_expansion_case(case), True, True),
        ("06_load_scaling", _load_scaled_case(case, load_multiplier=1.2), True, True),
        ("07_angle_limits", _angle_limited_case(case, base_result), True, True),
        ("08_slack_consistency", _alternate_slack_case(case), True, True),
        ("09_topology_branch_outage", _branch_outage_case(case, base_result), True, True),
    ]


def _solve_scenario(
    scenario: str,
    case: PowerCase,
    *,
    cost_power_units: str,
    include_branch_limits: bool,
    include_angle_limits: bool,
    base_result=None,
) -> dict[str, object]:
    try:
        model, _raw_results, result = solve_dc_opf_pyomo(
            case,
            cost_power_units=cost_power_units,
            include_branch_limits=include_branch_limits,
            include_angle_limits=include_angle_limits,
        )
        summary = dc_opf_summary_row(case, result, scenario=scenario)
        summary.insert(1, "case_name", case.name)
        summary["branch_limit_constraints"] = len(model.branch_flow_limit)
        summary["angle_limit_constraints"] = len(model.angle_difference_limit)
        _append_base_comparison(summary, result, base_result)

        generators = dc_opf_generator_rows(case, result)
        branches = dc_opf_branch_rows(case, result)
        if not generators.empty:
            generators.insert(0, "scenario", scenario)
        if not branches.empty:
            branches.insert(0, "scenario", scenario)
    except Exception as exc:
        result = None
        summary = _error_summary(scenario, case, exc)
        generators = pd.DataFrame()
        branches = pd.DataFrame()

    return {
        "result": result,
        "summary": summary,
        "generators": generators,
        "branches": branches,
    }


def _append_base_comparison(summary: pd.DataFrame, result, base_result) -> None:
    if base_result is None:
        summary["objective_delta_vs_base"] = 0.0
        summary["total_generation_delta_vs_base"] = 0.0
        return

    summary["objective_delta_vs_base"] = result.objective - base_result.objective
    summary["total_generation_delta_vs_base"] = result.total_generation - base_result.total_generation


def _error_summary(scenario: str, case: PowerCase, exc: Exception) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "scenario": scenario,
                "case_name": case.name,
                "success": False,
                "status": "ERROR",
                "message": str(exc),
                "objective": math.nan,
                "max_power_balance_violation": math.nan,
                "max_generator_limit_violation": math.nan,
                "max_branch_thermal_violation": math.nan,
                "max_branch_angle_violation": math.nan,
                "n_generators_at_pmin": 0,
                "n_generators_at_pmax": 0,
                "n_branches_thermal_binding": 0,
                "n_branches_angle_binding": 0,
                "branch_limit_constraints": 0,
                "angle_limit_constraints": 0,
                "objective_delta_vs_base": math.nan,
                "total_generation_delta_vs_base": math.nan,
            }
        ]
    )


def _relaxed_limits_case(case: PowerCase) -> PowerCase:
    """Relax branch-flow and angle limits; solve call also disables both."""
    branches = [
        replace(
            branch,
            rate_a=branch.rate_a * 10.0 if branch.rate_a > 0.0 else 0.0,
            angmin=-math.pi,
            angmax=math.pi,
        )
        for branch in case.branches
    ]
    return replace(case, name=f"{case.name}_relaxed_limits", branches=branches)


def _branch_congestion_case(case: PowerCase, base_result) -> PowerCase:
    """Tighten the most-loaded branch below its base flow."""
    branch = _most_loaded_branch(case, base_result)
    if branch is None:
        return replace(case, name=f"{case.name}_branch_congestion")

    base_flow = abs(base_result.branch_flow[branch.id])
    new_rate = max(1e-6, 0.98 * base_flow)
    branches = [
        replace(candidate, rate_a=new_rate) if candidate.id == branch.id else candidate
        for candidate in case.branches
    ]
    return replace(case, name=f"{case.name}_branch_congestion_{branch.id}", branches=branches)


def _gen_reduction_case(case: PowerCase, base_result) -> PowerCase:
    """Reduce the cheapest dispatched generator's Pmax below base dispatch."""
    gen = _cheapest_dispatched_generator(case, base_result)
    if gen is None:
        return replace(case, name=f"{case.name}_gen_reduction")

    base_pg = base_result.pg[gen.id]
    other_headroom = sum(
        max(0.0, candidate.pmax - base_result.pg.get(candidate.id, candidate.pmin))
        for candidate in case.generators
        if candidate.status == 1 and candidate.id != gen.id
    )
    reduction = 0.5 * other_headroom
    new_pmax = max(gen.pmin, base_pg - reduction)
    generators = [
        replace(candidate, pmax=new_pmax) if candidate.id == gen.id else candidate
        for candidate in case.generators
    ]
    return replace(case, name=f"{case.name}_gen_reduction_{gen.id}", generators=generators)


def _gen_expansion_case(case: PowerCase) -> PowerCase:
    """Increase the cheapest online generator's Pmax."""
    active = [gen for gen in case.generators if gen.status == 1 and gen.pmax > gen.pmin]
    if not active:
        return replace(case, name=f"{case.name}_gen_expansion")

    target = min(active, key=lambda gen: (gen.cost_c1, gen.cost_c2, gen.index))
    generators = [
        replace(candidate, pmax=candidate.pmax * 1.5) if candidate.id == target.id else candidate
        for candidate in case.generators
    ]
    return replace(case, name=f"{case.name}_gen_expansion_{target.id}", generators=generators)


def _load_scaled_case(case: PowerCase, *, load_multiplier: float) -> PowerCase:
    loads: list[Load] = [
        replace(load, pd=load.pd * load_multiplier, qd=load.qd * load_multiplier)
        for load in case.loads
    ]
    return replace(case, name=f"{case.name}_load_x{load_multiplier}", loads=loads)


def _angle_limited_case(case: PowerCase, base_result) -> PowerCase:
    """Tighten one active angle-difference limit below its base angle."""
    branch = _largest_angle_branch(case, base_result)
    if branch is None:
        return replace(case, name=f"{case.name}_angle_limited")

    angle = base_result.theta[branch.from_bus] - base_result.theta[branch.to_bus]
    limit = max(1e-4, 0.98 * abs(angle))
    branches = [
        replace(candidate, angmin=-limit, angmax=limit) if candidate.id == branch.id else candidate
        for candidate in case.branches
    ]
    return replace(case, name=f"{case.name}_angle_limited_{branch.id}", branches=branches)


def _alternate_slack_case(case: PowerCase) -> PowerCase:
    """Change the reference bus to another bus; economic dispatch should match."""
    candidates = [bus.id for bus in case.buses if bus.id != case.slack_bus_id]
    if not candidates:
        return replace(case, name=f"{case.name}_same_slack")
    return replace(case, name=f"{case.name}_slack_{candidates[0]}", slack_bus_id=candidates[0])


def _branch_outage_case(case: PowerCase, base_result) -> PowerCase:
    """Open a low-loaded active branch to test topology-change behavior."""
    active = [
        branch
        for branch in case.branches
        if branch.status == 1 and branch.id in base_result.branch_flow
    ]
    if not active:
        return replace(case, name=f"{case.name}_branch_outage")

    target = min(active, key=lambda branch: abs(base_result.branch_flow[branch.id]))
    branches: list[Branch] = [
        replace(branch, status=0) if branch.id == target.id else branch
        for branch in case.branches
    ]
    return replace(case, name=f"{case.name}_branch_outage_{target.id}", branches=branches)


def _most_loaded_branch(case: PowerCase, base_result) -> Branch | None:
    candidates = [
        branch
        for branch in case.branches
        if branch.status == 1 and branch.rate_a > 0.0 and branch.id in base_result.branch_flow
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda branch: abs(base_result.branch_flow[branch.id]) / branch.rate_a)


def _largest_angle_branch(case: PowerCase, base_result) -> Branch | None:
    candidates = [
        branch
        for branch in case.branches
        if branch.status == 1
        and branch.id in base_result.branch_flow
        and branch.from_bus in base_result.theta
        and branch.to_bus in base_result.theta
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda branch: abs(base_result.theta[branch.from_bus] - base_result.theta[branch.to_bus]),
    )


def _cheapest_dispatched_generator(case: PowerCase, base_result) -> Generator | None:
    candidates = [
        gen
        for gen in case.generators
        if gen.status == 1 and gen.id in base_result.pg and base_result.pg[gen.id] > gen.pmin + 1e-6
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda gen: (gen.cost_c1, gen.cost_c2, gen.index))
