"""For each objective-mismatch scenario, list every individual constraint
(constraint_type + element_id) that is a binding mismatch (own_binding !=
pm_binding) under EITHER the constrained deviation-minimization or the
equality-only deviation-minimization diagnostic, with both diagnostics'
values shown side by side for that same constraint.

This is element-level, not an aggregate count: one row per actual mismatched
constraint. The expected pattern (constrained keeps bounds active, so a few
near-limit constraints can still disagree; equality-only removes bounds
entirely, so own model can land exactly on PowerModels' point) is that the
equality-only columns should show "no mismatch" for every row here, even
for constraints where constrained showed one.

Does NOT include load-flow validation -- that diagnostic compares two
independently re-solved PF endpoints (own-fed and PM-fed), not one solved
point against PM's point, so it isn't a like-for-like element-level
comparison with the other two. Kept separate on purpose.

Output:
    results/own_vs_powermodels/constraint_level_mismatch_comparison.csv
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2].parent
OUT_ROOT = REPO_ROOT / "results" / "own_vs_powermodels"
OUT_PATH = OUT_ROOT / "constraint_level_mismatch_comparison.csv"

KEY = ["case", "scenario", "constraint_type", "element_id"]


def load_julia(constrained_path: Path, equality_path: Path, variant_filter: str, source_variant: str) -> pd.DataFrame:
    c = pd.read_csv(constrained_path)
    c = c[c["variant"] == "full"] if "variant" in c.columns else c
    c = c.rename(columns={
        "deviation_value": "constrained_value", "deviation_margin": "constrained_margin",
        "deviation_binding": "constrained_binding", "binding_relation": "constrained_binding_relation",
        "pm_value": "constrained_pm_value", "pm_margin": "constrained_pm_margin", "pm_binding": "constrained_pm_binding",
    })
    c = c[KEY + ["bound", "constrained_value", "constrained_pm_value", "constrained_margin", "constrained_pm_margin", "constrained_binding", "constrained_pm_binding", "constrained_binding_relation"]]

    e = pd.read_csv(equality_path)
    e = e[e["variant"] == variant_filter]
    e = e.rename(columns={
        "equality_only_value": "equality_only_value", "equality_only_margin": "equality_only_margin",
        "equality_only_binding": "equality_only_binding", "binding_relation": "equality_only_binding_relation",
        "pm_value": "equality_only_pm_value", "pm_margin": "equality_only_pm_margin", "pm_binding": "equality_only_pm_binding",
    })
    e = e[KEY + ["equality_only_value", "equality_only_pm_value", "equality_only_margin", "equality_only_pm_margin", "equality_only_binding", "equality_only_pm_binding", "equality_only_binding_relation"]]

    merged = c.merge(e, on=KEY, how="outer")
    merged["source_variant"] = source_variant
    return merged


def load_python() -> pd.DataFrame:
    c = pd.read_csv(OUT_ROOT / "python_pm_like" / "constrained_deviation_inequality_levels.csv")
    c = c[c["variant"] == "full"]
    c = c.rename(columns={
        "constrained_value": "constrained_value", "constrained_margin": "constrained_margin",
        "constrained_binding": "constrained_binding", "binding_relation": "constrained_binding_relation",
        "pm_value": "constrained_pm_value", "pm_margin": "constrained_pm_margin", "pm_binding": "constrained_pm_binding",
    })
    c = c[KEY + ["bound", "constrained_value", "constrained_pm_value", "constrained_margin", "constrained_pm_margin", "constrained_binding", "constrained_pm_binding", "constrained_binding_relation"]]

    e = pd.read_csv(OUT_ROOT / "python_pm_like" / "equality_only_deviation_inequality_levels.csv")
    e = e[e["variant"] == "full"]
    e = e.rename(columns={
        "equality_only_value": "equality_only_value", "equality_only_margin": "equality_only_margin",
        "equality_only_binding": "equality_only_binding", "binding_relation": "equality_only_binding_relation",
        "pm_value": "equality_only_pm_value", "pm_margin": "equality_only_pm_margin", "pm_binding": "equality_only_pm_binding",
    })
    e = e[KEY + ["equality_only_value", "equality_only_pm_value", "equality_only_margin", "equality_only_pm_margin", "equality_only_binding", "equality_only_pm_binding", "equality_only_binding_relation"]]

    merged = c.merge(e, on=KEY, how="outer")
    merged["source_variant"] = "pm_like_python"
    return merged


def main() -> None:
    frames = [
        load_julia(
            OUT_ROOT / "current_constrained_deviation_binding_differences_julia.csv",
            OUT_ROOT / "equality_only_deviation_inequality_levels_julia.csv",
            "current", "current_julia",
        ),
        load_julia(
            OUT_ROOT / "pm_like_constrained_deviation_binding_differences_julia.csv",
            OUT_ROOT / "equality_only_deviation_inequality_levels_julia.csv",
            "pm_like", "pm_like_julia",
        ),
        load_python(),
    ]
    combined = pd.concat(frames, ignore_index=True)

    # Keep only rows that are an actual mismatch under at least one diagnostic.
    is_mismatch = (combined["constrained_binding_relation"] != "same") | (combined["equality_only_binding_relation"] != "same")
    combined = combined[is_mismatch.fillna(True)].copy()

    combined = combined[
        ["source_variant", "case", "scenario", "constraint_type", "element_id", "bound",
         "constrained_value", "constrained_pm_value", "constrained_margin", "constrained_pm_margin",
         "constrained_binding", "constrained_pm_binding", "constrained_binding_relation",
         "equality_only_value", "equality_only_pm_value", "equality_only_margin", "equality_only_pm_margin",
         "equality_only_binding", "equality_only_pm_binding", "equality_only_binding_relation"]
    ].sort_values(["source_variant", "case", "scenario", "constraint_type", "element_id"])

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    combined.to_csv(OUT_PATH, index=False)

    print(f"Total rows (actual mismatched constraints, either diagnostic): {len(combined)}")
    print()
    print("Rows where constrained mismatched but equality-only did NOT (the expected pattern):")
    resolved = combined[(combined["constrained_binding_relation"] != "same") & (combined["equality_only_binding_relation"] == "same")]
    print(len(resolved), "of", len(combined[combined['constrained_binding_relation'] != 'same']), "constrained mismatches")
    print()
    print("Rows where equality-only STILL mismatches (should be ~0):")
    still = combined[combined["equality_only_binding_relation"] != "same"]
    print(len(still))
    if len(still):
        print(still[["source_variant", "case", "scenario", "constraint_type", "element_id", "equality_only_margin", "equality_only_pm_margin"]].to_string(index=False))
    print(f"\nSaved: {OUT_PATH}")


if __name__ == "__main__":
    main()
