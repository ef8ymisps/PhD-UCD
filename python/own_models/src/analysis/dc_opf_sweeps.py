"""Notebook-friendly DC OPF validation sweeps across PGLib cases."""

from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import time
from typing import Iterable

import pandas as pd

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat
from own_models.src.analysis.contingencies import apply_branch_outage, apply_generator_outage
from own_models.src.data.case import Branch, Generator, Load, PowerCase
from own_models.src.opf.dc.solve import solve_dc_opf_pyomo


DEFAULT_SETTINGS = {
    "relaxed_rate_multiplier": 10.0,
    "relaxed_angle_limit_deg": 180.0,
    "branch_rate_multipliers": [1.0, 0.8, 0.6, 0.4],
    "generator_pmax_multipliers": [1.0, 0.8, 0.6, 0.4],
    "load_multipliers": [0.8, 1.0, 1.2, 1.4],
    "angle_limits_deg": [30.0, 20.0, 10.0, 5.0],
    "include_no_reference_slack": True,
    "branch_outage_top_k_loading": 3,
    "branch_outage_top_k_flow": 3,
    "generator_outage_top_k_cheapest": 2,
    "generator_outage_top_k_pg": 2,
}


def run_compact_dc_validation_for_paths(
    case_paths: Iterable[str | Path],
    *,
    settings: dict | None = None,
    cost_power_units: str = "mw",
) -> pd.DataFrame:
    """Run compact DC OPF validation sweeps for many MATPOWER ``.mat`` files."""
    rows: list[dict[str, object]] = []
    for path in case_paths:
        case = load_powercase_from_matpower_mat(path)
        rows.extend(
            run_compact_dc_validation(
                case,
                settings=settings,
                cost_power_units=cost_power_units,
            )
        )
    return pd.DataFrame(rows)


def run_compact_dc_validation(
    case: PowerCase,
    *,
    settings: dict | None = None,
    cost_power_units: str = "mw",
) -> list[dict[str, object]]:
    """Run base, limit, stress, slack, and outage checks for one case."""
    cfg = {**DEFAULT_SETTINGS, **(settings or {})}
    rows: list[dict[str, object]] = []

    base = _run_case(
        case,
        scenario="base",
        cost_power_units=cost_power_units,
    )
    rows.append(base)
    base_result = base["result"]

    if base_result is None:
        return [_strip_result(row) for row in rows]

    rows.append(
        _run_case(
            relax_limits(
                case,
                rate_multiplier=float(cfg["relaxed_rate_multiplier"]),
                angle_limit_deg=float(cfg["relaxed_angle_limit_deg"]),
            ),
            scenario="relaxed_limits",
            parameter_name="rate_multiplier",
            parameter_value=float(cfg["relaxed_rate_multiplier"]),
            cost_power_units=cost_power_units,
            base_result=base_result,
        )
    )

    outage_target_branch = select_highest_loaded_branch(case, base_result)
    for multiplier in cfg["branch_rate_multipliers"]:
        rows.append(
            _run_case(
                scale_all_branch_rates(case, float(multiplier)),
                scenario="branch_congestion",
                target_type="system",
                target_id="all_branches",
                parameter_name="rate_multiplier",
                parameter_value=float(multiplier),
                cost_power_units=cost_power_units,
                base_result=base_result,
            )
        )

    generator = select_cheapest_dispatched_generator(case, base_result)
    if generator is not None:
        for multiplier in cfg["generator_pmax_multipliers"]:
            rows.append(
                _run_case(
                    scale_generator_pmax(case, generator.id, float(multiplier)),
                    scenario="generator_capacity",
                    target_type="generator",
                    target_id=generator.id,
                    parameter_name="pmax_multiplier",
                    parameter_value=float(multiplier),
                    cost_power_units=cost_power_units,
                    base_result=base_result,
                )
            )

    for multiplier in cfg["load_multipliers"]:
        rows.append(
            _run_case(
                scale_loads(case, float(multiplier)),
                scenario="load_scaling",
                parameter_name="load_multiplier",
                parameter_value=float(multiplier),
                cost_power_units=cost_power_units,
                base_result=base_result,
            )
        )

    for angle_limit_deg in cfg["angle_limits_deg"]:
        rows.append(
            _run_case(
                set_system_angle_limit(case, float(angle_limit_deg)),
                scenario="system_angle_limits",
                parameter_name="angle_limit_deg",
                parameter_value=float(angle_limit_deg),
                cost_power_units=cost_power_units,
                base_result=base_result,
            )
        )

    alternate_slack = select_alternate_slack_bus(case)
    if alternate_slack is not None:
        rows.append(
            _run_case(
                change_slack_bus(case, alternate_slack),
                scenario="slack_consistency",
                target_type="bus",
                target_id=alternate_slack,
                parameter_name="slack_bus",
                parameter_value=alternate_slack,
                cost_power_units=cost_power_units,
                base_result=base_result,
            )
        )

    if bool(cfg["include_no_reference_slack"]):
        rows.append(
            _run_case(
                case,
                scenario="slack_without_reference",
                parameter_name="fix_reference_angle",
                parameter_value=False,
                cost_power_units=cost_power_units,
                base_result=base_result,
                fix_reference_angle=False,
            )
        )

    for outage in select_branch_outages(
        case,
        base_result,
        top_k_loading=int(cfg["branch_outage_top_k_loading"]),
        top_k_flow=int(cfg["branch_outage_top_k_flow"]),
    ):
        rows.append(
            _run_case(
                apply_branch_outage(case, outage["branch_id"]),
                scenario="branch_outage",
                target_type="branch",
                target_id=outage["branch_id"],
                parameter_name="status",
                parameter_value=0.0,
                cost_power_units=cost_power_units,
                base_result=base_result,
                metadata=outage,
            )
        )

    for outage in select_generator_outages(
        case,
        base_result,
        top_k_cheapest=int(cfg["generator_outage_top_k_cheapest"]),
        top_k_pg=int(cfg["generator_outage_top_k_pg"]),
    ):
        rows.append(
            _run_case(
                apply_generator_outage(case, outage["gen_id"]),
                scenario="generator_outage",
                target_type="generator",
                target_id=outage["gen_id"],
                parameter_name="status",
                parameter_value=0.0,
                cost_power_units=cost_power_units,
                base_result=base_result,
                metadata=outage,
            )
        )

    return [_strip_result(row) for row in rows]


