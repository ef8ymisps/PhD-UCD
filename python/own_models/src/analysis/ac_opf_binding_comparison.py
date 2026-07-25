"""Per-element own-vs-PowerModels AC-OPF comparison: variable diffs and
binding-constraint-set agreement.

Python mirror of julia/own_models/src/analysis/opf_solution_inspection.jl's
ac_opf_summary_row / compare_ac_opf_{generator,bus,branch}_rows /
binding_set_comparison -- same 1e-6 boundary tolerance, same both/only_own/
only_pm classification, same column-naming scheme (n_pmin_both etc.), so a
Python-generated summary row is directly comparable to a Julia-generated one.

PowerModels reference data comes from the existing per-element CSVs already
produced by julia/own_models/scripts/08_run_ac_opf_powermodels_references.jl
(powermodels_ac_opf_{generators,buses,branches}.csv) -- no new PowerModels
solves needed here, this module only compares against what's already there.
"""

from __future__ import annotations

import math
from statistics import stdev
from typing import Iterable

TOL = 1e-6


def _limit_flag(value: float, limit: float, tol: float = TOL) -> bool:
    return abs(value - limit) <= tol


def _binding(loading: float, rate_a: float, tol: float = TOL) -> bool:
    return rate_a > 0.0 and abs(loading - rate_a) <= tol


def _loading_pct(loading: float, rate_a: float) -> float:
    return 100.0 * loading / rate_a if rate_a > 0.0 else math.nan


def _absmax(values: list[float]) -> float:
    return max((abs(v) for v in values), default=0.0)


def _absmean(values: list[float]) -> float:
    return sum(abs(v) for v in values) / len(values) if values else 0.0


def _absstd(values: list[float]) -> float:
    magnitudes = [abs(v) for v in values]
    return stdev(magnitudes) if len(magnitudes) >= 2 else 0.0


def classify_binding(own_flag: bool, pm_flag: bool) -> str:
    """both / only_own / only_pm / neither -- matches Julia's classify_binding exactly."""
    if own_flag and pm_flag:
        return "both"
    if own_flag:
        return "only_own"
    if pm_flag:
        return "only_pm"
    return "neither"


def _binding_counts(classes: list[str]) -> dict[str, int]:
    return {
        "both": sum(1 for c in classes if c == "both"),
        "only_own": sum(1 for c in classes if c == "only_own"),
        "only_pm": sum(1 for c in classes if c == "only_pm"),
    }


def compare_generator_rows(case, result, pm_generators: dict[str, dict], *, tol: float = TOL) -> list[dict]:
    """One row per generator with an own-model AND a PM reference value."""
    rows = []
    for gen in case.generators:
        if gen.id not in result.pg or gen.id not in pm_generators:
            continue
        own_pg, own_qg = result.pg[gen.id], result.qg[gen.id]
        pm = pm_generators[gen.id]
        pm_pg, pm_qg = pm["pm_pg"], pm["pm_qg"]
        rows.append({
            "gen_id": gen.id,
            "bus_id": gen.bus,
            "own_pg": own_pg, "pm_pg": pm_pg, "pg_diff": own_pg - pm_pg, "abs_pg_diff": abs(own_pg - pm_pg),
            "own_qg": own_qg, "pm_qg": pm_qg, "qg_diff": own_qg - pm_qg, "abs_qg_diff": abs(own_qg - pm_qg),
            "pmin": gen.pmin, "pmax": gen.pmax, "qmin": gen.qmin, "qmax": gen.qmax,
            "own_at_pmin": _limit_flag(own_pg, gen.pmin, tol), "own_at_pmax": _limit_flag(own_pg, gen.pmax, tol),
            "own_at_qmin": _limit_flag(own_qg, gen.qmin, tol), "own_at_qmax": _limit_flag(own_qg, gen.qmax, tol),
            "pm_at_pmin": _limit_flag(pm_pg, gen.pmin, tol), "pm_at_pmax": _limit_flag(pm_pg, gen.pmax, tol),
            "pm_at_qmin": _limit_flag(pm_qg, gen.qmin, tol), "pm_at_qmax": _limit_flag(pm_qg, gen.qmax, tol),
        })
    return rows


