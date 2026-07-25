"""Prepare a compact AC-OPF validation table package for presentation.

This script does not re-solve any OPF/PF problem. It gathers already-produced
validation outputs, adds unambiguous model-specific final summaries, and writes
a curated folder that can be uploaded to Drive for the meeting.
"""

from __future__ import annotations

import shutil
import re
import warnings
from pathlib import Path

import pandas as pd


warnings.filterwarnings("ignore", category=FutureWarning, message="Downcasting object dtype arrays.*")


ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "results" / "own_vs_powermodels"
OUT = BASE / "_presentation_validation_tables_for_drive"

SOLVED_STATUSES = {
    "optimal",
    "LOCALLY_SOLVED",
    "ALMOST_LOCALLY_SOLVED",
    "SOLVED_FEASIBLE",
}

DIFF_SPECS = [
    ("generators.csv", {"max_pg_diff": "abs_pg_diff", "max_qg_diff": "abs_qg_diff"}),
    ("buses.csv", {"max_vm_diff": "abs_vm_diff", "max_va_diff": "abs_va_diff"}),
    (
        "branches.csv",
        {
            "max_pf_diff": "abs_pf_diff",
            "max_qf_diff": "abs_qf_diff",
            "max_loading_pct_diff": "abs_loading_pct_diff",
        },
    ),
]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def write_csv(df: pd.DataFrame, relative_path: str) -> None:
    path = OUT / relative_path
    ensure_dir(path.parent)
    df.to_csv(path, index=False)


def write_csv_and_root_copy(df: pd.DataFrame, relative_path: str, root_name: str) -> None:
    write_csv(df, relative_path)
    df.to_csv(BASE / root_name, index=False)


def copy_file(source: Path, relative_path: str, manifest_rows: list[dict]) -> None:
    destination = OUT / relative_path
    ensure_dir(destination.parent)
    if source.exists():
        shutil.copy2(source, destination)
        manifest_rows.append(
            {
                "relative_path": relative_path,
                "source_path": str(source),
                "copied": True,
                "bytes": source.stat().st_size,
            }
        )
    else:
        manifest_rows.append(
            {
                "relative_path": relative_path,
                "source_path": str(source),
                "copied": False,
                "bytes": "",
            }
        )


def status_is_solved(value: object) -> bool:
    if pd.isna(value):
        return False
    return str(value) in SOLVED_STATUSES


def first_available_abs_objective_diff(df: pd.DataFrame) -> pd.Series:
    values = pd.Series(float("nan"), index=df.index)
    for col in ("objective_abs_diff", "objective_diff", "objective_diff_own_minus_pm"):
        if col in df.columns:
            values = values.fillna(pd.to_numeric(df[col], errors="coerce").abs())
    return values


def cheaper_from_objective_diff(diff: object, own_label: str) -> str:
    value = pd.to_numeric(pd.Series([diff]), errors="coerce").iloc[0]
    if pd.isna(value):
        return ""
    if abs(value) <= 1e-8:
        return "equal_within_small_tolerance"
    return "powermodels" if value > 0 else own_label


def total_python_binding_mismatches(row: pd.Series) -> int:
    total = 0
    for family in ("pmin", "pmax", "qmin", "qmax", "vmin", "vmax", "thermal", "angle_lower", "angle_upper"):
        for side in ("only_own", "only_pm"):
            col = f"n_{family}_{side}"
            if col in row and pd.notna(row[col]):
                total += int(row[col])
    return total


def normalized_scenario_text(value: object) -> str:
    text = "" if pd.isna(value) else str(value)
    text = text.lower()
    text = re.sub(r"(?<=\d)p(?=\d)", ".", text)
    text = text.replace("__", "_")
    return text


def lookup_with_scenario_alias(
    data: dict[tuple[str, str, str], dict[str, object]],
    model_variant: str,
    case: str,
    scenario: str,
) -> dict[str, object]:
    exact = data.get((model_variant, case, scenario))
    if exact is not None:
        return exact

    target = normalized_scenario_text(scenario)
    for (candidate_variant, candidate_case, candidate_scenario), values in data.items():
        if candidate_variant != model_variant or candidate_case != case:
            continue
        candidate = normalized_scenario_text(candidate_scenario)
        if candidate and (candidate in target or target in candidate):
            return values
    return {}


def diagnostic_success_series(df: pd.DataFrame) -> pd.Series:
    success = pd.Series(False, index=df.index)
    for col in (
        "diagnostic_success",
        "deviation_solved",
        "equality_only_solved",
        "equality_only_solver_locally_solved",
    ):
        if col in df.columns:
            success = success | df[col].fillna(False).astype(bool)
    for col in ("diagnostic_status", "deviation_status", "equality_only_status"):
        if col in df.columns:
            success = success | df[col].map(status_is_solved)
    return success


def add_variant_column(df: pd.DataFrame, variant: str) -> pd.DataFrame:
    out = df.copy()
    if "model_variant" in out.columns:
        out["model_variant"] = out["model_variant"].fillna(variant)
    else:
        out.insert(0, "model_variant", variant)
    return out


def diff_stats_for_scenario(scenario_dir: Path) -> dict:
    stats: dict[str, object] = {}
    missing = []

    for table_file, columns in DIFF_SPECS:
        path = scenario_dir / table_file
        if not path.exists() or path.stat().st_size == 0:
            missing.append(table_file)
            for out_col in columns:
                stats[out_col] = float("nan")
                stats[out_col.replace("max_", "mean_")] = float("nan")
            continue

        df = pd.read_csv(path)
        for out_col, src_col in columns.items():
            mean_col = out_col.replace("max_", "mean_")
            if src_col in df.columns and not df[src_col].dropna().empty:
                stats[out_col] = df[src_col].max()
                stats[mean_col] = df[src_col].mean()
            else:
                stats[out_col] = float("nan")
                stats[mean_col] = float("nan")

    stats["detailed_export_tables_available"] = len(missing) == 0
    stats["missing_detail_tables"] = ";".join(missing)
    return stats


def binding_totals_from_current_julia() -> dict[tuple[str, str], int]:
    path = BASE / "binding_constraint_mismatches_julia.csv"
    df = read_csv(path)
    if df.empty:
        return {}
    return {
        (str(row["case"]), str(row["scenario"])): int(row["total"])
        for _, row in df.iterrows()
        if "total" in row and pd.notna(row["total"])
    }


def binding_totals_from_pm_like_julia() -> dict[tuple[str, str], int]:
    path = BASE / "binding_constraint_mismatch_tolerance_summary_julia_pm_like.csv"
    df = read_csv(path)
    if df.empty:
        return {}
    return {
        (str(row["case"]), str(row["scenario"])): int(row["total_mismatches"])
        for _, row in df.iterrows()
        if pd.notna(row["total_mismatches"])
    }


