"""Binding-constraint mismatch count table, reproducing the structure of the
original "Binding Constraint Mismatches -- Full Count Table" slide, but now
computed from results/own_vs_powermodels/<language>/<case>/<scenario>/
constraints_binding.csv (own_binding vs pm_binding columns) instead of a
one-off script -- so it's regenerable for every case/scenario this pipeline
covers, not just the original 8 base cases.

A "mismatch" is any constraint instance where own_binding != pm_binding
(one tool considers it binding, the other doesn't) -- exactly the definition
used in the original table.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BASE_DIR = ROOT / "results" / "own_vs_powermodels"

# Display order/labels matching the original slide's columns exactly.
DISPLAY_COLUMNS = [
    ("pg_min", "p_min"),
    ("pg_max", "p_max"),
    ("qg_min", "q_min"),
    ("qg_max", "q_max"),
    ("vm_min", "v_min"),
    ("vm_max", "v_max"),
    ("thermal", "thermal"),
    ("angle_lower", "theta_lo"),
    ("angle_upper", "theta_hi"),
]


def mismatch_counts_for_file(path: Path) -> dict[str, int]:
    df = pd.read_csv(path)
    counts = {label: 0 for _, label in DISPLAY_COLUMNS}
    for constraint_type, label in DISPLAY_COLUMNS:
        subset = df[df["constraint_type"] == constraint_type]
        if subset.empty:
            continue
        counts[label] = int((subset["own_binding"] != subset["pm_binding"]).sum())
    return counts


def main() -> None:
    for language_dir in sorted(BASE_DIR.iterdir()):
        if not language_dir.is_dir() or language_dir.name not in ("julia", "python"):
            continue
        language = language_dir.name
        rows = []
        for case_dir in sorted(language_dir.iterdir()):
            if not case_dir.is_dir():
                continue
            for scenario_dir in sorted(case_dir.iterdir()):
                binding_path = scenario_dir / "constraints_binding.csv"
                if not binding_path.exists():
                    continue
                counts = mismatch_counts_for_file(binding_path)
                counts["case"] = case_dir.name
                counts["scenario"] = scenario_dir.name
                counts["total"] = sum(counts[label] for _, label in DISPLAY_COLUMNS)
                rows.append(counts)

        if not rows:
            continue
        table = pd.DataFrame(rows)
        ordered_cols = ["case", "scenario"] + [label for _, label in DISPLAY_COLUMNS] + ["total"]
        table = table[ordered_cols].sort_values(["case", "scenario"]).reset_index(drop=True)
        out_path = BASE_DIR / f"binding_constraint_mismatches_{language}.csv"
        table.to_csv(out_path, index=False)

        print(f"=== {language} ===")
        print(table.to_string(index=False))
        print(f"Saved: {out_path}\n")


if __name__ == "__main__":
    main()