def compare_bus_rows(case, result, pm_buses: dict[str, dict], *, tol: float = TOL) -> list[dict]:
    rows = []
    for bus in case.buses:
        if bus.id not in result.vm or bus.id not in pm_buses:
            continue
        own_vm, own_va = result.vm[bus.id], result.va[bus.id]
        pm = pm_buses[bus.id]
        pm_vm, pm_va = pm["pm_vm"], pm["pm_va"]
        rows.append({
            "bus_id": bus.id,
            "own_vm": own_vm, "pm_vm": pm_vm, "vm_diff": own_vm - pm_vm, "abs_vm_diff": abs(own_vm - pm_vm),
            "own_va": own_va, "pm_va": pm_va, "va_diff": own_va - pm_va, "abs_va_diff": abs(own_va - pm_va),
            "vmin": bus.vmin, "vmax": bus.vmax,
            "own_at_vmin": _limit_flag(own_vm, bus.vmin, tol), "own_at_vmax": _limit_flag(own_vm, bus.vmax, tol),
            "pm_at_vmin": _limit_flag(pm_vm, bus.vmin, tol), "pm_at_vmax": _limit_flag(pm_vm, bus.vmax, tol),
        })
    return rows


def compare_branch_rows(case, result, pm_branches: dict[str, dict], own_va: dict[str, float], pm_va: dict[str, float], *, tol: float = TOL) -> list[dict]:
    rows = []
    for branch in case.branches:
        if branch.id not in result.branch_flows or branch.id not in pm_branches:
            continue
        if branch.from_bus not in own_va or branch.to_bus not in own_va:
            continue
        if branch.from_bus not in pm_va or branch.to_bus not in pm_va:
            continue
        own_flow = result.branch_flows[branch.id]
        pm_flow = pm_branches[branch.id]
        own_pf, own_qf, own_pt, own_qt = own_flow["pf"], own_flow["qf"], own_flow["pt"], own_flow["qt"]
        pm_pf, pm_qf, pm_pt, pm_qt = pm_flow["pm_pf"], pm_flow["pm_qf"], pm_flow["pm_pt"], pm_flow["pm_qt"]
        own_sf, own_st = math.hypot(own_pf, own_qf), math.hypot(own_pt, own_qt)
        pm_sf, pm_st = math.hypot(pm_pf, pm_qf), math.hypot(pm_pt, pm_qt)
        own_loading, pm_loading = max(own_sf, own_st), max(pm_sf, pm_st)
        own_loading_pct, pm_loading_pct = _loading_pct(own_loading, branch.rate_a), _loading_pct(pm_loading, branch.rate_a)
        own_angle_diff = own_va[branch.from_bus] - own_va[branch.to_bus]
        pm_angle_diff = pm_va[branch.from_bus] - pm_va[branch.to_bus]
        rows.append({
            "branch_id": branch.id, "from_bus": branch.from_bus, "to_bus": branch.to_bus,
            "own_pf": own_pf, "pm_pf": pm_pf, "pf_diff": own_pf - pm_pf, "abs_pf_diff": abs(own_pf - pm_pf),
            "own_qf": own_qf, "pm_qf": pm_qf, "qf_diff": own_qf - pm_qf, "abs_qf_diff": abs(own_qf - pm_qf),
            "own_loading_pct": own_loading_pct, "pm_loading_pct": pm_loading_pct,
            "loading_pct_diff": own_loading_pct - pm_loading_pct, "abs_loading_pct_diff": abs(own_loading_pct - pm_loading_pct),
            "rate_a": branch.rate_a, "angmin": branch.angmin, "angmax": branch.angmax,
            "own_binding": _binding(own_loading, branch.rate_a, tol), "pm_binding": _binding(pm_loading, branch.rate_a, tol),
            "own_angle_diff": own_angle_diff, "pm_angle_diff": pm_angle_diff,
            "own_angle_lower_binding": abs(own_angle_diff - branch.angmin) <= tol,
            "own_angle_upper_binding": abs(own_angle_diff - branch.angmax) <= tol,
            "pm_angle_lower_binding": abs(pm_angle_diff - branch.angmin) <= tol,
            "pm_angle_upper_binding": abs(pm_angle_diff - branch.angmax) <= tol,
        })
    return rows


