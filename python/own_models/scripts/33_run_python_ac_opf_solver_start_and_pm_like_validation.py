"""Python AC-OPF solver/start diagnostics and PM-like validation.

Runs normal generation-cost OPF on the compact-reference rows that are
meaningful for solved-solution comparison:

* current model with PowerModels-like zero start;
* PM-like Python model with zero start.

For each run, capture compact own-vs-PowerModels comparison metrics and parse
Ipopt's final diagnostic summary.  This complements the deviation-minimization
checks: deviation proves the PM point is reachable; this script asks whether a
normal OPF solve reaches it when started like PowerModels.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import math
import os
import re
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
from own_models.src.analysis.ac_opf_binding_comparison import ac_opf_summary_row  # noqa: E402
from own_models.src.analysis.ac_opf_sweeps import (  # noqa: E402
    scale_all_branch_rates,
    scale_loads,
    set_system_angle_limit,
    tighten_reactive_generation_limits,
)
from own_models.src.analysis.contingencies import apply_branch_outage, apply_generator_outage  # noqa: E402
from own_models.src.data.case import PowerCase  # noqa: E402
from own_models.src.opf.ac.model import DEFAULT_IPOPT_TOLERANCES, build_ac_opf_pyomo_model  # noqa: E402
from own_models.src.opf.ac.results import extract_ac_opf_result  # noqa: E402
from own_models.src.opf.solver_iterations import _parse_iteration_count  # noqa: E402


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


def ensure_conda_solver_path() -> None:
    env_root = Path(sys.executable).resolve().parent
    library_bin = env_root / "Library" / "bin"
    if library_bin.exists():
        os.environ["PATH"] = str(library_bin) + os.pathsep + os.environ.get("PATH", "")


def parse_float_token(text: str) -> float:
    clean = str(text).replace("p", ".").replace("m", "-")
    return float(clean)


def case_path(short_name: str) -> Path:
    return REPO_ROOT / "data" / "raw" / "pglib" / "matpower_mat" / "base_case" / f"pglib_opf_case{short_name}.mat"


def base_short_name(case_name_or_scenario_id: str) -> str:
    for name in CASE_SHORT_NAMES:
        if case_name_or_scenario_id.startswith(name):
            return name
    raise ValueError(f"Cannot infer PGLib base case from {case_name_or_scenario_id!r}.")


def parse_scenario_id(scenario_id: str) -> dict[str, str]:
    parts = scenario_id.split("__")
    if len(parts) != 6:
        raise ValueError(f"Expected compact 6-part scenario_id, got {scenario_id!r}.")
    return {
        "case_name": parts[0],
        "scenario": parts[1],
        "target_type": "" if parts[2] == "none" else parts[2],
        "target_id": "" if parts[3] == "none" else parts[3],
        "parameter_name": "" if parts[4] == "none" else parts[4],
        "parameter_value": "" if parts[5] == "none" else parts[5],
    }


def load_base_case(short_name: str) -> PowerCase:
    case = load_powercase_from_matpower_mat(case_path(short_name))
    return replace(case, name=short_name)


def reconstruct_scenario_case(scenario_id: str) -> PowerCase:
    meta = parse_scenario_id(scenario_id)
    short = base_short_name(meta["case_name"])
    case = load_base_case(short)
    scenario = meta["scenario"]
    value = meta["parameter_value"]

    if scenario == "base":
        return case
    if scenario == "branch_congestion":
        return scale_all_branch_rates(case, parse_float_token(value))
    if scenario == "load_scaling":
        return scale_loads(case, parse_float_token(value), scale_p=True, scale_q=True)
    if scenario == "reactive_power_stress":
        return scale_loads(case, parse_float_token(value), scale_p=False, scale_q=True)
    if scenario == "reactive_generation_limits":
        return tighten_reactive_generation_limits(case, parse_float_token(value))
    if scenario == "system_angle_limits":
        return set_system_angle_limit(case, parse_float_token(value))
    if scenario == "branch_outage":
        return apply_branch_outage(case, meta["target_id"])
    if scenario == "generator_outage":
        return apply_generator_outage(case, meta["target_id"])
    raise ValueError(f"Scenario reconstruction is not implemented for {scenario_id!r}.")


def load_pm_reference():
    summary = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_summary.csv")
    generators = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_generators.csv")
    buses = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_buses.csv")
    branches = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_branches.csv")

    summary_by_id = {str(row.scenario_id): row for row in summary.itertuples()}
    gens_by_id: dict[str, dict[str, dict[str, float]]] = {}
    for row in generators.itertuples():
        gens_by_id.setdefault(str(row.scenario_id), {})[str(row.gen_id)] = {
            "pm_pg": float(row.pm_pg),
            "pm_qg": float(row.pm_qg),
        }
    buses_by_id: dict[str, dict[str, dict[str, float]]] = {}
    for row in buses.itertuples():
        buses_by_id.setdefault(str(row.scenario_id), {})[str(row.bus_id)] = {
            "pm_vm": float(row.pm_vm),
            "pm_va": float(row.pm_va),
        }
    branches_by_id: dict[str, dict[str, dict[str, float]]] = {}
    for row in branches.itertuples():
        branches_by_id.setdefault(str(row.scenario_id), {})[str(row.branch_id)] = {
            "pm_pf": float(row.pm_pf),
            "pm_qf": float(row.pm_qf),
            "pm_pt": float(row.pm_pt),
            "pm_qt": float(row.pm_qt),
        }
    return summary_by_id, gens_by_id, buses_by_id, branches_by_id


def aligned_pm_buses(pm_buses: dict[str, dict[str, float]], slack_bus_id: str) -> tuple[dict[str, dict[str, float]], float]:
    offset = float(pm_buses.get(slack_bus_id, {}).get("pm_va", 0.0))
    return (
        {
            bus_id: {"pm_vm": values["pm_vm"], "pm_va": values["pm_va"] - offset}
            for bus_id, values in pm_buses.items()
        },
        offset,
    )


def build_model(case: PowerCase, *, run_label: str):
    common = {
        "objective_type": "generation_cost",
        "cost_power_units": "mw",
        "voltage_bounds": None,
        "include_branch_thermal": True,
        "include_branch_angles": True,
        "fix_reference_angle": True,
    }
    if run_label == "pm_like_zero":
        return build_ac_opf_pyomo_model(
            case,
            warm_start="zero",
            model_name=f"{case.name}_pm_like_zero",
            **common,
        )
    raise ValueError(f"Unknown run_label={run_label!r}. 'current_zero_start' was removed with bus-injection.")


def solve_with_log(model, *, max_iter: int = 3000):
    opt = pyo.SolverFactory("ipopt")
    if opt is None or not opt.available(exception_flag=False):
        raise RuntimeError("Ipopt is not available through Pyomo in this environment.")
    options = {**DEFAULT_IPOPT_TOLERANCES, "max_iter": int(max_iter)}
    for key, value in options.items():
        opt.options[key] = value
    buffer = io.StringIO()
    started = time.perf_counter()
    with contextlib.redirect_stdout(buffer):
        raw_result = opt.solve(model, tee=True)
    solve_time_sec = time.perf_counter() - started
    log = buffer.getvalue()
    iterations = _parse_iteration_count(log)
    result = extract_ac_opf_result(model, raw_result, solve_iterations=iterations)
    return result, raw_result, log, solve_time_sec


def parse_ipopt_final_diagnostics(log: str) -> dict[str, float]:
    diagnostics: dict[str, float] = {
        "ipopt_iterations": _parse_iteration_count(log),
        "ipopt_objective_scaled": math.nan,
        "ipopt_objective_unscaled": math.nan,
        "ipopt_dual_infeas_scaled": math.nan,
        "ipopt_dual_infeas_unscaled": math.nan,
        "ipopt_constraint_violation_scaled": math.nan,
        "ipopt_constraint_violation_unscaled": math.nan,
        "ipopt_variable_bound_violation_scaled": math.nan,
        "ipopt_variable_bound_violation_unscaled": math.nan,
        "ipopt_complementarity_scaled": math.nan,
        "ipopt_complementarity_unscaled": math.nan,
        "ipopt_overall_nlp_error_scaled": math.nan,
        "ipopt_overall_nlp_error_unscaled": math.nan,
        "ipopt_final_inf_pr": math.nan,
        "ipopt_final_inf_du": math.nan,
        "ipopt_final_lg_mu": math.nan,
    }
    field_map = {
        "Objective": ("ipopt_objective_scaled", "ipopt_objective_unscaled"),
        "Dual infeasibility": ("ipopt_dual_infeas_scaled", "ipopt_dual_infeas_unscaled"),
        "Constraint violation": ("ipopt_constraint_violation_scaled", "ipopt_constraint_violation_unscaled"),
        "Variable bound violation": ("ipopt_variable_bound_violation_scaled", "ipopt_variable_bound_violation_unscaled"),
        "Complementarity": ("ipopt_complementarity_scaled", "ipopt_complementarity_unscaled"),
        "Overall NLP error": ("ipopt_overall_nlp_error_scaled", "ipopt_overall_nlp_error_unscaled"),
    }
    for label, (scaled_key, unscaled_key) in field_map.items():
        pattern = re.compile(
            rf"{re.escape(label)}\.*:\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)"
        )
        match = pattern.search(log)
        if match:
            diagnostics[scaled_key] = float(match.group(1))
            diagnostics[unscaled_key] = float(match.group(2))

    iter_lines = [
        line.strip()
        for line in log.splitlines()
        if re.match(r"^\s*\d+\w?\s+", line)
    ]
    if iter_lines:
        parts = iter_lines[-1].split()
        if len(parts) >= 5:
            diagnostics["ipopt_final_inf_pr"] = float(parts[2])
            diagnostics["ipopt_final_inf_du"] = float(parts[3])
            diagnostics["ipopt_final_lg_mu"] = float(parts[4])
    return diagnostics


def selected_scenario_rows(args) -> list[dict[str, str]]:
    queue = pd.read_csv(QUEUE_PATH).fillna("")
    if args.scenario_ids:
        queue = queue[queue["scenario_id"].isin(args.scenario_ids)]
    else:
        queue = queue[queue["recommended_steps"].str.contains("deviation", regex=False)]
    if args.max_targets is not None:
        queue = queue.head(args.max_targets)
    rows: dict[str, dict[str, str]] = {}
    for row in queue.to_dict(orient="records"):
        rows[str(row["scenario_id"])] = {key: str(value) for key, value in row.items()}
    return list(rows.values())


def append_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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
    parser.add_argument("--scenario-ids", nargs="*", default=None)
    parser.add_argument("--max-targets", type=int, default=6)
    parser.add_argument(
        "--runs",
        nargs="*",
        default=["pm_like_zero"],
        choices=["pm_like_zero"],
    )
    parser.add_argument("--max-iter", type=int, default=3000)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    rows = selected_scenario_rows(args)
    summary_by_id, gens_by_id, buses_by_id, branches_by_id = load_pm_reference()

    diagnostics_path = RESULTS_ROOT / "python_solver_start_diagnostics.csv"
    validation_path = RESULTS_ROOT / "python_start_validation_summary.csv"
    pm_like_path = RESULTS_ROOT / "python_pm_like_meaningful_validation_summary.csv"
    outputs = [diagnostics_path, validation_path, pm_like_path]
    if args.overwrite:
        for path in outputs:
            path.unlink(missing_ok=True)

    print(f"Selected {len(rows)} compact-reference scenario(s).")
    for row in rows:
        scenario_id = row["scenario_id"]
        if scenario_id not in summary_by_id:
            print(f"[skip] missing PM summary for {scenario_id}", flush=True)
            continue
        pm_summary = summary_by_id[scenario_id]
        if str(pm_summary.pm_status) != "LOCALLY_SOLVED":
            print(f"[skip] PM status is {pm_summary.pm_status} for {scenario_id}", flush=True)
            continue

        case = reconstruct_scenario_case(scenario_id)
        aligned_buses, pm_va_offset = aligned_pm_buses(buses_by_id[scenario_id], case.slack_bus_id)
        for run_label in args.runs:
            print(f"=== {run_label} | {scenario_id} ===", flush=True)
            try:
                model = build_model(case, run_label=run_label)
                result, raw_result, log, solve_time_sec = solve_with_log(model, max_iter=args.max_iter)
                ipopt = parse_ipopt_final_diagnostics(log)
                comparison = ac_opf_summary_row(
                    case,
                    result,
                    scenario_id=scenario_id,
                    scenario=row.get("scenario", parse_scenario_id(scenario_id)["scenario"]),
                    case_name=row.get("case", parse_scenario_id(scenario_id)["case_name"]),
                    pm_generators=gens_by_id.get(scenario_id, {}),
                    pm_buses=aligned_buses,
                    pm_branches=branches_by_id.get(scenario_id, {}),
                    pm_status=str(pm_summary.pm_status),
                    pm_objective=float(pm_summary.pm_objective),
                    pm_solve_time_sec=float(pm_summary.pm_solve_time_sec),
                    pm_solve_iterations=float(pm_summary.pm_solve_iterations),
                    own_solve_time_sec=solve_time_sec,
                )
                comparison.update(
                    {
                        "run_label": run_label,
                        "warm_start": "zero",
                        "pm_va_reference_offset_subtracted": pm_va_offset,
                    }
                )
                diagnostic_row = {
                    "run_label": run_label,
                    "scenario_id": scenario_id,
                    "case": row.get("case", parse_scenario_id(scenario_id)["case_name"]),
                    "scenario": row.get("scenario", parse_scenario_id(scenario_id)["scenario"]),
                    "warm_start": "zero",
                    "status": result.status,
                    "success": result.success,
                    "message": result.message,
                    "objective": result.objective,
                    "pm_objective": float(pm_summary.pm_objective),
                    "objective_diff": result.objective - float(pm_summary.pm_objective),
                    "objective_rel_diff": abs(result.objective - float(pm_summary.pm_objective)) / (1.0 + abs(float(pm_summary.pm_objective))),
                    "solve_time_sec": solve_time_sec,
                    "solve_iterations": result.solve_iterations,
                    "max_p_balance_violation": result.max_p_balance_violation,
                    "max_q_balance_violation": result.max_q_balance_violation,
                    "max_pg_limit_violation": result.max_pg_limit_violation,
                    "max_qg_limit_violation": result.max_qg_limit_violation,
                    "max_voltage_violation": result.max_voltage_violation,
                    "max_thermal_violation": result.max_thermal_violation,
                    "max_angle_violation": result.max_angle_violation,
                    **ipopt,
                }
            except Exception as exc:
                diagnostic_row = {
                    "run_label": run_label,
                    "scenario_id": scenario_id,
                    "case": row.get("case", ""),
                    "scenario": row.get("scenario", ""),
                    "warm_start": "zero",
                    "status": "ERROR",
                    "success": False,
                    "message": str(exc),
                    "objective": math.nan,
                    "pm_objective": float(getattr(pm_summary, "pm_objective", math.nan)),
                    "objective_diff": math.nan,
                    "objective_rel_diff": math.nan,
                    "solve_time_sec": math.nan,
                    "solve_iterations": math.nan,
                    "max_p_balance_violation": math.nan,
                    "max_q_balance_violation": math.nan,
                    "max_pg_limit_violation": math.nan,
                    "max_qg_limit_violation": math.nan,
                    "max_voltage_violation": math.nan,
                    "max_thermal_violation": math.nan,
                    "max_angle_violation": math.nan,
                }
                comparison = {
                    "scenario_id": scenario_id,
                    "scenario": row.get("scenario", ""),
                    "case_name": row.get("case", ""),
                    "own_status": "ERROR",
                    "own_success": False,
                    "pm_status": str(getattr(pm_summary, "pm_status", "")),
                    "own_objective": math.nan,
                    "pm_objective": float(getattr(pm_summary, "pm_objective", math.nan)),
                    "objective_diff": math.nan,
                    "objective_rel_diff": math.nan,
                    "max_pg_diff": math.nan,
                    "mean_pg_diff": math.nan,
                    "std_pg_diff": math.nan,
                    "max_qg_diff": math.nan,
                    "mean_qg_diff": math.nan,
                    "std_qg_diff": math.nan,
                    "max_vm_diff": math.nan,
                    "mean_vm_diff": math.nan,
                    "std_vm_diff": math.nan,
                    "max_va_diff": math.nan,
                    "mean_va_diff": math.nan,
                    "std_va_diff": math.nan,
                    "max_pf_diff": math.nan,
                    "mean_pf_diff": math.nan,
                    "std_pf_diff": math.nan,
                    "max_qf_diff": math.nan,
                    "mean_qf_diff": math.nan,
                    "std_qf_diff": math.nan,
                    "max_loading_pct_diff": math.nan,
                    "mean_loading_pct_diff": math.nan,
                    "std_loading_pct_diff": math.nan,
                    "own_solve_iterations": math.nan,
                    "pm_solve_iterations": float(getattr(pm_summary, "pm_solve_iterations", math.nan)),
                    "own_solve_time_sec": math.nan,
                    "pm_solve_time_sec": float(getattr(pm_summary, "pm_solve_time_sec", math.nan)),
                    "run_label": run_label,
                    "warm_start": "zero",
                    "pm_va_reference_offset_subtracted": math.nan,
                }
                print(f"  ERROR: {exc}", flush=True)

            append_csv(diagnostics_path, [diagnostic_row])
            append_csv(validation_path, [comparison])
            if run_label == "pm_like_zero":
                append_csv(pm_like_path, [comparison])
            print(
                f"  status={diagnostic_row['status']} obj_rel={diagnostic_row['objective_rel_diff']} "
                f"iters={diagnostic_row['solve_iterations']} time={diagnostic_row['solve_time_sec']}",
                flush=True,
            )

    copy_outputs(outputs)
    print("Saved:")
    for path in outputs:
        print(f"  {path}")
    print(f"Copied outputs to {ORGANIZED_ROOT}")


if __name__ == "__main__":
    main()