def relax_limits(case: PowerCase, *, rate_multiplier: float, angle_limit_deg: float) -> PowerCase:
    """Relax all branch rate and angle-difference limits."""
    limit = math.radians(angle_limit_deg)
    branches = [
        replace(
            branch,
            rate_a=branch.rate_a * rate_multiplier if branch.rate_a > 0.0 else 0.0,
            angmin=-limit,
            angmax=limit,
        )
        for branch in case.branches
    ]
    return replace(case, name=f"{case.name}_relaxed", branches=branches)


def scale_branch_rate(case: PowerCase, branch_id: str, multiplier: float) -> PowerCase:
    """Scale one branch's active flow rating by ``multiplier``."""
    branches: list[Branch] = [
        replace(branch, rate_a=branch.rate_a * multiplier) if branch.id == branch_id else branch
        for branch in case.branches
    ]
    return replace(case, name=f"{case.name}_branch_{branch_id}_rate_x{multiplier}", branches=branches)


def scale_all_branch_rates(case: PowerCase, multiplier: float) -> PowerCase:
    """Scale every positive active branch-flow rating by ``multiplier``."""
    branches: list[Branch] = [
        replace(branch, rate_a=branch.rate_a * multiplier if branch.rate_a > 0.0 else branch.rate_a)
        for branch in case.branches
    ]
    return replace(case, name=f"{case.name}_all_branch_rates_x{multiplier}", branches=branches)


def scale_generator_pmax(case: PowerCase, gen_id: str, multiplier: float) -> PowerCase:
    """Scale one generator's active-power upper limit by ``multiplier``."""
    generators: list[Generator] = [
        replace(gen, pmax=max(gen.pmin, gen.pmax * multiplier)) if gen.id == gen_id else gen
        for gen in case.generators
    ]
    return replace(case, name=f"{case.name}_gen_{gen_id}_pmax_x{multiplier}", generators=generators)


