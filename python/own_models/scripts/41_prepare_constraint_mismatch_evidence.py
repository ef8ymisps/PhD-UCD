"""Build constraint-mismatch evidence tables for objective-gap scenarios.

This script does not solve OPF/PF problems. It gathers existing validation CSVs
and writes a compact local package for inspection before any Drive upload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "results" / "own_vs_powermodels"
OUT = BASE / "_organized_for_meeting" / "09_constraint_mismatch_evidence"

NOISE_TOL = 1e-5


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, **kwargs)


def write_csv(df: pd.DataFrame, relative_path: str) -> None:
    path = OUT / relative_path
    ensure_dir(path.parent)
    df.to_csv(path, index=False)


def as_bool(series: pd.Series) -> pd.Series:
    if series.empty:
        return series.astype(bool)
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def canonical_scenario(value) -> str:
    text = "" if pd.isna(value) else str(value)
    if "qg_limits_x0p6" in text or "qg_limits_x0.6" in text:
        return "qg_limits_x0.6"
    if "qg_limits_x0p8" in text or "qg_limits_x0.8" in text:
        return "qg_limits_x0.8"
    if "load_x0p8" in text or "load_x0.8" in text:
        return "load_x0.8"
    if "load_x1p2" in text or "load_x1.2" in text:
        return "load_x1.2_reactive_stress"
    return text


def constraint_family(value) -> str:
    text = "" if pd.isna(value) else str(value).lower()
    if text.startswith("pg") or text in {"pmin", "pmax"}:
        return "pg"
    if text.startswith("qg") or text in {"qmin", "qmax"}:
        return "qg"
    if text.startswith("vm") or text.startswith("v_"):
        return "vm"
    if text.startswith("thermal") or text.startswith("sf") or text.startswith("st"):
        return "thermal"
    if text.startswith("angle") or text in {"angmin", "angmax"}:
        return "angle"
    return "other"


def normalize_binding_relation(row: pd.Series, left_col: str, right_col: str) -> str:
    left = bool(row.get(left_col, False))
    right = bool(row.get(right_col, False))
    if left and right:
        return "same" if float_or_zero(row.get("abs_margin_diff", 0.0)) <= NOISE_TOL else "both_binding_different_level"
    if left:
        return "only_left_binding"
    if right:
        return "only_right_binding"
    return "same"


def float_or_zero(value) -> float:
    try:
        if pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def target_scenarios() -> pd.DataFrame:
    targets = read_csv(BASE / "objective_mismatch_details_selected_models.csv")
    if targets.empty:
        return targets
    targets = targets.copy()
    targets["canonical_scenario"] = targets["scenario"].map(canonical_scenario)
    targets = targets[
        targets["model_variant"].isin(["julia_current", "julia_pm_like", "python_pm_like_zero"])
    ].copy()
    cols = [
        "model_variant",
        "case",
        "scenario",
        "canonical_scenario",
        "own_objective",
        "pm_objective",
        "objective_diff_own_minus_pm",
        "abs_objective_diff",
        "objective_rel_diff_pct",
        "cheaper_solution",
        "target_reason",
        "source_table",
    ]
    return targets[[c for c in cols if c in targets.columns]]


def target_keys(targets: pd.DataFrame) -> set[tuple[str, str, str]]:
    if targets.empty:
        return set()
    return {
        (str(r.model_variant), str(r.case), str(r.canonical_scenario))
        for r in targets.itertuples(index=False)
    }


def filter_to_targets(df: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    if df.empty or targets.empty:
        return df.iloc[0:0].copy()
    df = df.copy()
    df["canonical_scenario"] = df["scenario"].map(canonical_scenario)
    keys = target_keys(targets)
    mask = [
        (str(model), str(case), str(scenario)) in keys
        for model, case, scenario in zip(df["model_variant"], df["case"], df["canonical_scenario"])
    ]
    return df.loc[mask].copy()


def annotate_detail_rows(
    df: pd.DataFrame,
    *,
    stage: str,
    left_label: str,
    right_label: str,
    left_binding_col: str,
    right_binding_col: str,
    left_violation_col: str | None = None,
    right_violation_col: str | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["comparison_stage"] = stage
    df["left_solution_label"] = left_label
    df["right_solution_label"] = right_label
    df["canonical_scenario"] = df["scenario"].map(canonical_scenario)
    df["constraint_family"] = df["constraint_type"].map(constraint_family)
    if left_binding_col in df.columns:
        df[left_binding_col] = as_bool(df[left_binding_col])
    if right_binding_col in df.columns:
        df[right_binding_col] = as_bool(df[right_binding_col])
    if "binding_relation" not in df.columns:
        df["binding_relation"] = df.apply(
            lambda row: normalize_binding_relation(row, left_binding_col, right_binding_col),
            axis=1,
        )
    if "abs_value_diff" not in df.columns:
        value_cols = [c for c in df.columns if c.startswith("abs_") and "value" in c]
        df["abs_value_diff"] = df[value_cols[0]] if value_cols else 0.0
    if "abs_margin_diff" not in df.columns:
        margin_cols = [c for c in df.columns if c.startswith("abs_") and "margin" in c]
        df["abs_margin_diff"] = df[margin_cols[0]] if margin_cols else 0.0
    if "tolerance_attributable" not in df.columns:
        df["tolerance_attributable"] = df["abs_margin_diff"].fillna(0.0).astype(float).abs() <= NOISE_TOL
    else:
        df["tolerance_attributable"] = as_bool(df["tolerance_attributable"])
    if left_violation_col and left_violation_col in df.columns:
        df["left_violates_limit"] = as_bool(df[left_violation_col])
    else:
        df["left_violates_limit"] = False
    if right_violation_col and right_violation_col in df.columns:
        df["right_violates_limit"] = as_bool(df[right_violation_col])
    else:
        df["right_violates_limit"] = False
    df["endpoint_specific_violation"] = df["left_violates_limit"] != df["right_violates_limit"]
    df["shared_violation"] = df["left_violates_limit"] & df["right_violates_limit"]
    df["material_mismatch"] = (
        (df["binding_relation"].astype(str) != "same")
        & ~df["tolerance_attributable"].fillna(False).astype(bool)
    ) | df["left_violates_limit"] | df["right_violates_limit"]
    return df


def mismatch_filter(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    relation = df.get("binding_relation", pd.Series("", index=df.index)).astype(str)
    left_v = df.get("left_violates_limit", pd.Series(False, index=df.index)).fillna(False).astype(bool)
    right_v = df.get("right_violates_limit", pd.Series(False, index=df.index)).fillna(False).astype(bool)
    return df[(relation != "same") | left_v | right_v].copy()


def summarize_details(df: pd.DataFrame, targets: pd.DataFrame, stage: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    base_cols = ["model_variant", "case", "canonical_scenario", "comparison_stage", "diagnostic_variant"]
    family_cols = base_cols + ["constraint_family"]
    if df.empty:
        empty_summary_cols = base_cols + [
            "n_rows",
            "n_binding_mismatches",
            "n_only_left_binding",
            "n_only_right_binding",
            "n_both_binding_different_level",
            "n_tolerance_attributable",
            "n_material_mismatches",
            "n_left_violations",
            "n_right_violations",
            "n_endpoint_specific_violations",
            "n_shared_violations",
            "max_abs_value_diff",
            "max_abs_margin_diff",
            "n_pg",
            "n_qg",
            "n_vm",
            "n_thermal",
            "n_angle",
        ]
        return pd.DataFrame(columns=empty_summary_cols), pd.DataFrame(columns=family_cols + empty_summary_cols[5:])

    df = df.copy()
    if "diagnostic_variant" not in df.columns:
        df["diagnostic_variant"] = "original"
    relation = df["binding_relation"].astype(str)
    df["is_binding_mismatch"] = relation != "same"
    df["is_only_left_binding"] = relation.isin(["only_left_binding", "only_own_binding", "only_deviation_binding", "only_equality_binding", "only_lf_binding"])
    df["is_only_right_binding"] = relation.isin(["only_right_binding", "only_pm_binding", "only_pm_opf_binding", "only_pm_pf_binding"])
    df["is_both_binding_different_level"] = relation == "both_binding_different_level"

    grouped = (
        df.groupby(base_cols, dropna=False)
        .agg(
            n_rows=("constraint_type", "count"),
            n_binding_mismatches=("is_binding_mismatch", "sum"),
            n_only_left_binding=("is_only_left_binding", "sum"),
            n_only_right_binding=("is_only_right_binding", "sum"),
            n_both_binding_different_level=("is_both_binding_different_level", "sum"),
            n_tolerance_attributable=("tolerance_attributable", "sum"),
            n_material_mismatches=("material_mismatch", "sum"),
            n_left_violations=("left_violates_limit", "sum"),
            n_right_violations=("right_violates_limit", "sum"),
            n_endpoint_specific_violations=("endpoint_specific_violation", "sum"),
            n_shared_violations=("shared_violation", "sum"),
            max_abs_value_diff=("abs_value_diff", "max"),
            max_abs_margin_diff=("abs_margin_diff", "max"),
            n_pg=("constraint_family", lambda x: int((x == "pg").sum())),
            n_qg=("constraint_family", lambda x: int((x == "qg").sum())),
            n_vm=("constraint_family", lambda x: int((x == "vm").sum())),
            n_thermal=("constraint_family", lambda x: int((x == "thermal").sum())),
            n_angle=("constraint_family", lambda x: int((x == "angle").sum())),
        )
        .reset_index()
    )
    grouped["comparison_stage"] = stage

    by_family = (
        df.groupby(family_cols, dropna=False)
        .agg(
            n_rows=("constraint_type", "count"),
            n_binding_mismatches=("is_binding_mismatch", "sum"),
            n_tolerance_attributable=("tolerance_attributable", "sum"),
            n_material_mismatches=("material_mismatch", "sum"),
            n_left_violations=("left_violates_limit", "sum"),
            n_right_violations=("right_violates_limit", "sum"),
            n_endpoint_specific_violations=("endpoint_specific_violation", "sum"),
            n_shared_violations=("shared_violation", "sum"),
            max_abs_value_diff=("abs_value_diff", "max"),
            max_abs_margin_diff=("abs_margin_diff", "max"),
        )
        .reset_index()
    )
    return grouped, by_family


def load_original_julia_details(targets: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    sources = [
        ("julia_current", BASE / "binding_constraint_mismatch_details_julia_current.csv"),
        ("julia_pm_like", BASE / "binding_constraint_mismatch_details_julia_pm_like.csv"),
    ]
    for model_variant, path in sources:
        df = read_csv(path)
        if df.empty:
            continue
        df = df.copy()
        df.insert(0, "model_variant", model_variant)
        df["diagnostic_variant"] = "original"
        df["binding_relation"] = df["mismatch_direction"]
        df["binding_relation"] = df["binding_relation"].replace(
            {"only_own_binding": "only_left_binding", "only_pm_binding": "only_right_binding"}
        )
        pieces.append(df)
    if not pieces:
        return pd.DataFrame()
    df = pd.concat(pieces, ignore_index=True, sort=False)
    df = filter_to_targets(df, targets)
    df = annotate_detail_rows(
        df,
        stage="original_opf",
        left_label="own_opf_solution",
        right_label="powermodels_opf_solution",
        left_binding_col="own_binding",
        right_binding_col="pm_binding",
    )
    return mismatch_filter(df)


def load_original_python_pm_like_details(targets: pd.DataFrame) -> pd.DataFrame:
    rows = []
    py_targets = targets[targets["model_variant"] == "python_pm_like_zero"]
    for target in py_targets.itertuples(index=False):
        scenario = str(target.canonical_scenario)
        path = BASE / "python_pm_like" / str(target.case) / scenario / "constraints_binding.csv"
        if not path.exists():
            continue
        df = read_csv(path)
        if df.empty:
            continue
        df = df.copy()
        df.insert(0, "model_variant", "python_pm_like_zero")
        df["case"] = str(target.case)
        df["scenario"] = scenario
        df["diagnostic_variant"] = "original"
        df["source_folder"] = str(path.parent)
        df["abs_value_diff"] = (df["own_value"] - df["pm_value"]).abs()
        df["abs_margin_diff"] = (df["own_margin"] - df["pm_margin"]).abs()
        df["own_binding"] = as_bool(df["own_binding"])
        df["pm_binding"] = as_bool(df["pm_binding"])
        df["binding_relation"] = df.apply(
            lambda row: normalize_binding_relation(row, "own_binding", "pm_binding"),
            axis=1,
        )
        nonbinding_margin = []
        for row in df.itertuples(index=False):
            if row.own_binding and not row.pm_binding:
                nonbinding_margin.append(abs(float_or_zero(row.pm_margin)))
            elif row.pm_binding and not row.own_binding:
                nonbinding_margin.append(abs(float_or_zero(row.own_margin)))
            else:
                nonbinding_margin.append(float_or_zero(row.abs_margin_diff))
        df["nonbinding_side_margin_abs"] = nonbinding_margin
        df["tolerance_attributable"] = df["nonbinding_side_margin_abs"] <= NOISE_TOL
        df["mismatch_class"] = df["tolerance_attributable"].map(
            {True: "tolerance_threshold", False: "material_active_set_difference"}
        )
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    df = pd.concat(rows, ignore_index=True, sort=False)
    df = annotate_detail_rows(
        df,
        stage="original_opf",
        left_label="own_opf_solution",
        right_label="powermodels_opf_solution",
        left_binding_col="own_binding",
        right_binding_col="pm_binding",
    )
    return mismatch_filter(df)


def load_constrained_deviation_details(targets: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    sources = [
        ("julia_current", BASE / "current_constrained_deviation_binding_differences_julia.csv"),
        ("julia_pm_like", BASE / "pm_like_constrained_deviation_binding_differences_julia.csv"),
        ("python_pm_like_zero", BASE / "python_pm_like" / "constrained_deviation_inequality_levels.csv"),
    ]
    for model_variant, path in sources:
        df = read_csv(path)
        if df.empty:
            continue
        df = df.copy()
        if "model_variant" not in df.columns:
            df.insert(0, "model_variant", model_variant)
        df["model_variant"] = model_variant
        df["diagnostic_variant"] = df.get("variant", "full")
        if "deviation_value" in df.columns:
            df["left_value"] = df["deviation_value"]
        elif "constrained_value" in df.columns:
            df["left_value"] = df["constrained_value"]
        if "deviation_margin" in df.columns:
            df["left_margin"] = df["deviation_margin"]
        elif "constrained_margin" in df.columns:
            df["left_margin"] = df["constrained_margin"]
        if "deviation_binding" in df.columns:
            df["left_binding"] = df["deviation_binding"]
        elif "constrained_binding" in df.columns:
            df["left_binding"] = df["constrained_binding"]
        if "deviation_violates_limit" in df.columns:
            df["left_violates_limit"] = df["deviation_violates_limit"]
        elif "constrained_violates_limit" in df.columns:
            df["left_violates_limit"] = df["constrained_violates_limit"]
        if "pm_binding" not in df.columns and "constrained_pm_binding" in df.columns:
            df["pm_binding"] = df["constrained_pm_binding"]
        if "pm_value" not in df.columns and "constrained_pm_value" in df.columns:
            df["pm_value"] = df["constrained_pm_value"]
        if "pm_margin" not in df.columns and "constrained_pm_margin" in df.columns:
            df["pm_margin"] = df["constrained_pm_margin"]
        pieces.append(df)
    if not pieces:
        return pd.DataFrame()
    df = pd.concat(pieces, ignore_index=True, sort=False)
    df = filter_to_targets(df, targets)
    df = annotate_detail_rows(
        df,
        stage="constrained_deviation_minimization",
        left_label="constrained_deviation_solution",
        right_label="powermodels_opf_solution",
        left_binding_col="left_binding",
        right_binding_col="pm_binding",
        left_violation_col="left_violates_limit",
        right_violation_col="pm_violates_limit",
    )
    return mismatch_filter(df)


def load_equality_only_details(targets: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    sources = [
        ("julia_current", BASE / "equality_only_deviation_inequality_levels_julia.csv", "current"),
        ("julia_pm_like", BASE / "equality_only_deviation_inequality_levels_julia.csv", "pm_like"),
        ("python_pm_like_zero", BASE / "python_pm_like" / "equality_only_deviation_inequality_levels.csv", None),
    ]
    for model_variant, path, variant_filter in sources:
        df = read_csv(path)
        if df.empty:
            continue
        df = df.copy()
        if variant_filter is not None:
            df = df[df["variant"].astype(str) == variant_filter].copy()
        if "model_variant" not in df.columns:
            df.insert(0, "model_variant", model_variant)
        df["model_variant"] = model_variant
        df["diagnostic_variant"] = df.get("variant", "full")
        if "equality_only_binding" in df.columns:
            df["left_binding"] = df["equality_only_binding"]
        if "equality_only_violates_limit" in df.columns:
            df["left_violates_limit"] = df["equality_only_violates_limit"]
        pieces.append(df)
    if not pieces:
        return pd.DataFrame()
    df = pd.concat(pieces, ignore_index=True, sort=False)
    df = filter_to_targets(df, targets)
    df = annotate_detail_rows(
        df,
        stage="equality_only_deviation_minimization",
        left_label="equality_only_projection",
        right_label="powermodels_opf_solution",
        left_binding_col="left_binding",
        right_binding_col="pm_binding",
        left_violation_col="left_violates_limit",
        right_violation_col="pm_violates_limit",
    )
    return mismatch_filter(df)


def load_julia_loadflow_details(targets: pd.DataFrame) -> pd.DataFrame:
    df = read_csv(BASE / "opf_endpoint_pf_inequality_binding_differences_julia.csv")
    if df.empty:
        return df
    df = df.copy()
    df["model_variant"] = df["variant"].map(lambda value: f"julia_{value}")
    df["diagnostic_variant"] = "endpoint_pf"
    df["binding_relation"] = df["pf_binding_relation"]
    df["binding_relation"] = df["binding_relation"].replace(
        {"only_own_pf_binding": "only_left_binding", "only_pm_pf_binding": "only_right_binding", "different": "both_binding_different_level"}
    )
    df["abs_value_diff"] = df.get("abs_pf_value_diff", df.get("abs_value_diff", 0.0))
    df["abs_margin_diff"] = df.get("abs_pf_margin_diff", df.get("abs_margin_diff", 0.0))
    df["left_binding"] = df.get("own_pf_binding", False)
    df["right_binding"] = df.get("pm_pf_binding", False)
    df["left_violates_limit"] = df.get("own_pf_violation", False)
    df["right_violates_limit"] = df.get("pm_pf_violation", False)
    df["tolerance_attributable"] = df["abs_margin_diff"].fillna(0.0).astype(float).abs() <= NOISE_TOL
    df = filter_to_targets(df, targets)
    df = annotate_detail_rows(
        df,
        stage="loadflow_validation",
        left_label="own_opf_endpoint_pf_solution",
        right_label="powermodels_opf_endpoint_pf_solution",
        left_binding_col="left_binding",
        right_binding_col="right_binding",
        left_violation_col="left_violates_limit",
        right_violation_col="right_violates_limit",
    )
    return mismatch_filter(df)


def load_python_pm_like_loadflow_endpoint_details(targets: pd.DataFrame) -> pd.DataFrame:
    path = BASE / "python_endpoint_pypowsybl_lf_inequality_levels.csv"
    py_targets = targets[targets["model_variant"] == "python_pm_like_zero"]
    if py_targets.empty or not path.exists():
        return pd.DataFrame()

    scenarios = set(py_targets["canonical_scenario"].astype(str))
    usecols = [
        "case",
        "scenario",
        "model_variant",
        "endpoint_source",
        "constraint_type",
        "element_id",
        "bound",
        "lf_value",
        "lf_margin",
        "lf_binding",
        "lf_violates_limit",
        "lf_violation",
        "binding_relation",
        "independently_rederived_after_lf",
        "independence_note",
        "bus_type",
        "bus_has_active_generator",
        "level_units",
        "limit_violation_tolerance",
    ]
    selected = []
    for chunk in pd.read_csv(path, usecols=usecols, chunksize=250_000, dtype=str):
        chunk = chunk[chunk["model_variant"].astype(str) == "python_pm_like_zero"].copy()
        if chunk.empty:
            continue
        chunk["canonical_scenario"] = chunk["scenario"].map(canonical_scenario)
        chunk = chunk[chunk["canonical_scenario"].isin(scenarios)].copy()
        if not chunk.empty:
            selected.append(chunk)
    if not selected:
        return pd.DataFrame()

    df = pd.concat(selected, ignore_index=True, sort=False)
    keys = ["case", "canonical_scenario", "constraint_type", "element_id", "bound"]
    own = df[df["endpoint_source"] == "own_opf_solution"].copy()
    pm = df[df["endpoint_source"] == "powermodels_opf_solution"].copy()
    suffix_cols = {
        "lf_value": "value",
        "lf_margin": "margin",
        "lf_binding": "binding",
        "lf_violates_limit": "violates_limit",
        "lf_violation": "violation_amount",
        "binding_relation": "source_binding_relation",
        "independently_rederived_after_lf": "independently_rederived_after_lf",
        "independence_note": "independence_note",
        "bus_type": "bus_type",
        "bus_has_active_generator": "bus_has_active_generator",
        "level_units": "level_units",
        "limit_violation_tolerance": "limit_violation_tolerance",
    }
    own = own[keys + list(suffix_cols.keys())].rename(
        columns={col: f"own_lf_{name}" for col, name in suffix_cols.items()}
    )
    pm = pm[keys + list(suffix_cols.keys())].rename(
        columns={col: f"pm_lf_{name}" for col, name in suffix_cols.items()}
    )
    merged = own.merge(pm, on=keys, how="inner")
    merged["model_variant"] = "python_pm_like_zero"
    merged["scenario"] = merged["canonical_scenario"]
    merged["diagnostic_variant"] = "endpoint_lf"
    for col in ["own_lf_value", "pm_lf_value", "own_lf_margin", "pm_lf_margin", "bound"]:
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")
    merged["abs_value_diff"] = (merged["own_lf_value"] - merged["pm_lf_value"]).abs()
    merged["abs_margin_diff"] = (merged["own_lf_margin"] - merged["pm_lf_margin"]).abs()
    merged["left_binding"] = as_bool(merged["own_lf_binding"])
    merged["right_binding"] = as_bool(merged["pm_lf_binding"])
    merged["left_violates_limit"] = as_bool(merged["own_lf_violates_limit"])
    merged["right_violates_limit"] = as_bool(merged["pm_lf_violates_limit"])
    merged["binding_relation"] = merged.apply(
        lambda row: normalize_binding_relation(row, "left_binding", "right_binding"),
        axis=1,
    )
    merged["tolerance_attributable"] = merged["abs_margin_diff"].fillna(0.0).astype(float).abs() <= NOISE_TOL
    merged = annotate_detail_rows(
        merged,
        stage="loadflow_validation",
        left_label="own_opf_endpoint_lf_solution",
        right_label="powermodels_opf_endpoint_lf_solution",
        left_binding_col="left_binding",
        right_binding_col="right_binding",
        left_violation_col="left_violates_limit",
        right_violation_col="right_violates_limit",
    )
    return mismatch_filter(merged)


def write_stage(stage_name: str, details: pd.DataFrame, targets: pd.DataFrame, prefix: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary, by_family = summarize_details(details, targets, stage_name)
    write_csv(details, f"{prefix}_details.csv")
    write_csv(summary, f"{prefix}_summary_by_scenario.csv")
    write_csv(by_family, f"{prefix}_summary_by_family.csv")
    return summary, by_family


def apply_detail_classification(source: pd.DataFrame, detail_summary: pd.DataFrame, key_cols: list[str]) -> pd.DataFrame:
    """Use row-level detail aggregation to classify source summary rows.

    Some source summaries report max differences over all constraints in a
    scenario/variant, while the family summary is about the subset of binding
    mismatches in that family.  Material/tolerance flags must therefore come
    from the actual detail rows, not from a broader max column.
    """
    if source.empty:
        return source
    result = source.copy()
    for col in [
        "n_tolerance_attributable",
        "n_material_mismatches",
        "n_left_violations",
        "n_right_violations",
        "n_endpoint_specific_violations",
        "n_shared_violations",
        "max_abs_value_diff",
        "max_abs_margin_diff",
    ]:
        if col not in result.columns:
            result[col] = 0

    if detail_summary.empty:
        result["n_material_mismatches"] = result.get("n_binding_mismatches", 0)
        return result

    update_cols = [
        col
        for col in [
            "n_tolerance_attributable",
            "n_material_mismatches",
            "n_left_violations",
            "n_right_violations",
            "n_endpoint_specific_violations",
            "n_shared_violations",
            "max_abs_value_diff",
            "max_abs_margin_diff",
        ]
        if col in detail_summary.columns
    ]
    details = detail_summary[key_cols + update_cols].copy()
    details = details.rename(columns={col: f"{col}__detail" for col in update_cols})
    result = result.merge(details, on=key_cols, how="left")
    for col in update_cols:
        detail_col = f"{col}__detail"
        result[col] = result[detail_col].combine_first(result[col])
        result = result.drop(columns=[detail_col])

    return result


def reorder_preferred_columns(df: pd.DataFrame, preferred: list[str]) -> pd.DataFrame:
    if df.empty:
        return df
    leading = [col for col in preferred if col in df.columns]
    trailing = [col for col in df.columns if col not in leading]
    return df[leading + trailing]


def constrained_source_summaries(targets: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    family_rows = []
    julia_sources = [
        ("julia_current", BASE / "current_constrained_deviation_binding_summary_julia.csv"),
        ("julia_pm_like", BASE / "pm_like_constrained_deviation_binding_summary_julia.csv"),
    ]
    for model_variant, path in julia_sources:
        df = read_csv(path)
        if df.empty:
            continue
        df["model_variant"] = model_variant
        df["canonical_scenario"] = df["scenario"].map(canonical_scenario)
        df = filter_to_targets(df, targets)
        for row in df.itertuples(index=False):
            n_binding = int(getattr(row, "n_binding_mismatches", 0))
            max_margin = float_or_zero(getattr(row, "max_abs_margin_diff", 0.0))
            material = 0 if max_margin <= NOISE_TOL else n_binding
            rows.append(
                {
                    "model_variant": model_variant,
                    "case": row.case,
                    "canonical_scenario": row.canonical_scenario,
                    "comparison_stage": "constrained_deviation_minimization",
                    "diagnostic_variant": row.variant,
                    "n_rows": n_binding,
                    "n_constraints": getattr(row, "n_constraints", ""),
                    "n_binding_mismatches": n_binding,
                    "n_only_left_binding": getattr(row, "n_only_deviation_binding", 0),
                    "n_only_right_binding": getattr(row, "n_only_pm_binding", 0),
                    "n_tolerance_attributable": 0 if material else n_binding,
                    "n_material_mismatches": material,
                    "max_abs_value_diff": getattr(row, "max_abs_value_diff", 0.0),
                    "max_abs_margin_diff": getattr(row, "max_abs_margin_diff", 0.0),
                    "n_pg": getattr(row, "n_pg_mismatches", 0),
                    "n_qg": getattr(row, "n_qg_mismatches", 0),
                    "n_vm": getattr(row, "n_vm_mismatches", 0),
                    "n_thermal": getattr(row, "n_thermal_mismatches", 0),
                    "n_angle": getattr(row, "n_angle_mismatches", 0),
                }
            )
            for family, count_attr in [
                ("pg", "n_pg_mismatches"),
                ("qg", "n_qg_mismatches"),
                ("vm", "n_vm_mismatches"),
                ("thermal", "n_thermal_mismatches"),
                ("angle", "n_angle_mismatches"),
            ]:
                count = int(getattr(row, count_attr, 0))
                if count == 0:
                    continue
                family_rows.append(
                    {
                        "model_variant": model_variant,
                        "case": row.case,
                        "canonical_scenario": row.canonical_scenario,
                        "comparison_stage": "constrained_deviation_minimization",
                        "diagnostic_variant": row.variant,
                        "constraint_family": family,
                        "n_rows": count,
                        "n_binding_mismatches": count,
                        "n_material_mismatches": 0 if material == 0 else count,
                    }
                )

    py = read_csv(BASE / "python_pm_like" / "constrained_deviation_binding_summary.csv")
    if not py.empty:
        py = filter_to_targets(py, targets)
        all_rows = py[py["constraint_family"].astype(str) == "all"].copy()
        for row in all_rows.itertuples(index=False):
            n_binding = int(getattr(row, "n_binding_mismatches", 0))
            max_margin = float_or_zero(getattr(row, "max_abs_margin_diff", 0.0))
            material = 0 if max_margin <= NOISE_TOL else n_binding
            rows.append(
                {
                    "model_variant": row.model_variant,
                    "case": row.case,
                    "canonical_scenario": row.canonical_scenario,
                    "comparison_stage": "constrained_deviation_minimization",
                    "diagnostic_variant": row.variant,
                    "n_rows": n_binding,
                    "n_constraints": getattr(row, "n_constraints", ""),
                    "n_binding_mismatches": n_binding,
                    "n_only_left_binding": getattr(row, "n_only_constrained_binding", 0),
                    "n_only_right_binding": getattr(row, "n_only_pm_binding", 0),
                    "n_tolerance_attributable": 0 if material else n_binding,
                    "n_material_mismatches": material,
                    "n_left_violations": getattr(row, "n_constrained_violations", 0),
                    "n_right_violations": getattr(row, "n_pm_violations", 0),
                    "max_abs_value_diff": getattr(row, "max_abs_value_diff", 0.0),
                    "max_abs_margin_diff": getattr(row, "max_abs_margin_diff", 0.0),
                }
            )
        fam = py[py["constraint_family"].astype(str) != "all"].copy()
        for row in fam.itertuples(index=False):
            n_binding = int(getattr(row, "n_binding_mismatches", 0))
            max_margin = float_or_zero(getattr(row, "max_abs_margin_diff", 0.0))
            family_rows.append(
                {
                    "model_variant": row.model_variant,
                    "case": row.case,
                    "canonical_scenario": row.canonical_scenario,
                    "comparison_stage": "constrained_deviation_minimization",
                    "diagnostic_variant": row.variant,
                    "constraint_family": row.constraint_family,
                    "n_rows": n_binding,
                    "n_binding_mismatches": n_binding,
                    "n_material_mismatches": 0 if max_margin <= NOISE_TOL else n_binding,
                    "max_abs_value_diff": getattr(row, "max_abs_value_diff", 0.0),
                    "max_abs_margin_diff": getattr(row, "max_abs_margin_diff", 0.0),
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(family_rows)


def equality_source_summaries(targets: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    family_rows = []
    julia = read_csv(BASE / "equality_only_deviation_binding_summary_julia.csv")
    if not julia.empty:
        julia["model_variant"] = julia["variant"].map(lambda value: f"julia_{value}")
        julia["canonical_scenario"] = julia["scenario"].map(canonical_scenario)
        julia = filter_to_targets(julia, targets)
        for row in julia.itertuples(index=False):
            rows.append(
                {
                    "model_variant": row.model_variant,
                    "case": row.case,
                    "canonical_scenario": row.canonical_scenario,
                    "comparison_stage": "equality_only_deviation_minimization",
                    "diagnostic_variant": "full",
                    "n_rows": getattr(row, "n_binding_mismatches", 0),
                    "n_constraints": getattr(row, "n_constraints", ""),
                    "n_binding_mismatches": getattr(row, "n_binding_mismatches", 0),
                    "n_only_left_binding": getattr(row, "n_only_equality_binding", 0),
                    "n_only_right_binding": getattr(row, "n_only_pm_binding", 0),
                    "n_material_mismatches": getattr(row, "n_binding_mismatches", 0),
                    "n_left_violations": getattr(row, "n_equality_only_violations", 0),
                    "max_abs_value_diff": getattr(row, "max_abs_value_diff", 0.0),
                    "max_abs_margin_diff": getattr(row, "max_abs_margin_diff", 0.0),
                    "max_violation": getattr(row, "max_violation", 0.0),
                    "n_pg": getattr(row, "n_pg_mismatches", 0),
                    "n_qg": getattr(row, "n_qg_mismatches", 0),
                    "n_vm": getattr(row, "n_vm_mismatches", 0),
                    "n_thermal": getattr(row, "n_thermal_mismatches", 0),
                    "n_angle": getattr(row, "n_angle_mismatches", 0),
                }
            )
    py = read_csv(BASE / "python_pm_like" / "equality_only_deviation_binding_summary.csv")
    if not py.empty:
        py = filter_to_targets(py, targets)
        all_rows = py[py["constraint_family"].astype(str) == "all"].copy()
        for row in all_rows.itertuples(index=False):
            rows.append(
                {
                    "model_variant": row.model_variant,
                    "case": row.case,
                    "canonical_scenario": row.canonical_scenario,
                    "comparison_stage": "equality_only_deviation_minimization",
                    "diagnostic_variant": row.variant,
                    "n_rows": getattr(row, "n_binding_mismatches", 0),
                    "n_constraints": getattr(row, "n_constraints", ""),
                    "n_binding_mismatches": getattr(row, "n_binding_mismatches", 0),
                    "n_only_left_binding": getattr(row, "n_only_equality_binding", 0),
                    "n_only_right_binding": getattr(row, "n_only_pm_binding", 0),
                    "n_material_mismatches": getattr(row, "n_binding_mismatches", 0),
                    "n_left_violations": getattr(row, "n_equality_only_violations", 0),
                    "n_right_violations": getattr(row, "n_pm_violations", 0),
                    "max_abs_value_diff": getattr(row, "max_abs_value_diff", 0.0),
                    "max_abs_margin_diff": getattr(row, "max_abs_margin_diff", 0.0),
                    "max_equality_only_violation": getattr(row, "max_equality_only_violation", 0.0),
                }
            )
        fam = py[py["constraint_family"].astype(str) != "all"].copy()
        family_rows.extend(fam.to_dict("records"))
    return pd.DataFrame(rows), pd.DataFrame(family_rows)


def build_storyline(
    targets: pd.DataFrame,
    opf_summary: pd.DataFrame,
    constrained_summary: pd.DataFrame,
    equality_summary: pd.DataFrame,
    loadflow_summary: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for target in targets.itertuples(index=False):
        key = (target.model_variant, target.case, target.canonical_scenario)

        def pick(summary: pd.DataFrame, variant: str | None = None) -> pd.DataFrame:
            if summary.empty:
                return summary
            mask = (
                (summary["model_variant"].astype(str) == str(key[0]))
                & (summary["case"].astype(str) == str(key[1]))
                & (summary["canonical_scenario"].astype(str) == str(key[2]))
            )
            if variant is not None and "diagnostic_variant" in summary.columns:
                mask &= summary["diagnostic_variant"].astype(str) == variant
            return summary[mask]

        opf = pick(opf_summary)
        constrained_full = pick(constrained_summary, "full")
        equality = pick(equality_summary)
        loadflow = pick(loadflow_summary, "endpoint_pf" if str(key[0]).startswith("julia") else "endpoint_lf")

        def val(df: pd.DataFrame, col: str, default=0):
            if df.empty or col not in df.columns:
                return default
            return df[col].fillna(default).max()

        opf_material = int(val(opf, "n_material_mismatches", 0))
        constrained_material = int(val(constrained_full, "n_material_mismatches", 0))
        equality_material = int(val(equality, "n_material_mismatches", 0))
        loadflow_material = int(val(loadflow, "n_material_mismatches", 0))
        loadflow_endpoint_specific_violations = int(val(loadflow, "n_endpoint_specific_violations", 0))
        loadflow_shared_violations = int(val(loadflow, "n_shared_violations", 0))
        final_reading = "needs_inspection"
        if loadflow_endpoint_specific_violations > 0:
            final_reading = "post_lf_endpoint_specific_violation_remains"
        elif constrained_material > 0:
            final_reading = "constrained_deviation_binding_mismatch_remains"
        elif opf_material > 0 and constrained_material == 0 and equality_material == 0:
            final_reading = "opf_active_set_diff_not_reproduced_by_deviation_or_equality_projection"
        elif opf_material == 0 and constrained_material == 0 and equality_material == 0:
            final_reading = "no_material_constraint_mismatch_in_evidence_tables"

        rows.append(
            {
                "model_variant": key[0],
                "case": key[1],
                "scenario": key[2],
                "own_objective": getattr(target, "own_objective", ""),
                "pm_objective": getattr(target, "pm_objective", ""),
                "objective_diff_own_minus_pm": getattr(target, "objective_diff_own_minus_pm", ""),
                "cheaper_solution": getattr(target, "cheaper_solution", ""),
                "original_opf_material_mismatches": opf_material,
                "original_opf_tolerance_mismatches": int(val(opf, "n_tolerance_attributable", 0)),
                "constrained_full_material_mismatches": constrained_material,
                "equality_only_material_mismatches": equality_material,
                "loadflow_binding_mismatches": loadflow_material,
                "loadflow_endpoint_specific_violations": loadflow_endpoint_specific_violations,
                "loadflow_shared_violations": loadflow_shared_violations,
                "final_reading": final_reading,
            }
        )
    return pd.DataFrame(rows)


def write_readme() -> None:
    text = """# Constraint mismatch evidence package

