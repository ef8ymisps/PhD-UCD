"""For each objective-mismatch scenario, show binding-constraint mismatches
side by side across the three diagnostics that progressively remove the
"stay close to PowerModels" pressure:

  1. constrained deviation-min: bounds active, objective = L1 distance to PM
  2. equality-only deviation-min: bounds OFF, objective = L1 distance to PM
  3. real OPF (load-flow validation endpoint): bounds active, objective =
     genuine generation cost -- own model is not being pushed toward PM's
     point at all here, it just finds its own cost-optimal dispatch

Expected/actual pattern (this is the point of the table): mismatch counts
should go 0 (equality-only) -> small/mostly-noise (constrained) -> large/real
(real OPF) as that artificial pressure is removed, which is direct evidence
that real-OPF's mismatches reflect genuine alternate local optima, not a
missing-constraint bug -- the same own-vs-PM disagreement is either erasable
(given no cost objective) or real (given the actual cost objective), never
"stuck because the model is wrong".

"Material" mismatch = max(|own_margin|, |pm_margin|) >= 1e-4 (Ipopt's own
constr_viol_tol, not an ad hoc number) -- values below that are floating-
point-scale noise at the classification boundary, not a real disagreement.

Output:
    results/own_vs_powermodels/objective_mismatch_binding_progression.csv
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2].parent
OUT_ROOT = REPO_ROOT / "results" / "own_vs_powermodels"
ORGANIZED_ROOT = OUT_ROOT / "_organized_for_meeting"
MATERIAL_TOL = 1e-4
OUT_PATH = OUT_ROOT / "objective_mismatch_binding_progression.csv"


def material_count(df: pd.DataFrame, margin_cols: list[str]) -> int:
    if df.empty:
        return 0
    abs_margins = df[margin_cols].abs().max(axis=1)
    return int((abs_margins >= MATERIAL_TOL).sum())


def julia_constrained(variant_label: str, path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df = df[df["variant"] == "full"] if "variant" in df.columns else df
    out = df[["case", "scenario", "n_binding_mismatches"]].copy()
    out["diagnostic"] = "constrained_dev_min"
    out["source_variant"] = variant_label
    out = out.rename(columns={"n_binding_mismatches": "n_mismatches_material"})
    return out


def julia_equality_only(variant_filter: str, variant_label: str, path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df = df[df["variant"] == variant_filter]
    out = df[["case", "scenario", "n_binding_mismatches"]].copy()
    out["diagnostic"] = "equality_only_dev_min"
    out["source_variant"] = variant_label
    out = out.rename(columns={"n_binding_mismatches": "n_mismatches_material"})
    return out


SCENARIO_LABEL_FIX = {
    "1888_rte_qg_limits_x0p6__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p6": "qg_limits_x0.6",
}


def julia_load_flow(variant_filter: str, variant_label: str) -> pd.DataFrame:
    path = OUT_ROOT / "opf_endpoint_pf_inequality_binding_differences_julia.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df = df[df["variant"] == variant_filter].copy()
    df["scenario"] = df["scenario"].replace(SCENARIO_LABEL_FIX)
    rows = []
    for (case, scenario), group in df.groupby(["case", "scenario"]):
        rows.append(
            {
                "case": case,
                "scenario": scenario,
                "diagnostic": "real_opf_load_flow",
                "source_variant": variant_label,
                "n_mismatches_material": material_count(group, ["own_pf_margin", "pm_pf_margin"]),
                "n_mismatches_raw": len(group),
            }
        )
    return pd.DataFrame(rows)


def python_constrained() -> pd.DataFrame:
    path = OUT_ROOT / "python_pm_like" / "constrained_deviation_inequality_levels.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df = df[(df.get("variant") == "full") & (df["binding_relation"] != "same")]
    rows = []
    for (case, scenario), group in df.groupby(["case", "scenario"]):
        rows.append(
            {
                "case": case,
                "scenario": scenario,
                "diagnostic": "constrained_dev_min",
                "source_variant": "pm_like_python",
                "n_mismatches_material": material_count(group, ["constrained_margin", "pm_margin"]),
                "n_mismatches_raw": len(group),
            }
        )
    return pd.DataFrame(rows)


def python_equality_only() -> pd.DataFrame:
    path = OUT_ROOT / "python_pm_like" / "equality_only_deviation_binding_summary.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df = df[df.get("variant") == "full"]
    out = df[["case", "scenario", "n_binding_mismatches"]].copy()
    out["diagnostic"] = "equality_only_dev_min"
    out["source_variant"] = "pm_like_python"
    out = out.rename(columns={"n_binding_mismatches": "n_mismatches_material"})
    return out


def python_load_flow() -> pd.DataFrame:
    path = OUT_ROOT / "python_endpoint_pypowsybl_lf_inequality_levels.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, low_memory=False)
    df = df[(df["model_variant"] == "python_pm_like_zero") & (df["binding_relation"] != "same")].copy()
    rows = []
    for (case, scenario), group in df.groupby(["case", "scenario"]):
        rows.append(
            {
                "case": case,
                "scenario": scenario,
                "diagnostic": "real_opf_load_flow",
                "source_variant": "pm_like_python",
                "n_mismatches_material": material_count(group, ["lf_margin", "pm_opf_margin"]),
                "n_mismatches_raw": len(group),
            }
        )
    return pd.DataFrame(rows)


def objective_diffs() -> pd.DataFrame:
    frames = []
    for variant_label, path in [
        ("current_julia", OUT_ROOT / "objective_mismatch_details_julia_current.csv"),
        ("pm_like_julia", OUT_ROOT / "objective_mismatch_details_julia_pm_like.csv"),
    ]:
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if "objective_diff_own_minus_pm" not in df.columns:
            continue
        out = df[["case", "scenario", "objective_diff_own_minus_pm"]].copy()
        out.columns = ["case", "scenario", "objective_diff"]
        out["scenario"] = out["scenario"].replace(SCENARIO_LABEL_FIX)
        out["source_variant"] = variant_label
        frames.append(out.drop_duplicates(subset=["case", "scenario"]))

    python_path = OUT_ROOT / "python_pm_like" / "constrained_deviation_targets.csv"
    if python_path.exists():
        df = pd.read_csv(python_path)
        if "objective_diff" in df.columns:
            out = df[["case", "scenario", "objective_diff"]].drop_duplicates(subset=["case", "scenario"]).copy()
            out["source_variant"] = "pm_like_python"
            frames.append(out)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    parts = [
        julia_constrained("current_julia", OUT_ROOT / "current_constrained_deviation_binding_summary_julia.csv"),
        julia_constrained("pm_like_julia", OUT_ROOT / "pm_like_constrained_deviation_binding_summary_julia.csv"),
        julia_equality_only("current", "current_julia", OUT_ROOT / "equality_only_deviation_binding_summary_julia.csv"),
        julia_equality_only("pm_like", "pm_like_julia", OUT_ROOT / "equality_only_deviation_binding_summary_julia.csv"),
        julia_load_flow("current", "current_julia"),
        julia_load_flow("pm_like", "pm_like_julia"),
        python_constrained(),
        python_equality_only(),
        python_load_flow(),
    ]
    combined = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    if "n_mismatches_raw" not in combined.columns:
        combined["n_mismatches_raw"] = combined["n_mismatches_material"]
    combined["n_mismatches_raw"] = combined["n_mismatches_raw"].fillna(combined["n_mismatches_material"])

    pivot = combined.pivot_table(
        index=["case", "scenario", "source_variant"],
        columns="diagnostic",
        values="n_mismatches_material",
        aggfunc="first",
    ).reset_index()

    obj = objective_diffs()
    if not obj.empty:
        pivot = pivot.merge(obj, on=["case", "scenario", "source_variant"], how="left")

    for col in ["constrained_dev_min", "equality_only_dev_min", "real_opf_load_flow"]:
        if col not in pivot.columns:
            pivot[col] = math.nan

    pivot = pivot[["case", "scenario", "source_variant", "objective_diff", "equality_only_dev_min", "constrained_dev_min", "real_opf_load_flow"]]
    pivot = pivot.sort_values(["case", "scenario", "source_variant"])

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    pivot.to_csv(OUT_PATH, index=False)
    (ORGANIZED_ROOT / "00_overview").mkdir(parents=True, exist_ok=True)
    pivot.to_csv(ORGANIZED_ROOT / "00_overview" / "objective_mismatch_binding_progression.csv", index=False)

    print(pivot.to_string(index=False))
    print(f"\nSaved: {OUT_PATH}")


if __name__ == "__main__":
    main()