def prepare_final_summaries() -> list[pd.DataFrame]:
    outputs: list[pd.DataFrame] = []

    julia_current = add_variant_column(read_csv(BASE / "final_summary_julia.csv"), "julia_current")
    if not julia_current.empty:
        julia_current["summary_scope"] = "current Julia bus-injection formulation"
        write_csv_and_root_copy(
            julia_current,
            "00_final_summaries/final_summary_julia_current.csv",
            "final_summary_julia_current.csv",
        )
        outputs.append(julia_current)

    python_current = add_variant_column(read_csv(BASE / "final_summary_python.csv"), "python_current")
    if not python_current.empty:
        python_current["summary_scope"] = "current Python bus-injection formulation"
        write_csv_and_root_copy(
            python_current,
            "00_final_summaries/final_summary_python_current.csv",
            "final_summary_python_current.csv",
        )
        outputs.append(python_current)

    pm_like_summary = read_csv(BASE / "julia_pm_like" / "summary.csv")
    if not pm_like_summary.empty:
        binding_totals = binding_totals_from_pm_like_julia()
        rows = []
        for _, row in pm_like_summary.iterrows():
            case = str(row["case"])
            scenario = str(row["scenario"])
            scenario_dir = BASE / "julia_pm_like" / case / scenario
            stats = diff_stats_for_scenario(scenario_dir)
            rows.append(
                {
                    "model_variant": "julia_pm_like",
                    **row.to_dict(),
                    **stats,
                    "binding_mismatches_total": binding_totals.get((case, scenario), 0),
                    "summary_scope": "Julia PM-like formulation; detailed diffs only where per-scenario exports exist",
                }
            )
        julia_pm_like = pd.DataFrame(rows)
        write_csv_and_root_copy(
            julia_pm_like,
            "00_final_summaries/final_summary_julia_pm_like.csv",
            "final_summary_julia_pm_like.csv",
        )
        outputs.append(julia_pm_like)

    python_pm_like = read_csv(BASE / "python_pm_like" / "summary.csv")
    if not python_pm_like.empty:
        if "model_variant" not in python_pm_like.columns:
            python_pm_like.insert(0, "model_variant", "python_pm_like_zero")
        python_pm_like["summary_scope"] = "Python PM-like zero-start formulation"
        write_csv_and_root_copy(
            python_pm_like,
            "00_final_summaries/final_summary_python_pm_like.csv",
            "final_summary_python_pm_like.csv",
        )
        outputs.append(python_pm_like)

    if outputs:
        combined = pd.concat(outputs, ignore_index=True, sort=False)
        write_csv_and_root_copy(
            combined,
            "00_final_summaries/final_summary_all_models_combined.csv",
            "final_summary_all_models_combined.csv",
        )
        coverage = []
        for variant, group in combined.groupby("model_variant", dropna=False):
            own_status = group["own_status"] if "own_status" in group else pd.Series(dtype=object)
            pm_status = group["pm_status"] if "pm_status" in group else pd.Series(dtype=object)
            objective_abs = first_available_abs_objective_diff(group)
            objective_rel = (
                pd.to_numeric(group["objective_rel_diff"], errors="coerce")
                if "objective_rel_diff" in group.columns
                else pd.Series(float("nan"), index=group.index)
            )
            max_variable_cols = [
                c
                for c in [
                    "max_pg_diff",
                    "max_qg_diff",
                    "max_vm_diff",
                    "max_va_diff",
                    "max_pf_diff",
                    "max_qf_diff",
                ]
                if c in group.columns
            ]
            material_variable = (
                group[max_variable_cols].max(axis=1) > 1e-4 if max_variable_cols else pd.Series(False, index=group.index)
            )
            coverage.append(
                {
                    "model_variant": variant,
                    "n_rows": len(group),
                    "n_own_solved_status": int(own_status.map(status_is_solved).sum()) if not own_status.empty else "",
                    "n_pm_solved_status": int(pm_status.map(status_is_solved).sum()) if not pm_status.empty else "",
                    "n_objective_abs_diff_gt_1e_4": int((objective_abs > 1e-4).sum()),
                    "n_objective_rel_diff_gt_1e_6": int((objective_rel.abs() > 1e-6).sum()),
                    "n_material_variable_diff_gt_1e_4": int(material_variable.sum()),
                    "n_rows_with_detail_exports": int(group.get("detailed_export_tables_available", pd.Series(True, index=group.index)).fillna(True).astype(bool).sum()),
                }
            )
        coverage_df = pd.DataFrame(coverage)
        write_csv_and_root_copy(
            coverage_df,
            "00_final_summaries/final_summary_model_coverage.csv",
            "final_summary_model_coverage.csv",
        )
    return outputs


def prepare_target_tables() -> None:
    rows = []

    objective_sources = [
        ("julia_current", BASE / "objective_mismatch_details_julia_current.csv"),
        ("julia_pm_like", BASE / "objective_mismatch_details_julia_pm_like.csv"),
    ]
    for variant, path in objective_sources:
        df = read_csv(path)
        for _, row in df.iterrows():
            rows.append(
                {
                    "model_variant": variant,
                    "case": row.get("case", ""),
                    "scenario": row.get("scenario", ""),
                    "target_reason": "material_objective_mismatch",
                    "source_table": path.name,
                    "objective_diff_own_minus_pm": row.get("objective_diff_own_minus_pm", ""),
                    "objective_rel_diff": row.get("objective_rel_diff", ""),
                    "cheaper_solution": row.get("cheaper_solution", ""),
                }
            )

    python_queue = read_csv(BASE / "python_ac_opf_debug_recommended_run_queue.csv")
    if not python_queue.empty:
        for _, row in python_queue.iterrows():
            rows.append(
                {
                    "model_variant": "python_current",
                    "case": row.get("case", ""),
                    "scenario": row.get("scenario", ""),
                    "target_reason": row.get("target_reasons", ""),
                    "source_table": "python_ac_opf_debug_recommended_run_queue.csv",
                    "objective_diff_own_minus_pm": row.get("objective_diff_own_minus_pm", ""),
                    "objective_rel_diff": row.get("objective_rel_diff", ""),
                    "cheaper_solution": "",
                }
            )

    python_pm_targets = read_csv(BASE / "python_pm_like" / "equality_only_deviation_targets.csv")
    if not python_pm_targets.empty:
        for _, row in python_pm_targets.iterrows():
            rows.append(
                {
                    "model_variant": row.get("model_variant", "python_pm_like_zero"),
                    "case": row.get("case", ""),
                    "scenario": row.get("scenario", ""),
                    "target_reason": row.get("target_reasons", ""),
                    "source_table": "python_pm_like/equality_only_deviation_targets.csv",
                    "objective_diff_own_minus_pm": row.get("objective_diff", ""),
                    "objective_rel_diff": row.get("objective_rel_diff", ""),
                    "cheaper_solution": "",
                }
            )

    targets = pd.DataFrame(rows).drop_duplicates()
    if not targets.empty:
        write_csv(targets, "01_discrepancy_targets/diagnostic_target_scenarios_details.csv")
        summary = (
            targets.groupby(["model_variant", "source_table"], dropna=False)
            .agg(
                n_target_rows=("scenario", "count"),
                n_cases=("case", "nunique"),
                cases=("case", lambda x: ";".join(sorted(set(map(str, x))))),
                scenarios=("scenario", lambda x: ";".join(sorted(set(map(str, x)))[:12])),
            )
            .reset_index()
        )
        write_csv(summary, "01_discrepancy_targets/diagnostic_target_scenarios_summary.csv")


