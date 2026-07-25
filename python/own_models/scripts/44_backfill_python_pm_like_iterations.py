"""Backfill Python PM-like AC-OPF Ipopt iteration counts.

The first full Python PM-like sweep used Ipopt ``print_level=0``.  That made
the solves quiet, but it also suppressed the "Number of Iterations" log line
that ``solve_with_iteration_capture`` parses.  This script reruns only rows
that already solved successfully and writes a small sidecar CSV with the
captured iteration counts.  It does not touch the full validation summary or
per-scenario export tables.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import os
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
from own_models.src.analysis.compare_ac_with_powermodels import load_powermodels_ac_reference  # noqa: E402
from own_models.src.opf.ac.model import solve_ac_opf_pyomo  # noqa: E402


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


pm_like_sweep = _load_module(SCRIPT_DIR / "34_run_own_vs_powermodels_pm_like_full_sweep.py", "pm_like_sweep")

REFERENCE_DIR = ROOT / "results" / "reference" / "powermodels" / "ac_opf"
SUMMARY_PATH = ROOT / "results" / "own_vs_powermodels" / "python_pm_like" / "summary.csv"
OUT_PATH = ROOT / "results" / "own_vs_powermodels" / "python_pm_like" / "iteration_backfill.csv"
ORG_PATH = (
    ROOT
    / "results"
    / "own_vs_powermodels"
    / "_organized_for_meeting"
    / "08_python_ac_opf_debugging"
    / "python_pm_like_iteration_backfill.csv"
)


def disk_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    resolved = path.resolve()
    text = str(resolved)
    return Path(text if text.startswith("\\\\?\\") else "\\\\?\\" + text)


def ensure_conda_solver_path() -> None:
    env_root = Path(sys.executable).resolve().parent
    library_bin = env_root / "Library" / "bin"
    if library_bin.exists():
        os.environ["PATH"] = str(library_bin) + os.pathsep + os.environ.get("PATH", "")


def truthy(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def finite_number(value) -> bool:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(parsed)


def append_csv(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_csv(path, mode="a", index=False, header=not path.exists())


def completed_pairs(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if frame.empty:
        return set()
    return set(zip(frame["case"], frame["scenario"]))


def candidate_rows(summary_path: Path, cases: set[str] | None) -> list[dict]:
    frame = pd.read_csv(summary_path, dtype=str, keep_default_na=False)
    rows: list[dict] = []
    for row in frame.to_dict(orient="records"):
        if cases and row.get("case") not in cases:
            continue
        if not truthy(row.get("own_success")):
            continue
        if row.get("own_status") != "optimal":
            continue
        if finite_number(row.get("own_solve_iterations")):
            continue
        rows.append(row)
    return rows


def scenario_suffix_map(stem: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for suffix in pm_like_sweep.discover_scenarios(stem):
        label = pm_like_sweep.KNOWN_SUFFIXES.get(suffix, suffix)
        mapping[label] = suffix
    return mapping


def solve_iteration_row(stem: str, case_label: str, row: dict, *, max_iter: int, max_cpu_time: float | None) -> dict:
    scenario_label = row["scenario"]
    suffix = scenario_suffix_map(stem).get(scenario_label, scenario_label)
    case_json_path = REFERENCE_DIR / f"{stem}_{suffix}_case.json"
    ref_json_path = REFERENCE_DIR / f"{stem}_{suffix}_reference.json"
    if not case_json_path.exists():
        return {
            "case": case_label,
            "scenario": scenario_label,
            "status": "MISSING_CASE_JSON",
            "success": False,
            "solve_time_sec": math.nan,
            "solve_iterations": math.nan,
            "objective": math.nan,
            "objective_diff_vs_existing": math.nan,
            "error": f"missing {case_json_path}",
        }

    case = load_powercase_from_powermodels_json(disk_path(case_json_path))
    reference = load_powermodels_ac_reference(disk_path(ref_json_path)) if ref_json_path.exists() else {}
    solver_options = {"max_iter": int(max_iter)}
    if max_cpu_time is not None:
        solver_options["max_cpu_time"] = float(max_cpu_time)

    start = time.perf_counter()
    try:
        result = solve_ac_opf_pyomo(
            case,
            cost_power_units="pu",
            voltage_bounds=None,
            warm_start="zero",
            fix_reference_angle=True,
            solver_options=solver_options,
            model_name=f"{case.name}_pm_like_zero_iteration_backfill",
        )
        elapsed = time.perf_counter() - start
        old_objective = float(row["own_objective"]) if finite_number(row.get("own_objective")) else math.nan
        return {
            "case": case_label,
            "scenario": scenario_label,
            "status": result.status,
            "success": bool(result.success),
            "solve_time_sec": elapsed,
            "solve_iterations": result.solve_iterations,
            "objective": result.objective if result.success else math.nan,
            "existing_objective": old_objective,
            "objective_diff_vs_existing": result.objective - old_objective if result.success and math.isfinite(old_objective) else math.nan,
            "pm_solve_iterations": reference.get("solve_iterations", math.nan),
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "case": case_label,
            "scenario": scenario_label,
            "status": "ERROR",
            "success": False,
            "solve_time_sec": time.perf_counter() - start,
            "solve_iterations": math.nan,
            "objective": math.nan,
            "existing_objective": row.get("own_objective", math.nan),
            "objective_diff_vs_existing": math.nan,
            "pm_solve_iterations": reference.get("solve_iterations", math.nan),
            "error": str(exc),
        }


def main() -> None:
    ensure_conda_solver_path()
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="*", default=None, help="Optional case labels to backfill.")
    parser.add_argument("--max-scenarios", type=int, default=None, help="Optional total row cap for smoke tests.")
    parser.add_argument("--max-iter", type=int, default=3000)
    parser.add_argument("--max-cpu-time", type=float, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.overwrite:
        for path in (OUT_PATH, ORG_PATH):
            if path.exists():
                path.unlink()

    cases = set(args.cases) if args.cases else None
    case_to_stem = {label: stem for stem, label in pm_like_sweep.CASES}
    done = completed_pairs(OUT_PATH)
    rows = candidate_rows(SUMMARY_PATH, cases)
    total = 0
    print(f"Backfill candidates: {len(rows)}", flush=True)
    for row in rows:
        case_label = row["case"]
        scenario_label = row["scenario"]
        if (case_label, scenario_label) in done:
            continue
        if args.max_scenarios is not None and total >= args.max_scenarios:
            break
        stem = case_to_stem.get(case_label)
        if stem is None:
            continue
        print(f"[{total + 1}] {case_label} / {scenario_label}", flush=True)
        out_row = solve_iteration_row(
            stem,
            case_label,
            row,
            max_iter=args.max_iter,
            max_cpu_time=args.max_cpu_time,
        )
        append_csv(OUT_PATH, out_row)
        append_csv(ORG_PATH, out_row)
        done.add((case_label, scenario_label))
        total += 1
        print(
            f"  status={out_row['status']} iterations={out_row['solve_iterations']} "
            f"time={out_row['solve_time_sec']:.3f}s",
            flush=True,
        )

    print(f"Wrote backfill rows to {OUT_PATH}", flush=True)
    print(f"Organized copy: {ORG_PATH}", flush=True)


if __name__ == "__main__":
    main()
