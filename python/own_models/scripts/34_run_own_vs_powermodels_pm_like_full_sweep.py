"""Full-scenario Python PM-like AC OPF vs PowerModels sweep.

This is the PM-like counterpart of 27_run_own_vs_powermodels_full_sweep.py.
It is intentionally separate from the current Python AC OPF Set B runner so the
existing validation artifacts remain untouched.

The script is driven by the PowerModels reference JSON files that already exist
under results/reference/powermodels/ac_opf.  For each reference scenario it:

* solves the experimental Python PowerModels-like AC OPF with zero start;
* appends a summary row;
* when both own and PowerModels solutions are feasible, exports the same
  inspection tables used elsewhere: generators, buses, branches, shunts,
  constraints_inventory, and constraints_binding.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.powermodels_csv_adapter import (  # noqa: E402
    load_powercase_from_powermodels_json,
)
from own_models.src.analysis.compare_ac_with_powermodels import (  # noqa: E402
    ac_opf_powermodels_comparison_summary,
    classify_powermodels_ac_outcome,
    compare_ac_opf_branches_with_powermodels,
    compare_ac_opf_buses_with_powermodels,
    compare_ac_opf_generators_with_powermodels,
    load_powermodels_ac_reference,
)
from own_models.src.opf.ac.model import solve_ac_opf_pyomo  # noqa: E402


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


set_b = _load_module(SCRIPT_DIR / "23_run_own_vs_powermodels_tables.py", "own_vs_powermodels_set_b")

REFERENCE_DIR = ROOT / "results" / "reference" / "powermodels" / "ac_opf"
OUT_ROOT = ROOT / "results" / "own_vs_powermodels" / "python_pm_like"
SUMMARY_PATH = OUT_ROOT / "summary.csv"
SOLVER_DIAGNOSTICS_PATH = OUT_ROOT / "solver_diagnostics.csv"
ORGANIZED_DEBUG_ROOT = (
    ROOT
    / "results"
    / "own_vs_powermodels"
    / "_organized_for_meeting"
    / "08_python_ac_opf_debugging"
)

CASES = set_b.CASES

KNOWN_SUFFIXES = {
    "base": "base",
    "1888_rte_load_x0p8__load_scaling__none__none__load_multiplier__0p8": "load_x0.8",
    "1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2": "load_x1.2_reactive_stress",
    "1888_rte_qg_limits_x0p8__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p8": "qg_limits_x0.8",
}


def disk_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    resolved = path.resolve()
    text = str(resolved)
    return Path(text if text.startswith("\\\\?\\") else "\\\\?\\" + text)


def path_exists(path: Path) -> bool:
    return disk_path(path).exists()


def ensure_conda_solver_path() -> None:
    env_root = Path(sys.executable).resolve().parent
    library_bin = env_root / "Library" / "bin"
    if library_bin.exists():
        os.environ["PATH"] = str(library_bin) + os.pathsep + os.environ.get("PATH", "")


def constraints_inventory_rows_pm_like(case) -> pd.DataFrame:
    n_bus = len(case.buses)
    n_gen = len(case.generators)
    n_branch = len(case.branches)
    rows = [
        {
            "constraint_type": "power_balance_p",
            "applies_to": "bus",
            "formula": "sum(P arcs incident at bus) = sum(Pg) - Pd - gs*Vm^2",
            "file_line": "opf/ac/powermodels_like.py:255-268 (_add_pm_like_power_balance_constraints)",
            "n_instances": n_bus,
            "tolerance_used": float("nan"),
        },
        {
            "constraint_type": "power_balance_q",
            "applies_to": "bus",
            "formula": "sum(Q arcs incident at bus) = sum(Qg) - Qd + bs*Vm^2",
            "file_line": "opf/ac/powermodels_like.py:255-268 (_add_pm_like_power_balance_constraints)",
            "n_instances": n_bus,
            "tolerance_used": float("nan"),
        },
        {
            "constraint_type": "ohms_from_p",
            "applies_to": "branch",
            "formula": "directed from-end active branch-flow variable equals AC branch-flow expression",
            "file_line": "opf/ac/powermodels_like.py:299-305 (_add_pm_like_branch_constraints)",
            "n_instances": n_branch,
            "tolerance_used": float("nan"),
        },
        {
            "constraint_type": "ohms_from_q",
            "applies_to": "branch",
            "formula": "directed from-end reactive branch-flow variable equals AC branch-flow expression",
            "file_line": "opf/ac/powermodels_like.py:307-313 (_add_pm_like_branch_constraints)",
            "n_instances": n_branch,
            "tolerance_used": float("nan"),
        },
        {
            "constraint_type": "ohms_to_p",
            "applies_to": "branch",
            "formula": "directed to-end active branch-flow variable equals AC branch-flow expression",
            "file_line": "opf/ac/powermodels_like.py:315-321 (_add_pm_like_branch_constraints)",
            "n_instances": n_branch,
            "tolerance_used": float("nan"),
        },
        {
            "constraint_type": "ohms_to_q",
            "applies_to": "branch",
            "formula": "directed to-end reactive branch-flow variable equals AC branch-flow expression",
            "file_line": "opf/ac/powermodels_like.py:323-329 (_add_pm_like_branch_constraints)",
            "n_instances": n_branch,
            "tolerance_used": float("nan"),
        },
        {
            "constraint_type": "pg_min",
            "applies_to": "generator",
            "formula": "Pg >= Pmin",
            "file_line": "opf/ac/powermodels_like.py:171-253 (_add_pm_like_variables)",
            "n_instances": n_gen,
            "tolerance_used": 1e-6,
        },
        {
            "constraint_type": "pg_max",
            "applies_to": "generator",
            "formula": "Pg <= Pmax",
            "file_line": "opf/ac/powermodels_like.py:171-253 (_add_pm_like_variables)",
            "n_instances": n_gen,
            "tolerance_used": 1e-6,
        },
        {
            "constraint_type": "qg_min",
            "applies_to": "generator",
            "formula": "Qg >= Qmin",
            "file_line": "opf/ac/powermodels_like.py:171-253 (_add_pm_like_variables)",
            "n_instances": n_gen,
            "tolerance_used": 1e-6,
        },
        {
            "constraint_type": "qg_max",
            "applies_to": "generator",
            "formula": "Qg <= Qmax",
            "file_line": "opf/ac/powermodels_like.py:171-253 (_add_pm_like_variables)",
            "n_instances": n_gen,
            "tolerance_used": 1e-6,
        },
        {
            "constraint_type": "vm_min",
            "applies_to": "bus",
            "formula": "Vm >= Vmin",
            "file_line": "opf/ac/powermodels_like.py:171-253 (_add_pm_like_variables)",
            "n_instances": n_bus,
            "tolerance_used": 1e-6,
        },
        {
            "constraint_type": "vm_max",
            "applies_to": "bus",
            "formula": "Vm <= Vmax",
            "file_line": "opf/ac/powermodels_like.py:171-253 (_add_pm_like_variables)",
            "n_instances": n_bus,
            "tolerance_used": 1e-6,
        },
        {
            "constraint_type": "thermal",
            "applies_to": "branch",
            "formula": "Sf^2 <= rate_a^2 and St^2 <= rate_a^2",
            "file_line": "opf/ac/powermodels_like.py:331-338 (_add_pm_like_branch_constraints)",
            "n_instances": n_branch,
            "tolerance_used": 1e-6,
        },
        {
            "constraint_type": "angle_lower",
            "applies_to": "branch",
            "formula": "theta_i - theta_j >= angmin",
            "file_line": "opf/ac/powermodels_like.py:291-297 (_add_pm_like_branch_constraints)",
            "n_instances": n_branch,
            "tolerance_used": 1e-6,
        },
        {
            "constraint_type": "angle_upper",
            "applies_to": "branch",
            "formula": "theta_i - theta_j <= angmax",
            "file_line": "opf/ac/powermodels_like.py:291-297 (_add_pm_like_branch_constraints)",
            "n_instances": n_branch,
            "tolerance_used": 1e-6,
        },
    ]
    return pd.DataFrame(rows)


def discover_scenarios(stem: str) -> list[str]:
    pattern = re.compile(rf"^{re.escape(stem)}_(.+)_case\.json$")
    suffixes: list[str] = []
    for path in REFERENCE_DIR.glob(f"{stem}_*_case.json"):
        match = pattern.match(path.name)
        if match:
            suffixes.append(match.group(1))
    return sorted(suffixes)


def completed_pairs() -> set[tuple[str, str]]:
    if not SUMMARY_PATH.exists():
        return set()
    try:
        df = pd.read_csv(SUMMARY_PATH, usecols=["case", "scenario"], dtype=str, keep_default_na=False)
    except (FileNotFoundError, ValueError, pd.errors.EmptyDataError):
        return set()
    return set(zip(df["case"], df["scenario"]))


def drop_rows_with_statuses(statuses: set[str]) -> int:
    if not statuses or not SUMMARY_PATH.exists():
        return 0

    dropped_pairs: set[tuple[str, str]] = set()
    total_dropped = 0
    for path in (SUMMARY_PATH, SOLVER_DIAGNOSTICS_PATH):
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        except pd.errors.EmptyDataError:
            continue
        if "own_status" not in frame.columns:
            continue
        mask = frame["own_status"].isin(statuses)
        if "case" in frame.columns and "scenario" in frame.columns:
            for case, scenario in zip(frame.loc[mask, "case"], frame.loc[mask, "scenario"]):
                dropped_pairs.add((case, scenario))
        total_dropped += int(mask.sum())
        backup = path.with_suffix(path.suffix + ".before_rerun_statuses")
        if not backup.exists():
            shutil.copy2(path, backup)
        frame.loc[~mask].to_csv(path, index=False)
    return len(dropped_pairs) or total_dropped


def append_csv(path: Path, row: dict) -> None:
    frame = pd.DataFrame([row])
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, mode="a", header=not path.exists(), index=False)


def append_terminal_row(
    *,
    label: str,
    scenario_label: str,
    own_status: str,
    pm_status: str = "UNKNOWN",
    error: str,
) -> None:
    row = {
        "case": label,
        "scenario": scenario_label,
        "model_variant": "python_pm_like_zero",
        "warm_start": "zero",
        "own_status": own_status,
        "pm_status": pm_status,
        "outcome_match": False,
        "own_objective": math.nan,
        "pm_objective": math.nan,
        "objective_diff": math.nan,
        "objective_rel_diff": math.nan,
        "own_solve_time_sec": math.nan,
        "pm_solve_time_sec": math.nan,
        "own_solve_iterations": math.nan,
        "pm_solve_iterations": math.nan,
        "max_p_balance_violation": math.nan,
        "max_q_balance_violation": math.nan,
        "max_pg_limit_violation": math.nan,
        "max_qg_limit_violation": math.nan,
        "max_voltage_violation": math.nan,
        "max_thermal_violation": math.nan,
        "max_angle_violation": math.nan,
        "error": error,
    }
    append_csv(SUMMARY_PATH, row)
    append_csv(
        SOLVER_DIAGNOSTICS_PATH,
        {
            "case": label,
            "scenario": scenario_label,
            "model_variant": "python_pm_like_zero",
            "warm_start": "zero",
            "own_status": own_status,
            "pm_status": pm_status,
            "success": False,
            "objective": math.nan,
            "pm_objective": math.nan,
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
            "error": error,
        },
    )


def scenario_output_dir(label: str, scenario_label: str) -> Path:
    """Return a Windows-safe scenario directory path.

    Some validation scenario ids are long enough that adding
    ``constraints_inventory.csv`` exceeds the classic Windows path limit.  Keep
    the directory readable, but shorten long labels with a stable hash.
    ``scenario_label.txt`` inside the directory stores the full original label.
    """
    safe_label = re.sub(r"[^A-Za-z0-9._=-]+", "_", scenario_label).strip("_")
    candidate = OUT_ROOT / label / safe_label
    if len(str(candidate / "constraints_inventory.csv")) <= 190:
        return candidate

    digest = hashlib.sha1(scenario_label.encode("utf-8")).hexdigest()[:10]
    shortened = f"{safe_label[:48].rstrip('_')}__{digest}"
    return OUT_ROOT / label / shortened


def pm_is_solved(reference: dict) -> bool:
    return classify_powermodels_ac_outcome(reference) == "SOLVED_FEASIBLE"


def solve_pm_like(case, *, max_iter: int, max_cpu_time: float | None):
    start = time.perf_counter()
    # Do not suppress Ipopt output here: solve_with_iteration_capture parses the
    # "Number of Iterations" line from the solver log while keeping tee=False quiet.
    solver_options = {"max_iter": int(max_iter)}
    if max_cpu_time is not None:
        solver_options["max_cpu_time"] = float(max_cpu_time)
    result = solve_ac_opf_pyomo(
        case,
        cost_power_units="pu",
        voltage_bounds=None,
        warm_start="zero",
        fix_reference_angle=True,
        solver_options=solver_options,
        model_name=f"{case.name}_pm_like_zero",
    )
    return result, time.perf_counter() - start


def process_scenario(
    stem: str,
    label: str,
    scenario_label: str,
    reference_suffix: str,
    *,
    max_iter: int,
    max_cpu_time: float | None,
) -> None:
    print(f"=== {label} / {scenario_label} ===", flush=True)
    case_json_path = REFERENCE_DIR / f"{stem}_{reference_suffix}_case.json"
    reference_json_path = REFERENCE_DIR / f"{stem}_{reference_suffix}_reference.json"
    if not path_exists(case_json_path) or not path_exists(reference_json_path):
        error_message = f"missing reference files for {stem}_{reference_suffix}"
        print(f"  SKIPPED -- {error_message}", flush=True)
        append_terminal_row(
            label=label,
            scenario_label=scenario_label,
            own_status="MISSING_REFERENCE",
            error=error_message,
        )
        return

    case = load_powercase_from_powermodels_json(disk_path(case_json_path))
    reference = load_powermodels_ac_reference(disk_path(reference_json_path))
    pm_ok = pm_is_solved(reference)

    try:
        own_result, own_solve_time_sec = solve_pm_like(case, max_iter=max_iter, max_cpu_time=max_cpu_time)
        own_ok = bool(own_result.success)
        error_message = ""
    except Exception as exc:  # noqa: BLE001 -- checkpoint and continue
        own_result = None
        own_solve_time_sec = math.nan
        own_ok = False
        error_message = str(exc)
        print(f"  ERROR -- {error_message}", flush=True)

    pm_objective = reference.get("objective")
    if own_result is not None and own_ok and pm_ok and pm_objective is not None:
        obj_diff = own_result.objective - float(pm_objective)
        obj_rel_diff = abs(obj_diff) / (1.0 + abs(float(pm_objective)))
    else:
        obj_diff = math.nan
        obj_rel_diff = math.nan

    summary_row = {
        "case": label,
        "scenario": scenario_label,
        "model_variant": "python_pm_like_zero",
        "warm_start": "zero",
        "own_status": getattr(own_result, "status", "ERROR" if error_message else "UNKNOWN"),
        "pm_status": reference.get("termination_status", "UNKNOWN"),
        "outcome_match": own_ok == pm_ok,
        "own_objective": getattr(own_result, "objective", math.nan) if own_ok else math.nan,
        "pm_objective": pm_objective if pm_ok else math.nan,
        "objective_diff": obj_diff,
        "objective_rel_diff": obj_rel_diff,
        "own_solve_time_sec": own_solve_time_sec,
        "pm_solve_time_sec": reference.get("solve_time_sec"),
        "own_solve_iterations": getattr(own_result, "solve_iterations", math.nan),
        "pm_solve_iterations": reference.get("solve_iterations"),
        "max_p_balance_violation": getattr(own_result, "max_p_balance_violation", math.nan),
        "max_q_balance_violation": getattr(own_result, "max_q_balance_violation", math.nan),
        "max_pg_limit_violation": getattr(own_result, "max_pg_limit_violation", math.nan),
        "max_qg_limit_violation": getattr(own_result, "max_qg_limit_violation", math.nan),
        "max_voltage_violation": getattr(own_result, "max_voltage_violation", math.nan),
        "max_thermal_violation": getattr(own_result, "max_thermal_violation", math.nan),
        "max_angle_violation": getattr(own_result, "max_angle_violation", math.nan),
        "error": error_message,
    }

    if own_result is not None and own_ok and pm_ok:
        gen_rows = compare_ac_opf_generators_with_powermodels(case, own_result, reference)
        bus_rows = compare_ac_opf_buses_with_powermodels(case, own_result, reference)
        branch_rows = compare_ac_opf_branches_with_powermodels(case, own_result, reference)
        comparison = ac_opf_powermodels_comparison_summary(
            case,
            own_result,
            reference,
            gen_rows,
            bus_rows,
            branch_rows,
            scenario=scenario_label,
            own_solve_time_sec=own_solve_time_sec,
        ).iloc[0].to_dict()
        for key, value in comparison.items():
            if key not in summary_row:
                summary_row[key] = value

        out_dir = scenario_output_dir(label, scenario_label)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "scenario_label.txt").write_text(scenario_label, encoding="utf-8")
        gen_rows.to_csv(out_dir / "generators.csv", index=False)
        bus_rows.to_csv(out_dir / "buses.csv", index=False)
        branch_rows.to_csv(out_dir / "branches.csv", index=False)
        set_b.shunts_rows(case).to_csv(out_dir / "shunts.csv", index=False)
        constraints_inventory_rows_pm_like(case).to_csv(out_dir / "constraints_inventory.csv", index=False)
        set_b.constraints_binding_rows(gen_rows, bus_rows, branch_rows).to_csv(
            out_dir / "constraints_binding.csv",
            index=False,
        )
        print(f"  wrote tables to {out_dir}", flush=True)
    else:
        print(f"  SKIPPED tables -- own_ok={own_ok} pm_ok={pm_ok}", flush=True)

    append_csv(SUMMARY_PATH, summary_row)
    append_csv(
        SOLVER_DIAGNOSTICS_PATH,
        {
            "case": label,
            "scenario": scenario_label,
            "model_variant": "python_pm_like_zero",
            "warm_start": "zero",
            "own_status": summary_row["own_status"],
            "pm_status": summary_row["pm_status"],
            "success": own_ok,
            "objective": getattr(own_result, "objective", math.nan) if own_result is not None else math.nan,
            "pm_objective": pm_objective,
            "objective_diff": obj_diff,
            "objective_rel_diff": obj_rel_diff,
            "solve_time_sec": own_solve_time_sec,
            "solve_iterations": getattr(own_result, "solve_iterations", math.nan),
            "max_p_balance_violation": summary_row["max_p_balance_violation"],
            "max_q_balance_violation": summary_row["max_q_balance_violation"],
            "max_pg_limit_violation": summary_row["max_pg_limit_violation"],
            "max_qg_limit_violation": summary_row["max_qg_limit_violation"],
            "max_voltage_violation": summary_row["max_voltage_violation"],
            "max_thermal_violation": summary_row["max_thermal_violation"],
            "max_angle_violation": summary_row["max_angle_violation"],
            "error": error_message,
        },
    )


def checkpoint_skipped_scenario(
    stem: str,
    label: str,
    scenario_label: str,
    reference_suffix: str,
    *,
    reason: str,
) -> None:
    reference_json_path = REFERENCE_DIR / f"{stem}_{reference_suffix}_reference.json"
    reference = load_powermodels_ac_reference(disk_path(reference_json_path)) if path_exists(reference_json_path) else {}
    row = {
        "case": label,
        "scenario": scenario_label,
        "model_variant": "python_pm_like_zero",
        "warm_start": "zero",
        "own_status": "SKIPPED_TIMEOUT",
        "pm_status": reference.get("termination_status", "UNKNOWN"),
        "outcome_match": False,
        "own_objective": math.nan,
        "pm_objective": reference.get("objective", math.nan),
        "objective_diff": math.nan,
        "objective_rel_diff": math.nan,
        "own_solve_time_sec": math.nan,
        "pm_solve_time_sec": reference.get("solve_time_sec", math.nan),
        "own_solve_iterations": math.nan,
        "pm_solve_iterations": reference.get("solve_iterations", math.nan),
        "max_p_balance_violation": math.nan,
        "max_q_balance_violation": math.nan,
        "max_pg_limit_violation": math.nan,
        "max_qg_limit_violation": math.nan,
        "max_voltage_violation": math.nan,
        "max_thermal_violation": math.nan,
        "max_angle_violation": math.nan,
        "error": reason,
    }
    append_csv(SUMMARY_PATH, row)
    append_csv(
        SOLVER_DIAGNOSTICS_PATH,
        {
            "case": label,
            "scenario": scenario_label,
            "model_variant": "python_pm_like_zero",
            "warm_start": "zero",
            "own_status": "SKIPPED_TIMEOUT",
            "pm_status": row["pm_status"],
            "success": False,
            "objective": math.nan,
            "pm_objective": row["pm_objective"],
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
            "error": reason,
        },
    )


def copy_compact_outputs() -> None:
    ORGANIZED_DEBUG_ROOT.mkdir(parents=True, exist_ok=True)
    for path in (SUMMARY_PATH, SOLVER_DIAGNOSTICS_PATH):
        if path.exists():
            shutil.copy2(path, ORGANIZED_DEBUG_ROOT / f"python_pm_like_full_sweep_{path.name}")


def filtered_cases(case_filters: list[str] | None):
    if not case_filters:
        return CASES
    wanted = set(case_filters)
    return [
        (stem, label)
        for stem, label in CASES
        if stem in wanted or label in wanted or stem.replace("pglib_opf_case", "") in wanted
    ]


def main() -> None:
    ensure_conda_solver_path()
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="*", default=None, help="Optional case labels or PGLib stems to run.")
    parser.add_argument("--max-scenarios", type=int, default=None, help="Optional total scenario cap for smoke tests.")
    parser.add_argument("--max-iter", type=int, default=3000)
    parser.add_argument("--max-cpu-time", type=float, default=None, help="Optional Ipopt max_cpu_time option in seconds.")
    parser.add_argument(
        "--rerun-statuses",
        nargs="*",
        default=None,
        help="Remove existing summary/diagnostic rows with these own_status values before resuming.",
    )
    parser.add_argument(
        "--skip-scenarios",
        nargs="*",
        default=None,
        help="Optional scenario labels to checkpoint as SKIPPED_TIMEOUT and continue.",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.overwrite:
        if OUT_ROOT.exists():
            shutil.rmtree(OUT_ROOT)
        SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)

    dropped = drop_rows_with_statuses(set(args.rerun_statuses or []))
    if dropped:
        print(f"Removed {dropped} completed pair(s) for --rerun-statuses.", flush=True)

    completed = completed_pairs()
    skip_scenarios = set(args.skip_scenarios or [])
    selected_cases = filtered_cases(args.cases)
    total_run = 0
    print(
        "Starting fresh -- no prior PM-like Python progress found."
        if not completed
        else f"Resuming -- {len(completed)} PM-like Python (case, scenario) pairs already done.",
        flush=True,
    )

    for stem, label in selected_cases:
        suffixes = discover_scenarios(stem)
        print(f"\n=== {label}: {len(suffixes)} reference scenario(s) available ===", flush=True)
        for i, suffix in enumerate(suffixes, start=1):
            if args.max_scenarios is not None and total_run >= args.max_scenarios:
                copy_compact_outputs()
                print("Reached --max-scenarios cap.", flush=True)
                return
            scenario_label = KNOWN_SUFFIXES.get(suffix, suffix)
            if (label, scenario_label) in completed:
                print(f"  [{i}/{len(suffixes)}] {scenario_label} -- already done, skipping", flush=True)
                continue
            if scenario_label in skip_scenarios:
                print(f"  [{i}/{len(suffixes)}] {scenario_label} -- checkpointing as SKIPPED_TIMEOUT", flush=True)
                checkpoint_skipped_scenario(
                    stem,
                    label,
                    scenario_label,
                    suffix,
                    reason="manual skip after repeated long-running Ipopt attempt",
                )
                completed.add((label, scenario_label))
                total_run += 1
                continue
            print(f"  [{i}/{len(suffixes)}] {scenario_label}", flush=True)
            started = time.time()
            process_scenario(
                stem,
                label,
                scenario_label,
                suffix,
                max_iter=args.max_iter,
                max_cpu_time=args.max_cpu_time,
            )
            completed.add((label, scenario_label))
            total_run += 1
            print(f"  finished in {time.time() - started:.1f}s", flush=True)

    copy_compact_outputs()
    print("\nPython PM-like full sweep complete.", flush=True)
    print(f"Summary: {SUMMARY_PATH}", flush=True)
    print(f"Solver diagnostics: {SOLVER_DIAGNOSTICS_PATH}", flush=True)
    print(f"Per-scenario tables: {OUT_ROOT}", flush=True)


if __name__ == "__main__":
    main()
