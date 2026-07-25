"""Three-way generation-cost objective comparison: PowerModels, Julia, Python.

Consolidates the two existing two-column (PowerModels vs. one language's own
model) comparison tables --
`results/own_vs_powermodels/julia_pm_like/summary.csv` and
`results/own_vs_powermodels/python_pm_like/summary.csv` -- into a single
three-column table per case/scenario, for the default generation-cost
objective. Does not solve anything itself; both source summaries are
produced by Julia script 27 (`27_run_own_vs_powermodels_full_sweep.jl`) /
script 25 and Python script 34
(`34_run_own_vs_powermodels_pm_like_full_sweep.py`) / script 23, which must
already have been (re-)run.

Coverage: every (case, scenario) pair present in BOTH source CSVs -- the
full ~313-scenario compact_scenarios grid across all 8 default PGLib cases,
not just the base case.

Both source CSVs are append-only logs that have accumulated rows from
different script versions over time with different column counts (a real,
pre-existing schema-drift issue, not introduced by this script) -- read via
the stdlib `csv` module (tolerant of ragged rows) rather than
`pandas.read_csv` (which errors on inconsistent field counts across an
entire file), keeping only rows whose length matches the header and taking
the LAST matching row per (case, scenario) key.

Output: results/own_vs_powermodels/three_way_generation_cost/summary.csv
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pandas as pd


def safe_float(text: str) -> float:
    """Empty string means the solve failed for that scenario (own_status
    != solved), written as NaN by pandas/CSV.writer -- not a parse error."""
    return math.nan if text == "" else float(text)

ROOT = Path(__file__).resolve().parents[3]
JULIA_SUMMARY = ROOT / "results" / "own_vs_powermodels" / "julia_pm_like" / "summary.csv"
PYTHON_SUMMARY = ROOT / "results" / "own_vs_powermodels" / "python_pm_like" / "summary.csv"
OUT_PATH = ROOT / "results" / "own_vs_powermodels" / "three_way_generation_cost" / "summary.csv"


# python_pm_like/summary.csv is written by TWO different scripts sharing one
# path/header (23_run_own_vs_powermodels_tables.py's 13-column schema
# establishes the header, but 34_run_own_vs_powermodels_pm_like_full_sweep.py
# appends its own wider, differently-positioned schema underneath it -- 23
# or 78 columns depending on whether per-scenario diff-stats were merged in)
# -- a pre-existing structural issue, not introduced by this script. Both of
# script 34's own row shapes share the same first-9-field layout (only
# extra diff-stat columns get appended after), so one positional map for
# (case, scenario, own_objective, pm_objective) covers every non-header-
# shaped row actually seen in this file.
_SCRIPT34_POSITIONS = {"case": 0, "scenario": 1, "own_objective": 7, "pm_objective": 8}


def load_latest_rows(path: Path, *, fallback_positions: dict[str, int] | None = None) -> dict[tuple[str, str], dict[str, str]]:
    """Read an append-only summary CSV tolerantly.

    Rows matching the header's own column count are read via the header's
    names (this alone is enough for a file with only ragged-by-truncation
    rows, e.g. Julia's summary.csv, where a short row is just missing
    trailing optional columns at the SAME positions as the header).

    `fallback_positions`, when given, is used for rows of any OTHER length
    -- needed for python_pm_like/summary.csv specifically, which is written
    by two different scripts (23's narrow schema establishes the header;
    34's wider schema uses different field positions entirely, not just a
    truncated version of the header) -- without this, rows from whichever
    script did NOT write the header would be silently dropped."""
    latest: dict[tuple[str, str], dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        n_cols = len(header)
        dropped = 0
        fallback_used = 0
        for row in reader:
            if len(row) == n_cols:
                record = dict(zip(header, row))
                latest[(record["case"], record["scenario"])] = {
                    "own_objective": record["own_objective"],
                    "pm_objective": record["pm_objective"],
                }
                continue
            if fallback_positions is not None and max(fallback_positions.values()) < len(row):
                fallback_used += 1
                latest[(row[fallback_positions["case"]], row[fallback_positions["scenario"]])] = {
                    "own_objective": row[fallback_positions["own_objective"]],
                    "pm_objective": row[fallback_positions["pm_objective"]],
                }
            else:
                dropped += 1
    if fallback_used:
        print(f"  {path.name}: {fallback_used} row(s) used the fallback column layout.")
    if dropped:
        print(f"  {path.name}: dropped {dropped} row(s) matching neither known schema.")
    return latest


def main() -> None:
    if not JULIA_SUMMARY.exists():
        raise FileNotFoundError(f"Missing {JULIA_SUMMARY} -- run Julia script 25/27 first.")
    if not PYTHON_SUMMARY.exists():
        raise FileNotFoundError(f"Missing {PYTHON_SUMMARY} -- run Python script 23/34 first.")

    julia_rows = load_latest_rows(JULIA_SUMMARY)
    python_rows = load_latest_rows(PYTHON_SUMMARY, fallback_positions=_SCRIPT34_POSITIONS)

    shared_keys = sorted(set(julia_rows) & set(python_rows))
    julia_only = set(julia_rows) - set(python_rows)
    python_only = set(python_rows) - set(julia_rows)
    if julia_only:
        print(f"  {len(julia_only)} (case, scenario) pair(s) present only in Julia's summary -- skipped.")
    if python_only:
        print(f"  {len(python_only)} (case, scenario) pair(s) present only in Python's summary -- skipped.")

    rows = []
    for case, scenario in shared_keys:
        jrow = julia_rows[(case, scenario)]
        prow = python_rows[(case, scenario)]

        pm_objective = safe_float(jrow["pm_objective"])
        julia_objective = safe_float(jrow["own_objective"])
        python_objective = safe_float(prow["own_objective"])
        python_pm_objective = safe_float(prow["pm_objective"])

        if math.isnan(pm_objective) or math.isnan(julia_objective) or math.isnan(python_objective):
            print(f"  SKIPPED {case}/{scenario}: a solve failed (NaN objective) in at least one language.")
            continue

        rows.append(
            {
                "case": case,
                "scenario": scenario,
                "pm_objective": pm_objective,
                "julia_objective": julia_objective,
                "python_objective": python_objective,
                "julia_vs_pm_gap_abs": julia_objective - pm_objective,
                "julia_vs_pm_gap_pct": 100.0 * abs(julia_objective - pm_objective) / (1.0 + abs(pm_objective)),
                "python_vs_pm_gap_abs": python_objective - pm_objective,
                "python_vs_pm_gap_pct": 100.0 * abs(python_objective - pm_objective) / (1.0 + abs(pm_objective)),
                "julia_vs_python_gap_abs": julia_objective - python_objective,
                "julia_pm_objective_cross_check": python_pm_objective,
                "pm_objective_agrees_across_languages": abs(pm_objective - python_pm_objective) < 1e-3,
            }
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out_df = pd.DataFrame(rows).sort_values(["case", "scenario"]).reset_index(drop=True)
    out_df.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(rows)} rows to {OUT_PATH}")


if __name__ == "__main__":
    main()
