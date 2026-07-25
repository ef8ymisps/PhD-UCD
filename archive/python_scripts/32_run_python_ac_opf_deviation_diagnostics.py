"""Run Python AC-OPF deviation-minimization diagnostics.

This is the Python-side counterpart of the Julia deviation checks:

* constrained: minimize L1 distance to the PowerModels OPF point while keeping
  the normal OPF equalities and inequalities;
* equality_only: minimize the same L1 distance but remove physical inequality
  limits, then evaluate those limits externally at the projected point.

The script is deliberately filterable.  The full Python debug queue is large,
so the safe workflow is to start with a few objective-mismatch scenarios, check
the output shape, then broaden the run.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import pyomo.environ as pyo

PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat  # noqa: E402
from own_models.src.analysis.ac_opf_sweeps import (  # noqa: E402
    DEFAULT_SETTINGS,
    compact_ac_scenario_id,
    iter_compact_ac_validation,
)
from own_models.src.data.case import PowerCase  # noqa: E402
from own_models.src.network.admittance import calc_ac_branch_flows  # noqa: E402
from own_models.src.opf.ac.constraints import evaluate_ac_opf_constraint_violations  # noqa: E402
from own_models.src.opf.ac.model import DEFAULT_IPOPT_TOLERANCES, build_ac_opf_pyomo_model  # noqa: E402
from own_models.src.opf.ac.results import extract_ac_opf_result  # noqa: E402
from own_models.src.opf.solver_iterations import solve_with_iteration_capture  # noqa: E402


PM_REF_DIR = REPO_ROOT / "results" / "reference" / "powermodels" / "ac_opf_compact_sweeps"
QUEUE_PATH = REPO_ROOT / "results" / "own_vs_powermodels" / "python_ac_opf_debug_recommended_run_queue.csv"
RESULTS_ROOT = REPO_ROOT / "results" / "own_vs_powermodels"
ORGANIZED_ROOT = RESULTS_ROOT / "_organized_for_meeting" / "08_python_ac_opf_debugging"

CASE_SHORT_NAMES = [
    "6470_rte",
    "1888_rte",
    "1354_pegase",
    "300_ieee",
    "118_ieee",
    "30_ieee",
    "14_ieee",
    "5_pjm",
]

VARIANT_TARGETS = {
    "full": {"pg", "qg", "vm", "va"},
    "no_qg": {"pg", "vm", "va"},
    "no_v": {"pg", "qg"},
    "no_qg_no_v": {"pg"},
}

TIGHT_IPOPT_TOLERANCES: dict[str, Any] = {
    "tol": 1e-10,
    "constr_viol_tol": 1e-9,
    "compl_inf_tol": 1e-9,
    "dual_inf_tol": 1.0,
}


def ensure_conda_solver_path() -> None:
    """Make directly-invoked Conda Python behave like an activated env.

    On Windows, Ipopt lives in ``<env>/Library/bin``.  If the environment is
    not activated, Pyomo cannot discover ``ipopt.exe`` even though it is
    installed.  Prepending this directory is local to this process.
    """
    env_root = Path(sys.executable).resolve().parent
    library_bin = env_root / "Library" / "bin"
    if library_bin.exists():
        os.environ["PATH"] = str(library_bin) + os.pathsep + os.environ.get("PATH", "")


def case_path(short_name: str) -> Path:
    return REPO_ROOT / "data" / "raw" / "pglib" / "matpower_mat" / "base_case" / f"pglib_opf_case{short_name}.mat"


def base_short_name(scenario_id: str) -> str:
    for name in CASE_SHORT_NAMES:
        if scenario_id.startswith(name):
            return name
    raise ValueError(f"Cannot infer PGLib base case from scenario_id={scenario_id!r}.")


def load_pm_reference(scenario_id: str) -> tuple[dict[str, dict[str, float]], float, str]:
    gens = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_generators.csv", usecols=["scenario_id", "gen_id", "pm_pg", "pm_qg"])
    buses = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_buses.csv", usecols=["scenario_id", "bus_id", "pm_vm", "pm_va"])
    summary = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_summary.csv", usecols=["scenario_id", "pm_status", "pm_objective"])

    g = gens[gens.scenario_id == scenario_id]
    b = buses[buses.scenario_id == scenario_id]
    s = summary[summary.scenario_id == scenario_id]
    if g.empty or b.empty or s.empty:
        raise ValueError(f"No complete PowerModels reference found for scenario_id={scenario_id!r}.")

    target = {
        "pg": {str(row.gen_id): float(row.pm_pg) for row in g.itertuples()},
        "qg": {str(row.gen_id): float(row.pm_qg) for row in g.itertuples()},
        "vm": {str(row.bus_id): float(row.pm_vm) for row in b.itertuples()},
        "va": {str(row.bus_id): float(row.pm_va) for row in b.itertuples()},
    }
    summary_row = s.iloc[0]
    return target, float(summary_row.pm_objective), str(summary_row.pm_status)


def align_target_reference_angle(target: dict[str, dict[str, float]], slack_bus_id: str) -> tuple[dict[str, dict[str, float]], float]:
    """Shift PM voltage angles so the Python slack/reference bus is zero."""
    offset = float(target["va"].get(slack_bus_id, 0.0))
    aligned = {
        "pg": dict(target["pg"]),
        "qg": dict(target["qg"]),
        "vm": dict(target["vm"]),
        "va": {bus_id: float(value) - offset for bus_id, value in target["va"].items()},
    }
    return aligned, offset


def load_scenario_case(scenario_id: str, model_variant: str) -> PowerCase:
    if model_variant != "pm_like_zero":
        raise ValueError(f"Unsupported model_variant={model_variant!r}. Expected 'pm_like_zero' (bus-injection was deleted).")
    short = base_short_name(scenario_id)
    case = load_powercase_from_matpower_mat(case_path(short))
    case = replace(case, name=short)
    settings = {
        **DEFAULT_SETTINGS,
        "keep_raw_result": True,
        "scenario_filter_ids": {scenario_id},
        "warm_start": "zero",
    }
    for row in iter_compact_ac_validation(case, settings=settings):
        if compact_ac_scenario_id(row) == scenario_id and row.get("case") is not None:
            return row["case"]
    raise ValueError(f"Could not reconstruct scenario case for {scenario_id!r}.")


def target_options(target: dict[str, dict[str, float]], variant: str) -> dict[str, object]:
    groups = VARIANT_TARGETS[variant]
    options: dict[str, object] = {}
    if "pg" in groups:
        options["target_pg"] = target["pg"]
    if "qg" in groups:
        options["target_qg"] = target["qg"]
    if "vm" in groups:
        options["target_vm"] = target["vm"]
    if "va" in groups:
        options["target_va"] = target["va"]
    return options


def remove_physical_bounds(model) -> None:
    """Remove inequality bounds for equality-only diagnostics.

    The L1 slack variables keep their nonnegative bounds because those are the
    objective linearization, not physical OPF limits.
    """
    for name in ["pg", "qg", "vm", "p", "q", "pf", "qf", "pt", "qt"]:
        if not hasattr(model, name):
            continue
        var = getattr(model, name)
        for idx in var:
            var[idx].setlb(None)
            var[idx].setub(None)


def build_diagnostic_model(
    case: PowerCase,
    *,
    model_variant: str,
    diagnostic_mode: str,
    variant: str,
    target: dict[str, dict[str, float]],
):
    options = target_options(target, variant)
    include_inequalities = diagnostic_mode == "constrained"
    common = {
        "objective_type": "target_deviation",
        "objective_options": options,
        "voltage_bounds": None,
        "include_branch_thermal": include_inequalities,
        "include_branch_angles": include_inequalities,
        "fix_reference_angle": True,
    }

    if model_variant != "pm_like_zero":
        raise ValueError(f"Unsupported model_variant={model_variant!r}. Expected 'pm_like_zero' (bus-injection was deleted).")
    model = build_ac_opf_pyomo_model(
        case,
        warm_start="zero",
        model_name=f"{case.name}_{diagnostic_mode}_{variant}_pm_like",
        **common,
    )

    if diagnostic_mode == "equality_only":
        remove_physical_bounds(model)
    return model


def solve_model(model):
    opt = pyo.SolverFactory("ipopt")
    if opt is None or not opt.available(exception_flag=False):
        raise RuntimeError("Ipopt is not available through Pyomo in this environment.")
    for key, value in {**DEFAULT_IPOPT_TOLERANCES, **TIGHT_IPOPT_TOLERANCES}.items():
        opt.options[key] = value
    started = time.perf_counter()
    raw_result, iterations = solve_with_iteration_capture(opt, model, tee=False)
    elapsed = time.perf_counter() - started
    return extract_ac_opf_result(model, raw_result, solve_iterations=iterations), elapsed


def generation_cost(case: PowerCase, pg: dict[str, float]) -> float:
    return sum(
        float(gen.cost_c2) * (pg[gen.id] * case.base_mva) ** 2
        + float(gen.cost_c1) * (pg[gen.id] * case.base_mva)
        + float(gen.cost_c0)
        for gen in case.generators
        if gen.status == 1 and gen.id in pg
    )


def max_group_diff(values: dict[str, float], target: dict[str, float]) -> tuple[float, float]:
    diffs = [abs(float(values[key]) - float(target[key])) for key in target if key in values]
    if not diffs:
        return math.nan, math.nan
    return max(diffs), sum(diffs)


def target_arrays(case: PowerCase, target: dict[str, dict[str, float]]):
    vm = pd.Series(target["vm"], dtype=float)
    va = pd.Series(target["va"], dtype=float)
    return (
        vm.reindex([bus.id for bus in case.buses]).to_numpy(dtype=float),
        va.reindex([bus.id for bus in case.buses]).to_numpy(dtype=float),
    )


def result_arrays(case: PowerCase, result):
    vm = pd.Series(result.vm, dtype=float)
    va = pd.Series(result.va, dtype=float)
    return (
        vm.reindex([bus.id for bus in case.buses]).to_numpy(dtype=float),
        va.reindex([bus.id for bus in case.buses]).to_numpy(dtype=float),
    )


def inequality_level_rows(case: PowerCase, pg, qg, vm_array, va_array, *, source: str, tol: float = 1e-5):
    rows: list[dict[str, object]] = []
    flows = calc_ac_branch_flows(case, vm_array, va_array)

    def add(family: str, element_id: str, value: float, bound: float, margin: float) -> None:
        rows.append(
            {
                "source": source,
                "constraint_family": family,
                "element_id": element_id,
                "value": value,
                "bound": bound,
                "margin_to_bound": margin,
                "violation": max(0.0, -margin),
                "is_binding": abs(margin) <= tol,
            }
        )

    for gen in case.generators:
        if gen.status != 1 or gen.id not in pg or gen.id not in qg:
            continue
        add("pg_min", gen.id, pg[gen.id], gen.pmin, pg[gen.id] - gen.pmin)
        add("pg_max", gen.id, pg[gen.id], gen.pmax, gen.pmax - pg[gen.id])
        add("qg_min", gen.id, qg[gen.id], gen.qmin, qg[gen.id] - gen.qmin)
        add("qg_max", gen.id, qg[gen.id], gen.qmax, gen.qmax - qg[gen.id])

    for idx, bus in enumerate(case.buses):
        vm = float(vm_array[idx])
        add("vm_min", bus.id, vm, bus.vmin, vm - bus.vmin)
        add("vm_max", bus.id, vm, bus.vmax, bus.vmax - vm)

    for branch in case.branches:
        if branch.status != 1:
            continue
        angle = float(va_array[case.bus_id_to_index[branch.from_bus]] - va_array[case.bus_id_to_index[branch.to_bus]])
        add("angle_min", branch.id, angle, branch.angmin, angle - branch.angmin)
        add("angle_max", branch.id, angle, branch.angmax, branch.angmax - angle)
        if branch.rate_a > 0.0 and branch.id in flows:
            flow = flows[branch.id]
            sf2 = flow["pf"] ** 2 + flow["qf"] ** 2
            st2 = flow["pt"] ** 2 + flow["qt"] ** 2
            add("thermal_from", branch.id, sf2, branch.rate_a**2, branch.rate_a**2 - sf2)
            add("thermal_to", branch.id, st2, branch.rate_a**2, branch.rate_a**2 - st2)

    return rows


def compare_inequality_levels(pm_rows, diagnostic_rows) -> list[dict[str, object]]:
    pm_by_key = {(row["constraint_family"], row["element_id"]): row for row in pm_rows}
    diag_by_key = {(row["constraint_family"], row["element_id"]): row for row in diagnostic_rows}
    out = []
    for key in sorted(set(pm_by_key) | set(diag_by_key)):
        pm = pm_by_key.get(key)
        diag = diag_by_key.get(key)
        family, element_id = key
        pm_margin = float(pm["margin_to_bound"]) if pm else math.nan
        diag_margin = float(diag["margin_to_bound"]) if diag else math.nan
        pm_binding = bool(pm["is_binding"]) if pm else False
        diag_binding = bool(diag["is_binding"]) if diag else False
        out.append(
            {
                "constraint_family": family,
                "element_id": element_id,
                "pm_margin_to_bound": pm_margin,
                "diagnostic_margin_to_bound": diag_margin,
                "margin_difference_diagnostic_minus_pm": diag_margin - pm_margin
                if math.isfinite(pm_margin) and math.isfinite(diag_margin)
                else math.nan,
                "pm_violation": float(pm["violation"]) if pm else math.nan,
                "diagnostic_violation": float(diag["violation"]) if diag else math.nan,
                "pm_binding": pm_binding,
                "diagnostic_binding": diag_binding,
                "binding_status": "same" if pm_binding == diag_binding else "different",
            }
        )
    return out


def select_queue_rows(args) -> list[dict[str, str]]:
    rows = pd.read_csv(QUEUE_PATH).fillna("")
    rows = rows[rows["recommended_steps"].str.contains("deviation", regex=False)]
    if args.model_variants:
        rows = rows[rows["model_variant"].isin(args.model_variants)]
    if args.scenario_ids:
        rows = rows[rows["scenario_id"].isin(args.scenario_ids)]
    if args.case_names:
        rows = rows[rows["case"].isin(args.case_names)]
    if args.only_cheaper:
        rows = rows[rows["target_reasons"].str.contains("own_cheaper_objective", regex=False)]
    rows = rows.sort_values(
        by=["priority", "model_variant", "case", "scenario_id"],
        ascending=[True, True, True, True],
    )
    if args.max_targets is not None:
        rows = rows.head(args.max_targets)
    return [dict(row) for row in rows.to_dict(orient="records")]


def run_one(row: dict[str, str], diagnostic_mode: str, variant: str):
    model_variant = str(row["model_variant"])
    scenario_id = str(row["scenario_id"])
    case = load_scenario_case(scenario_id, model_variant)
    target, pm_objective, pm_status = load_pm_reference(scenario_id)
    target, pm_va_reference_offset = align_target_reference_angle(target, case.slack_bus_id)

    model = build_diagnostic_model(
        case,
        model_variant=model_variant,
        diagnostic_mode=diagnostic_mode,
        variant=variant,
        target=target,
    )
    result, solve_time_sec = solve_model(model)

    max_pg, l1_pg = max_group_diff(result.pg, target["pg"])
    max_qg, l1_qg = max_group_diff(result.qg, target["qg"])
    max_vm, l1_vm = max_group_diff(result.vm, target["vm"])
    max_va, l1_va = max_group_diff(result.va, target["va"])
    own_cost = generation_cost(case, result.pg)
    vm_diag, va_diag = result_arrays(case, result)
    vm_pm, va_pm = target_arrays(case, target)

    data = model._ac_data
    diag_violations = evaluate_ac_opf_constraint_violations(case, data, result.pg, result.qg, vm_diag, va_diag)
    pm_violations = evaluate_ac_opf_constraint_violations(case, data, target["pg"], target["qg"], vm_pm, va_pm)

    summary_row = {
        "case": row["case"],
        "scenario": row["scenario"],
        "scenario_id": scenario_id,
        "model_variant": model_variant,
        "diagnostic_mode": diagnostic_mode,
        "variant": variant,
        "pm_status": pm_status,
        "pm_objective": pm_objective,
        "pm_va_reference_offset_subtracted": pm_va_reference_offset,
        "diagnostic_status": result.status,
        "diagnostic_success": result.success,
        "diagnostic_objective_l1": result.objective,
        "diagnostic_generation_cost": own_cost,
        "cost_diff_diagnostic_minus_pm": own_cost - pm_objective,
        "cost_rel_diff_diagnostic_vs_pm": abs(own_cost - pm_objective) / (1.0 + abs(pm_objective)),
        "solve_time_sec": solve_time_sec,
        "solve_iterations": result.solve_iterations,
        "max_abs_pg_deviation": max_pg,
        "max_abs_qg_deviation": max_qg,
        "max_abs_vm_deviation": max_vm,
        "max_abs_va_deviation": max_va,
        "l1_pg_deviation": l1_pg,
        "l1_qg_deviation": l1_qg,
        "l1_vm_deviation": l1_vm,
        "l1_va_deviation": l1_va,
        **{f"diagnostic_{key}": value for key, value in diag_violations.items()},
        **{f"pm_{key}": value for key, value in pm_violations.items()},
        "source_reasons": row["target_reasons"],
    }

    variable_rows = []
    for name, values, target_values in [
        ("pg", result.pg, target["pg"]),
        ("qg", result.qg, target["qg"]),
        ("vm", result.vm, target["vm"]),
        ("va", result.va, target["va"]),
    ]:
        for element_id, target_value in target_values.items():
            if element_id not in values:
                continue
            value = float(values[element_id])
            variable_rows.append(
                {
                    "case": row["case"],
                    "scenario": row["scenario"],
                    "scenario_id": scenario_id,
                    "model_variant": model_variant,
                    "diagnostic_mode": diagnostic_mode,
                    "variant": variant,
                    "variable": name,
                    "element_id": element_id,
                    "powermodels_value": float(target_value),
                    "diagnostic_value": value,
                    "difference_diagnostic_minus_pm": value - float(target_value),
                    "abs_difference": abs(value - float(target_value)),
                }
            )

    pm_level_rows = inequality_level_rows(case, target["pg"], target["qg"], vm_pm, va_pm, source="powermodels_reference")
    diag_level_rows = inequality_level_rows(case, result.pg, result.qg, vm_diag, va_diag, source="diagnostic_solution")
    compared_levels = compare_inequality_levels(pm_level_rows, diag_level_rows)
    for level_row in compared_levels:
        level_row.update(
            {
                "case": row["case"],
                "scenario": row["scenario"],
                "scenario_id": scenario_id,
                "model_variant": model_variant,
                "diagnostic_mode": diagnostic_mode,
                "variant": variant,
            }
        )

    by_family: dict[str, dict[str, int]] = {}
    for level_row in compared_levels:
        family = str(level_row["constraint_family"])
        stats = by_family.setdefault(
            family,
            {
                "n_constraints": 0,
                "n_pm_binding": 0,
                "n_diagnostic_binding": 0,
                "n_binding_mismatches": 0,
                "n_diagnostic_violations": 0,
            },
        )
        stats["n_constraints"] += 1
        stats["n_pm_binding"] += int(bool(level_row["pm_binding"]))
        stats["n_diagnostic_binding"] += int(bool(level_row["diagnostic_binding"]))
        stats["n_binding_mismatches"] += int(level_row["binding_status"] == "different")
        stats["n_diagnostic_violations"] += int(float(level_row["diagnostic_violation"]) > 1e-7)

    binding_summary_rows = [
        {
            "case": row["case"],
            "scenario": row["scenario"],
            "scenario_id": scenario_id,
            "model_variant": model_variant,
            "diagnostic_mode": diagnostic_mode,
            "variant": variant,
            "constraint_family": family,
            **stats,
        }
        for family, stats in sorted(by_family.items())
    ]
    return summary_row, variable_rows, compared_levels, binding_summary_rows


def append_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def copy_outputs(paths: list[Path]) -> None:
    ORGANIZED_ROOT.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if path.exists():
            shutil.copy2(path, ORGANIZED_ROOT / path.name)


def main() -> None:
    ensure_conda_solver_path()

    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostics", nargs="*", default=["constrained", "equality_only"], choices=["constrained", "equality_only"])
    parser.add_argument("--model-variants", nargs="*", default=["pm_like_zero"], choices=["pm_like_zero"])
    parser.add_argument("--variants", nargs="*", default=["full"], choices=sorted(VARIANT_TARGETS))
    parser.add_argument("--scenario-ids", nargs="*", default=None)
    parser.add_argument("--case-names", nargs="*", default=None)
    parser.add_argument("--max-targets", type=int, default=3)
    parser.add_argument("--only-cheaper", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    selected = select_queue_rows(args)
    if not selected:
        print("No matching Python deviation diagnostic targets.")
        return

    summary_path = RESULTS_ROOT / "python_deviation_minimization_diagnostics.csv"
    variables_path = RESULTS_ROOT / "python_deviation_variable_differences.csv"
    levels_path = RESULTS_ROOT / "python_deviation_inequality_levels.csv"
    binding_path = RESULTS_ROOT / "python_deviation_binding_summary.csv"
    outputs = [summary_path, variables_path, levels_path, binding_path]
    if args.overwrite:
        for path in outputs:
            path.unlink(missing_ok=True)

    print(f"Selected {len(selected)} scenario target(s).")
    for row in selected:
        print(f"=== {row['model_variant']} | {row['scenario_id']} ===", flush=True)
        for diagnostic_mode in args.diagnostics:
            for variant in args.variants:
                print(f"  {diagnostic_mode} / {variant}", flush=True)
                try:
                    summary_row, variable_rows, level_rows, binding_rows = run_one(row, diagnostic_mode, variant)
                except Exception as exc:
                    summary_row = {
                        "case": row["case"],
                        "scenario": row["scenario"],
                        "scenario_id": row["scenario_id"],
                        "model_variant": row["model_variant"],
                        "diagnostic_mode": diagnostic_mode,
                        "variant": variant,
                        "pm_status": "",
                        "pm_objective": math.nan,
                        "pm_va_reference_offset_subtracted": math.nan,
                        "diagnostic_status": "ERROR",
                        "diagnostic_success": False,
                        "diagnostic_objective_l1": math.nan,
                        "diagnostic_generation_cost": math.nan,
                        "cost_diff_diagnostic_minus_pm": math.nan,
                        "cost_rel_diff_diagnostic_vs_pm": math.nan,
                        "solve_time_sec": math.nan,
                        "solve_iterations": math.nan,
                        "max_abs_pg_deviation": math.nan,
                        "max_abs_qg_deviation": math.nan,
                        "max_abs_vm_deviation": math.nan,
                        "max_abs_va_deviation": math.nan,
                        "l1_pg_deviation": math.nan,
                        "l1_qg_deviation": math.nan,
                        "l1_vm_deviation": math.nan,
                        "l1_va_deviation": math.nan,
                        "diagnostic_max_p_balance_violation": math.nan,
                        "diagnostic_max_q_balance_violation": math.nan,
                        "diagnostic_max_pg_limit_violation": math.nan,
                        "diagnostic_max_qg_limit_violation": math.nan,
                        "diagnostic_max_voltage_violation": math.nan,
                        "diagnostic_max_thermal_violation": math.nan,
                        "diagnostic_max_angle_violation": math.nan,
                        "pm_max_p_balance_violation": math.nan,
                        "pm_max_q_balance_violation": math.nan,
                        "pm_max_pg_limit_violation": math.nan,
                        "pm_max_qg_limit_violation": math.nan,
                        "pm_max_voltage_violation": math.nan,
                        "pm_max_thermal_violation": math.nan,
                        "pm_max_angle_violation": math.nan,
                        "source_reasons": f"{row['target_reasons']} | ERROR: {exc}",
                    }
                    variable_rows = []
                    level_rows = []
                    binding_rows = []
                    print(f"    ERROR: {exc}", flush=True)
                append_csv(summary_path, [summary_row])
                append_csv(variables_path, variable_rows)
                append_csv(levels_path, level_rows)
                append_csv(binding_path, binding_rows)
                print(
                    "    status="
                    f"{summary_row['diagnostic_status']} L1={summary_row['diagnostic_objective_l1']} "
                    f"maxPg={summary_row['max_abs_pg_deviation']} maxVm={summary_row['max_abs_vm_deviation']}",
                    flush=True,
                )

    copy_outputs(outputs)
    print("Saved:")
    for path in outputs:
        print(f"  {path}")
    print(f"Copied outputs to {ORGANIZED_ROOT}")


if __name__ == "__main__":
    main()
