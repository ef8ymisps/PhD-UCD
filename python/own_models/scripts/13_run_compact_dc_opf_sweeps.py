"""Run compact DC OPF validation sweeps on selected PGLib cases."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd


PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from own_models.src.analysis.dc_opf_sweeps import (  # noqa: E402
    DEFAULT_SETTINGS,
    run_compact_dc_validation,
)
from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat  # noqa: E402


DEFAULT_CASE_NAMES = [
    "pglib_opf_case5_pjm",
    "pglib_opf_case14_ieee",
    "pglib_opf_case30_ieee",
    "pglib_opf_case118_ieee",
    "pglib_opf_case300_ieee",
    "pglib_opf_case1354_pegase",
    "pglib_opf_case1888_rte",
    "pglib_opf_case6470_rte",
]


def case_path(case_name: str, *, family: str = "base_case") -> Path:
    return (
        REPO_ROOT
        / "data"
        / "raw"
        / "pglib"
        / "matpower_mat"
        / family
        / f"{case_name}.mat"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        nargs="*",
        default=DEFAULT_CASE_NAMES,
        help="PGLib case stems without .mat.",
    )
    parser.add_argument(
        "--family",
        default="base_case",
        help="Subdirectory under data/raw/pglib/matpower_mat.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "results" / "own_python" / "dc_opf_validation" / "compact_dc_opf_sweeps.csv",
    )
    parser.add_argument(
        "--include-no-reference-slack",
        action="store_true",
        help="Include the deliberately underdetermined no-reference-angle experiment.",
    )
    parser.add_argument(
        "--round-digits",
        type=int,
        default=2,
        help="Decimal places used only for the printed summary table.",
    )
    parser.add_argument(
        "--write-rounded",
        action="store_true",
        help="Round numeric columns in the written CSV as well as the printed table.",
    )
    parser.add_argument(
        "--strip-case-prefix",
        action="store_true",
        help="Remove the pglib_opf_case prefix from case_name values in printed/written results.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete the existing output before running. By default completed cases are skipped.",
    )
    args = parser.parse_args()

    paths = [case_path(name, family=args.family) for name in args.cases]
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing case files: " + ", ".join(str(path) for path in missing))

    settings = {
        **DEFAULT_SETTINGS,
        "include_no_reference_slack": args.include_no_reference_slack,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.overwrite and args.output.exists():
        args.output.unlink()

    completed_cases = _completed_cases(args.output)
    display_frames = []

    for path in paths:
        output_case_name = _output_case_name(path.stem, strip_case_prefix=args.strip_case_prefix)
        if output_case_name in completed_cases:
            print(f"Skipping {path.stem}; already present in {args.output}", flush=True)
            continue

        print(f"Running compact DC OPF sweeps for {path.stem}...", flush=True)
        case = load_powercase_from_matpower_mat(path)
        frame = pd.DataFrame(run_compact_dc_validation(case, settings=settings))
        if args.strip_case_prefix:
            frame = _strip_case_prefix_frame(frame)
        if args.write_rounded:
            frame = _rounded_display_frame(frame, args.round_digits)
        frame = _normalize_output_columns(frame)
        _append_csv(args.output, frame)
        display_frames.append(frame)
        completed_cases.add(output_case_name)
        print(f"Finished {path.stem}; checkpoint written to {args.output}", flush=True)

    summary = pd.read_csv(args.output) if args.output.exists() else pd.DataFrame()

    display_columns = [
        "case_name",
        "scenario",
        "target_id",
        "selection_reason",
        "loading_rank",
        "flow_rank",
        "cost_rank",
        "pg_rank",
        "parameter_name",
        "parameter_value",
        "success",
        "termination",
        "objective",
        "objective_delta_vs_base",
        "max_branch_loading_pct",
        "n_thermal_binding",
        "n_angle_binding",
        "solve_time_sec",
    ]
    for column in display_columns:
        if column not in summary:
            summary[column] = ""
    display_summary = _rounded_display_frame(summary[display_columns], args.round_digits)
    with pd.option_context("display.max_rows", None, "display.max_columns", None, "display.width", 220):
        print(display_summary.to_string(index=False))

    print(f"\nWrote compact DC OPF sweep summary to {args.output}")


def _rounded_display_frame(frame: pd.DataFrame, digits: int) -> pd.DataFrame:
    """Round numeric columns for terminal/notebook readability only."""
    rounded = frame.copy()
    numeric_columns = rounded.select_dtypes(include="number").columns
    rounded[numeric_columns] = rounded[numeric_columns].round(digits)
    return rounded


def _strip_case_prefix_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Remove the PGLib case prefix from case_name for compact displays."""
    cleaned = frame.copy()
    if "case_name" in cleaned:
        cleaned["case_name"] = (
            cleaned["case_name"]
            .astype(str)
            .str.replace("pglib_opf_case", "", regex=False)
        )
    return cleaned


def _output_case_name(case_stem: str, *, strip_case_prefix: bool) -> str:
    if strip_case_prefix:
        return case_stem.replace("pglib_opf_case", "")
    return case_stem


def _completed_cases(output_path: Path) -> set[str]:
    """Read only the case_name column from an existing checkpoint CSV."""
    if not output_path.exists():
        return set()
    try:
        return set(pd.read_csv(output_path, usecols=["case_name"])["case_name"].dropna().astype(str))
    except (FileNotFoundError, ValueError, pd.errors.EmptyDataError):
        return set()


def _append_csv(output_path: Path, frame: pd.DataFrame) -> None:
    """Append one completed case to a checkpoint CSV."""
    frame.to_csv(output_path, mode="a", header=not output_path.exists(), index=False)


def _normalize_output_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep append-mode CSV writes schema-stable across all scenarios."""
    columns = [
        "case_name",
        "n_buses",
        "n_branches",
        "n_generators",
        "scenario",
        "target_type",
        "target_id",
        "selection_reason",
        "loading_rank",
        "flow_rank",
        "cost_rank",
        "pg_rank",
        "parameter_name",
        "parameter_value",
        "success",
        "status",
        "termination",
        "objective",
        "objective_delta_vs_base",
        "total_generation",
        "total_load",
        "max_balance_residual",
        "max_branch_loading_pct",
        "n_thermal_binding",
        "n_angle_binding",
        "n_gen_at_pmin",
        "n_gen_at_pmax",
        "branch_limit_constraints",
        "angle_limit_constraints",
        "solve_time_sec",
        "message",
        "branch_id",
        "base_flow_pu",
        "rate_a_pu",
        "base_loading_pct",
        "gen_id",
        "bus_id",
        "base_pg_pu",
        "pmax_pu",
        "cost_c1",
        "cost_c2",
    ]
    for column in columns:
        if column not in frame:
            frame[column] = pd.NA
    return frame[columns]


if __name__ == "__main__":
    main()
