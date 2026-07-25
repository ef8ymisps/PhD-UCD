"""Run compact AC OPF validation sweeps on selected PGLib cases."""

from __future__ import annotations

import argparse
from dataclasses import replace
import logging
from pathlib import Path
import sys

import pandas as pd


PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat  # noqa: E402
from own_models.src.analysis.ac_opf_sweeps import (  # noqa: E402
    DEFAULT_SETTINGS,
    compact_ac_scenario_key,
    iter_compact_ac_validation,
)


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
    """Return the MATPOWER ``.mat`` path for a PGLib case stem."""
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
    parser.add_argument("--cases", nargs="*", default=DEFAULT_CASE_NAMES, help="PGLib case stems without .mat.")
    parser.add_argument("--family", default="base_case", help="Subdirectory under data/raw/pglib/matpower_mat.")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "results" / "own_python" / "ac_opf_validation" / "compact_ac_opf_sweeps.csv",
    )
    parser.add_argument(
        "--include-no-reference-slack",
        action="store_true",
        help="Include the deliberately underdetermined no-reference-angle experiment.",
    )
    parser.add_argument("--round-digits", type=int, default=2, help="Decimal places used for displayed results.")
    parser.add_argument("--write-rounded", action="store_true", help="Round numeric columns in the written CSV.")
    parser.add_argument(
        "--strip-case-prefix",
        action="store_true",
        help="Remove the pglib_opf_case prefix from case_name values.",
    )
    parser.add_argument(
        "--show-pyomo-warnings",
        action="store_true",
        help="Show Pyomo warning-status load messages for infeasible stress scenarios.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete the existing output before running. By default completed cases are skipped.",
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=None,
        help="Override Ipopt max_iter for this sweep run.",
    )
    parser.add_argument(
        "--model-variant",
        choices=["pm_like_zero"],
        default="pm_like_zero",
        help="AC OPF implementation to run. 'pm_like_zero' is the sole AC-OPF model "
        "(bus-injection/'current' was deleted).",
    )
    parser.add_argument(
        "--warm-start",
        default=None,
        help="Override AC OPF warm-start mode. Defaults to zero for pm_like_zero and existing settings otherwise.",
    )
    parser.add_argument(
        "--only-scenario-ids",
        nargs="*",
        default=None,
        help="Run only the listed compact scenario ids. The base case is still solved internally for selections.",
    )
    args = parser.parse_args()
    if not args.show_pyomo_warnings:
        _suppress_pyomo_warning_status_logs()

    paths = [case_path(name, family=args.family) for name in args.cases]
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing case files: " + ", ".join(str(path) for path in missing))

    settings = {
        **DEFAULT_SETTINGS,
        "include_no_reference_slack": args.include_no_reference_slack,
        "model_variant": args.model_variant,
    }
    if args.model_variant == "pm_like_zero":
        settings["model_variant"] = "pm_like"
        settings["warm_start"] = "zero"
    if args.warm_start is not None:
        settings["warm_start"] = args.warm_start
    if args.max_iter is not None:
        settings["solver_options"] = {
            **dict(DEFAULT_SETTINGS.get("solver_options", {})),
            "max_iter": int(args.max_iter),
        }
    if args.only_scenario_ids:
        settings["scenario_filter_ids"] = set(args.only_scenario_ids)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.overwrite and args.output.exists():
        args.output.unlink()

    completed_scenario_keys = _completed_scenario_keys(args.output)

    for path in paths:
        output_case_name = _output_case_name(path.stem, strip_case_prefix=args.strip_case_prefix)

        print(f"Running compact AC OPF sweeps for {path.stem}...", flush=True)
        case = load_powercase_from_matpower_mat(path)
        if args.strip_case_prefix:
            case = replace(case, name=output_case_name)

        n_written = 0
        for row in iter_compact_ac_validation(
            case,
            settings=settings,
            completed_scenario_keys=completed_scenario_keys,
        ):
            frame = pd.DataFrame([row])
            if args.write_rounded:
                frame = _rounded_display_frame(frame, args.round_digits)
            frame = _normalize_output_columns(frame)
            _append_csv(args.output, frame)
            completed_scenario_keys.add(compact_ac_scenario_key(frame.iloc[0].to_dict()))
            n_written += 1
            print(
                "  checkpointed "
                f"{frame.iloc[0]['case_name']} | {frame.iloc[0]['scenario']} | "
                f"{frame.iloc[0]['target_id']} | {frame.iloc[0]['parameter_name']}="
                f"{frame.iloc[0]['parameter_value']}",
                flush=True,
            )

        if n_written == 0:
            print(f"  no new scenarios for {path.stem}; all matching rows already checkpointed", flush=True)
        else:
            print(f"Finished {path.stem}; wrote {n_written} scenario checkpoints to {args.output}", flush=True)

    summary = pd.read_csv(args.output) if args.output.exists() else pd.DataFrame()
    display_columns = [
        "case_name",
        "scenario",
        "target_id",
        "selection_reason",
        "parameter_name",
        "parameter_value",
        "success",
        "termination",
        "objective",
        "objective_delta_vs_base",
        "min_vm",
        "max_vm",
        "max_branch_loading_pct",
        "n_thermal_binding",
        "n_qg_at_qmax",
        "solve_time_sec",
    ]
    for column in display_columns:
        if column not in summary:
            summary[column] = ""
    display_summary = _rounded_display_frame(summary[display_columns], args.round_digits)
    with pd.option_context("display.max_rows", None, "display.max_columns", None, "display.width", 240):
        print(display_summary.to_string(index=False))

    print(f"\nWrote compact AC OPF sweep summary to {args.output}")


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
        cleaned["case_name"] = cleaned["case_name"].astype(str).str.replace("pglib_opf_case", "", regex=False)
    return cleaned


