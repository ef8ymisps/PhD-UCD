"""Final three-way active-losses (loss-minimization) table, full scenario grid.

Merges:
* julia/own_models/scripts/48_run_full_sweep_active_losses_table.jl's output
  (results/own_vs_powermodels/three_way_active_losses/julia_full_sweep.csv)
* 55_run_full_sweep_active_losses_table.py's output (.../python_full_sweep.csv)

Handles one label mismatch between them: script 48 uses compact_scenarios'
raw scenario_id for the base case (e.g.
"5_pjm__base__none__none__none__none"), while script 55 uses the reference
export's friendly "base" label (matching how base-case reference JSONs are
always named). Falls back to that raw-id pattern when looking up a "base"
row in Julia's data under the plain label fails.

Output: results/own_vs_powermodels/three_way_active_losses/full_grid_summary.csv
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
LOSSES_DIR = ROOT / "results" / "own_vs_powermodels" / "three_way_active_losses"
JULIA_PATH = LOSSES_DIR / "julia_full_sweep.csv"
PYTHON_PATH = LOSSES_DIR / "python_full_sweep.csv"
OUT_PATH = LOSSES_DIR / "full_grid_summary.csv"


def raw_base_scenario_id(case: str) -> str:
    """compact_scenarios' own base-entry scenario_id convention (see
    julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl's
    `scenario_id`/`make_entry`): "{case}__base__none__none__none__none"."""
    return f"{case}__base__none__none__none__none"


def main() -> None:
    if not JULIA_PATH.exists():
        raise FileNotFoundError(f"Missing {JULIA_PATH} -- run Julia script 48 first.")
    if not PYTHON_PATH.exists():
        raise FileNotFoundError(f"Missing {PYTHON_PATH} -- run Python script 55 first.")

    julia_df = pd.read_csv(JULIA_PATH)
    python_df = pd.read_csv(PYTHON_PATH)

    julia_lookup = {
        (row["case"], row["scenario"]): row for _, row in julia_df.iterrows()
    }
    python_lookup = {
        (row["case"], row["scenario"]): row for _, row in python_df.iterrows()
    }

    rows = []
    skipped = 0
    for (case, scenario), prow in python_lookup.items():
        jrow = julia_lookup.get((case, scenario))
        if jrow is None and scenario == "base":
            jrow = julia_lookup.get((case, raw_base_scenario_id(case)))
        if jrow is None:
            skipped += 1
            continue

        pm_objective = float(jrow["pm_loss_objective"])
        julia_objective = float(jrow["julia_loss_objective"])
        python_objective = float(prow["python_loss_objective"]) if not pd.isna(prow["python_loss_objective"]) else math.nan

        if any(math.isnan(v) for v in (pm_objective, julia_objective, python_objective)):
            skipped += 1
            continue

        rows.append(
            {
                "case": case,
                "scenario": scenario,
                "pm_loss_objective": pm_objective,
                "julia_loss_objective": julia_objective,
                "python_loss_objective": python_objective,
                "julia_vs_pm_gap_pct": 100.0 * abs(julia_objective - pm_objective) / (1.0 + abs(pm_objective)),
                "python_vs_pm_gap_pct": 100.0 * abs(python_objective - pm_objective) / (1.0 + abs(pm_objective)),
                "julia_vs_python_gap_abs": julia_objective - python_objective,
            }
        )

    print(f"Skipped {skipped} (case, scenario) pair(s) -- missing or NaN in at least one source.")
    out_df = pd.DataFrame(rows).sort_values(["case", "scenario"]).reset_index(drop=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(rows)} rows to {OUT_PATH}")


if __name__ == "__main__":
    main()
