"""Roll up binding-constraint-mismatch data across all three diagnostics
(constrained deviation-minimization, equality-only deviation-minimization,
load-flow validation) and all three model variants (current_julia,
pm_like_julia, pm_like_python) into one presentation-ready aggregate table.

Each diagnostic's per-scenario binding-mismatch tables already exist (built
by earlier Julia/Python scripts); this script only reads them, filters each
to its "all constraints combined" rollup, and sums/maxes across scenarios --
it does not re-solve or re-derive anything.

Output:
    results/own_vs_powermodels/binding_mismatch_aggregate_summary.csv
    (one row per diagnostic x variant, 9 rows total)
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2].parent
OUT_ROOT = REPO_ROOT / "results" / "own_vs_powermodels"
ORGANIZED_ROOT = OUT_ROOT / "_organized_for_meeting"
OUT_PATH = OUT_ROOT / "binding_mismatch_aggregate_summary.csv"


def safe_max(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.max()) if not values.empty else math.nan


def safe_sum(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.sum()) if not values.empty else 0.0


# (diagnostic, variant, source path, filter dict, column-name map)
SOURCES = [
    (
        "constrained_deviation", "current_julia",
        OUT_ROOT / "current_constrained_deviation_binding_summary_julia.csv",
        {"variant": "full"},
        {"constraints": "n_constraints", "mismatches": "n_binding_mismatches", "violations": None, "max_diff": "max_abs_value_diff"},
    ),
    (
        "constrained_deviation", "pm_like_julia",
        OUT_ROOT / "pm_like_constrained_deviation_binding_summary_julia.csv",
        {"variant": "full"},
        {"constraints": "n_constraints", "mismatches": "n_binding_mismatches", "violations": None, "max_diff": "max_abs_value_diff"},
    ),
    (
        "constrained_deviation", "pm_like_python",
        OUT_ROOT / "python_pm_like" / "constrained_deviation_binding_summary.csv",
        {"variant": "full", "constraint_family": "all"},
        {"constraints": "n_constraints", "mismatches": "n_binding_mismatches", "violations": "n_constrained_violations", "max_diff": "max_abs_value_diff"},
    ),
    (
        "equality_only_deviation", "current_julia",
        OUT_ROOT / "equality_only_deviation_binding_summary_julia.csv",
        {"variant": "current"},
        {"constraints": "n_constraints", "mismatches": "n_binding_mismatches", "violations": "n_equality_only_violations", "max_diff": "max_abs_value_diff"},
    ),
    (
        "equality_only_deviation", "pm_like_julia",
        OUT_ROOT / "equality_only_deviation_binding_summary_julia.csv",
        {"variant": "pm_like"},
        {"constraints": "n_constraints", "mismatches": "n_binding_mismatches", "violations": "n_equality_only_violations", "max_diff": "max_abs_value_diff"},
    ),
    (
        "equality_only_deviation", "pm_like_python",
        OUT_ROOT / "python_pm_like" / "equality_only_deviation_binding_summary.csv",
        {"variant": "full", "constraint_family": "all"},
        {"constraints": "n_constraints", "mismatches": "n_binding_mismatches", "violations": "n_equality_only_violations", "max_diff": "max_abs_value_diff"},
    ),
    (
        "load_flow_validation", "current_julia",
        OUT_ROOT / "opf_endpoint_pf_inequality_feasibility_summary_julia.csv",
        {"variant": "current"},
        {"constraints": "n_constraints", "mismatches": "n_pf_binding_mismatches", "violations": "n_own_pf_violations", "max_diff": "max_abs_pf_margin_diff"},
    ),
    (
        "load_flow_validation", "pm_like_julia",
        OUT_ROOT / "opf_endpoint_pf_inequality_feasibility_summary_julia.csv",
        {"variant": "pm_like"},
        {"constraints": "n_constraints", "mismatches": "n_pf_binding_mismatches", "violations": "n_own_pf_violations", "max_diff": "max_abs_pf_margin_diff"},
    ),
    (
        "load_flow_validation", "pm_like_python",
        OUT_ROOT / "python_endpoint_pypowsybl_lf_binding_summary.csv",
        # NOTE: this file stores the SAME data 3x, once per `independence_scope`
        # ("all_constraints" is the true total; "independently_rederived_only"
        # and "specified_or_pinned_only" are a breakdown that sums back to it).
        # Must pin independence_scope too, or totals get triple-counted.
        {"model_variant": "python_pm_like_zero", "constraint_family": "all", "independence_scope": "all_constraints"},
        {"constraints": "n_constraints", "mismatches": "n_binding_mismatches", "violations": "n_lf_violations", "max_diff": "max_abs_value_diff"},
    ),
]


def build_row(diagnostic: str, variant: str, path: Path, filters: dict, cols: dict) -> dict:
    if not path.exists():
        return {
            "diagnostic": diagnostic, "variant": variant, "source_file": str(path),
            "status": "MISSING_FILE", "n_scenarios": 0, "total_constraints_checked": math.nan,
            "total_binding_mismatches": math.nan, "total_violations": math.nan,
            "max_abs_value_diff": math.nan, "pct_mismatches": math.nan,
        }
    df = pd.read_csv(path)
    for key, value in filters.items():
        if key not in df.columns:
            return {
                "diagnostic": diagnostic, "variant": variant, "source_file": str(path),
                "status": f"MISSING_COLUMN:{key}", "n_scenarios": 0, "total_constraints_checked": math.nan,
                "total_binding_mismatches": math.nan, "total_violations": math.nan,
                "max_abs_value_diff": math.nan, "pct_mismatches": math.nan,
            }
        df = df[df[key].astype(str) == str(value)]

    n_scenarios = len(df.drop_duplicates(subset=["case", "scenario"])) if {"case", "scenario"}.issubset(df.columns) else len(df)
    total_constraints = safe_sum(df[cols["constraints"]]) if cols["constraints"] in df.columns else math.nan
    total_mismatches = safe_sum(df[cols["mismatches"]]) if cols["mismatches"] in df.columns else math.nan
    total_violations = safe_sum(df[cols["violations"]]) if cols["violations"] and cols["violations"] in df.columns else math.nan
    max_diff = safe_max(df[cols["max_diff"]]) if cols["max_diff"] in df.columns else math.nan
    pct = (100.0 * total_mismatches / total_constraints) if total_constraints else math.nan

    return {
        "diagnostic": diagnostic,
        "variant": variant,
        "source_file": str(path.relative_to(REPO_ROOT)),
        "status": "ok" if n_scenarios > 0 else "NO_MATCHING_ROWS",
        "n_scenarios": n_scenarios,
        "total_constraints_checked": total_constraints,
        "total_binding_mismatches": total_mismatches,
        "pct_mismatches": pct,
        "total_violations": total_violations,
        "max_abs_value_diff": max_diff,
    }


def main() -> None:
    rows = [build_row(*source) for source in SOURCES]
    df = pd.DataFrame(rows)
    df = df[[
        "diagnostic", "variant", "status", "n_scenarios",
        "total_constraints_checked", "total_binding_mismatches", "pct_mismatches",
        "total_violations", "max_abs_value_diff", "source_file",
    ]]
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_PATH, index=False)
    ORGANIZED_ROOT.mkdir(parents=True, exist_ok=True)
    df.to_csv(ORGANIZED_ROOT / "00_overview" / "binding_mismatch_aggregate_summary.csv", index=False)
    print(df.to_string(index=False))
    print(f"\nSaved: {OUT_PATH}")


if __name__ == "__main__":
    main()