def _output_case_name(case_stem: str, *, strip_case_prefix: bool) -> str:
    if strip_case_prefix:
        return case_stem.replace("pglib_opf_case", "")
    return case_stem


def _completed_scenario_keys(output_path: Path) -> set[tuple[str, str, str, str, str, str]]:
    """Read completed scenario-level checkpoint keys from an existing CSV."""
    if not output_path.exists():
        return set()
    try:
        frame = pd.read_csv(
            output_path,
            usecols=["case_name", "scenario", "target_type", "target_id", "parameter_name", "parameter_value"],
            dtype=str,
            keep_default_na=False,
        )
        return {compact_ac_scenario_key(row) for row in frame.to_dict(orient="records")}
    except (FileNotFoundError, ValueError, pd.errors.EmptyDataError):
        return set()


def _append_csv(output_path: Path, frame: pd.DataFrame) -> None:
    """Append one scenario checkpoint using the existing CSV header order."""
    write_header = not output_path.exists()
    if not write_header:
        existing_columns = list(pd.read_csv(output_path, nrows=0).columns)
        frame = frame.reindex(columns=existing_columns)
    frame.to_csv(output_path, mode="a", header=write_header, index=False)


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
        "parameter_name",
        "parameter_value",
        "success",
        "status",
        "termination",
        "objective",
        "objective_delta_vs_base",
        "total_pg",
        "total_qg",
        "total_pd",
        "total_qd",
        "min_vm",
        "max_vm",
        "max_p_balance_violation",
        "max_q_balance_violation",
        "max_voltage_violation",
        "max_thermal_violation",
        "max_angle_violation",
        "max_branch_loading_pct",
        "n_thermal_binding",
        "n_angle_binding",
        "n_gen_at_pmin",
        "n_gen_at_pmax",
        "n_qg_at_qmin",
        "n_qg_at_qmax",
        "loadability_factor",
        "total_load_curtailment",
        "warm_start_source",
        "solve_time_sec",
        "message",
        "expected_observation",
        "observation_note",
        "pm_status",
        "pm_objective",
        "objective_diff_vs_pm",
        "branch_id",
        "base_flow_pu",
        "rate_a_pu",
        "base_loading_pct",
        "selection_reason",
        "loading_rank",
        "flow_rank",
        "gen_id",
        "bus_id",
        "base_pg_pu",
        "pmax_pu",
        "cost_c1",
        "cost_c2",
        "cost_rank",
        "pg_rank",
    ]
    for column in columns:
        if column not in frame:
            frame[column] = pd.NA
    return frame[columns]


class _PyomoWarningStatusFilter(logging.Filter):
    """Hide expected Pyomo warning-status messages in validation sweeps."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "Loading a SolverResults object with a warning status" not in record.getMessage()


def _suppress_pyomo_warning_status_logs() -> None:
    """Suppress noisy Pyomo messages for expected infeasible stress cases."""
    warning_filter = _PyomoWarningStatusFilter()
    for logger_name in ("pyomo.core", "pyomo"):
        logger = logging.getLogger(logger_name)
        logger.addFilter(warning_filter)
        for handler in logger.handlers:
            handler.addFilter(warning_filter)


if __name__ == "__main__":
    main()