def scale_loads(case: PowerCase, multiplier: float) -> PowerCase:
    """Scale all active and reactive loads by ``multiplier``."""
    loads: list[Load] = [
        replace(load, pd=load.pd * multiplier, qd=load.qd * multiplier)
        for load in case.loads
    ]
    return replace(case, name=f"{case.name}_load_x{multiplier}", loads=loads)


def set_system_angle_limit(case: PowerCase, angle_limit_deg: float) -> PowerCase:
    """Set the same symmetric angle-difference limit on every branch."""
    limit = math.radians(angle_limit_deg)
    branches: list[Branch] = [
        replace(branch, angmin=-limit, angmax=limit)
        for branch in case.branches
    ]
    return replace(case, name=f"{case.name}_angle_{angle_limit_deg}deg", branches=branches)


def change_slack_bus(case: PowerCase, slack_bus_id: str) -> PowerCase:
    """Change only the reference bus id used by the OPF model."""
    return replace(case, name=f"{case.name}_slack_{slack_bus_id}", slack_bus_id=slack_bus_id)


def select_highest_loaded_branch(case: PowerCase, result) -> Branch | None:
    """Select the active branch with highest absolute flow/rating ratio."""
    candidates = [
        branch
        for branch in case.branches
        if branch.status == 1 and branch.rate_a > 0.0 and branch.id in result.branch_flow
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda branch: abs(result.branch_flow[branch.id]) / branch.rate_a)


def select_cheapest_dispatched_generator(case: PowerCase, result) -> Generator | None:
    """Select the cheapest online generator that produces above Pmin."""
    candidates = [
        gen
        for gen in case.generators
        if gen.status == 1 and gen.id in result.pg and result.pg[gen.id] > gen.pmin + 1e-7
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda gen: (gen.cost_c1, gen.cost_c2, gen.index))


def select_alternate_slack_bus(case: PowerCase) -> str | None:
    """Pick a deterministic non-current slack bus for reference-bus checks."""
    for bus in case.buses:
        if bus.id != case.slack_bus_id:
            return bus.id
    return None


def select_branch_outages(
    case: PowerCase,
    result,
    *,
    top_k_loading: int,
    top_k_flow: int,
) -> list[dict[str, object]]:
    """Select branch outages by highest loading and highest absolute flow."""
    candidates = []
    for branch in case.branches:
        if branch.status == 0 or branch.id not in result.branch_flow:
            continue
        flow = result.branch_flow[branch.id]
        loading_pct = abs(flow) / branch.rate_a * 100.0 if branch.rate_a > 0.0 else math.inf
        candidates.append(
            {
                "branch": branch,
                "branch_id": branch.id,
                "base_flow_pu": flow,
                "rate_a_pu": branch.rate_a,
                "base_loading_pct": loading_pct,
            }
        )

    by_loading = sorted(
        candidates,
        key=lambda item: (float(item["base_loading_pct"]), abs(float(item["base_flow_pu"]))),
        reverse=True,
    )
    by_flow = sorted(candidates, key=lambda item: abs(float(item["base_flow_pu"])), reverse=True)

    selected: dict[str, dict[str, object]] = {}
    _add_branch_selection(selected, by_loading[:top_k_loading], "highest_loading", "loading_rank")
    _add_branch_selection(selected, by_flow[:top_k_flow], "highest_flow", "flow_rank")

    return sorted(
        selected.values(),
        key=lambda item: (
            min(_rank_or_large(item.get("loading_rank")), _rank_or_large(item.get("flow_rank"))),
            str(item["branch_id"]),
        ),
    )