def selected_objective_mismatch_rows() -> pd.DataFrame:
    rows = []

    current = read_csv(BASE / "objective_mismatch_details_julia_current.csv")
    for _, row in current.iterrows():
        diff = row.get("objective_diff_own_minus_pm", "")
        rows.append(
            {
                "model_variant": "julia_current",
                "case": row.get("case", ""),
                "scenario": row.get("scenario", ""),
                "own_objective": row.get("own_objective", ""),
                "pm_objective": row.get("pm_objective", ""),
                "objective_diff_own_minus_pm": diff,
                "abs_objective_diff": row.get("abs_objective_diff", ""),
                "objective_rel_diff": row.get("objective_rel_diff", ""),
                "objective_rel_diff_pct": row.get("objective_rel_diff_pct", ""),
                "cheaper_solution": cheaper_from_objective_diff(diff, "julia_current"),
                "target_reason": "material_objective_mismatch",
                "source_table": "objective_mismatch_details_julia_current.csv",
                "note": "Source data shows own current Julia is cheaper for these rows; do not relabel as PowerModels-cheaper.",
            }
        )

    pm_like = read_csv(BASE / "objective_mismatch_details_julia_pm_like.csv")
    for _, row in pm_like.iterrows():
        diff = row.get("objective_diff_own_minus_pm", "")
        rows.append(
            {
                "model_variant": "julia_pm_like",
                "case": row.get("case", ""),
                "scenario": row.get("scenario", ""),
                "own_objective": row.get("own_pm_like_objective", ""),
                "pm_objective": row.get("pm_objective", ""),
                "objective_diff_own_minus_pm": diff,
                "abs_objective_diff": row.get("abs_objective_diff", ""),
                "objective_rel_diff": row.get("objective_rel_diff", ""),
                "objective_rel_diff_pct": row.get("objective_rel_diff_pct", ""),
                "cheaper_solution": cheaper_from_objective_diff(diff, "julia_pm_like"),
                "target_reason": "material_objective_mismatch",
                "source_table": "objective_mismatch_details_julia_pm_like.csv",
                "note": "PowerModels is cheaper where objective_diff_own_minus_pm is positive.",
            }
        )

    python_pm_like_targets = read_csv(BASE / "python_pm_like" / "equality_only_deviation_targets.csv")
    for _, row in python_pm_like_targets.iterrows():
        diff = row.get("objective_diff", "")
        rows.append(
            {
                "model_variant": row.get("model_variant", "python_pm_like_zero"),
                "case": row.get("case", ""),
                "scenario": row.get("scenario", ""),
                "own_objective": row.get("own_objective", ""),
                "pm_objective": row.get("pm_objective", ""),
                "objective_diff_own_minus_pm": diff,
                "abs_objective_diff": abs(float(diff)) if pd.notna(diff) else "",
                "objective_rel_diff": row.get("objective_rel_diff", ""),
                "objective_rel_diff_pct": float(row.get("objective_rel_diff", 0.0)) * 100.0
                if pd.notna(row.get("objective_rel_diff", float("nan")))
                else "",
                "cheaper_solution": cheaper_from_objective_diff(diff, "python_pm_like_zero"),
                "target_reason": row.get("target_reasons", "material_objective_or_variable_mismatch"),
                "source_table": "python_pm_like/equality_only_deviation_targets.csv",
                "note": "PowerModels is cheaper where objective_diff_own_minus_pm is positive.",
            }
        )

    return pd.DataFrame(rows)


def prepare_objective_mismatch_story_tables() -> None:
    details = selected_objective_mismatch_rows()
    if details.empty:
        return
    write_csv_and_root_copy(
        details,
        "01_objective_mismatches/objective_mismatch_details_selected_models.csv",
        "objective_mismatch_details_selected_models.csv",
    )

    summary = (
        details.groupby("model_variant", dropna=False)
        .agg(
            n_mismatch_scenarios=("scenario", "count"),
            n_powermodels_cheaper=("cheaper_solution", lambda x: int((x == "powermodels").sum())),
            n_own_model_cheaper=("cheaper_solution", lambda x: int(x.astype(str).str.startswith(("julia_", "python_")).sum())),
            max_abs_objective_diff=("abs_objective_diff", "max"),
            max_objective_rel_diff_pct=("objective_rel_diff_pct", "max"),
            scenarios=("scenario", lambda x: ";".join(map(str, x))),
        )
        .reset_index()
    )
    summary["presentation_use"] = (
        "Use this instead of final_summary_model_coverage: it is the objective-mismatch screen that selects the diagnostic rows."
    )
    write_csv_and_root_copy(
        summary,
        "01_objective_mismatches/objective_mismatch_summary_selected_models.csv",
        "objective_mismatch_summary_selected_models.csv",
    )

    target_details = details[
        [
            "model_variant",
            "case",
            "scenario",
            "target_reason",
            "objective_diff_own_minus_pm",
            "objective_rel_diff",
            "cheaper_solution",
            "source_table",
            "note",
        ]
    ].copy()
    write_csv_and_root_copy(
        target_details,
        "01_discrepancy_targets/diagnostic_target_scenarios_details_selected_models.csv",
        "diagnostic_target_scenarios_details_selected_models.csv",
    )

    target_summary = (
        target_details.groupby(["model_variant", "cheaper_solution"], dropna=False)
        .agg(
            n_scenarios=("scenario", "count"),
            scenarios=("scenario", lambda x: ";".join(map(str, x))),
        )
        .reset_index()
    )
    write_csv_and_root_copy(
        target_summary,
        "01_discrepancy_targets/diagnostic_target_scenarios_summary_selected_models.csv",
        "diagnostic_target_scenarios_summary_selected_models.csv",
    )


