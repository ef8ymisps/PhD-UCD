"""Full-scenario-grid active-losses (loss-minimization) objective comparison:
PowerModels, Julia, Python.

Extends 54_run_three_way_active_losses_table.py (base case only, 8 cases) to
the full ~313-scenario grid, matching the generation-cost full sweep
(34_run_own_vs_powermodels_pm_like_full_sweep.py). PowerModels' loss
objective comes from Julia's own custom-objective PowerModels solve
(`julia/own_models/scripts/48_run_full_sweep_active_losses_table.jl`'s
output), not re-derived here -- PowerModels.jl has no Python binding.
Checkpointed/resumable, same pattern as script 34.

Output: results/own_vs_powermodels/three_way_active_losses/python_full_sweep.csv
"""

from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
REPO_ROOT = SCRIPT_DIR.parents[2]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.powermodels_csv_adapter import load_powercase_from_powermodels_json  # noqa: E402
from own_models.src.opf.ac import solve_ac_opf_pyomo  # noqa: E402

REFERENCE_DIR = REPO_ROOT / "results" / "reference" / "powermodels" / "ac_opf"
LOSSES_OUT = REPO_ROOT / "results" / "own_vs_powermodels" / "three_way_active_losses"
JULIA_FULL_SWEEP_PATH = LOSSES_OUT / "julia_full_sweep.csv"
OUT_PATH = LOSSES_OUT / "python_full_sweep.csv"

CASES = [
    ("pglib_opf_case5_pjm", "5_pjm"),
    ("pglib_opf_case14_ieee", "14_ieee"),
    ("pglib_opf_case30_ieee", "30_ieee"),
    ("pglib_opf_case118_ieee", "118_ieee"),
    ("pglib_opf_case300_ieee", "300_ieee"),
    ("pglib_opf_case1354_pegase", "1354_pegase"),
    ("pglib_opf_case1888_rte", "1888_rte"),
    ("pglib_opf_case6470_rte", "6470_rte"),
]


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


def discover_scenarios(stem: str) -> list[str]:
    import re

    pattern = re.compile(rf"^{re.escape(stem)}_(.+)_case\.json$")
    suffixes: list[str] = []
    for path in REFERENCE_DIR.glob(f"{stem}_*_case.json"):
        match = pattern.match(path.name)
        if match:
            suffixes.append(match.group(1))
    return sorted(suffixes)


def completed_pairs() -> set[tuple[str, str]]:
    if not OUT_PATH.exists():
        return set()
    try:
        df = pd.read_csv(OUT_PATH, usecols=["case", "scenario"], dtype=str, keep_default_na=False)
    except (FileNotFoundError, ValueError, pd.errors.EmptyDataError):
        return set()
    return set(zip(df["case"], df["scenario"]))


def append_row(row: dict) -> None:
    frame = pd.DataFrame([row])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT_PATH, mode="a", header=not OUT_PATH.exists(), index=False)


def main() -> None:
    ensure_conda_solver_path()
    if not JULIA_FULL_SWEEP_PATH.exists():
        raise FileNotFoundError(
            f"Missing {JULIA_FULL_SWEEP_PATH} -- run "
            "julia/own_models/scripts/48_run_full_sweep_active_losses_table.jl first."
        )
    julia_full_sweep = pd.read_csv(JULIA_FULL_SWEEP_PATH)
    julia_lookup = {
        (row["case"], row["scenario"]): row
        for _, row in julia_full_sweep.iterrows()
    }
    # Julia's script 48 writes scenarios under compact_scenarios' own raw
    # scenario_id (e.g. "5_pjm__base__none__none__none__none"), while this
    # script's own scenario discovery (from reference JSON filenames, below)
    # yields the FRIENDLY labels those reference JSONs were originally
    # exported under (Julia script 25 renames "base" and the 3 case1888_rte
    # Q1-Q10 spotlight scenarios before exporting). Without reconciling the
    # two, every one of these 4 known scenarios' lookup misses silently
    # ("SKIPPED, no Julia/PM loss row yet") and Python's own active-losses
    # solve never runs for them at all -- alias the raw keys to their
    # friendly labels so the lookup succeeds.
    known_friendly_labels = {
        "base": "{label}__base__none__none__none__none",
        "load_x0.8": "1888_rte_load_x0p8__load_scaling__none__none__load_multiplier__0p8",
        "load_x1.2_reactive_stress": "1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2",
        "qg_limits_x0.8": "1888_rte_qg_limits_x0p8__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p8",
    }
    for stem, label in CASES:
        raw_key = (label, known_friendly_labels["base"].format(label=label))
        if raw_key in julia_lookup:
            julia_lookup[(label, "base")] = julia_lookup[raw_key]
    for friendly, raw_id in known_friendly_labels.items():
        if friendly == "base":
            continue  # handled per-case above
        raw_key = ("1888_rte", raw_id)
        if raw_key in julia_lookup:
            julia_lookup[("1888_rte", friendly)] = julia_lookup[raw_key]

    completed = completed_pairs()
    print(
        "Starting fresh." if not completed else f"Resuming -- {len(completed)} pairs already done."
    )

    for stem, label in CASES:
        suffixes = discover_scenarios(stem)
        print(f"\n=== {label}: {len(suffixes)} scenario(s) ===", flush=True)
        for i, suffix in enumerate(suffixes, start=1):
            if (label, suffix) in completed:
                print(f"  [{i}/{len(suffixes)}] {suffix} -- already done, skipping", flush=True)
                continue
            if (label, suffix) not in julia_lookup:
                print(f"  [{i}/{len(suffixes)}] {suffix} -- SKIPPED, no Julia/PM loss row yet", flush=True)
                continue

            print(f"  [{i}/{len(suffixes)}] {suffix} (active losses) ...", flush=True)
            started = time.time()
            case_json_path = REFERENCE_DIR / f"{stem}_{suffix}_case.json"
            try:
                case = load_powercase_from_powermodels_json(disk_path(case_json_path))
                # max_cpu_time safety cap: Julia's equivalent sweep got stuck
                # 8+ hours of active CPU time on one case1888_rte scenario
                # under this same objective without hitting max_iter.
                result = solve_ac_opf_pyomo(
                    case,
                    objective_type="active_losses",
                    voltage_bounds=None,
                    warm_start="zero",
                    solver_options={"max_cpu_time": 300},
                )
                python_objective = float(result.objective) if result.success else math.nan
                python_status = result.status
            except Exception as exc:  # noqa: BLE001 -- checkpoint and continue
                python_objective = math.nan
                python_status = f"ERROR: {exc}"

            jrow = julia_lookup[(label, suffix)]
            pm_objective = float(jrow["pm_loss_objective"])
            julia_objective = float(jrow["julia_loss_objective"])

            row = {
                "case": label,
                "scenario": suffix,
                "pm_loss_objective": pm_objective,
                "julia_loss_objective": julia_objective,
                "python_loss_objective": python_objective,
                "python_status": python_status,
                "julia_vs_pm_gap_pct": float(jrow["julia_vs_pm_gap_pct"]),
                "python_vs_pm_gap_abs": python_objective - pm_objective,
                "python_vs_pm_gap_pct": 100.0 * abs(python_objective - pm_objective) / (1.0 + abs(pm_objective))
                if not math.isnan(python_objective)
                else math.nan,
                "julia_vs_python_gap_abs": julia_objective - python_objective,
            }
            append_row(row)
            completed.add((label, suffix))
            print(f"    python={python_objective} pm={pm_objective} ({time.time() - started:.1f}s)", flush=True)

    print(f"\nFull active-losses Python sweep complete. Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