def select_generator_outages(
    case: PowerCase,
    result,
    *,
    top_k_cheapest: int,
    top_k_pg: int,
) -> list[dict[str, object]]:
    """Select generator outages by cheapest dispatched and largest dispatched."""
    candidates = []
    for gen in case.generators:
        if gen.status == 0 or gen.id not in result.pg:
            continue
        pg = result.pg[gen.id]
        if pg <= gen.pmin + 1e-7:
            continue
        candidates.append(
            {
                "gen": gen,
                "gen_id": gen.id,
                "bus_id": gen.bus,
                "base_pg_pu": pg,
                "pmax_pu": gen.pmax,
                "cost_c1": gen.cost_c1,
                "cost_c2": gen.cost_c2,
            }
        )

    by_cost = sorted(
        candidates,
        key=lambda item: (
            float(item["cost_c1"]),
            float(item["cost_c2"]),
            int(item["gen"].index),
        ),
    )
    by_pg = sorted(candidates, key=lambda item: float(item["base_pg_pu"]), reverse=True)

    selected: dict[str, dict[str, object]] = {}
    _add_generator_selection(selected, by_cost[:top_k_cheapest], "cheapest_dispatched", "cost_rank")
    _add_generator_selection(selected, by_pg[:top_k_pg], "largest_dispatched", "pg_rank")

    return sorted(
        selected.values(),
        key=lambda item: (
            min(_rank_or_large(item.get("cost_rank")), _rank_or_large(item.get("pg_rank"))),
            str(item["gen_id"]),
        ),
    )


def _add_branch_selection(
    selected: dict[str, dict[str, object]],
    items: list[dict[str, object]],
    reason: str,
    rank_key: str,
) -> None:
    for rank, item in enumerate(items, start=1):
        branch_id = str(item["branch_id"])
        if branch_id not in selected:
            selected[branch_id] = {
                key: value for key, value in item.items() if key != "branch"
            }
            selected[branch_id]["selection_reason"] = reason
        else:
            selected[branch_id]["selection_reason"] += f"+{reason}"
        selected[branch_id][rank_key] = rank


def _add_generator_selection(
    selected: dict[str, dict[str, object]],
    items: list[dict[str, object]],
    reason: str,
    rank_key: str,
) -> None:
    for rank, item in enumerate(items, start=1):
        gen_id = str(item["gen_id"])
        if gen_id not in selected:
            selected[gen_id] = {
                key: value for key, value in item.items() if key != "gen"
            }
            selected[gen_id]["selection_reason"] = reason
        else:
            selected[gen_id]["selection_reason"] += f"+{reason}"
        selected[gen_id][rank_key] = rank


def _rank_or_large(value) -> int:
    return int(value) if value is not None and not pd.isna(value) else 10**9