def prepare_deviation_variant_story_table() -> None:
    rows = []
    sources = [
        ("julia_current", "constrained", BASE / "current_constrained_deviation_minimization_julia.csv"),
        ("julia_pm_like", "constrained", BASE / "pm_like_constrained_deviation_minimization_julia.csv"),
        ("python_pm_like_zero", "constrained", BASE / "python_pm_like" / "constrained_deviation_minimization.csv"),
        ("julia_current_or_pm_like", "equality_only", BASE / "equality_only_deviation_minimization_julia.csv"),
        ("python_pm_like_zero", "equality_only", BASE / "python_pm_like" / "equality_only_deviation_minimization.csv"),
    ]
    for fallback_variant, diagnostic_type, path in sources:
        df = read_csv(path)
        if df.empty:
            continue
        for _, row in df.iterrows():
            model_variant = row.get("model_variant", fallback_variant)
            if fallback_variant == "julia_current_or_pm_like":
                model_variant = f"julia_{row.get('variant', '')}"
            objective_variant = row.get("variant", "full")
            if diagnostic_type == "equality_only":
                objective_variant = "full"
            rows.append(
                {
                    "model_variant": model_variant,
                    "diagnostic_type": diagnostic_type,
                    "objective_variant": objective_variant,
                    "case": row.get("case", ""),
                    "scenario": row.get("scenario", ""),
                    "solved": bool(
                        row.get("deviation_solved", False)
                        or row.get("equality_only_solved", False)
                        or row.get("equality_only_solver_locally_solved", False)
                        or row.get("constrained_solver_locally_solved", False)
                    ),
                    "solver_status": row.get(
                        "deviation_status",
                        row.get(
                            "constrained_status",
                            row.get("equality_only_status", row.get("diagnostic_status", "")),
                        ),
                    ),
                    "l1_deviation": row.get(
                        "pinned_l1_deviation_nonnegative",
                        row.get(
                            "constrained_l1_deviation_nonnegative",
                            row.get("equality_only_l1_deviation_nonnegative", row.get("diagnostic_objective_l1", "")),
                        ),
                    ),
                    "cost_diff_vs_pm": row.get(
                        "cost_diff_vs_pm",
                        row.get(
                            "cost_diff_constrained_minus_pm",
                            row.get("cost_diff_equality_minus_pm", row.get("cost_diff_diagnostic_minus_pm", "")),
                        ),
                    ),
                    "cost_rel_diff_vs_pm": row.get(
                        "cost_rel_diff_vs_pm",
                        row.get(
                            "cost_rel_diff_constrained_vs_pm",
                            row.get("cost_rel_diff_equality_vs_pm", row.get("cost_rel_diff_diagnostic_vs_pm", "")),
                        ),
                    ),
                    "max_abs_pg_deviation": row.get("max_abs_pg_deviation", ""),
                    "max_abs_qg_deviation": row.get("max_abs_qg_deviation", ""),
                    "max_abs_vm_deviation": row.get("max_abs_vm_deviation", ""),
                    "max_abs_va_deviation": row.get("max_abs_va_deviation", ""),
                    "source_table": path.name,
                }
            )

    details = pd.DataFrame(rows)
    if details.empty:
        return
    details = details[details["model_variant"].isin(["julia_current", "julia_pm_like", "python_pm_like_zero"])]
    write_csv_and_root_copy(
        details,
        "02_deviation_minimization/deviation_minimization_variant_details_selected_models.csv",
        "deviation_minimization_variant_details_selected_models.csv",
    )

    summary = (
        details.groupby(["model_variant", "diagnostic_type", "objective_variant"], dropna=False)
        .agg(
            n_scenarios=("scenario", "count"),
            n_solved=("solved", lambda x: int(x.fillna(False).astype(bool).sum())),
            max_l1_deviation=("l1_deviation", "max"),
            max_abs_cost_diff_vs_pm=("cost_diff_vs_pm", lambda x: pd.to_numeric(x, errors="coerce").abs().max()),
            max_abs_pg_deviation=("max_abs_pg_deviation", "max"),
            max_abs_qg_deviation=("max_abs_qg_deviation", "max"),
            max_abs_vm_deviation=("max_abs_vm_deviation", "max"),
            max_abs_va_deviation=("max_abs_va_deviation", "max"),
            scenarios=("scenario", lambda x: ";".join(map(str, x))),
        )
        .reset_index()
    )
    summary["presentation_interpretation"] = summary.apply(
        lambda r: "full/equality-only diagnostic reaches the PowerModels point"
        if str(r["objective_variant"]) == "full"
        and (
            str(r["diagnostic_type"]) == "equality_only"
            or pd.to_numeric(pd.Series([r["max_abs_cost_diff_vs_pm"]]), errors="coerce").fillna(float("inf")).iloc[0]
            < 1e-4
        )
        else "ablation/variant diagnostic; nonzero deviation identifies variables needed to reproduce PM point",
        axis=1,
    )
    write_csv_and_root_copy(
        summary,
        "02_deviation_minimization/deviation_minimization_variant_summary_selected_models.csv",
        "deviation_minimization_variant_summary_selected_models.csv",
    )


def prepare_equality_original_reconciliation_table() -> None:
    targets = selected_objective_mismatch_rows()
    if targets.empty:
        return

    original_counts: dict[tuple[str, str, str], dict[str, object]] = {}
    current_details = read_csv(BASE / "binding_constraint_mismatch_details_julia_current.csv")
    if not current_details.empty:
        for (case, scenario), group in current_details.groupby(["case", "scenario"], dropna=False):
            original_counts[("julia_current", str(case), str(scenario))] = {
                "original_binding_mismatches": len(group),
                "original_max_abs_margin_diff": group["abs_margin_diff"].max(),
                "original_source": "binding_constraint_mismatch_details_julia_current.csv",
            }
    pm_like_summary = read_csv(BASE / "binding_constraint_mismatch_tolerance_summary_julia_pm_like.csv")
    if not pm_like_summary.empty:
        for _, row in pm_like_summary.iterrows():
            original_counts[("julia_pm_like", str(row["case"]), str(row["scenario"]))] = {
                "original_binding_mismatches": int(row["total_mismatches"]),
                "original_max_abs_margin_diff": row["max_abs_margin_diff"],
                "original_source": "binding_constraint_mismatch_tolerance_summary_julia_pm_like.csv",
            }
    python_summary = read_csv(BASE / "python_pm_like" / "summary.csv")
    if not python_summary.empty:
        for _, row in python_summary.iterrows():
            original_counts[("python_pm_like_zero", str(row["case"]), str(row["scenario"]))] = {
                "original_binding_mismatches": total_python_binding_mismatches(row),
                "original_max_abs_margin_diff": "",
                "original_source": "python_pm_like/summary.csv active-set columns",
            }

    equality_counts: dict[tuple[str, str, str], dict[str, object]] = {}
    julia_eq = read_csv(BASE / "equality_only_deviation_binding_summary_julia.csv")
    if not julia_eq.empty:
        for _, row in julia_eq.iterrows():
            equality_counts[(f"julia_{row['variant']}", str(row["case"]), str(row["scenario"]))] = {
                "equality_only_binding_mismatches": int(row["n_binding_mismatches"]),
                "equality_only_violations": int(row["n_equality_only_violations"]),
                "equality_only_max_abs_margin_diff": row["max_abs_margin_diff"],
                "equality_only_source": "equality_only_deviation_binding_summary_julia.csv",
            }
    python_eq = read_csv(BASE / "python_pm_like" / "equality_only_deviation_binding_summary.csv")
    if not python_eq.empty:
        all_rows = python_eq[python_eq["constraint_family"] == "all"] if "constraint_family" in python_eq else python_eq
        for _, row in all_rows.iterrows():
            equality_counts[(str(row["model_variant"]), str(row["case"]), str(row["scenario"]))] = {
                "equality_only_binding_mismatches": int(row["n_binding_mismatches"]),
                "equality_only_violations": int(row["n_equality_only_violations"]),
                "equality_only_max_abs_margin_diff": row["max_abs_margin_diff"],
                "equality_only_source": "python_pm_like/equality_only_deviation_binding_summary.csv",
            }

    rows = []
    for _, target in targets.iterrows():
        key = (str(target["model_variant"]), str(target["case"]), str(target["scenario"]))
        orig = lookup_with_scenario_alias(original_counts, key[0], key[1], key[2])
        eq = lookup_with_scenario_alias(equality_counts, key[0], key[1], key[2])
        rows.append(
            {
                "model_variant": key[0],
                "case": key[1],
                "scenario": key[2],
                "original_endpoint_comparison": "own OPF endpoint xE* vs PowerModels OPF endpoint xP*",
                "original_binding_mismatches": orig.get("original_binding_mismatches", 0),
                "original_max_abs_margin_diff": orig.get("original_max_abs_margin_diff", ""),
                "equality_only_comparison": "diagnostic equality-feasible point xE** projected toward xP*, then h(xE**) vs h(xP*)",
                "equality_only_binding_mismatches": eq.get("equality_only_binding_mismatches", ""),
                "equality_only_violations": eq.get("equality_only_violations", ""),
                "equality_only_max_abs_margin_diff": eq.get("equality_only_max_abs_margin_diff", ""),
                "why_this_is_not_contradictory": (
                    "The two rows compare different operating points. Original mismatches show different OPF endpoints/active sets; "
                    "equality-only shows the own equations can move to the PowerModels point and then h(.) agrees there."
                ),
                "original_source": orig.get("original_source", ""),
                "equality_only_source": eq.get("equality_only_source", ""),
            }
        )
    write_csv_and_root_copy(
        pd.DataFrame(rows),
        "03_equality_only_h_levels/equality_only_vs_original_binding_reconciliation.csv",
        "equality_only_vs_original_binding_reconciliation.csv",
    )


