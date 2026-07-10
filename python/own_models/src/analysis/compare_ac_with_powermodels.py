from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from own_models.src.adapters.powermodels_csv_adapter import (
    cost_coefficient_validation_table,
    load_powercase_from_powermodels_json,
    load_powermodels_json,
)
from own_models.src.analysis.violations import (
    ac_opf_branch_rows,
    ac_opf_bus_rows,
    ac_opf_constraints_row,
    ac_opf_generator_rows,
    ac_opf_summary_row,
    binding_set_comparison,
)
from own_models.src.opf.ac import solve_ac_opf_pyomo


SUCCESS_STATUSES = {"OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED"}
INFEASIBLE_STATUSES = {"INFEASIBLE", "LOCALLY_INFEASIBLE", "ALMOST_INFEASIBLE"}


def load_powermodels_ac_reference(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def classify_powermodels_ac_outcome(reference: dict) -> str:
    status = str(reference.get("termination_status", "")).upper()
    if status in SUCCESS_STATUSES or bool(reference.get("solved", False)):
        return "SOLVED_FEASIBLE"
    if "INFEASIBLE" in status or status in INFEASIBLE_STATUSES or status in {"ITERATION_LIMIT", "TIME_LIMIT"}:
        return "FAILED_OR_INFEASIBLE"
    return "NUMERICAL_FAILURE"


def classify_python_ac_outcome(result) -> str:
    if result.success:
        return "SOLVED_FEASIBLE"
    status = str(result.status).upper()
    message = str(result.message).upper()
    if "INFEASIBLE" in message or "INCOMPATIBLE" in message:
        return "FAILED_OR_INFEASIBLE"
    if result.max_p_balance_violation > 1e-4 or result.max_q_balance_violation > 1e-4:
        return "FAILED_OR_INFEASIBLE"
    if status in {"4", "8", "9"}:
        return "FAILED_OR_INFEASIBLE"
    return "NUMERICAL_FAILURE"


def compare_ac_opf_generators_with_powermodels(case, own_result, reference: dict) -> pd.DataFrame:
    rows = []
    pm_generators = reference.get("generators", {})
    for gen in case.generators:
        if gen.status == 0 or gen.id not in own_result.pg or gen.id not in pm_generators:
            continue
        pm_pg = pm_generators[gen.id].get("pg")
        pm_qg = pm_generators[gen.id].get("qg")
        if pm_pg is None or pm_qg is None:
            continue
        pm_pg = float(pm_pg)
        pm_qg = float(pm_qg)
        rows.append(
            {
                "gen_id": gen.id,
                "own_pg": own_result.pg[gen.id],
                "pm_pg": pm_pg,
                "pg_diff": own_result.pg[gen.id] - pm_pg,
                "abs_pg_diff": abs(own_result.pg[gen.id] - pm_pg),
                "own_qg": own_result.qg[gen.id],
                "pm_qg": pm_qg,
                "qg_diff": own_result.qg[gen.id] - pm_qg,
                "abs_qg_diff": abs(own_result.qg[gen.id] - pm_qg),
                "pmin": gen.pmin,
                "pmax": gen.pmax,
                "qmin": gen.qmin,
                "qmax": gen.qmax,
                "own_at_pmin": abs(own_result.pg[gen.id] - gen.pmin) <= 1e-6,
                "own_at_pmax": abs(own_result.pg[gen.id] - gen.pmax) <= 1e-6,
                "own_at_qmin": abs(own_result.qg[gen.id] - gen.qmin) <= 1e-6,
                "own_at_qmax": abs(own_result.qg[gen.id] - gen.qmax) <= 1e-6,
                "pm_at_pmin": abs(pm_pg - gen.pmin) <= 1e-6,
                "pm_at_pmax": abs(pm_pg - gen.pmax) <= 1e-6,
                "pm_at_qmin": abs(pm_qg - gen.qmin) <= 1e-6,
                "pm_at_qmax": abs(pm_qg - gen.qmax) <= 1e-6,
            }
        )
    return pd.DataFrame(rows)


def compare_ac_opf_buses_with_powermodels(case, own_result, reference: dict) -> pd.DataFrame:
    rows = []
    pm_buses = reference.get("buses", {})
    for bus in case.buses:
        if bus.id not in own_result.vm or bus.id not in pm_buses:
            continue
        pm_vm = pm_buses[bus.id].get("vm")
        pm_va = pm_buses[bus.id].get("va")
        if pm_vm is None or pm_va is None:
            continue
        pm_vm = float(pm_vm)
        pm_va = float(pm_va)
        rows.append(
            {
                "bus_id": bus.id,
                "own_vm": own_result.vm[bus.id],
                "pm_vm": pm_vm,
                "vm_diff": own_result.vm[bus.id] - pm_vm,
                "abs_vm_diff": abs(own_result.vm[bus.id] - pm_vm),
                "own_va": own_result.va[bus.id],
                "pm_va": pm_va,
                "va_diff": own_result.va[bus.id] - pm_va,
                "abs_va_diff": abs(own_result.va[bus.id] - pm_va),
                "vmin": bus.vmin,
                "vmax": bus.vmax,
                "own_at_vmin": abs(own_result.vm[bus.id] - bus.vmin) <= 1e-6,
                "own_at_vmax": abs(own_result.vm[bus.id] - bus.vmax) <= 1e-6,
                "pm_at_vmin": abs(pm_vm - bus.vmin) <= 1e-6,
                "pm_at_vmax": abs(pm_vm - bus.vmax) <= 1e-6,
            }
        )
    return pd.DataFrame(rows)


def compare_ac_opf_branches_with_powermodels(case, own_result, reference: dict) -> pd.DataFrame:
    branch_by_id = {branch.id: branch for branch in case.branches}
    rows = []
    pm_branches = reference.get("branches", {})
    pm_buses = reference.get("buses", {})
    for branch_id, flow in own_result.branch_flows.items():
        if branch_id not in pm_branches:
            continue
        pm_flow = pm_branches[branch_id]
        if any(pm_flow.get(key) is None for key in ("pf", "qf", "pt", "qt")):
            continue
        branch = branch_by_id[branch_id]
        pm_from_va = pm_buses.get(branch.from_bus, {}).get("va")
        pm_to_va = pm_buses.get(branch.to_bus, {}).get("va")
        own_sf = (flow["pf"] ** 2 + flow["qf"] ** 2) ** 0.5
        own_st = (flow["pt"] ** 2 + flow["qt"] ** 2) ** 0.5
        pm_pf = float(pm_flow["pf"])
        pm_qf = float(pm_flow["qf"])
        pm_pt = float(pm_flow["pt"])
        pm_qt = float(pm_flow["qt"])
        pm_sf = (pm_pf**2 + pm_qf**2) ** 0.5
        pm_st = (pm_pt**2 + pm_qt**2) ** 0.5
        own_loading = max(own_sf, own_st)
        pm_loading = max(pm_sf, pm_st)
        own_loading_pct = 100.0 * own_loading / branch.rate_a if branch.rate_a > 0.0 else float("nan")
        pm_loading_pct = 100.0 * pm_loading / branch.rate_a if branch.rate_a > 0.0 else float("nan")
        angle_diff = own_result.va[branch.from_bus] - own_result.va[branch.to_bus]
        row = {
            "branch_id": branch_id,
            "own_pf": flow["pf"],
            "pm_pf": pm_pf,
            "abs_pf_diff": abs(flow["pf"] - pm_pf),
            "own_qf": flow["qf"],
            "pm_qf": pm_qf,
            "abs_qf_diff": abs(flow["qf"] - pm_qf),
            "own_pt": flow["pt"],
            "pm_pt": pm_pt,
            "abs_pt_diff": abs(flow["pt"] - pm_pt),
            "own_qt": flow["qt"],
            "pm_qt": pm_qt,
            "abs_qt_diff": abs(flow["qt"] - pm_qt),
            "own_sf": own_sf,
            "own_st": own_st,
            "pm_sf": pm_sf,
            "pm_st": pm_st,
            "rate_a": branch.rate_a,
            "own_loading_pct": own_loading_pct,
            "pm_loading_pct": pm_loading_pct,
            "loading_pct_diff": own_loading_pct - pm_loading_pct,
            "abs_loading_pct_diff": abs(own_loading_pct - pm_loading_pct),
            "own_thermal_binding": branch.rate_a > 0.0
            and (abs(own_sf - branch.rate_a) <= 1e-6 or abs(own_st - branch.rate_a) <= 1e-6),
            "pm_thermal_binding": branch.rate_a > 0.0
            and (abs(pm_sf - branch.rate_a) <= 1e-6 or abs(pm_st - branch.rate_a) <= 1e-6),
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


def ac_opf_powermodels_comparison_summary(
    case,
    own_result,
    reference: dict,
    generator_rows: pd.DataFrame,
    bus_rows: pd.DataFrame,
    branch_rows: pd.DataFrame,
    *,
    scenario: str,
) -> pd.DataFrame:
    own_class = classify_python_ac_outcome(own_result)
    pm_class = classify_powermodels_ac_outcome(reference)
    both_solved = own_class == "SOLVED_FEASIBLE" and pm_class == "SOLVED_FEASIBLE"
    pm_objective = reference.get("objective")
    objective_abs_diff = abs(own_result.objective - float(pm_objective)) if both_solved and pm_objective is not None else np.nan
    objective_rel_diff = (
        objective_abs_diff / (1.0 + abs(float(pm_objective))) if both_solved and pm_objective is not None else np.nan
    )
    branch_flow_cols = ["abs_pf_diff", "abs_qf_diff", "abs_pt_diff", "abs_qt_diff"]

    def _agg(frame: pd.DataFrame, col: str) -> tuple[float, float, float]:
        if not both_solved or frame.empty or col not in frame.columns:
            return np.nan, np.nan, np.nan
        series = frame[col].dropna()
        if series.empty:
            return np.nan, np.nan, np.nan
        return float(series.max()), float(series.mean()), float(series.std(ddof=0))

    max_pg_diff, mean_pg_diff, std_pg_diff = _agg(generator_rows, "abs_pg_diff")
    max_qg_diff, mean_qg_diff, std_qg_diff = _agg(generator_rows, "abs_qg_diff")
    max_vm_diff, mean_vm_diff, std_vm_diff = _agg(bus_rows, "abs_vm_diff")
    max_va_diff, mean_va_diff, std_va_diff = _agg(bus_rows, "abs_va_diff")
    max_pf_diff, mean_pf_diff, std_pf_diff = _agg(branch_rows, "abs_pf_diff")
    max_qf_diff, mean_qf_diff, std_qf_diff = _agg(branch_rows, "abs_qf_diff")
    max_loading_pct_diff, mean_loading_pct_diff, std_loading_pct_diff = _agg(
        branch_rows, "abs_loading_pct_diff"
    )

    binding_counts = (
        binding_set_comparison(generator_rows, bus_rows=bus_rows, branch_rows=branch_rows)
        if both_solved
        else {}
    )

    row = {
        "case_name": case.name,
        "scenario": scenario,
        "own_success": own_result.success,
        "pm_success": pm_class == "SOLVED_FEASIBLE",
        "own_outcome_class": own_class,
        "pm_outcome_class": pm_class,
        "outcome_match": own_class == pm_class,
        "own_status": own_result.status,
        "pm_status": reference.get("termination_status", "UNKNOWN"),
        "own_objective": own_result.objective,
        "pm_objective": pm_objective,
        "objective_abs_diff": objective_abs_diff,
        "objective_rel_diff": objective_rel_diff,
        "max_pg_diff": max_pg_diff,
        "mean_pg_diff": mean_pg_diff,
        "std_pg_diff": std_pg_diff,
        "max_qg_diff": max_qg_diff,
        "mean_qg_diff": mean_qg_diff,
        "std_qg_diff": std_qg_diff,
        "max_vm_diff": max_vm_diff,
        "mean_vm_diff": mean_vm_diff,
        "std_vm_diff": std_vm_diff,
        "max_va_diff": max_va_diff,
        "mean_va_diff": mean_va_diff,
        "std_va_diff": std_va_diff,
        "max_pf_diff": max_pf_diff,
        "mean_pf_diff": mean_pf_diff,
        "std_pf_diff": std_pf_diff,
        "max_qf_diff": max_qf_diff,
        "mean_qf_diff": mean_qf_diff,
        "std_qf_diff": std_qf_diff,
        "max_loading_pct_diff": max_loading_pct_diff,
        "mean_loading_pct_diff": mean_loading_pct_diff,
        "std_loading_pct_diff": std_loading_pct_diff,
        "max_branch_flow_diff": branch_rows[branch_flow_cols].max().max()
        if both_solved and not branch_rows.empty
        else np.nan,
        "max_p_balance_violation": own_result.max_p_balance_violation,
        "max_q_balance_violation": own_result.max_q_balance_violation,
        "max_thermal_violation": own_result.max_thermal_violation,
        "max_angle_violation": own_result.max_angle_violation,
        "own_solve_iterations": getattr(own_result, "solve_iterations", np.nan),
        "pm_solve_iterations": np.nan,
    }
    row.update(binding_counts)

    return pd.DataFrame([row])


def run_python_ac_opf_against_powermodels(
    case_json_path: str | Path,
    reference_json_path: str | Path,
    *,
    scenario: str,
    solver: str = "pyomo",
) -> dict[str, pd.DataFrame]:
    pm_data = load_powermodels_json(case_json_path)
    case = load_powercase_from_powermodels_json(case_json_path)
    reference = load_powermodels_ac_reference(reference_json_path)
    if solver != "pyomo":
        raise ValueError("The AC OPF implementation now supports solver='pyomo' only.")
    # cost_power_units="pu" and case-native voltage bounds (voltage_bounds=None) match
    # PowerModels.jl's conventions; solve_ac_opf_pyomo's own defaults ("mw", (0.95, 1.05))
    # are tuned for standalone use and inflate the objective ~100x vs PowerModels otherwise.
    own_result = solve_ac_opf_pyomo(case, cost_power_units="pu", voltage_bounds=None)

    generator_rows = compare_ac_opf_generators_with_powermodels(case, own_result, reference)
    bus_rows = compare_ac_opf_buses_with_powermodels(case, own_result, reference)
    branch_rows = compare_ac_opf_branches_with_powermodels(case, own_result, reference)
    summary = ac_opf_powermodels_comparison_summary(
        case,
        own_result,
        reference,
        generator_rows,
        bus_rows,
        branch_rows,
        scenario=scenario,
    )

    return {
        "summary": summary,
        "generators": generator_rows,
        "buses": bus_rows,
        "branches": branch_rows,
        "constraints": ac_opf_constraints_row(case, own_result, scenario=scenario),
        "own_summary": ac_opf_summary_row(case, own_result, scenario=scenario),
        "own_generator_rows": ac_opf_generator_rows(case, own_result),
        "own_bus_rows": ac_opf_bus_rows(case, own_result),
        "own_branch_rows": ac_opf_branch_rows(case, own_result),
        "cost_validation": cost_coefficient_validation_table(pm_data, case),
    }
