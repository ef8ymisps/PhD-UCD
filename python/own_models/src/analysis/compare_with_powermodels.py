from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from own_models.src.adapters.powermodels_csv_adapter import (
    cost_coefficient_validation_table,
    load_powermodels_json,
    load_powercase_from_powermodels_json,
)
from own_models.src.analysis.violations import (
    binding_set_comparison,
    dc_opf_branch_rows,
    dc_opf_generator_rows,
    dc_opf_summary_row,
)
from own_models.src.opf.dc.solve import solve_dc_opf_pyomo


SUCCESS_STATUSES = {"OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED"}


def load_powermodels_reference(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _reference_solved(reference: dict) -> bool:
    return str(reference.get("termination_status", "")).upper() in SUCCESS_STATUSES or bool(
        reference.get("solved", False)
    )


def compare_dc_opf_generators_with_powermodels(case, own_result, reference: dict) -> pd.DataFrame:
    rows = []
    pm_generators = reference.get("generators", {})
    for gen in case.generators:
        if gen.status == 0 or gen.id not in own_result.pg or gen.id not in pm_generators:
            continue
        pm_pg = pm_generators[gen.id].get("pg")
        if pm_pg is None:
            continue
        rows.append(
            {
                "gen_id": gen.id,
                "own_pg": own_result.pg[gen.id],
                "pm_pg": float(pm_pg),
                "pg_diff": own_result.pg[gen.id] - float(pm_pg),
                "abs_pg_diff": abs(own_result.pg[gen.id] - float(pm_pg)),
                "pmin": gen.pmin,
                "pmax": gen.pmax,
                "own_at_pmin": abs(own_result.pg[gen.id] - gen.pmin) <= 1e-6,
                "own_at_pmax": abs(own_result.pg[gen.id] - gen.pmax) <= 1e-6,
                "pm_at_pmin": abs(float(pm_pg) - gen.pmin) <= 1e-6,
                "pm_at_pmax": abs(float(pm_pg) - gen.pmax) <= 1e-6,
            }
        )
    return pd.DataFrame(rows)


def compare_dc_opf_buses_with_powermodels(case, own_result, reference: dict) -> pd.DataFrame:
    rows = []
    pm_buses = reference.get("buses", {})
    for bus in case.buses:
        if bus.id not in own_result.theta or bus.id not in pm_buses:
            continue
        pm_va = pm_buses[bus.id].get("va")
        if pm_va is None:
            continue
        rows.append(
            {
                "bus_id": bus.id,
                "own_va": own_result.theta[bus.id],
                "pm_va": float(pm_va),
                "va_diff": own_result.theta[bus.id] - float(pm_va),
                "abs_va_diff": abs(own_result.theta[bus.id] - float(pm_va)),
            }
        )
    return pd.DataFrame(rows)


def compare_dc_opf_branches_with_powermodels(case, own_result, reference: dict) -> pd.DataFrame:
    branch_by_id = {branch.id: branch for branch in case.branches}
    rows = []
    pm_branches = reference.get("branches", {})
    pm_buses = reference.get("buses", {})
    for branch_id, flow in own_result.branch_flows.items():
        if branch_id not in pm_branches:
            continue
        pm_pf = pm_branches[branch_id].get("pf")
        if pm_pf is None:
            continue
        pm_pf = float(pm_pf)
        branch = branch_by_id[branch_id]
        pm_from_va = pm_buses.get(branch.from_bus, {}).get("va")
        pm_to_va = pm_buses.get(branch.to_bus, {}).get("va")
        own_loading_pct = 100.0 * abs(flow) / branch.rate_a if branch.rate_a > 0.0 else float("nan")
        pm_loading_pct = 100.0 * abs(pm_pf) / branch.rate_a if branch.rate_a > 0.0 else float("nan")
        angle_diff = own_result.theta[branch.from_bus] - own_result.theta[branch.to_bus]
        row = {
            "branch_id": branch_id,
            "own_pf": flow,
            "pm_pf": pm_pf,
            "pf_diff": flow - pm_pf,
            "abs_pf_diff": abs(flow - pm_pf),
            "rate_a": branch.rate_a,
            "own_loading_pct": own_loading_pct,
            "pm_loading_pct": pm_loading_pct,
            "loading_pct_diff": own_loading_pct - pm_loading_pct,
            "abs_loading_pct_diff": abs(own_loading_pct - pm_loading_pct),
            "own_thermal_binding": branch.rate_a > 0.0 and abs(abs(flow) - branch.rate_a) <= 1e-6,
            "pm_thermal_binding": branch.rate_a > 0.0 and abs(abs(pm_pf) - branch.rate_a) <= 1e-6,
            "angle_diff": angle_diff,
            "angmin": branch.angmin,
            "angmax": branch.angmax,
            "own_angle_lower_binding": abs(angle_diff - branch.angmin) <= 1e-6,
            "own_angle_upper_binding": abs(angle_diff - branch.angmax) <= 1e-6,
        }
        if pm_from_va is not None and pm_to_va is not None:
            pm_angle_diff = float(pm_from_va) - float(pm_to_va)
            row["pm_angle_diff"] = pm_angle_diff
            row["pm_angle_lower_binding"] = abs(pm_angle_diff - branch.angmin) <= 1e-6
            row["pm_angle_upper_binding"] = abs(pm_angle_diff - branch.angmax) <= 1e-6
        else:
            row["pm_angle_diff"] = float("nan")
            row["pm_angle_lower_binding"] = False
            row["pm_angle_upper_binding"] = False
        rows.append(row)
    return pd.DataFrame(rows)


def dc_opf_powermodels_comparison_summary(
    case,
    own_result,
    reference: dict,
    generator_rows: pd.DataFrame,
    bus_rows: pd.DataFrame,
    branch_rows: pd.DataFrame,
    *,
    scenario: str,
) -> pd.DataFrame:
    own_solved = bool(own_result.success)
    pm_solved = _reference_solved(reference)
    pm_objective = reference.get("objective")
    own_objective = own_result.objective
    objective_abs_diff = abs(own_objective - float(pm_objective)) if pm_objective is not None else np.nan
    objective_rel_diff = (
        objective_abs_diff / (1.0 + abs(float(pm_objective))) if pm_objective is not None else np.nan
    )

    both_solved = own_solved and pm_solved

    def _agg(frame: pd.DataFrame, col: str) -> tuple[float, float, float]:
        if not both_solved or frame.empty or col not in frame.columns:
            return np.nan, np.nan, np.nan
        series = frame[col].dropna()
        if series.empty:
            return np.nan, np.nan, np.nan
        return float(series.max()), float(series.mean()), float(series.std(ddof=0))

    max_pg_diff, mean_pg_diff, std_pg_diff = _agg(generator_rows, "abs_pg_diff")
    max_va_diff, mean_va_diff, std_va_diff = _agg(bus_rows, "abs_va_diff")
    max_pf_diff, mean_pf_diff, std_pf_diff = _agg(branch_rows, "abs_pf_diff")
    max_loading_pct_diff, mean_loading_pct_diff, std_loading_pct_diff = _agg(
        branch_rows, "abs_loading_pct_diff"
    )

    binding_counts = (
        binding_set_comparison(generator_rows, branch_rows=branch_rows) if both_solved else {}
    )

    row = {
        "case_name": case.name,
        "scenario": scenario,
        "own_success": own_solved,
        "pm_success": pm_solved,
        "outcome_match": own_solved == pm_solved,
        "own_status": own_result.status,
        "pm_status": reference.get("termination_status", "UNKNOWN"),
        "own_objective": own_objective,
        "pm_objective": pm_objective,
        "objective_abs_diff": objective_abs_diff,
        "objective_rel_diff": objective_rel_diff,
        "max_pg_diff": max_pg_diff,
        "mean_pg_diff": mean_pg_diff,
        "std_pg_diff": std_pg_diff,
        "max_va_diff": max_va_diff,
        "mean_va_diff": mean_va_diff,
        "std_va_diff": std_va_diff,
        "max_pf_diff": max_pf_diff,
        "mean_pf_diff": mean_pf_diff,
        "std_pf_diff": std_pf_diff,
        "max_loading_pct_diff": max_loading_pct_diff,
        "mean_loading_pct_diff": mean_loading_pct_diff,
        "std_loading_pct_diff": std_loading_pct_diff,
        "max_power_balance_violation": own_result.max_power_balance_violation,
        "own_solve_iterations": getattr(own_result, "solve_iterations", np.nan),
        "pm_solve_iterations": np.nan,
    }
    row.update(binding_counts)

    return pd.DataFrame([row])


def run_python_dc_opf_against_powermodels(
    case_json_path: str | Path,
    reference_json_path: str | Path,
    *,
    scenario: str,
) -> dict[str, pd.DataFrame]:
    pm_data = load_powermodels_json(case_json_path)
    case = load_powercase_from_powermodels_json(case_json_path)
    reference = load_powermodels_reference(reference_json_path)
    try:
        _model, _raw_results, own_result = solve_dc_opf_pyomo(
            case,
            cost_power_units="pu",
            include_branch_limits=True,
            include_angle_limits=True,
        )

        generator_rows = compare_dc_opf_generators_with_powermodels(case, own_result, reference)
        bus_rows = compare_dc_opf_buses_with_powermodels(case, own_result, reference)
        branch_rows = compare_dc_opf_branches_with_powermodels(case, own_result, reference)
        summary = dc_opf_powermodels_comparison_summary(
            case,
            own_result,
            reference,
            generator_rows,
            bus_rows,
            branch_rows,
            scenario=scenario,
        )
        own_summary = dc_opf_summary_row(case, own_result, scenario=scenario)
        own_generator_rows = dc_opf_generator_rows(case, own_result)
        own_branch_rows = dc_opf_branch_rows(case, own_result)
    except Exception as exc:
        own_result = None
        generator_rows = pd.DataFrame()
        bus_rows = pd.DataFrame()
        branch_rows = pd.DataFrame()
        pm_solved = _reference_solved(reference)
        summary = pd.DataFrame(
            [
                {
                    "case_name": case.name,
                    "scenario": scenario,
                    "own_success": False,
                    "pm_success": pm_solved,
                    "outcome_match": not pm_solved,
                    "own_status": "ERROR",
                    "pm_status": reference.get("termination_status", "UNKNOWN"),
                    "own_objective": np.nan,
                    "pm_objective": reference.get("objective"),
                    "objective_abs_diff": np.nan,
                    "objective_rel_diff": np.nan,
                    "max_pg_diff": np.nan,
                    "max_va_diff": np.nan,
                    "max_pf_diff": np.nan,
                    "max_power_balance_violation": np.nan,
                    "own_message": str(exc),
                }
            ]
        )
        own_summary = summary.copy()
        own_generator_rows = pd.DataFrame()
        own_branch_rows = pd.DataFrame()

    return {
        "summary": summary,
        "generators": generator_rows,
        "buses": bus_rows,
        "branches": branch_rows,
        "own_summary": own_summary,
        "own_generator_rows": own_generator_rows,
        "own_branch_rows": own_branch_rows,
        "cost_validation": cost_coefficient_validation_table(pm_data, case),
    }