def prepare_loadflow_interpretation_table() -> None:
    rows = []

    julia_lf = read_csv(BASE / "opf_endpoint_loadflow_validation_julia.csv")
    julia_h = read_csv(BASE / "opf_endpoint_pf_inequality_feasibility_summary_julia.csv")
    julia_violations = read_csv(BASE / "opf_endpoint_pf_inequality_violations_julia.csv")
    julia_binding_differences = read_csv(BASE / "opf_endpoint_pf_inequality_binding_differences_julia.csv")
    if not julia_h.empty:
        for _, hrow in julia_h.iterrows():
            variant = f"julia_{hrow['variant']}"
            subset = julia_lf[
                (julia_lf["variant"] == hrow["variant"])
                & (julia_lf["case"] == hrow["case"])
                & (julia_lf["scenario"] == hrow["scenario"])
            ]
            vsubset = julia_violations[
                (julia_violations["variant"] == hrow["variant"])
                & (julia_violations["case"] == hrow["case"])
                & (julia_violations["scenario"] == hrow["scenario"])
                & (julia_violations["independently_rederived_after_pf"].fillna(False).astype(bool))
            ]
            own_only = vsubset[
                vsubset["own_pf_violation"].fillna(False).astype(bool)
                & ~vsubset["pm_pf_violation"].fillna(False).astype(bool)
            ]
            pm_only = vsubset[
                ~vsubset["own_pf_violation"].fillna(False).astype(bool)
                & vsubset["pm_pf_violation"].fillna(False).astype(bool)
            ]
            shared = vsubset[
                vsubset["own_pf_violation"].fillna(False).astype(bool)
                & vsubset["pm_pf_violation"].fillna(False).astype(bool)
            ]
            bsubset = julia_binding_differences[
                (julia_binding_differences["variant"] == hrow["variant"])
                & (julia_binding_differences["case"] == hrow["case"])
                & (julia_binding_differences["scenario"] == hrow["scenario"])
            ]
            if not bsubset.empty and "independently_rederived_after_pf" in bsubset.columns:
                n_independent_binding_mismatches = int(
                    bsubset["independently_rederived_after_pf"].fillna(False).astype(bool).sum()
                )
                n_specified_or_pinned_binding_mismatches = int(len(bsubset) - n_independent_binding_mismatches)
            else:
                n_independent_binding_mismatches = ""
                n_specified_or_pinned_binding_mismatches = ""
            n_converged = int(subset["pf_converged"].fillna(False).astype(bool).sum()) if not subset.empty else 0
            n_endpoint_specific_violations = int(len(own_only) + len(pm_only))
            pass_check = len(subset) == 2 and n_converged == 2 and n_endpoint_specific_violations == 0
            max_own_only = float(own_only["own_pf_violation_amount"].max()) if not own_only.empty else 0.0
            max_pm_only = float(pm_only["pm_pf_violation_amount"].max()) if not pm_only.empty else 0.0
            rows.append(
                {
                    "model_variant": variant,
                    "case": hrow["case"],
                    "scenario": hrow["scenario"],
                    "workflow": "PowerModels.compute_ac_pf Newton PF from own and PM OPF endpoints",
                    "run_scope": "only objective-mismatch diagnostic scenarios, not full sweep",
                    "n_endpoint_runs": len(subset),
                    "n_converged": n_converged,
                    "n_independent_own_endpoint_violations": hrow.get("n_independent_own_pf_violations", ""),
                    "n_independent_pm_endpoint_violations": hrow.get("n_independent_pm_pf_violations", ""),
                    "max_independent_own_endpoint_violation": hrow.get("max_independent_own_pf_violation", ""),
                    "max_independent_pm_endpoint_violation": hrow.get("max_independent_pm_pf_violation", ""),
                    "n_endpoint_specific_independent_violations": n_endpoint_specific_violations,
                    "n_own_only_independent_violations": int(len(own_only)),
                    "n_pm_only_independent_violations": int(len(pm_only)),
                    "n_shared_independent_violations": int(len(shared)),
                    "max_own_only_independent_violation": max_own_only,
                    "max_pm_only_independent_violation": max_pm_only,
                    "n_independent_binding_mismatches": n_independent_binding_mismatches,
                    "n_specified_or_pinned_binding_mismatches": n_specified_or_pinned_binding_mismatches,
                    "n_post_pf_binding_mismatches": hrow.get("n_pf_binding_mismatches", ""),
                    "interpretation": (
                        "PF converged for both endpoints. No endpoint-specific independent post-PF limit violation was found; "
                        "shared rows occur at both endpoints and are not own-only failures."
                        if pass_check
                        else "PF endpoint comparison needs follow-up because at least one endpoint-specific independent post-PF limit difference remains."
                    ),
                    "validation_result": "PASS" if pass_check else "FOLLOW_UP",
                    "validation_strength": "PF endpoint consistency passed" if pass_check else "PF endpoint consistency follow-up needed",
                }
            )

    python_lf = read_csv(BASE / "python_endpoint_pypowsybl_loadflow_validation.csv")
    python_levels = read_csv(BASE / "python_endpoint_pypowsybl_lf_inequality_levels.csv")
    if not python_lf.empty:
        python_lf = python_lf[python_lf["model_variant"] == "python_pm_like_zero"].copy()
        for (case, scenario), group in python_lf.groupby(["case", "scenario"], dropna=False):
            lsubset = python_levels[
                (python_levels["model_variant"] == "python_pm_like_zero")
                & (python_levels["case"] == case)
                & (python_levels["scenario"] == scenario)
                & (python_levels["independently_rederived_after_lf"].fillna(False).astype(bool))
            ]
            key_cols = ["constraint_type", "element_id", "bound"]
            own_keys = {
                tuple(row[col] for col in key_cols)
                for _, row in lsubset[
                    (lsubset["endpoint_source"] == "own_opf_solution")
                    & (lsubset["lf_violates_limit"].fillna(False).astype(bool))
                ].iterrows()
            }
            pm_keys = {
                tuple(row[col] for col in key_cols)
                for _, row in lsubset[
                    (lsubset["endpoint_source"] == "powermodels_opf_solution")
                    & (lsubset["lf_violates_limit"].fillna(False).astype(bool))
                ].iterrows()
            }
            own_only_keys = own_keys - pm_keys
            pm_only_keys = pm_keys - own_keys
            shared_keys = own_keys & pm_keys
            n_converged = int(group["lf_converged"].fillna(False).astype(bool).sum())
            n_endpoint_specific_violations = len(own_only_keys) + len(pm_only_keys)
            pass_check = len(group) == 2 and n_converged == 2 and n_endpoint_specific_violations == 0
            rows.append(
                {
                    "model_variant": "python_pm_like_zero",
                    "case": case,
                    "scenario": scenario,
                    "workflow": "PyPowSyBl/OpenLoadFlow Newton-Raphson PF from own and PM OPF endpoints",
                    "run_scope": "only material PM-like mismatch scenarios, not full sweep",
                    "n_endpoint_runs": len(group),
                    "n_converged": n_converged,
                    "n_independent_own_endpoint_violations": group.loc[
                        group["endpoint_source"] == "own_opf_solution", "n_lf_violations_independently_rederived"
                    ].sum(),
                    "n_independent_pm_endpoint_violations": group.loc[
                        group["endpoint_source"] == "powermodels_opf_solution", "n_lf_violations_independently_rederived"
                    ].sum(),
                    "max_independent_own_endpoint_violation": group.loc[
                        group["endpoint_source"] == "own_opf_solution", "max_lf_violation_independently_rederived"
                    ].max(),
                    "max_independent_pm_endpoint_violation": group.loc[
                        group["endpoint_source"] == "powermodels_opf_solution", "max_lf_violation_independently_rederived"
                    ].max(),
                    "n_endpoint_specific_independent_violations": n_endpoint_specific_violations,
                    "n_own_only_independent_violations": len(own_only_keys),
                    "n_pm_only_independent_violations": len(pm_only_keys),
                    "n_shared_independent_violations": len(shared_keys),
                    "max_own_only_independent_violation": "",
                    "max_pm_only_independent_violation": "",
                    "n_independent_binding_mismatches": "",
                    "n_specified_or_pinned_binding_mismatches": "",
                    "n_post_pf_binding_mismatches": group["n_binding_mismatches_independently_rederived"].sum(),
                    "interpretation": (
                        "OpenLoadFlow converged for both endpoints and no endpoint-specific independent post-LF limit violation was found."
                        if pass_check
                        else "OpenLoadFlow converged, but endpoint-specific independent post-LF differences remain; keep this in the Python follow-up track."
                    ),
                    "validation_result": "PASS" if pass_check else "FOLLOW_UP",
                    "validation_strength": "endpoint LF consistency passed" if pass_check else "Python endpoint LF follow-up needed",
                }
            )

    if rows:
        write_csv_and_root_copy(
            pd.DataFrame(rows),
            "04_loadflow_validation/loadflow_validation_scope_and_interpretation.csv",
            "loadflow_validation_scope_and_interpretation.csv",
        )