Generated by `python/own_models/scripts/41_prepare_constraint_mismatch_evidence.py`.

Scope:

- Only scenarios with non-zero/material objective differences in
  `objective_mismatch_details_selected_models.csv`.
- Models: Julia current, Julia PM-like, Python PM-like zero-start AC OPF.

Table groups:

- `opf_binding_mismatch_*`: original own OPF endpoint versus PowerModels OPF endpoint.
- `constrained_deviation_constraint_mismatch_*`: constrained deviation-minimization endpoint versus PowerModels OPF endpoint.
- `equality_only_constraint_level_*`: equality-only projection endpoint versus PowerModels OPF endpoint, with externally evaluated inequality levels.
- `loadflow_constraint_level_*`: post-PF/LF endpoint comparison from the load-flow validation workflow.
- `constraint_mismatch_storyline_for_slides.csv`: compact before/after diagnostic chain.

Interpretation columns:

- `tolerance_attributable`: mismatch is inside the numerical threshold window used by this package (`1e-5`) or already flagged as tolerance-level in the source table.
- `material_mismatch`: binding/violation difference not attributed to tolerance.
- `canonical_scenario`: normalized scenario label used to join tables whose source scripts use slightly different long/short names.

No Drive upload is performed by this script.
"""
    (OUT / "README.md").write_text(text, encoding="utf-8")


def main() -> None:
    ensure_dir(OUT)
    targets = target_scenarios()
    write_csv(targets, "objective_gap_filtered_scenarios.csv")

    opf_details = pd.concat(
        [load_original_julia_details(targets), load_original_python_pm_like_details(targets)],
        ignore_index=True,
        sort=False,
    )
    opf_summary, _ = write_stage("original_opf", opf_details, targets, "opf_binding_mismatch")

    constrained_details = load_constrained_deviation_details(targets)
    constrained_detail_summary, constrained_detail_family = write_stage(
        "constrained_deviation_minimization",
        constrained_details,
        targets,
        "constrained_deviation_constraint_mismatch",
    )
    constrained_summary = constrained_detail_summary
    constrained_source_summary, constrained_source_family = constrained_source_summaries(targets)
    if not constrained_source_summary.empty:
        constrained_summary = apply_detail_classification(
            constrained_source_summary,
            constrained_detail_summary,
            ["model_variant", "case", "canonical_scenario", "comparison_stage", "diagnostic_variant"],
        )
        constrained_summary = reorder_preferred_columns(
            constrained_summary,
            [
                "model_variant",
                "case",
                "canonical_scenario",
                "comparison_stage",
                "diagnostic_variant",
                "n_rows",
                "n_constraints",
                "n_binding_mismatches",
                "n_only_left_binding",
                "n_only_right_binding",
                "n_tolerance_attributable",
                "n_material_mismatches",
                "max_abs_value_diff",
                "max_abs_margin_diff",
                "n_pg",
                "n_qg",
                "n_vm",
                "n_thermal",
                "n_angle",
                "n_left_violations",
                "n_right_violations",
                "n_endpoint_specific_violations",
                "n_shared_violations",
            ],
        )
        write_csv(constrained_summary, "constrained_deviation_constraint_mismatch_summary_by_scenario.csv")
    if not constrained_source_family.empty:
        constrained_source_family = apply_detail_classification(
            constrained_source_family,
            constrained_detail_family,
            ["model_variant", "case", "canonical_scenario", "comparison_stage", "diagnostic_variant", "constraint_family"],
        )
        constrained_source_family = reorder_preferred_columns(
            constrained_source_family,
            [
                "model_variant",
                "case",
                "canonical_scenario",
                "comparison_stage",
                "diagnostic_variant",
                "constraint_family",
                "n_rows",
                "n_binding_mismatches",
                "n_tolerance_attributable",
                "n_material_mismatches",
                "max_abs_value_diff",
                "max_abs_margin_diff",
                "n_left_violations",
                "n_right_violations",
                "n_endpoint_specific_violations",
                "n_shared_violations",
            ],
        )
        write_csv(constrained_source_family, "constrained_deviation_constraint_mismatch_summary_by_family.csv")

    equality_details = load_equality_only_details(targets)
    equality_summary, _ = write_stage(
        "equality_only_deviation_minimization",
        equality_details,
        targets,
        "equality_only_constraint_level",
    )
    equality_source_summary, equality_source_family = equality_source_summaries(targets)
    if not equality_source_summary.empty:
        equality_summary = equality_source_summary
        write_csv(equality_source_summary, "equality_only_constraint_level_summary_by_scenario.csv")
    if not equality_source_family.empty:
        write_csv(equality_source_family, "equality_only_constraint_level_summary_by_family.csv")

    loadflow_details = pd.concat(
        [load_julia_loadflow_details(targets), load_python_pm_like_loadflow_endpoint_details(targets)],
        ignore_index=True,
        sort=False,
    )
    loadflow_summary, _ = write_stage(
        "loadflow_validation",
        loadflow_details,
        targets,
        "loadflow_constraint_level",
    )

    storyline = build_storyline(targets, opf_summary, constrained_summary, equality_summary, loadflow_summary)
    write_csv(storyline, "constraint_mismatch_storyline_for_slides.csv")
    write_readme()
    print(f"Wrote constraint mismatch evidence package to: {OUT}")


if __name__ == "__main__":
    main()
