"""Merge compact Python AC OPF sweeps with PowerModels reference results."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd


PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from own_models.src.analysis.ac_opf_cross_model_export import (  # noqa: E402
    SCENARIO_KEY_COLUMNS,
    compact_ac_reference_scenario_id,
)


SOLVED_STATUSES = {"optimal", "locally_solved", "almost_optimal", "almost_locally_solved"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--compact-sweep",
        type=Path,
        default=REPO_ROOT / "results" / "own_python" / "ac_opf_validation" / "compact_ac_opf_sweeps.csv",
    )
    parser.add_argument(
        "--pm-summary",
        type=Path,
        default=REPO_ROOT
        / "results"
        / "reference"
        / "powermodels"
        / "ac_opf_compact_sweeps"
        / "powermodels_ac_opf_summary.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT
        / "results"
        / "own_python"
        / "ac_opf_validation"
        / "compact_ac_opf_sweeps_powermodels_comparison.csv",
    )
    parser.add_argument("--round-digits", type=int, default=6)
    parser.add_argument(
        "--include-no-reference-slack",
        action="store_true",
        help=(
            "Include slack_without_reference rows in the merged comparison. "
            "These rows are diagnostic only because PowerModels is exported as an ordinary referenced case."
        ),
    )
    args = parser.parse_args()

    own = pd.read_csv(args.compact_sweep, dtype=str, keep_default_na=False)
    pm = pd.read_csv(args.pm_summary, dtype=str, keep_default_na=False)

    own["scenario_id"] = own.apply(lambda row: compact_ac_reference_scenario_id(row.to_dict()), axis=1)
    if not args.include_no_reference_slack:
        own = own[own["scenario"] != "slack_without_reference"].copy()
    own = own.drop(
        columns=[
            "pm_status",
            "pm_objective",
            "objective_diff_vs_pm",
            "solve_time_diff_vs_pm",
            "solve_time_ratio_vs_pm",
        ],
        errors="ignore",
    )

    merged = own.merge(
        pm[
            [
                *SCENARIO_KEY_COLUMNS,
                "scenario_id",
                "pm_status",
                "pm_solved",
                "pm_objective",
                "pm_solve_time_sec",
                "pm_error",
            ]
        ],
        on=[*SCENARIO_KEY_COLUMNS, "scenario_id"],
        how="left",
        suffixes=("", "_reference"),
    )

    merged["own_solved"] = merged["success"].map(_bool_text)
    merged["pm_solved_bool"] = merged["pm_solved"].map(_bool_text)
    merged["outcome_match"] = merged["own_solved"] == merged["pm_solved_bool"]
    missing_pm = merged["pm_status"].isna() | merged["pm_status"].astype(str).eq("")
    merged.loc[missing_pm, "outcome_match"] = False
    merged["objective"] = pd.to_numeric(merged["objective"], errors="coerce")
    merged["pm_objective"] = pd.to_numeric(merged["pm_objective"], errors="coerce")
    merged["objective_diff_vs_pm"] = merged["objective"] - merged["pm_objective"]
    merged["objective_rel_diff_vs_pm"] = merged["objective_diff_vs_pm"].abs() / (1.0 + merged["pm_objective"].abs())
    merged["solve_time_sec"] = pd.to_numeric(merged["solve_time_sec"], errors="coerce")
    merged["pm_solve_time_sec"] = pd.to_numeric(merged["pm_solve_time_sec"], errors="coerce")
    merged["solve_time_diff_vs_pm"] = merged["solve_time_sec"] - merged["pm_solve_time_sec"]
    merged["solve_time_ratio_vs_pm"] = merged["solve_time_sec"] / merged["pm_solve_time_sec"]

    display_columns = [
        "case_name",
        "scenario",
        "target_id",
        "parameter_name",
        "parameter_value",
        "success",
        "status",
        "pm_status",
        "outcome_match",
        "objective",
        "pm_objective",
        "objective_diff_vs_pm",
        "objective_rel_diff_vs_pm",
        "solve_time_sec",
        "pm_solve_time_sec",
        "solve_time_diff_vs_pm",
        "solve_time_ratio_vs_pm",
        "pm_error",
    ]
    for column in display_columns:
        if column not in merged:
            merged[column] = ""

    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = merged.copy()
    numeric_columns = output.select_dtypes(include="number").columns
    output[numeric_columns] = output[numeric_columns].round(args.round_digits)
    output.to_csv(args.output, index=False)

    print(f"Wrote AC OPF PowerModels comparison to {args.output}")
    print(f"Rows compared: {len(output)}")
    print(f"Missing PowerModels rows: {int(missing_pm.sum())}")
    print(f"Outcome matches: {int(output['outcome_match'].sum())}")
    with pd.option_context("display.max_rows", 40, "display.max_columns", None, "display.width", 220):
        print(output[display_columns].head(40).to_string(index=False))


def _bool_text(value) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in SOLVED_STATUSES:
        return True
    return False


if __name__ == "__main__":
    main()