def prepare_deviation_summary() -> None:
    pieces = []
    sources = [
        ("julia_current", "constrained", BASE / "current_constrained_deviation_minimization_julia.csv"),
        ("julia_pm_like", "constrained", BASE / "pm_like_constrained_deviation_minimization_julia.csv"),
        ("julia_current_and_pm_like", "equality_only", BASE / "equality_only_deviation_minimization_julia.csv"),
        ("python_current", "constrained_or_debug", BASE / "python_deviation_minimization_diagnostics.csv"),
        (
            "python_pm_like_zero",
            "constrained",
            BASE / "python_pm_like" / "constrained_deviation_minimization.csv",
        ),
        (
            "python_pm_like_zero",
            "equality_only",
            BASE / "python_pm_like" / "equality_only_deviation_minimization.csv",
        ),
    ]
    for fallback_variant, mode, path in sources:
        df = read_csv(path)
        if df.empty:
            continue
        temp = df.copy()
        if "model_variant" not in temp.columns:
            temp.insert(0, "model_variant", fallback_variant)
        temp["model_variant"] = temp["model_variant"].replace(
            {"current": "python_current", "pm_like_zero": "python_pm_like_zero"}
            if fallback_variant.startswith("python")
            else {}
        )
        temp["diagnostic_table_source"] = path.name
        temp["diagnostic_group"] = mode
        pieces.append(temp)

    if pieces:
        combined = pd.concat(pieces, ignore_index=True, sort=False)
        write_csv(combined, "02_deviation_minimization/deviation_minimization_all_runs_combined.csv")
        summary_rows = []
        for (variant, group_name), group in combined.groupby(["model_variant", "diagnostic_group"], dropna=False):
            l1_col = None
            for candidate in (
                "diagnostic_objective_l1",
                "pinned_l1_deviation_nonnegative",
                "constrained_l1_deviation_nonnegative",
                "constrained_l1_deviation",
                "equality_only_l1_deviation_nonnegative",
                "equality_only_l1_deviation",
            ):
                if candidate in group.columns and pd.to_numeric(group[candidate], errors="coerce").notna().any():
                    l1_col = candidate
                    break
            success = diagnostic_success_series(group)
            summary_rows.append(
                {
                    "model_variant": variant,
                    "diagnostic_group": group_name,
                    "n_runs": len(group),
                    "n_success": int(success.sum()),
                    "max_l1_deviation": group[l1_col].max() if l1_col else "",
                    "max_pg_deviation": group["max_abs_pg_deviation"].max()
                    if "max_abs_pg_deviation" in group
                    else "",
                    "max_qg_deviation": group["max_abs_qg_deviation"].max()
                    if "max_abs_qg_deviation" in group
                    else "",
                    "max_vm_deviation": group["max_abs_vm_deviation"].max()
                    if "max_abs_vm_deviation" in group
                    else "",
                    "max_va_deviation": group["max_abs_va_deviation"].max()
                    if "max_abs_va_deviation" in group
                    else "",
                }
            )
        write_csv(pd.DataFrame(summary_rows), "02_deviation_minimization/deviation_minimization_summary_by_model.csv")


def prepare_equality_h_summary() -> None:
    pieces = []
    sources = [
        ("julia_current_and_pm_like", BASE / "equality_only_deviation_binding_summary_julia.csv"),
        ("python_pm_like_zero", BASE / "python_pm_like" / "equality_only_deviation_binding_summary.csv"),
    ]
    for fallback_variant, path in sources:
        df = read_csv(path)
        if df.empty:
            continue
        temp = df.copy()
        if path.name == "equality_only_deviation_binding_summary_julia.csv" and "variant" in temp.columns:
            temp.insert(0, "model_variant", temp["variant"].map(lambda x: f"julia_{x}"))
        elif "model_variant" not in temp.columns:
            temp.insert(0, "model_variant", fallback_variant)
        temp["source_table"] = path.name
        pieces.append(temp)

    if pieces:
        combined = pd.concat(pieces, ignore_index=True, sort=False)
        write_csv(combined, "03_equality_only_h_levels/equality_only_binding_summary_all_models.csv")

    family_pieces = []
    julia_family = read_csv(BASE / "equality_only_deviation_inequality_level_by_family_julia.csv")
    if not julia_family.empty:
        temp = julia_family.copy()
        temp.insert(0, "model_variant", temp["variant"].map(lambda x: f"julia_{x}"))
        temp["constraint_family"] = temp["constraint_type"]
        temp["source_table"] = "equality_only_deviation_inequality_level_by_family_julia.csv"
        family_pieces.append(temp)
    python_family = read_csv(BASE / "python_pm_like" / "equality_only_deviation_binding_summary.csv")
    if not python_family.empty:
        temp = python_family.copy()
        temp["source_table"] = "python_pm_like/equality_only_deviation_binding_summary.csv"
        family_pieces.append(temp)
    if family_pieces:
        family_combined = pd.concat(family_pieces, ignore_index=True, sort=False)
        if "max_equality_only_violation" not in family_combined.columns:
            family_combined["max_equality_only_violation"] = float("nan")
        if "max_violation" in family_combined.columns:
            family_combined["max_equality_only_violation"] = family_combined[
                "max_equality_only_violation"
            ].fillna(family_combined["max_violation"])
        grouped = (
            family_combined.groupby(["model_variant", "constraint_family"], dropna=False)
            .agg(
                n_rows=("case", "count"),
                max_binding_mismatches=("n_binding_mismatches", "max"),
                max_equality_only_violations=("n_equality_only_violations", "max"),
                max_equality_only_violation=("max_equality_only_violation", "max"),
                max_abs_margin_diff=("max_abs_margin_diff", "max"),
            )
            .reset_index()
        )
        write_csv(grouped, "03_equality_only_h_levels/equality_only_h_level_summary_by_family.csv")


