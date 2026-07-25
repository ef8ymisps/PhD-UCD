"""Three-way active-losses (loss-minimization) objective comparison:
PowerModels, Julia, Python.

Solves the own Python arc-based model with objective_type="active_losses"
for the 8 default PGLib base cases, then merges with the PowerModels/Julia
values already produced by
`julia/own_models/scripts/47_run_three_way_active_losses_table.jl`
(PowerModels has no Python binding, so its loss-objective reference comes
from Julia's own custom-objective PowerModels solve, not re-derived here).

Output: results/own_vs_powermodels/three_way_active_losses/summary.csv
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
REPO_ROOT = PYTHON_DIR.parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat  # noqa: E402
from own_models.src.opf.ac import solve_ac_opf_pyomo  # noqa: E402

MAT_DIR = REPO_ROOT / "data" / "raw" / "pglib" / "matpower_mat" / "base_case"
LOSSES_OUT = REPO_ROOT / "results" / "own_vs_powermodels" / "three_way_active_losses"
JULIA_AND_PM_PATH = LOSSES_OUT / "julia_and_pm.csv"

DEFAULT_CASES = [
    ("pglib_opf_case5_pjm", "5_pjm"),
    ("pglib_opf_case14_ieee", "14_ieee"),
    ("pglib_opf_case30_ieee", "30_ieee"),
    ("pglib_opf_case118_ieee", "118_ieee"),
    ("pglib_opf_case300_ieee", "300_ieee"),
    ("pglib_opf_case1354_pegase", "1354_pegase"),
    ("pglib_opf_case1888_rte", "1888_rte"),
    ("pglib_opf_case6470_rte", "6470_rte"),
]


def main() -> None:
    if not JULIA_AND_PM_PATH.exists():
        raise FileNotFoundError(
            f"Missing {JULIA_AND_PM_PATH} -- run "
            "julia/own_models/scripts/47_run_three_way_active_losses_table.jl first."
        )
    julia_and_pm = pd.read_csv(JULIA_AND_PM_PATH)

    rows = []
    for case_file, label in DEFAULT_CASES:
        print(f"=== {label} (active losses) ===")
        case = load_powercase_from_matpower_mat(MAT_DIR / f"{case_file}.mat")
        start = time.time()
        result = solve_ac_opf_pyomo(
            case, objective_type="active_losses", voltage_bounds=None, warm_start="zero"
        )
        elapsed = time.time() - start
        print(f"  python={result.objective} status={result.status}")

        jp_row = julia_and_pm[julia_and_pm["case"] == label].iloc[0]
        pm_objective = float(jp_row["pm_loss_objective"])
        julia_objective = float(jp_row["julia_loss_objective"])
        python_objective = float(result.objective)

        rows.append(
            {
                "case": label,
                "scenario": "base",
                "pm_loss_objective": pm_objective,
                "julia_loss_objective": julia_objective,
                "python_loss_objective": python_objective,
                "python_status": result.status,
                "python_solve_iterations": result.solve_iterations,
                "python_solve_time_sec": elapsed,
                "julia_vs_pm_gap_pct": float(jp_row["julia_vs_pm_gap_pct"]),
                "python_vs_pm_gap_abs": python_objective - pm_objective,
                "python_vs_pm_gap_pct": 100.0 * abs(python_objective - pm_objective) / (1.0 + abs(pm_objective)),
                "julia_vs_python_gap_abs": julia_objective - python_objective,
            }
        )

    LOSSES_OUT.mkdir(parents=True, exist_ok=True)
    out_path = LOSSES_OUT / "summary.csv"
    out_df = pd.DataFrame(rows)
    out_df.to_csv(out_path, index=False)
    print(f"\nWrote {len(rows)} rows to {out_path}")
    print(out_df.to_string(index=False))


if __name__ == "__main__":
    main()
