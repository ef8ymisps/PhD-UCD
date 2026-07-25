"""Detailed, classified inspection of every load-flow-validation "violation"
and "binding mismatch" -- not just an aggregate count.

Motivation: the aggregate rollup (script 40) reports raw counts using each
source script's own violation-classification tolerance (1e-7 for the Python
pypowsybl check). That tolerance is ~1000x tighter than Ipopt's own
constr_viol_tol=1e-4 (the repo's established solver-acceptance standard,
see docs/notes/solver_tolerances.md) -- so most flagged "violations" turn
out to be floating-point noise at a generator/bus sitting essentially
exactly at its bound, not a real physical violation. This script re-classifies
every violation row against that 1e-4 standard, split into:

  - tolerance_noise:  0 < |violation| < 1e-4 (not materially different from
    exactly-at-the-bound)
  - qg_convention:    a qg_min/qg_max violation, any size -- flagged
    separately since this repo already documented a real, expected,
    small Qg-limit convention violation for plain (non-reactive-limit-aware)
    power flow (see docs/notes/solver_tolerances.md's PowerModels endpoint
    section)
  - material:         |violation| >= 1e-4 and not a qg-limit row -- an
    actual, non-trivial bound violation worth investigating further

Outputs (per case/scenario/variant/source):
    results/own_vs_powermodels/load_flow_violation_classification_detail.csv
    results/own_vs_powermodels/load_flow_violation_classification_summary.csv
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2].parent
OUT_ROOT = REPO_ROOT / "results" / "own_vs_powermodels"
ORGANIZED_ROOT = OUT_ROOT / "_organized_for_meeting"
MATERIAL_TOL = 1e-4  # matches Ipopt's constr_viol_tol, docs/notes/solver_tolerances.md

DETAIL_PATH = OUT_ROOT / "load_flow_violation_classification_detail.csv"
SUMMARY_PATH = OUT_ROOT / "load_flow_violation_classification_summary.csv"


def classify(amount: float, constraint_type: str) -> str:
    if amount <= 0:
        return "not_a_violation"
    if str(constraint_type).startswith("qg_"):
        return "qg_convention"
    if amount < MATERIAL_TOL:
        return "tolerance_noise"
    return "material"


def load_julia_rows() -> pd.DataFrame:
    path = OUT_ROOT / "opf_endpoint_pf_inequality_binding_differences_julia.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    rows = []
    for side, amount_col, margin_col in [("own", "own_pf_violation_amount", "own_pf_margin"), ("pm", "pm_pf_violation_amount", "pm_pf_margin")]:
        if amount_col not in df.columns:
            continue
        sub = df[[c for c in ["variant", "case", "scenario", "constraint_type", "element_id", margin_col, amount_col] if c in df.columns]].copy()
        sub = sub.rename(columns={amount_col: "violation_amount", margin_col: "margin"})
        sub["endpoint_side"] = side
        sub["source"] = "julia_newton_pf"
        sub["variant"] = sub["variant"].apply(lambda v: f"{v}_julia")
        rows.append(sub)
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    out["violation_amount"] = pd.to_numeric(out["violation_amount"], errors="coerce").fillna(0.0)
    out["classification"] = [classify(a, t) for a, t in zip(out["violation_amount"], out["constraint_type"])]
    return out


def load_python_rows() -> pd.DataFrame:
    path = OUT_ROOT / "python_endpoint_pypowsybl_lf_violations.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df = df[df["model_variant"] == "python_pm_like_zero"].copy()
    out = df[["case", "scenario", "endpoint_source", "constraint_type", "element_id", "lf_margin"]].copy()
    out["violation_amount"] = (-pd.to_numeric(df["lf_margin"], errors="coerce")).clip(lower=0.0)
    out["variant"] = "pm_like_python"
    out["source"] = "python_pypowsybl"
    out["endpoint_side"] = out["endpoint_source"].map({"own_opf_solution": "own", "powermodels_opf_solution": "pm"}).fillna(df["endpoint_source"])
    out["margin"] = out["lf_margin"]
    out["classification"] = [classify(a, t) for a, t in zip(out["violation_amount"], out["constraint_type"])]
    return out[["variant", "case", "scenario", "constraint_type", "element_id", "margin", "violation_amount", "endpoint_side", "source", "classification"]]


def main() -> None:
    julia_rows = load_julia_rows()
    python_rows = load_python_rows()
    combined = pd.concat([df for df in [julia_rows, python_rows] if not df.empty], ignore_index=True)
    combined = combined[combined["violation_amount"] > 0].sort_values(["variant", "case", "scenario", "violation_amount"], ascending=[True, True, True, False])

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    combined.to_csv(DETAIL_PATH, index=False)

    summary = (
        combined.groupby(["source", "variant", "case", "scenario", "classification"])
        .size()
        .reset_index(name="n_rows")
        .pivot_table(index=["source", "variant", "case", "scenario"], columns="classification", values="n_rows", fill_value=0)
        .reset_index()
    )
    for col in ["not_a_violation", "tolerance_noise", "qg_convention", "material"]:
        if col not in summary.columns:
            summary[col] = 0
    summary.to_csv(SUMMARY_PATH, index=False)

    (ORGANIZED_ROOT / "04_loadflow_validation").mkdir(parents=True, exist_ok=True)
    combined.to_csv(ORGANIZED_ROOT / "04_loadflow_validation" / "load_flow_violation_classification_detail.csv", index=False)
    summary.to_csv(ORGANIZED_ROOT / "04_loadflow_validation" / "load_flow_violation_classification_summary.csv", index=False)

    print(summary.to_string(index=False))
    print()
    print(f"n material (real) violations across everything: {int(summary['material'].sum())}")
    print(f"n qg-convention violations: {int(summary['qg_convention'].sum())}")
    print(f"n tolerance-noise (misclassified) violations: {int(summary['tolerance_noise'].sum())}")
    print(f"\nSaved: {DETAIL_PATH}\nSaved: {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