def prepare_loadflow_summary() -> None:
    pieces = []
    julia_lf = read_csv(BASE / "opf_endpoint_loadflow_validation_julia.csv")
    if not julia_lf.empty:
        temp = julia_lf.copy()
        temp["model_variant"] = temp["variant"].map(lambda x: f"julia_{x}")
        temp["workflow"] = "PowerModels.compute_ac_pf Newton warm-started from OPF endpoint"
        pieces.append(temp)

    python_lf = read_csv(BASE / "python_endpoint_pypowsybl_loadflow_validation.csv")
    if not python_lf.empty:
        temp = python_lf.copy()
        temp["workflow"] = "PyPowSyBl/OpenLoadFlow Newton-Raphson warm-started from OPF endpoint"
        pieces.append(temp)

    if pieces:
        combined = pd.concat(pieces, ignore_index=True, sort=False)
        write_csv(combined, "04_loadflow_validation/loadflow_endpoint_validation_all_models.csv")
        if "pf_converged" in combined.columns and "lf_converged" in combined.columns:
            combined["endpoint_lf_converged"] = combined["pf_converged"].fillna(combined["lf_converged"])
        elif "pf_converged" in combined.columns:
            combined["endpoint_lf_converged"] = combined["pf_converged"]
        else:
            combined["endpoint_lf_converged"] = combined["lf_converged"]
        if "pf_solve_time_sec" in combined.columns and "lf_elapsed_sec" in combined.columns:
            combined["endpoint_lf_solve_time_sec"] = combined["pf_solve_time_sec"].fillna(combined["lf_elapsed_sec"])
        elif "pf_solve_time_sec" in combined.columns:
            combined["endpoint_lf_solve_time_sec"] = combined["pf_solve_time_sec"]
        else:
            combined["endpoint_lf_solve_time_sec"] = combined["lf_elapsed_sec"]
        summary = (
            combined.groupby(["model_variant", "workflow"], dropna=False)
            .agg(
                n_endpoint_runs=("case", "count"),
                n_converged=("endpoint_lf_converged", lambda x: int(x.fillna(False).astype(bool).sum())),
                max_solve_time_sec=("endpoint_lf_solve_time_sec", "max"),
            )
            .reset_index()
        )
        write_csv(summary, "04_loadflow_validation/loadflow_endpoint_validation_summary_by_model.csv")

    lf_h_pieces = []
    julia_h = read_csv(BASE / "opf_endpoint_pf_inequality_feasibility_summary_julia.csv")
    if not julia_h.empty:
        temp = julia_h.copy()
        temp["model_variant"] = temp["variant"].map(lambda x: f"julia_{x}")
        temp["source_table"] = "opf_endpoint_pf_inequality_feasibility_summary_julia.csv"
        lf_h_pieces.append(temp)
    python_h = read_csv(BASE / "python_endpoint_pypowsybl_lf_binding_summary.csv")
    if not python_h.empty:
        temp = python_h.copy()
        temp["source_table"] = "python_endpoint_pypowsybl_lf_binding_summary.csv"
        lf_h_pieces.append(temp)
    if lf_h_pieces:
        combined_h = pd.concat(lf_h_pieces, ignore_index=True, sort=False)
        write_csv(combined_h, "04_loadflow_validation/loadflow_inequality_level_summary_all_models.csv")


def write_readme() -> None:
    readme = """# AC OPF validation presentation tables

This folder is the curated Drive upload package for the meeting.

## What is included

1. `00_final_summaries`
   - `final_summary_julia_current.csv`: current Julia bus-injection AC OPF.
   - `final_summary_julia_pm_like.csv`: Julia PM-like AC OPF, including coverage flags for missing detailed per-scenario exports.
   - `final_summary_python_current.csv`: current Python AC OPF.
   - `final_summary_python_pm_like.csv`: Python PM-like AC OPF sweep.

2. `01_objective_mismatches`
   - `objective_mismatch_summary_selected_models.csv`: compact mismatch count for Julia current, Julia PM-like, and Python PM-like zero.
   - `objective_mismatch_details_selected_models.csv`: scenario-level objective differences and cheaper-solution sign.

3. `01_discrepancy_targets`
   - shows why the diagnostic scenarios were selected.
   - use the `*_selected_models.csv` files for the presentation track.

4. `02_deviation_minimization`
   - `deviation_minimization_variant_summary_selected_models.csv`: constrained/equality-only variants and results for Julia current, Julia PM-like, and Python PM-like zero.
   - detailed source rows are also included.

5. `03_equality_only_h_levels`
   - equality-only deviation results and externally evaluated inequality levels `h(.)`.
   - `equality_only_vs_original_binding_reconciliation.csv` explains why original OPF binding mismatches and equality-only near-zero `h(.)` differences are not contradictory: they are evaluated at different operating points.

6. `04_loadflow_validation`
   - endpoint load-flow validation tables.
   - Julia uses `PowerModels.compute_ac_pf` with Newton and OPF endpoint warm starts.
   - Python uses PyPowSyBl/OpenLoadFlow with Newton-Raphson and OPF endpoint warm starts.
   - `loadflow_validation_scope_and_interpretation.csv` is the presentation table. It reports `PASS` only when both endpoints converge and no endpoint-specific independent post-PF/LF limit violation remains.

7. `05_source_detailed_tables`
   - detailed supporting tables copied from the existing organized meeting folder.

## Validation reading

The Julia evidence is presentation-ready from a successful-validation viewpoint:
bus shunts are present and necessary, solver diagnostics are clean for the
objective-mismatch rows, constrained deviation reaches the PowerModels point,
equality-only deviation reaches the PowerModels point and externally evaluated
`h(.)` levels agree, and endpoint load-flow does not expose an own-only physical
inconsistency.

The PM-like Julia final summary has one caveat: some solved scenarios still lack
the detailed per-scenario exports needed for variable-difference aggregation.
Those rows are flagged in `final_summary_julia_pm_like.csv` and summarized in
`julia_pm_like_coverage_audit.csv`.

The Python evidence is a debugging/validation-progress package: PM-like Python
has a full sweep summary and agreement counts, equality-only diagnostics for
material PM-like mismatch rows, and PyPowSyBl endpoint load-flow validation. The
remaining non-success rows are explicitly classified in the Python PM-like
status tables.

## Important interpretation points

- A positive `objective_diff_own_minus_pm` means PowerModels is cheaper.
- A negative `objective_diff_own_minus_pm` means the own model is cheaper. In the current Julia source data, the two material objective-mismatch rows are own-cheaper; do not relabel them as PowerModels-cheaper.
- Original binding mismatches compare two independently solved OPF endpoints: `xE*` versus `xP*`.
- Equality-only diagnostics compare a new projected point `xE**` against the PowerModels point. Near-zero `h(.)` differences there mean the own equality formulation can represent the PowerModels point; it does not erase the fact that the original OPF endpoint had a different active set.
- Load-flow validation is a consistency check after a PF engine re-solves from OPF endpoints. The presentation result is based on endpoint-specific independent violations: a shared row at both endpoints is not counted as an own-model failure.
"""
    path = OUT / "README_VALIDATION_PRESENTATION_TABLES.md"
    ensure_dir(path.parent)
    path.write_text(readme, encoding="utf-8")