def binding_set_comparison(gen_rows: list[dict], bus_rows: list[dict], branch_rows: list[dict]) -> dict[str, int]:
    """Aggregate per-element binding classifications -- n_{pmin,pmax,qmin,qmax,vmin,vmax,thermal,angle_lower,angle_upper}_{both,only_own,only_pm}."""
    result: dict[str, int] = {}

    def add(name: str, classes: list[str]) -> None:
        counts = _binding_counts(classes)
        result[f"n_{name}_both"] = counts["both"]
        result[f"n_{name}_only_own"] = counts["only_own"]
        result[f"n_{name}_only_pm"] = counts["only_pm"]

    add("pmin", [classify_binding(r["own_at_pmin"], r["pm_at_pmin"]) for r in gen_rows])
    add("pmax", [classify_binding(r["own_at_pmax"], r["pm_at_pmax"]) for r in gen_rows])
    add("qmin", [classify_binding(r["own_at_qmin"], r["pm_at_qmin"]) for r in gen_rows])
    add("qmax", [classify_binding(r["own_at_qmax"], r["pm_at_qmax"]) for r in gen_rows])
    add("vmin", [classify_binding(r["own_at_vmin"], r["pm_at_vmin"]) for r in bus_rows])
    add("vmax", [classify_binding(r["own_at_vmax"], r["pm_at_vmax"]) for r in bus_rows])
    add("thermal", [classify_binding(r["own_binding"], r["pm_binding"]) for r in branch_rows])
    add("angle_lower", [classify_binding(r["own_angle_lower_binding"], r["pm_angle_lower_binding"]) for r in branch_rows])
    add("angle_upper", [classify_binding(r["own_angle_upper_binding"], r["pm_angle_upper_binding"]) for r in branch_rows])
    return result


def ac_opf_summary_row(
    case,
    result,
    *,
    scenario_id: str,
    scenario: str,
    case_name: str,
    pm_generators: dict[str, dict],
    pm_buses: dict[str, dict],
    pm_branches: dict[str, dict],
    pm_status: str,
    pm_objective: float,
    pm_solve_time_sec: float,
    pm_solve_iterations: float,
    own_solve_time_sec: float,
) -> dict:
    """Full per-scenario comparison row -- var diffs, binding-set counts,
    objective/status/iteration comparison. Matches Julia's ac_opf_summary_row
    schema (opf_solution_inspection.jl) field-for-field where applicable."""
    gen_rows = compare_generator_rows(case, result, pm_generators)
    bus_rows = compare_bus_rows(case, result, pm_buses)
    branch_rows = compare_branch_rows(case, result, pm_branches, result.va, {b: pm_buses[b]["pm_va"] for b in pm_buses})

    binding = binding_set_comparison(gen_rows, bus_rows, branch_rows)

    row = {
        "scenario_id": scenario_id,
        "scenario": scenario,
        "case_name": case_name,
        "own_status": result.status,
        "own_success": bool(result.success),
        "pm_status": pm_status,
        "own_objective": result.objective,
        "pm_objective": pm_objective,
        "objective_diff": result.objective - pm_objective if not math.isnan(pm_objective) else math.nan,
        "objective_rel_diff": (
            abs(result.objective - pm_objective) / (1.0 + abs(pm_objective))
            if not math.isnan(pm_objective) else math.nan
        ),
        "max_pg_diff": _absmax([r["pg_diff"] for r in gen_rows]),
        "mean_pg_diff": _absmean([r["pg_diff"] for r in gen_rows]),
        "std_pg_diff": _absstd([r["pg_diff"] for r in gen_rows]),
        "max_qg_diff": _absmax([r["qg_diff"] for r in gen_rows]),
        "mean_qg_diff": _absmean([r["qg_diff"] for r in gen_rows]),
        "std_qg_diff": _absstd([r["qg_diff"] for r in gen_rows]),
        "max_vm_diff": _absmax([r["vm_diff"] for r in bus_rows]),
        "mean_vm_diff": _absmean([r["vm_diff"] for r in bus_rows]),
        "std_vm_diff": _absstd([r["vm_diff"] for r in bus_rows]),
        "max_va_diff": _absmax([r["va_diff"] for r in bus_rows]),
        "mean_va_diff": _absmean([r["va_diff"] for r in bus_rows]),
        "std_va_diff": _absstd([r["va_diff"] for r in bus_rows]),
        "max_pf_diff": _absmax([r["pf_diff"] for r in branch_rows]),
        "mean_pf_diff": _absmean([r["pf_diff"] for r in branch_rows]),
        "std_pf_diff": _absstd([r["pf_diff"] for r in branch_rows]),
        "max_qf_diff": _absmax([r["qf_diff"] for r in branch_rows]),
        "mean_qf_diff": _absmean([r["qf_diff"] for r in branch_rows]),
        "std_qf_diff": _absstd([r["qf_diff"] for r in branch_rows]),
        "max_loading_pct_diff": _absmax([r["loading_pct_diff"] for r in branch_rows]),
        "mean_loading_pct_diff": _absmean([r["loading_pct_diff"] for r in branch_rows]),
        "std_loading_pct_diff": _absstd([r["loading_pct_diff"] for r in branch_rows]),
        "own_solve_iterations": result.solve_iterations,
        "pm_solve_iterations": pm_solve_iterations,
        "own_solve_time_sec": own_solve_time_sec,
        "pm_solve_time_sec": pm_solve_time_sec,
    }
    row.update(binding)
    return row
