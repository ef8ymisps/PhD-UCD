"""Scan every results/own_vs_powermodels/{julia,python}/<case>/<scenario>/
{generators,buses,branches}.csv for any own-vs-PowerModels difference
exceeding the 1e-6 tolerance used throughout this repo's binding-constraint
classification (n1_screening.jl, pypowsybl_security_validation.py, and the
own_at_pmin/pm_at_pmin-style flags already in these same tables).

Column names for the abs-diff fields are identical between the Julia (Set A)
and Python (Set B) tables (both ultimately derived from the same
compare_ac_opf_{generator,bus,branch}_rows-style functions), so one scan
covers both languages without a translation layer.

Writes results/own_vs_powermodels/significant_discrepancies.csv (one row per
violating element/variable) and prints a per-language/case/scenario summary
count to stdout.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BASE_DIR = ROOT / "results" / "own_vs_powermodels"
TOLERANCE = 1e-6

# (table file, id column, [abs-diff columns to check])
CHECKS = [
    ("generators.csv", "gen_id", ["abs_pg_diff", "abs_qg_diff"]),
    ("buses.csv", "bus_id", ["abs_vm_diff", "abs_va_diff"]),
    ("branches.csv", "branch_id", ["abs_pf_diff", "abs_qf_diff", "abs_pt_diff", "abs_qt_diff", "abs_loading_pct_diff"]),
]


def scan_file(path: Path, language: str, case: str, scenario: str, id_col: str, diff_cols: list[str]) -> list[dict]:
    if not path.exists():
        return []
    df = pd.read_csv(path)
    rows = []
    for diff_col in diff_cols:
        if diff_col not in df.columns:
            continue
        variable = diff_col.removeprefix("abs_").removesuffix("_diff")
        violations = df[df[diff_col].abs() > TOLERANCE]
        for _, r in violations.iterrows():
            rows.append(
                {
                    "language": language,
                    "case": case,
                    "scenario": scenario,
                    "table": path.name,
                    "element_id": r[id_col],
                    "variable": variable,
                    "abs_diff": r[diff_col],
                }
            )
    return rows


def main() -> None:
    all_rows: list[dict] = []
    for language_dir in sorted(BASE_DIR.iterdir()):
        if not language_dir.is_dir():
            continue
        language = language_dir.name
        for case_dir in sorted(language_dir.iterdir()):
            if not case_dir.is_dir():
                continue
            case = case_dir.name
            for scenario_dir in sorted(case_dir.iterdir()):
                if not scenario_dir.is_dir():
                    continue
                scenario = scenario_dir.name
                for table_file, id_col, diff_cols in CHECKS:
                    all_rows.extend(
                        scan_file(scenario_dir / table_file, language, case, scenario, id_col, diff_cols)
                    )

    report = pd.DataFrame(all_rows)
    if not report.empty:
        report = report.sort_values("abs_diff", ascending=False).reset_index(drop=True)
    output_path = BASE_DIR / "significant_discrepancies.csv"
    report.to_csv(output_path, index=False)

    print(f"Tolerance: {TOLERANCE:g}")
    print(f"Total violations: {len(report)}")
    print(f"Saved: {output_path}\n")

    if report.empty:
        print("No differences exceed tolerance anywhere.")
        return

    print("=== Count by language / case / scenario / variable ===")
    counts = report.groupby(["language", "case", "scenario", "variable"]).size().reset_index(name="n")
    for _, r in counts.iterrows():
        print(f"  {r['language']:8s} {r['case']:15s} {r['scenario']:28s} {r['variable']:12s} n={r['n']}")

    print("\n=== Top 15 worst violations overall ===")
    for _, r in report.head(15).iterrows():
        print(f"  {r['language']:8s} {r['case']:15s} {r['scenario']:28s} {r['table']:16s} {r['variable']:12s} id={r['element_id']!s:6s} |diff|={r['abs_diff']:.6g}")


if __name__ == "__main__":
    main()