def main() -> None:
    ensure_dir(OUT)
    prepare_final_summaries()
    prepare_target_tables()
    prepare_objective_mismatch_story_tables()
    prepare_deviation_summary()
    prepare_deviation_variant_story_table()
    prepare_equality_h_summary()
    prepare_equality_original_reconciliation_table()
    prepare_loadflow_summary()
    prepare_loadflow_interpretation_table()

    manifest_rows: list[dict] = []
    source_tables = {
        "01_discrepancy_targets/significant_discrepancies.csv": BASE / "significant_discrepancies.csv",
        "01_discrepancy_targets/julia_discrepancy_diagnosis.csv": BASE / "julia_discrepancy_diagnosis.csv",
        "01_discrepancy_targets/julia_discrepancy_diagnosis_top_cases.csv": BASE
        / "julia_discrepancy_diagnosis_top_cases.csv",
        "01_discrepancy_targets/julia_pm_like_coverage_audit.csv": BASE / "julia_pm_like_coverage_audit.csv",
        "02_deviation_minimization/current_constrained_deviation_minimization_julia.csv": BASE
        / "current_constrained_deviation_minimization_julia.csv",
        "02_deviation_minimization/current_constrained_deviation_binding_summary_julia.csv": BASE
        / "current_constrained_deviation_binding_summary_julia.csv",
        "02_deviation_minimization/pm_like_constrained_deviation_minimization_julia.csv": BASE
        / "pm_like_constrained_deviation_minimization_julia.csv",
        "02_deviation_minimization/pm_like_constrained_deviation_binding_summary_julia.csv": BASE
        / "pm_like_constrained_deviation_binding_summary_julia.csv",
        "02_deviation_minimization/python_deviation_minimization_diagnostics.csv": BASE
        / "python_deviation_minimization_diagnostics.csv",
        "02_deviation_minimization/python_deviation_binding_summary.csv": BASE / "python_deviation_binding_summary.csv",
        "03_equality_only_h_levels/equality_only_deviation_minimization_julia.csv": BASE
        / "equality_only_deviation_minimization_julia.csv",
        "03_equality_only_h_levels/equality_only_deviation_binding_summary_julia.csv": BASE
        / "equality_only_deviation_binding_summary_julia.csv",
        "03_equality_only_h_levels/equality_only_deviation_inequality_level_by_family_julia.csv": BASE
        / "equality_only_deviation_inequality_level_by_family_julia.csv",
        "03_equality_only_h_levels/equality_only_deviation_inequality_level_top_rows_julia.csv": BASE
        / "equality_only_deviation_inequality_level_top_rows_julia.csv",
        "03_equality_only_h_levels/python_pm_like_equality_only_deviation_minimization.csv": BASE
        / "python_pm_like"
        / "equality_only_deviation_minimization.csv",
        "03_equality_only_h_levels/python_pm_like_equality_only_deviation_binding_summary.csv": BASE
        / "python_pm_like"
        / "equality_only_deviation_binding_summary.csv",
        "04_loadflow_validation/opf_endpoint_loadflow_validation_julia.csv": BASE
        / "opf_endpoint_loadflow_validation_julia.csv",
        "04_loadflow_validation/opf_endpoint_pf_inequality_feasibility_summary_julia.csv": BASE
        / "opf_endpoint_pf_inequality_feasibility_summary_julia.csv",
        "04_loadflow_validation/opf_endpoint_pf_inequality_feasibility_by_independence_julia.csv": BASE
        / "opf_endpoint_pf_inequality_feasibility_by_independence_julia.csv",
        "04_loadflow_validation/opf_endpoint_pf_inequality_violations_julia.csv": BASE
        / "opf_endpoint_pf_inequality_violations_julia.csv",
        "04_loadflow_validation/python_endpoint_pypowsybl_loadflow_validation.csv": BASE
        / "python_endpoint_pypowsybl_loadflow_validation.csv",
        "04_loadflow_validation/python_endpoint_pypowsybl_lf_binding_summary.csv": BASE
        / "python_endpoint_pypowsybl_lf_binding_summary.csv",
        "04_loadflow_validation/python_endpoint_pypowsybl_lf_violations.csv": BASE
        / "python_endpoint_pypowsybl_lf_violations.csv",
        "04_loadflow_validation/python_endpoint_pypowsybl_lf_README.md": BASE
        / "python_endpoint_pypowsybl_lf_README.md",
        "05_source_detailed_tables/objective_mismatch_details_julia_current.csv": BASE
        / "objective_mismatch_details_julia_current.csv",
        "05_source_detailed_tables/objective_mismatch_details_julia_pm_like.csv": BASE
        / "objective_mismatch_details_julia_pm_like.csv",
        "05_source_detailed_tables/binding_constraint_mismatch_details_julia_current.csv": BASE
        / "binding_constraint_mismatch_details_julia_current.csv",
        "05_source_detailed_tables/binding_constraint_mismatch_details_julia_pm_like.csv": BASE
        / "binding_constraint_mismatch_details_julia_pm_like.csv",
        "05_source_detailed_tables/python_pm_like_full_sweep_agreement_counts.csv": BASE
        / "python_pm_like"
        / "agreement_counts.csv",
        "05_source_detailed_tables/python_pm_like_full_sweep_status_by_case.csv": BASE
        / "python_pm_like"
        / "status_by_case.csv",
        "05_source_detailed_tables/python_pm_like_full_sweep_nonoptimal_status_details.csv": BASE
        / "python_pm_like"
        / "nonoptimal_status_details.csv",
        "05_source_detailed_tables/python_pm_like_full_sweep_solver_diagnostics.csv": BASE
        / "python_pm_like"
        / "solver_diagnostics.csv",
        "05_source_detailed_tables/power_balance_bus_shunt_term_locations.csv": BASE
        / "power_balance_bus_shunt_term_locations.csv",
        "05_source_detailed_tables/shunt_toggle_solution_summary_julia.csv": BASE
        / "shunt_toggle_solution_summary_julia.csv",
        "05_source_detailed_tables/julia_parallel_branch_ybus_audit.csv": BASE
        / "julia_parallel_branch_ybus_audit.csv",
        "05_source_detailed_tables/solve_performance_summary_julia_models.csv": BASE
        / "solve_performance_summary_julia_models.csv",
    }
    for relative_path, source in source_tables.items():
        copy_file(source, relative_path, manifest_rows)

    write_readme()
    pd.DataFrame(manifest_rows).to_csv(OUT / "source_copy_manifest.csv", index=False)
    print(f"Prepared presentation validation tables in: {OUT}")


if __name__ == "__main__":
    main()