def _run_case(
    case: PowerCase,
    *,
    scenario: str,
    cost_power_units: str,
    target_type: str = "",
    target_id: str = "",
    parameter_name: str = "",
    parameter_value=None,
    base_result=None,
    fix_reference_angle: bool = True,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    start = time.perf_counter()
    try:
        model, _raw_results, result = solve_dc_opf_pyomo(
            case,
            cost_power_units=cost_power_units,
            include_branch_limits=True,
            include_angle_limits=True,
            fix_reference_angle=fix_reference_angle,
        )
        solve_time_sec = time.perf_counter() - start
        return {
            **_success_row(
                case,
                result,
                model,
                scenario=scenario,
                target_type=target_type,
                target_id=target_id,
                parameter_name=parameter_name,
                parameter_value=parameter_value,
                base_result=base_result,
                solve_time_sec=solve_time_sec,
                metadata=metadata,
            ),
            "result": result,
        }
    except Exception as exc:
        solve_time_sec = time.perf_counter() - start
        return {
            **_failure_row(
                case,
                exc,
                scenario=scenario,
                target_type=target_type,
                target_id=target_id,
                parameter_name=parameter_name,
                parameter_value=parameter_value,
                solve_time_sec=solve_time_sec,
                metadata=metadata,
            ),
            "result": None,
        }


def _success_row(
    case: PowerCase,
    result,
    model,
    *,
    scenario: str,
    target_type: str,
    target_id: str,
    parameter_name: str,
    parameter_value,
    base_result,
    solve_time_sec: float,
    metadata: dict[str, object] | None,
) -> dict[str, object]:
    branch_metrics = _branch_metrics(case, result)
    generator_metrics = _generator_metrics(case, result)
    objective_delta = 0.0 if base_result is None else result.objective - base_result.objective
    row = {
        "case_name": case.name,
        "n_buses": len(case.buses),
        "n_branches": sum(1 for branch in case.branches if branch.status == 1),
        "n_generators": sum(1 for gen in case.generators if gen.status == 1),
        "scenario": scenario,
        "target_type": target_type,
        "target_id": target_id,
        "parameter_name": parameter_name,
        "parameter_value": parameter_value,
        "success": True,
        "status": result.status,
        "termination": result.termination_condition,
        "objective": result.objective,
        "objective_delta_vs_base": objective_delta,
        "total_generation": result.total_generation,
        "total_load": result.total_load,
        "max_balance_residual": result.max_power_balance_residual,
        "max_branch_loading_pct": branch_metrics["max_branch_loading_pct"],
        "n_thermal_binding": branch_metrics["n_thermal_binding"],
        "n_angle_binding": branch_metrics["n_angle_binding"],
        "n_gen_at_pmin": generator_metrics["n_gen_at_pmin"],
        "n_gen_at_pmax": generator_metrics["n_gen_at_pmax"],
        "branch_limit_constraints": len(model.branch_flow_limit),
        "angle_limit_constraints": len(model.angle_difference_limit),
        "solve_time_sec": solve_time_sec,
        "message": result.message,
    }
    _add_metadata(row, metadata)
    return row


def _failure_row(
    case: PowerCase,
    exc: Exception,
    *,
    scenario: str,
    target_type: str,
    target_id: str,
    parameter_name: str,
    parameter_value,
    solve_time_sec: float,
    metadata: dict[str, object] | None,
) -> dict[str, object]:
    row = {
        "case_name": case.name,
        "n_buses": len(case.buses),
        "n_branches": sum(1 for branch in case.branches if branch.status == 1),
        "n_generators": sum(1 for gen in case.generators if gen.status == 1),
        "scenario": scenario,
        "target_type": target_type,
        "target_id": target_id,
        "parameter_name": parameter_name,
        "parameter_value": parameter_value,
        "success": False,
        "status": "ERROR",
        "termination": "ERROR",
        "objective": math.nan,
        "objective_delta_vs_base": math.nan,
        "total_generation": math.nan,
        "total_load": sum(load.pd for load in case.loads if load.status == 1),
        "max_balance_residual": math.nan,
        "max_branch_loading_pct": math.nan,
        "n_thermal_binding": 0,
        "n_angle_binding": 0,
        "n_gen_at_pmin": 0,
        "n_gen_at_pmax": 0,
        "branch_limit_constraints": 0,
        "angle_limit_constraints": 0,
        "solve_time_sec": solve_time_sec,
        "message": str(exc),
    }
    _add_metadata(row, metadata)
    return row


def _add_metadata(row: dict[str, object], metadata: dict[str, object] | None) -> None:
    if not metadata:
        return
    for key, value in metadata.items():
        row[key] = value


def _branch_metrics(case: PowerCase, result, *, tol: float = 1e-6) -> dict[str, float | int]:
    max_loading = 0.0
    thermal_binding = 0
    angle_binding = 0
    for branch in case.branches:
        if branch.status == 0 or branch.id not in result.branch_flow:
            continue
        flow = result.branch_flow[branch.id]
        if branch.rate_a > 0.0:
            loading = abs(flow) / branch.rate_a * 100.0
            max_loading = max(max_loading, loading)
            thermal_binding += int(abs(abs(flow) - branch.rate_a) <= tol)
        angle_diff = result.theta[branch.from_bus] - result.theta[branch.to_bus]
        angle_binding += int(
            abs(angle_diff - branch.angmin) <= tol or abs(angle_diff - branch.angmax) <= tol
        )
    return {
        "max_branch_loading_pct": max_loading,
        "n_thermal_binding": thermal_binding,
        "n_angle_binding": angle_binding,
    }


def _generator_metrics(case: PowerCase, result, *, tol: float = 1e-6) -> dict[str, int]:
    at_pmin = 0
    at_pmax = 0
    for gen in case.generators:
        if gen.status == 0 or gen.id not in result.pg:
            continue
        pg = result.pg[gen.id]
        at_pmin += int(abs(pg - gen.pmin) <= tol)
        at_pmax += int(abs(pg - gen.pmax) <= tol)
    return {
        "n_gen_at_pmin": at_pmin,
        "n_gen_at_pmax": at_pmax,
    }


def _strip_result(row: dict[str, object]) -> dict[str, object]:
    clean = dict(row)
    clean.pop("result", None)
    return clean
