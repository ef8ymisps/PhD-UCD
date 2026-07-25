"""One lean, non-redundant summary row per (language, case, scenario),
replacing the old compact_opf_validation_summary.csv format -- same
objective/solve-time/iteration fields, but the old 27 n_<bound>_{both,only_own,
only_pm} columns are replaced by a single binding_mismatches_total (full
per-category breakdown already lives in
binding_constraint_mismatches_{julia,python}.csv, no need to duplicate it
here), and the raw max/mean per-variable diffs are pulled directly from the
per-element tables (generators/buses/branches.csv) instead of being computed
by a separate one-off sweep.

Inputs (all already produced by this session's pipeline, nothing re-solved):
  results/own_vs_powermodels/<language>/summary.csv
    (objective/status/solve-time/iterations, from scripts 25.jl / 23.py)
  results/own_vs_powermodels/<language>/<case>/<scenario>/{generators,buses,branches}.csv
    (per-element diffs -- aggregated here into max/mean per variable)
  results/own_vs_powermodels/binding_constraint_mismatches_<language>.csv
    (from 25_scan_binding_constraint_mismatches.py)

Output: results/own_vs_powermodels/final_summary_<language>.csv
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BASE_DIR = ROOT / "results" / "own_vs_powermodels"

# (table, {output column: source abs-diff column})
DIFF_SPECS = [
    ("generators.csv", {"max_pg_diff": "abs_pg_diff", "max_qg_diff": "abs_qg_diff"}),
    ("buses.csv", {"max_vm_diff": "abs_vm_diff", "max_va_diff": "abs_va_diff"}),
    ("branches.csv", {"max_pf_diff": "abs_pf_diff", "max_qf_diff": "abs_qf_diff", "max_loading_pct_diff": "abs_loading_pct_diff"}),
]


def diff_stats_for_scenario(scenario_dir: Path) -> dict:
    stats = {}
    for table_file, columns in DIFF_SPECS:
        path = scenario_dir / table_file
        if not path.exists():
            for out_col in columns:
                stats[out_col] = float("nan")
                stats[out_col.replace("max_", "mean_")] = float("nan")
            continue
        df = pd.read_csv(path)
        for out_col, src_col in columns.items():
            mean_col = out_col.replace("max_", "mean_")
            if src_col in df.columns and not df[src_col].empty:
                stats[out_col] = df[src_col].max()
                stats[mean_col] = df[src_col].mean()
            else:
                stats[out_col] = float("nan")
                stats[mean_col] = float("nan")
    return stats


def main() -> None:
    for language in ("julia", "python"):
        summary_path = BASE_DIR / language / "summary.csv"
        binding_path = BASE_DIR / f"binding_constraint_mismatches_{language}.csv"
        if not summary_path.exists():
            print(f"SKIPPED {language} -- no summary.csv found")
            continue

        summary = pd.read_csv(summary_path)
        binding = pd.read_csv(binding_path) if binding_path.exists() else pd.DataFrame(columns=["case", "scenario", "total"])

        rows = []
        for _, row in summary.iterrows():
            case, scenario = row["case"], row["scenario"]
            scenario_dir = BASE_DIR / language / str(case) / str(scenario)
            stats = diff_stats_for_scenario(scenario_dir)
            match = binding[(binding["case"] == case) & (binding["scenario"] == scenario)]
            binding_total = int(match["total"].iloc[0]) if not match.empty else 0
            rows.append({**row.to_dict(), **stats, "binding_mismatches_total": binding_total})

        final = pd.DataFrame(rows)
        out_path = BASE_DIR / f"final_summary_{language}.csv"
        final.to_csv(out_path, index=False)
        print(f"=== {language} ===")
        print(final.to_string(index=False))
        print(f"Saved: {out_path}\n")


if __name__ == "__main__":
    main()
