"""Plot per-variable diffs, solver iterations, and binding-set agreement.

Unlike 17_plot_opf_validation_findings.py (objective/solve-time/outcome only),
this script surfaces what task 4/5 of the OPF validation plan actually asked
for: how close Pg/Qg/Vm/Va/branch-flow-loading are between the own model and
PowerModels, whether the two models agree on which constraints bind, and
(new) the true per-element distribution of diffs, not just per-scenario means.

Data sources:
- Python: the per-scenario case/reference JSON pairs already on disk under
  results/reference/powermodels/{ac_opf,dc_opf}/ (own model solved fresh here
  since those JSON pairs carry full PowerModels bus/gen/branch solutions).
  Per-element rows are classified (binding_class/mismatch_reason) and
  persisted to results/own_python/opf_validation_sweeps/detail_rows/, mirroring
  Julia's schema for cross-language joinability.
- Julia: results/own_julia/opf_validation_sweeps/compact_opf_validation_summary.csv
  for the mean/max/std/binding-set summary (full stress-sweep coverage), and
  results/own_julia/opf_validation_sweeps/detail_rows/*.csv for true
  per-element distributions where 09_run_compact_opf_validation_sweeps.jl was
  run with --write-detail-rows (skipped with a note otherwise).

Python's compact multi-thousand-scenario sweep
(compact_ac_opf_sweeps_with_no_ref_powermodels_comparison.csv) is NOT used
here: that pipeline merges against a scalar-only PowerModels summary CSV
(objective/status/solve_time, no per-element solution), so per-variable diffs
are not available there without a further PowerModels export-pipeline change.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import re
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
REPO_ROOT = PYTHON_DIR.parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.analysis.compare_ac_with_powermodels import (  # noqa: E402
    run_python_ac_opf_against_powermodels,
)
from own_models.src.analysis.compare_with_powermodels import (  # noqa: E402
    run_python_dc_opf_against_powermodels,
)
from own_models.src.analysis.violations import (  # noqa: E402
    ac_branch_flow_convention_check,
    classify_ac_branch_rows,
    classify_ac_bus_rows,
    classify_ac_generator_rows,
    classify_dc_branch_rows,
    classify_dc_generator_rows,
)


def _load_script17():
    """Load 17_plot_opf_validation_findings.py as a module via importlib
    (rather than duplicating its classify_rows/plot_outcome_heatmap/
    plot_objective_scatter/plot_solve_time_by_case/make_plot_set logic here)
    -- a normal `import` isn't possible since the filename starts with a
    digit."""
    spec = importlib.util.spec_from_file_location(
        "script17_plot_opf_validation_findings", SCRIPT_DIR / "17_plot_opf_validation_findings.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script17 = _load_script17()

CASES = ["pglib_opf_case5_pjm", "pglib_opf_case14_ieee", "pglib_opf_case30_ieee"]
SCENARIOS = ["base", "relaxed", "branch_congested", "generator_limited", "load_scaled"]

AC_VARIABLE_SOURCES = [
    ("pg", "generators", "abs_pg_diff"),
    ("qg", "generators", "abs_qg_diff"),
    ("vm", "buses", "abs_vm_diff"),
    ("va", "buses", "abs_va_diff"),
    ("pf", "branches", "abs_pf_diff"),
    ("qf", "branches", "abs_qf_diff"),
    ("loading_pct", "branches", "abs_loading_pct_diff"),
]
DC_VARIABLE_SOURCES = [
    ("pg", "generators", "abs_pg_diff"),
    ("va", "buses", "abs_va_diff"),
    ("pf", "branches", "abs_pf_diff"),
    ("loading_pct", "branches", "abs_loading_pct_diff"),
]

BINDING_TYPES = [
    "pmin", "pmax", "qmin", "qmax", "vmin", "vmax", "thermal", "angle_lower", "angle_upper"
]

# Short-form case order shared with 17_plot_opf_validation_findings.py's CASE_ORDER,
# duplicated here (rather than imported) because that module's filename starts with
# a digit and isn't importable as a normal Python module.
CASE_ORDER = [
    "5_pjm",
    "14_ieee",
    "30_ieee",
    "118_ieee",
    "300_ieee",
    "1354_pegase",
    "1888_rte",
    "6470_rte",
]

VARIABLE_UNITS_NOTE = (
    "pg, qg, vm, pf, qf in per-unit; va in radians; "
    "loading_pct in percentage points (branch |flow| / thermal rating x 100)"
)


def shorten_case_name(case_name: object) -> str:
    """'pglib_opf_case14_ieee' -> '14_ieee'; already-short names pass through unchanged."""
    return re.sub(r"^pglib_opf_case", "", str(case_name))


def ordered_present(values: pd.Series, preferred: list[str]) -> list[str]:
    present = set(values.dropna().astype(str))
    ordered = [item for item in preferred if item in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=REPO_ROOT / "results" / "reference" / "powermodels",
    )
    parser.add_argument(
        "--julia-summary",
        type=Path,
        default=REPO_ROOT / "results" / "own_julia" / "opf_validation_sweeps" / "compact_opf_validation_summary.csv",
    )
    parser.add_argument(
        "--julia-detail-dir",
        type=Path,
        default=REPO_ROOT / "results" / "own_julia" / "opf_validation_sweeps" / "detail_rows",
    )
    parser.add_argument(
        "--python-detail-dir",
        type=Path,
        default=REPO_ROOT / "results" / "own_python" / "opf_validation_sweeps" / "detail_rows",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "results" / "presentable" / "opf_validation_findings",
    )
    parser.add_argument("--dpi", type=int, default=220)
    return parser.parse_args()


def _append_csv(path: Path, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, mode="a", header=not path.exists(), index=False)


def _reset_detail_files(detail_dir: Path, model: str) -> None:
    """collect_python_summaries deterministically regenerates every
    case/scenario combo from scratch on each run, but _persist_detail_rows
    only ever appends -- so re-running this script N times left N stacked
    copies of every row in results/own_python/.../detail_rows/*.csv (found:
    560 rows for 80 unique case/scenario/gen_id combos, a 7x duplication after
    repeated runs this session). Delete this model's detail files at the start
    of a run so each run starts clean instead of accumulating duplicates."""
    kinds = ("generators", "buses", "branches") if model == "ac" else ("generators", "buses", "branches")
    for kind in kinds:
        path = detail_dir / f"{model}_{kind}.csv"
        path.unlink(missing_ok=True)
    if model == "ac":
        (detail_dir / "ac_branch_flow_convention_check.csv").unlink(missing_ok=True)


def collect_python_summaries(reference_dir: Path, detail_dir: Path, model: str) -> pd.DataFrame:
    subdir = reference_dir / f"{model}_opf"
    _reset_detail_files(detail_dir, model)
    rows = []
    for case in CASES:
        for scenario in SCENARIOS:
            case_path = subdir / f"{case}_{scenario}_case.json"
            ref_path = subdir / f"{case}_{scenario}_reference.json"
            if not case_path.is_file() or not ref_path.is_file():
                continue
            try:
                if model == "ac":
                    result = run_python_ac_opf_against_powermodels(case_path, ref_path, scenario=scenario)
                else:
                    result = run_python_dc_opf_against_powermodels(case_path, ref_path, scenario=scenario)
            except Exception as exc:  # noqa: BLE001 - keep going, record the failure
                rows.append({"case_name": case, "scenario": scenario, "model": model, "error": str(exc)})
                continue
            summary = result["summary"].copy()
            summary["model"] = model
            summary_row = summary.iloc[0].to_dict()
            rows.append(summary_row)

            # Only persist per-element rows when both sides actually solved.
            # Detail rows were previously written unconditionally, so a
            # doubly-infeasible scenario (both own and PM landed on garbage,
            # non-converged solver states) would still get "own vs PM" element
            # diffs computed and stored -- comparing two invalid points as if
            # they were a real disagreement. Confirmed on case30_ieee's
            # generator_limited scenario: own_status="infeasible",
            # pm_status="LOCALLY_INFEASIBLE", yet detail rows showed a 10.7
            # percentage-point loading_pct "diff" that meant nothing. Julia's
            # equivalent write path already gates on both sides being solved;
            # this brings Python's in line with that.
            if bool(summary_row.get("own_success")) and bool(summary_row.get("pm_success")):
                _persist_detail_rows(detail_dir, model, case, scenario, result)
    return pd.DataFrame(rows)


def _persist_detail_rows(detail_dir: Path, model: str, case: str, scenario: str, result: dict) -> None:
    def _tag(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        frame = frame.copy()
        frame.insert(0, "scenario", scenario)
        frame.insert(0, "case_name", case)
        return frame

    generator_rows = _tag(result.get("generators", pd.DataFrame()))
    branch_rows = _tag(result.get("branches", pd.DataFrame()))

    if model == "ac":
        bus_rows = _tag(result.get("buses", pd.DataFrame()))
        generator_rows = classify_ac_generator_rows(generator_rows)
        bus_rows = classify_ac_bus_rows(bus_rows)
        branch_rows = classify_ac_branch_rows(branch_rows)
        _append_csv(detail_dir / "ac_generators.csv", generator_rows)
        _append_csv(detail_dir / "ac_buses.csv", bus_rows)
        _append_csv(detail_dir / "ac_branches.csv", branch_rows)
        convention_rows = ac_branch_flow_convention_check(branch_rows)
        if not convention_rows.empty:
            convention_rows = convention_rows.copy()
            convention_rows.insert(0, "scenario", scenario)
            convention_rows.insert(0, "case_name", case)
        _append_csv(detail_dir / "ac_branch_flow_convention_check.csv", convention_rows)
    else:
        bus_rows = _tag(result.get("buses", pd.DataFrame()))
        generator_rows = classify_dc_generator_rows(generator_rows)
        branch_rows = classify_dc_branch_rows(branch_rows)
        _append_csv(detail_dir / "dc_generators.csv", generator_rows)
        _append_csv(detail_dir / "dc_buses.csv", bus_rows)
        _append_csv(detail_dir / "dc_branches.csv", branch_rows)


# Matches 17_plot_opf_validation_findings.py's own default, kept in sync
# manually since the two scripts don't share a config module.
OBJECTIVE_GAP_THRESHOLD = 1e-3


def normalize_python_dc_small_scale(frame: pd.DataFrame, objective_gap_threshold: float = OBJECTIVE_GAP_THRESHOLD) -> pd.DataFrame:
    """Adapt collect_python_summaries's DC frame (3 cases x 5 scenarios) to
    the schema script17's classify_rows/plot_outcome_heatmap/
    plot_objective_scatter/plot_solve_time_by_case expect. Python DC has no
    large-scale PowerModels-compared sweep CSV the way AC and Julia do (see
    module docstring) -- this is that small validation set standing in, so
    the resulting plots are labelled accordingly rather than implying
    Julia-sweep-scale coverage."""
    work = frame.copy()
    work["base_case"] = work["case_name"].map(shorten_case_name)
    work["model"] = "dc_python"
    work["own_status"] = work["own_status"].astype(str)
    work["pm_status"] = work["pm_status"].astype(str)
    work["own_solved_norm"] = work["own_success"].astype(bool)
    work["pm_solved_norm"] = work["pm_success"].astype(bool)
    work["outcome_match_norm"] = work["outcome_match"].astype(bool)
    work["own_objective_norm"] = pd.to_numeric(work["own_objective"], errors="coerce")
    work["pm_objective_norm"] = pd.to_numeric(work["pm_objective"], errors="coerce")
    work["objective_rel_diff_norm"] = pd.to_numeric(work["objective_rel_diff"], errors="coerce").abs()
    work["own_solve_time_norm"] = pd.to_numeric(work.get("own_solve_time_sec"), errors="coerce")
    work["pm_solve_time_norm"] = pd.to_numeric(work.get("pm_solve_time_sec"), errors="coerce")
    return script17.classify_rows(work, objective_gap_threshold)


def load_julia_summary(path: Path) -> pd.DataFrame | None:
    if not path.is_file():
        return None
    frame = pd.read_csv(path)
    if "mean_pg_diff" not in frame.columns:
        print(
            f"NOTE: {path} predates the mean/std/binding-set schema "
            "(re-run 09_run_compact_opf_validation_sweeps.jl with --overwrite to refresh it); "
            "skipping Julia panels."
        )
        return None
    return frame


def load_detail_tables(detail_dir: Path, model: str) -> dict[str, pd.DataFrame]:
    tables = {}
    for kind in ("generators", "buses", "branches"):
        path = detail_dir / f"{model}_{kind}.csv"
        if path.is_file():
            tables[kind] = pd.read_csv(path)
    return tables


def build_case_variable_diff_table(
    frame: pd.DataFrame, variables: list[str], case_col: str, stat: str = "mean"
) -> pd.DataFrame:
    """One row per case, one column per variable, built from the per-scenario
    mean_{var}_diff / max_{var}_diff columns (available for every case, unlike
    the sparse per-element detail rows).

    stat="mean": mean-of-per-scenario-means -- the case's typical diff.
    stat="max": max-of-per-scenario-maxes -- the single worst element diff seen
    anywhere in that case, across all its scenarios.
    """
    if stat not in ("mean", "max"):
        raise ValueError(f"Unsupported stat: {stat!r}")
    source_prefix = "mean_" if stat == "mean" else "max_"
    work = frame.copy()
    work["_case_short"] = work[case_col].map(shorten_case_name)
    rows = []
    for case, group in work.groupby("_case_short"):
        row = {"case": case}
        for var in variables:
            col = f"{source_prefix}{var}_diff"
            row[var] = group[col].agg(stat) if col in group.columns else np.nan
        rows.append(row)
    table = pd.DataFrame(rows).set_index("case")
    table = table.reindex(ordered_present(pd.Series(table.index), CASE_ORDER))
    return table


def _format_diff_cell(value: float) -> str:
    if pd.isna(value):
        return "—"
    if value == 0:
        return "0"
    return f"{value:.2e}"


def plot_variable_diff_table(
    frame: pd.DataFrame,
    variables: list[str],
    case_col: str,
    title: str,
    output_path: Path,
    dpi: int,
    stat: str = "mean",
) -> None:
    """Table of |own - PowerModels| diff, case (rows) x variable (columns).
    Replaces the old per-variable box plot, which pooled every case together and
    hid which case actually drove each variable's spread. stat="mean" shows each
    case's typical diff; stat="max" shows its single worst element diff."""
    table = build_case_variable_diff_table(frame, variables, case_col, stat=stat)
    if table.empty or table.isna().all(axis=None):
        return

    cell_text = [[_format_diff_cell(v) for v in row] for row in table.to_numpy()]
    fig_width = max(7.5, 1.35 * len(variables) + 2.2)
    fig_height = max(2.6, 0.5 * len(table) + 1.9)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis("off")
    tbl = ax.table(
        cellText=cell_text,
        rowLabels=table.index.tolist(),
        colLabels=table.columns.tolist(),
        cellLoc="center",
        rowLoc="center",
        loc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    tbl.scale(1.0, 1.65)
    for (row_idx, _), cell in tbl.get_celld().items():
        if row_idx == 0:
            cell.set_facecolor("#e7e9ee")
            cell.set_text_props(fontweight="bold")
    ax.set_title(title, fontsize=14, pad=16)
    fig.text(0.5, 0.01, VARIABLE_UNITS_NOTE, ha="center", fontsize=8, color="#6b7280")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    table.to_csv(output_path.with_suffix(".csv"))


def _instance_labels(frame: pd.DataFrame) -> pd.Series:
    """Display label per row for a scenario instance. Julia's case_name
    already uniquely encodes the scenario instance (e.g. "1888_rte_load_x0.8"
    is one specific scenario); Python's case_name is just the base case with a
    separate "scenario" column distinguishing instances (e.g. "base",
    "branch_congested"), so case_name alone silently pools every one of a
    case's scenarios together there (confirmed: this previously collapsed
    Python's 15 case/scenario detail-row combos down to 3 case-only rows).
    Disambiguate only where case_name is actually ambiguous."""
    case_name = frame["case_name"].astype(str)
    if "scenario" not in frame.columns:
        return case_name
    scenario = frame["scenario"].astype(str)
    ambiguous = frame.groupby("case_name")["scenario"].transform("nunique") > 1
    return case_name.where(~ambiguous, case_name + " (" + scenario + ")")


def build_element_diff_table(
    tables: dict[str, pd.DataFrame], variable_sources: list[tuple[str, str, str | None]]
) -> pd.DataFrame:
    """Long-format mean/max |own - PowerModels| per element, one row per
    (scenario instance, variable). Source is the raw per-element detail rows,
    which only exist for a sparse spot-check sample of scenarios (see module
    docstring) -- so a row here is an individual scenario instance
    (e.g. "1888_rte_load_x0.8", or "pglib_opf_case14_ieee (branch_congested)"
    for Python's case+scenario-column data), not the full 8-case set."""
    rows = []
    for var, source, diff_col in variable_sources:
        if diff_col is None or source not in tables:
            continue
        frame = tables[source]
        if diff_col not in frame.columns or "case_name" not in frame.columns:
            continue
        frame = frame.copy()
        frame["_instance"] = _instance_labels(frame)
        if "scenario" not in frame.columns:
            frame["scenario"] = frame["case_name"]
        for (instance, case_name, scenario), group in frame.groupby(["_instance", "case_name", "scenario"]):
            values = group[diff_col].dropna()
            values = values[np.isfinite(values)]
            if values.empty:
                continue
            rows.append(
                {
                    "instance_label": instance,
                    "case_name": case_name,
                    "scenario": scenario,
                    "variable": var,
                    "n_elements": int(len(values)),
                    "mean_abs_diff": values.mean(),
                    "max_abs_diff": values.max(),
                }
            )
    return pd.DataFrame(rows)


def plot_element_diff_table(
    tables: dict[str, pd.DataFrame],
    variable_sources: list[tuple[str, str, str | None]],
    title: str,
    output_path: Path,
    dpi: int,
) -> None:
    """Table of per-element mean (max) |own - PowerModels| diff: scenario
    instance (rows) x variable (columns). Replaces the old box-plot grid, whose
    x-axis labels were confusingly not full cases but individual sampled
    scenario instances -- the table makes that spot-check nature explicit via
    the caption instead of implying full-case coverage."""
    long_table = build_element_diff_table(tables, variable_sources)
    if long_table.empty:
        return

    cases = sorted(long_table["instance_label"].unique())
    variables = [v for v, _, d in variable_sources if d is not None]
    mean_pivot = long_table.pivot(index="instance_label", columns="variable", values="mean_abs_diff").reindex(
        index=cases, columns=variables
    )
    max_pivot = long_table.pivot(index="instance_label", columns="variable", values="max_abs_diff").reindex(
        index=cases, columns=variables
    )

    def format_cell(case: str, var: str) -> str:
        mean_v = mean_pivot.loc[case, var]
        if pd.isna(mean_v):
            return "—"
        max_v = max_pivot.loc[case, var]
        return f"{mean_v:.2e}\n(max {max_v:.2e})"

    cell_text = [[format_cell(case, var) for var in variables] for case in cases]
    fig_width = max(8.0, 1.6 * len(variables) + 2.6)
    fig_height = max(3.0, 0.95 * len(cases) + 2.1)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis("off")
    tbl = ax.table(
        cellText=cell_text,
        rowLabels=cases,
        colLabels=variables,
        cellLoc="center",
        rowLoc="center",
        loc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1.0, 2.3)
    for (row_idx, _), cell in tbl.get_celld().items():
        if row_idx == 0:
            cell.set_facecolor("#e7e9ee")
            cell.set_text_props(fontweight="bold")
    ax.set_title(title, fontsize=14, pad=16)
    fig.text(
        0.5,
        0.01,
        "Spot-check sample only -- rows are individual scenario instances with\n"
        "per-element detail data available, not the full case set. " + VARIABLE_UNITS_NOTE,
        ha="center",
        fontsize=7.5,
        color="#6b7280",
    )
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    long_table.to_csv(output_path.with_suffix(".csv"), index=False)


# A scenario instance is "spotlight-worthy" if its single worst element diff
# clears these thresholds -- set well above the ~1e-3-and-below noise floor
# seen in well-behaved scenarios (e.g. 6470_rte base, 1354_pegase_qg_limits)
# and well below the genuine outliers found in 1888_rte (diffs of O(1) pu),
# so it separates the two cleanly without hand-picking scenario names.
SPOTLIGHT_PU_DIFF_THRESHOLD = 0.05
SPOTLIGHT_LOADING_PCT_THRESHOLD = 1.0

SPOTLIGHT_PANELS = [
    ("Pg (generators)", "generators", "gen_id", "own_pg", "pm_pg", "abs_pg_diff"),
    ("Qg (generators)", "generators", "gen_id", "own_qg", "pm_qg", "abs_qg_diff"),
    ("Branch flow pf", "branches", "branch_id", "own_pf", "pm_pf", "abs_pf_diff"),
    ("Vm (buses)", "buses", "bus_id", "own_vm", "pm_vm", "abs_vm_diff"),
]


def find_spotlight_scenarios(long_table: pd.DataFrame) -> list[tuple[str, str]]:
    """From build_element_diff_table's long-format output, flag scenario
    instances (case_name, scenario) whose worst single-element diff is large
    enough to warrant a dedicated drill-down chart, rather than hand-picking a
    case by name. Keying on the (case_name, scenario) pair -- not case_name
    alone -- matters for Python's data, where case_name is just the base case
    and scenario is the real per-instance differentiator."""
    if long_table.empty:
        return []
    severe = np.where(
        long_table["variable"] == "loading_pct",
        long_table["max_abs_diff"] > SPOTLIGHT_LOADING_PCT_THRESHOLD,
        long_table["max_abs_diff"] > SPOTLIGHT_PU_DIFF_THRESHOLD,
    )
    pairs = long_table.loc[severe, ["case_name", "scenario"]].drop_duplicates()
    return sorted(pairs.itertuples(index=False, name=None))


def plot_scenario_spotlight(
    tables: dict[str, pd.DataFrame],
    case_name: str,
    scenario: str,
    summary_frame: pd.DataFrame,
    title_prefix: str,
    output_path: Path,
    dpi: int,
    top_n: int = 10,
) -> None:
    """Drill-down for one flagged scenario instance: grouped own-vs-PowerModels
    bars (Pg, Qg, branch flow, Vm) for its top-N most-diverging elements per
    variable, with the scenario's objective_rel_diff called out. Large
    per-element diffs alongside a near-zero objective gap is the signature of
    AC-OPF solution non-uniqueness under stress, not a correctness bug -- this
    chart is meant to make that story checkable at a glance."""
    if not summary_frame.empty and "scenario" in summary_frame.columns:
        match = summary_frame[
            (summary_frame["case_name"] == case_name) & (summary_frame["scenario"] == scenario)
        ]
    else:
        match = summary_frame[summary_frame["case_name"] == case_name] if not summary_frame.empty else summary_frame
    obj_rel_diff = (
        float(match["objective_rel_diff"].iloc[0])
        if match is not None and not match.empty and "objective_rel_diff" in match.columns
        else np.nan
    )

    available_panels = [
        panel
        for panel in SPOTLIGHT_PANELS
        if panel[1] in tables and all(c in tables[panel[1]].columns for c in (panel[2], panel[3], panel[4], panel[5]))
    ]
    if not available_panels:
        return

    fig, axes = plt.subplots(2, 2, figsize=(13.0, 9.5))
    axes_flat = axes.flatten()
    any_drawn = False
    for idx, (panel_title, source, id_col, own_col, pm_col, diff_col) in enumerate(available_panels):
        ax = axes_flat[idx]
        frame = tables[source]
        frame = frame[frame["case_name"] == case_name]
        if "scenario" in frame.columns:
            frame = frame[frame["scenario"] == scenario]
        top = frame.reindex(frame[diff_col].abs().sort_values(ascending=False).index).head(top_n)
        if top.empty:
            ax.set_visible(False)
            continue
        any_drawn = True
        labels = top[id_col].astype(str).tolist()
        x = np.arange(len(labels))
        width = 0.36
        ax.bar(x - width / 2, top[own_col].to_numpy(), width=width, color="#2563eb", label="Own model")
        ax.bar(x + width / 2, top[pm_col].to_numpy(), width=width, color="#f97316", label="PowerModels")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
        ax.set_title(panel_title, fontsize=11)
        ax.grid(True, axis="y", color="#e7e9ee", linewidth=0.8)
        if idx == 0:
            ax.legend(frameon=False, fontsize=8)
    for idx in range(len(available_panels), 4):
        axes_flat[idx].set_visible(False)
    if not any_drawn:
        plt.close(fig)
        return

    obj_text = f"objective rel. diff = {obj_rel_diff:.2e}" if np.isfinite(obj_rel_diff) else "objective rel. diff n/a"
    fig.suptitle(
        f"{title_prefix}: {case_name} ({scenario})\n"
        f"top-{top_n} diverging elements per variable -- {obj_text} "
        "(near-identical cost despite large per-element diffs: AC-OPF non-uniqueness under stress)",
        fontsize=12.5,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def _min_max_whiskers(stats: pd.DataFrame) -> np.ndarray:
    """Asymmetric errorbar yerr array (2, n) spanning mean->min and mean->max."""
    lower = (stats["mean"] - stats["min"]).clip(lower=0).to_numpy()
    upper = (stats["max"] - stats["mean"]).clip(lower=0).to_numpy()
    return np.vstack([lower, upper])


def plot_iteration_comparison(frame: pd.DataFrame, title: str, output_path: Path, dpi: int) -> None:
    if "own_solve_iterations" not in frame.columns:
        return
    solved = frame.dropna(subset=["own_solve_iterations"])
    solved = solved[np.isfinite(solved["own_solve_iterations"])]

    # Iteration counts from runs that never actually converged (hit the
    # solver's max_iter cap while LOCALLY_INFEASIBLE / ITERATION_LIMIT / etc.)
    # aren't a real "how many iterations does this take" data point -- they're
    # a timeout. Left in, a handful of these can single-handedly drag the mean
    # and blow out the min-max whiskers for an otherwise well-behaved case
    # (confirmed: every Julia AC row that hit the 3000-iteration cap had
    # own_solved=False and pm_solved=False; same pattern found in Python's
    # case14_ieee load_scaled scenario -- own_success=False, pm_success=False,
    # pm_solve_iterations=3000). Julia's summary uses own_solved/pm_solved;
    # Python's uses own_success/pm_success -- check both spellings.
    for own_col in ("own_solved", "own_success"):
        if own_col in solved.columns:
            solved = solved[solved[own_col].astype(bool)]
    for pm_col in ("pm_solved", "pm_success"):
        if pm_col in solved.columns:
            solved = solved[solved[pm_col].astype(bool)]
    if solved.empty:
        return

    # Julia's summary already has a short "base_case" column; Python's case_name
    # is the full "pglib_opf_case14_ieee" form and needs shortening. Grouping by
    # the raw case_name column (which for Julia has ~250 scenario-suffixed
    # values, e.g. "5_pjm_gen_5_pmax_x0.8") is what made this chart unreadable.
    solved = solved.copy()
    case_col = "base_case" if "base_case" in solved.columns else "case_name"
    solved["_case_short"] = solved[case_col].map(shorten_case_name)

    own_stats = solved.groupby("_case_short")["own_solve_iterations"].agg(["mean", "min", "max"])
    case_order = ordered_present(pd.Series(own_stats.index), CASE_ORDER)
    own_stats = own_stats.reindex(case_order)

    has_pm = "pm_solve_iterations" in solved.columns and solved["pm_solve_iterations"].notna().any()
    pm_stats = (
        solved.groupby("_case_short")["pm_solve_iterations"].agg(["mean", "min", "max"]).reindex(case_order)
        if has_pm
        else None
    )

    fig, ax = plt.subplots(figsize=(max(7.0, 0.85 * len(case_order) + 2.0), 4.8))
    x = np.arange(len(case_order))
    errorbar_kwargs = dict(fmt="none", ecolor="#111827", elinewidth=1.0, capsize=3, capthick=1.0)

    def _label_zero_bars(x_positions: np.ndarray, means: np.ndarray) -> None:
        # A genuinely-zero mean (solver's presolve fully solved the problem,
        # 0 simplex iterations needed -- confirmed on Python DC OPF for
        # case14_ieee/case30_ieee) draws as an invisible zero-height bar,
        # indistinguishable from missing data. Label it explicitly.
        for xi, mean in zip(x_positions, means):
            if np.isfinite(mean) and mean == 0:
                ax.annotate(
                    "0", xy=(xi, 0), xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", fontsize=8.5, color="#111827",
                )

    if has_pm:
        width = 0.36
        ax.bar(x - width / 2, own_stats["mean"].to_numpy(), width=width, color="#2563eb", label="Own model")
        ax.errorbar(x - width / 2, own_stats["mean"].to_numpy(), yerr=_min_max_whiskers(own_stats), **errorbar_kwargs)
        ax.bar(x + width / 2, pm_stats["mean"].to_numpy(), width=width, color="#f97316", label="PowerModels")
        ax.errorbar(x + width / 2, pm_stats["mean"].to_numpy(), yerr=_min_max_whiskers(pm_stats), **errorbar_kwargs)
        ax.legend(frameon=False)
        _label_zero_bars(x - width / 2, own_stats["mean"].to_numpy())
        _label_zero_bars(x + width / 2, pm_stats["mean"].to_numpy())
    else:
        ax.bar(x, own_stats["mean"].to_numpy(), color="#2563eb", label="Own model")
        ax.errorbar(x, own_stats["mean"].to_numpy(), yerr=_min_max_whiskers(own_stats), **errorbar_kwargs)
        _label_zero_bars(x, own_stats["mean"].to_numpy())
        ax.annotate(
            "PowerModels iteration count not available for this data source",
            xy=(0.5, 0.95),
            xycoords="axes fraction",
            ha="center",
            va="top",
            fontsize=8.5,
            color="#6b7280",
        )
    ax.set_xticks(x)
    ax.set_xticklabels(case_order, rotation=30, ha="right")
    ax.set_ylabel("Solver iterations")
    ax.set_title(title, fontsize=14, pad=12)
    ax.grid(True, axis="y", color="#e7e9ee", linewidth=0.8)
    fig.text(
        0.5, 0.01, "Bars = mean across scenarios; whiskers = min-max range. Non-converged runs excluded.",
        ha="center", fontsize=8, color="#6b7280",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def binding_set_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in frame.iterrows():
        # Julia's "case_name" is a fine-grained per-scenario-instance name
        # (e.g. "5_pjm_gen_5_pmax_x0.8"); "base_case" is the short 8-case form.
        # Python only has the raw "pglib_opf_caseN_ieee"-style case_name.
        # Prefer base_case when present so downstream case-level aggregation
        # groups by the actual case, not by every scenario variant.
        case_value = row.get("base_case")
        if pd.isna(case_value) or case_value is None:
            case_value = row.get("case_name")
        for kind in BINDING_TYPES:
            both_col, own_col, pm_col = f"n_{kind}_both", f"n_{kind}_only_own", f"n_{kind}_only_pm"
            if both_col not in frame.columns:
                continue
            both, own_only, pm_only = row.get(both_col), row.get(own_col), row.get(pm_col)
            if pd.isna(both) and pd.isna(own_only) and pd.isna(pm_only):
                continue
            rows.append(
                {
                    "case_name": row.get("case_name"),
                    "case": shorten_case_name(case_value) if case_value is not None else None,
                    "scenario": row.get("scenario"),
                    "model": row.get("model"),
                    "constraint_type": kind,
                    "n_both": both,
                    "n_only_own": own_only,
                    "n_only_pm": pm_only,
                }
            )
    return pd.DataFrame(rows)


def _render_zero_mismatch_notice(title: str, output_path: Path, dpi: int, binding_table: pd.DataFrame) -> None:
    """Explicit "confirmed clean" artifact for when the comparison actually
    ran but found zero binding-constraint mismatches -- e.g. DC-OPF agreed on
    every binding constraint across the entire 302-scenario sweep. Silently
    emitting no file for this case is indistinguishable from a broken/skipped
    pipeline, which undersells a genuinely strong result."""
    n_cases = binding_table["case"].nunique()
    scenario_col = "scenario" if "scenario" in binding_table.columns else "case"
    n_scenarios = binding_table[["case", scenario_col]].drop_duplicates().shape[0]

    fig, ax = plt.subplots(figsize=(8.0, 3.2))
    ax.axis("off")
    ax.text(
        0.5, 0.62, "0 binding-constraint mismatches", ha="center", va="center",
        fontsize=18, fontweight="bold", color="#2f9e44",
    )
    ax.text(
        0.5, 0.30,
        f"Own model and PowerModels agreed on every binding constraint\n"
        f"across {n_scenarios} scenario(s) in {n_cases} case(s).",
        ha="center", va="center", fontsize=11, color="#374151",
    )
    ax.set_title(title, fontsize=14, pad=10)
    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def plot_binding_mismatch_by_case(binding_table: pd.DataFrame, title: str, output_path: Path, dpi: int) -> None:
    """Stacked bar per case: total binding-constraint mismatches
    (n_only_own + n_only_pm), summed across all that case's scenarios,
    colored by constraint type. Answers "how many differ, and of what type"
    at the case level."""
    if binding_table.empty:
        return
    work = binding_table.copy()
    work["mismatch"] = work[["n_only_own", "n_only_pm"]].fillna(0).sum(axis=1)
    pivot = work.pivot_table(index="case", columns="constraint_type", values="mismatch", aggfunc="sum", fill_value=0)
    if pivot.to_numpy().sum() == 0:
        _render_zero_mismatch_notice(title, output_path, dpi, work)
        return
    case_order = ordered_present(pd.Series(pivot.index), CASE_ORDER)
    pivot = pivot.reindex(index=case_order).fillna(0)
    constraint_order = [c for c in BINDING_TYPES if c in pivot.columns]
    pivot = pivot[constraint_order]

    fig, ax = plt.subplots(figsize=(max(7.5, 0.9 * len(pivot) + 2.5), 5.2))
    x = np.arange(len(pivot))
    bottom = np.zeros(len(pivot))
    colors = plt.cm.tab10(np.linspace(0, 1, max(len(constraint_order), 1)))
    for color, ctype in zip(colors, constraint_order):
        values = pivot[ctype].to_numpy()
        ax.bar(x, values, bottom=bottom, color=color, label=ctype)
        bottom = bottom + values
    ax.set_xticks(x)
    ax.set_xticklabels(pivot.index, rotation=30, ha="right")
    ax.set_ylabel("Binding-constraint mismatches, summed across scenarios")
    ax.set_title(title, fontsize=14, pad=12)
    ax.grid(True, axis="y", color="#e7e9ee", linewidth=0.8)
    ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    pivot.to_csv(output_path.with_suffix(".csv"))


def plot_binding_mismatch_table(binding_table: pd.DataFrame, title: str, output_path: Path, dpi: int) -> None:
    """Table of exact binding-constraint mismatch counts: case (rows) x
    constraint type (columns), plus a Total column -- the same data as
    plot_binding_mismatch_by_case's stacked bar, but as precise numbers rather
    than stacked bar-segment lengths that are hard to read off exactly."""
    if binding_table.empty:
        return
    work = binding_table.copy()
    work["mismatch"] = work[["n_only_own", "n_only_pm"]].fillna(0).sum(axis=1)
    pivot = work.pivot_table(index="case", columns="constraint_type", values="mismatch", aggfunc="sum", fill_value=0)
    if pivot.to_numpy().sum() == 0:
        _render_zero_mismatch_notice(title, output_path, dpi, work)
        return
    case_order = ordered_present(pd.Series(pivot.index), CASE_ORDER)
    pivot = pivot.reindex(index=case_order).fillna(0)
    constraint_order = [c for c in BINDING_TYPES if c in pivot.columns]
    pivot = pivot[constraint_order].astype(int)
    pivot["Total"] = pivot.sum(axis=1)

    columns = constraint_order + ["Total"]
    cell_text = [[str(v) for v in row] for row in pivot[columns].to_numpy()]
    fig_width = max(7.5, 1.1 * len(columns) + 2.2)
    fig_height = max(2.6, 0.5 * len(pivot) + 1.9)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis("off")
    tbl = ax.table(
        cellText=cell_text,
        rowLabels=pivot.index.tolist(),
        colLabels=columns,
        cellLoc="center",
        rowLoc="center",
        loc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    tbl.scale(1.0, 1.65)
    for (row_idx, col_idx), cell in tbl.get_celld().items():
        if row_idx == 0:
            cell.set_facecolor("#e7e9ee")
            cell.set_text_props(fontweight="bold")
        elif col_idx == len(columns) - 1:
            cell.set_text_props(fontweight="bold")
    ax.set_title(title, fontsize=14, pad=16)
    fig.text(
        0.5, 0.01, "Counts = n_only_own + n_only_pm, summed across scenarios (see *_binding_mismatch_heatmap for the per-scenario breakdown).",
        ha="center", fontsize=8, color="#6b7280",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    pivot.to_csv(output_path.with_suffix(".csv"))


def plot_binding_mismatch_heatmap(binding_table: pd.DataFrame, title: str, output_path: Path, dpi: int) -> None:
    """Case x scenario-family heatmap of total binding-constraint mismatches
    (summed over constraint types), showing which scenario families drive the
    mismatches within each case -- the per-scenario complement to the
    case-level stacked bar."""
    if binding_table.empty:
        return
    work = binding_table.copy()
    work["mismatch"] = work[["n_only_own", "n_only_pm"]].fillna(0).sum(axis=1)
    pivot = work.pivot_table(index="case", columns="scenario", values="mismatch", aggfunc="sum", fill_value=0)
    if pivot.to_numpy().sum() == 0:
        _render_zero_mismatch_notice(title, output_path, dpi, work)
        return
    case_order = ordered_present(pd.Series(pivot.index), CASE_ORDER)
    pivot = pivot.reindex(index=case_order).fillna(0)
    scenario_order = pivot.sum(axis=0).sort_values(ascending=False).index.tolist()
    pivot = pivot[scenario_order]

    fig_width = max(9.0, 0.55 * len(scenario_order) + 2.5)
    fig_height = max(4.0, 0.55 * len(pivot) + 1.8)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    data = pivot.to_numpy()
    im = ax.imshow(data, cmap="Reds", aspect="auto")
    ax.set_xticks(range(len(scenario_order)))
    ax.set_xticklabels(scenario_order, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(pivot)))
    ax.set_yticklabels(pivot.index)
    ax.set_title(title, fontsize=14, pad=12)
    vmax = data.max() if data.size else 0.0
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            if data[i, j] > 0:
                ax.text(
                    j, i, int(data[i, j]), ha="center", va="center", fontsize=7.5,
                    color="white" if vmax > 0 and data[i, j] > vmax * 0.5 else "#111827",
                )
    fig.colorbar(im, ax=ax, label="Mismatches (summed over constraint types)")
    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    pivot.to_csv(output_path.with_suffix(".csv"))


def binding_class_table_from_details(tables: dict[str, pd.DataFrame], model: str, language: str) -> pd.DataFrame:
    """Element-level binding_class/mismatch_reason rows pulled straight from the
    persisted detail CSVs -- lets you drill into exactly which element mismatched."""
    rows = []
    for source_name, frame in tables.items():
        if frame.empty:
            continue
        class_cols = [c for c in frame.columns if c.endswith("_binding_class")]
        for class_col in class_cols:
            prefix = class_col[: -len("_binding_class")]
            reason_col = f"{prefix}_mismatch_reason"
            mismatched = frame[frame[class_col].isin(["only_own", "only_pm"])]
            if mismatched.empty:
                continue
            id_col = "gen_id" if "gen_id" in mismatched.columns else ("bus_id" if "bus_id" in mismatched.columns else "branch_id")
            for _, row in mismatched.iterrows():
                rows.append(
                    {
                        "language": language,
                        "model": model,
                        "source": source_name,
                        "case_name": row.get("case_name"),
                        "scenario": row.get("scenario"),
                        "element_id": row.get(id_col),
                        "constraint_type": prefix,
                        "binding_class": row[class_col],
                        "mismatch_reason": row.get(reason_col),
                    }
                )
    return pd.DataFrame(rows)


def variable_diff_summary_table(frame: pd.DataFrame, variables: list[str]) -> pd.DataFrame:
    rows = []
    for _, row in frame.iterrows():
        for var in variables:
            mean_col, max_col, std_col = f"mean_{var}_diff", f"max_{var}_diff", f"std_{var}_diff"
            if mean_col not in frame.columns:
                continue
            rows.append(
                {
                    "case_name": row.get("case_name"),
                    "scenario": row.get("scenario"),
                    "model": row.get("model"),
                    "variable": var,
                    "mean_abs_diff": row.get(mean_col),
                    "max_abs_diff": row.get(max_col),
                    "std_abs_diff": row.get(std_col),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    python_ac = collect_python_summaries(args.reference_dir, args.python_detail_dir, "ac")
    python_dc = collect_python_summaries(args.reference_dir, args.python_detail_dir, "dc")
    julia = load_julia_summary(args.julia_summary)

    summary_tables = []
    binding_tables = []
    mismatch_tables = []

    if not python_ac.empty:
        plot_variable_diff_table(
            python_ac, [v for v, _, _ in AC_VARIABLE_SOURCES], "case_name",
            "Python AC OPF vs PowerModels: mean |own - PowerModels| diff by case",
            args.output_dir / "python_ac_variable_diffs_table.png", args.dpi,
        )
        plot_variable_diff_table(
            python_ac, [v for v, _, _ in AC_VARIABLE_SOURCES], "case_name",
            "Python AC OPF vs PowerModels: worst-case |own - PowerModels| diff by case",
            args.output_dir / "python_ac_variable_diffs_max_table.png", args.dpi, stat="max",
        )
        plot_iteration_comparison(
            python_ac, "Python AC OPF: solver iterations by case",
            args.output_dir / "python_ac_solver_iterations.png", args.dpi,
        )
        summary_tables.append(variable_diff_summary_table(python_ac, [v for v, _, _ in AC_VARIABLE_SOURCES]))
        python_ac_binding = binding_set_table(python_ac)
        binding_tables.append(python_ac_binding)
        plot_binding_mismatch_by_case(
            python_ac_binding, "Python AC OPF vs PowerModels: binding-constraint mismatches by case",
            args.output_dir / "python_ac_binding_mismatch_by_case.png", args.dpi,
        )
        plot_binding_mismatch_table(
            python_ac_binding, "Python AC OPF vs PowerModels: binding-constraint mismatches by case",
            args.output_dir / "python_ac_binding_mismatch_table.png", args.dpi,
        )
        plot_binding_mismatch_heatmap(
            python_ac_binding, "Python AC OPF vs PowerModels: binding-constraint mismatches by case and scenario",
            args.output_dir / "python_ac_binding_mismatch_heatmap.png", args.dpi,
        )

    if not python_dc.empty:
        plot_variable_diff_table(
            python_dc, [v for v, _, _ in DC_VARIABLE_SOURCES], "case_name",
            "Python DC OPF vs PowerModels: mean |own - PowerModels| diff by case",
            args.output_dir / "python_dc_variable_diffs_table.png", args.dpi,
        )
        plot_variable_diff_table(
            python_dc, [v for v, _, _ in DC_VARIABLE_SOURCES], "case_name",
            "Python DC OPF vs PowerModels: worst-case |own - PowerModels| diff by case",
            args.output_dir / "python_dc_variable_diffs_max_table.png", args.dpi, stat="max",
        )
        plot_iteration_comparison(
            python_dc, "Python DC OPF: solver iterations by case",
            args.output_dir / "python_dc_solver_iterations.png", args.dpi,
        )
        summary_tables.append(variable_diff_summary_table(python_dc, [v for v, _, _ in DC_VARIABLE_SOURCES]))
        python_dc_binding = binding_set_table(python_dc)
        binding_tables.append(python_dc_binding)
        plot_binding_mismatch_by_case(
            python_dc_binding, "Python DC OPF vs PowerModels: binding-constraint mismatches by case",
            args.output_dir / "python_dc_binding_mismatch_by_case.png", args.dpi,
        )
        plot_binding_mismatch_table(
            python_dc_binding, "Python DC OPF vs PowerModels: binding-constraint mismatches by case",
            args.output_dir / "python_dc_binding_mismatch_table.png", args.dpi,
        )
        plot_binding_mismatch_heatmap(
            python_dc_binding, "Python DC OPF vs PowerModels: binding-constraint mismatches by case and scenario",
            args.output_dir / "python_dc_binding_mismatch_heatmap.png", args.dpi,
        )
        # python_ac/julia_ac/julia_dc all get an outcome/objective/solve-time
        # plot trio from 17_plot_opf_validation_findings.py's make_plot_set,
        # sourced from a large PowerModels-compared sweep CSV. No DC
        # equivalent of that sweep exists for Python (module docstring) --
        # this reuses the same plot functions against the smaller 3-case x
        # 5-scenario set instead, closing that gap at a smaller scale.
        python_dc_small_scale = normalize_python_dc_small_scale(python_dc)
        script17.make_plot_set(
            python_dc_small_scale, "python_dc_vs_powermodels",
            "Python DC OPF vs PowerModels (3-case validation set)", args.output_dir, args.dpi,
        )

    python_ac_tables = load_detail_tables(args.python_detail_dir, "ac")
    python_dc_tables = load_detail_tables(args.python_detail_dir, "dc")
    if python_ac_tables:
        plot_element_diff_table(
            python_ac_tables, AC_VARIABLE_SOURCES, "Python AC OPF vs PowerModels: per-element diff (spot-check sample)",
            args.output_dir / "python_ac_element_diff_table.png", args.dpi,
        )
        mismatch_tables.append(binding_class_table_from_details(python_ac_tables, "ac", "python"))
        python_ac_long = build_element_diff_table(python_ac_tables, AC_VARIABLE_SOURCES)
        for case_name, scenario in find_spotlight_scenarios(python_ac_long):
            plot_scenario_spotlight(
                python_ac_tables, case_name, scenario, python_ac, "Python AC OPF vs PowerModels",
                args.output_dir / f"python_ac_spotlight_{case_name}__{scenario}.png", args.dpi,
            )
    if python_dc_tables:
        plot_element_diff_table(
            python_dc_tables, DC_VARIABLE_SOURCES, "Python DC OPF vs PowerModels: per-element diff (spot-check sample)",
            args.output_dir / "python_dc_element_diff_table.png", args.dpi,
        )
        mismatch_tables.append(binding_class_table_from_details(python_dc_tables, "dc", "python"))
        python_dc_long = build_element_diff_table(python_dc_tables, DC_VARIABLE_SOURCES)
        for case_name, scenario in find_spotlight_scenarios(python_dc_long):
            plot_scenario_spotlight(
                python_dc_tables, case_name, scenario, python_dc, "Python DC OPF vs PowerModels",
                args.output_dir / f"python_dc_spotlight_{case_name}__{scenario}.png", args.dpi,
            )

    julia_ac_subset = pd.DataFrame()
    julia_dc_subset = pd.DataFrame()
    if julia is not None:
        for model, variable_sources in [("ac", AC_VARIABLE_SOURCES), ("dc", DC_VARIABLE_SOURCES)]:
            subset = julia[julia["model"].astype(str).str.lower() == model]
            if subset.empty:
                continue
            if model == "ac":
                julia_ac_subset = subset
            else:
                julia_dc_subset = subset
            plot_variable_diff_table(
                subset, [v for v, _, _ in variable_sources], "base_case",
                f"Julia {model.upper()} OPF vs PowerModels: mean |own - PowerModels| diff by case",
                args.output_dir / f"julia_{model}_variable_diffs_table.png", args.dpi,
            )
            plot_variable_diff_table(
                subset, [v for v, _, _ in variable_sources], "base_case",
                f"Julia {model.upper()} OPF vs PowerModels: worst-case |own - PowerModels| diff by case",
                args.output_dir / f"julia_{model}_variable_diffs_max_table.png", args.dpi, stat="max",
            )
            plot_iteration_comparison(
                subset, f"Julia {model.upper()} OPF: solver iterations by case",
                args.output_dir / f"julia_{model}_solver_iterations.png", args.dpi,
            )
            summary_tables.append(variable_diff_summary_table(subset, [v for v, _, _ in variable_sources]))
            julia_binding = binding_set_table(subset)
            binding_tables.append(julia_binding)
            plot_binding_mismatch_by_case(
                julia_binding, f"Julia {model.upper()} OPF vs PowerModels: binding-constraint mismatches by case",
                args.output_dir / f"julia_{model}_binding_mismatch_by_case.png", args.dpi,
            )
            plot_binding_mismatch_table(
                julia_binding, f"Julia {model.upper()} OPF vs PowerModels: binding-constraint mismatches by case",
                args.output_dir / f"julia_{model}_binding_mismatch_table.png", args.dpi,
            )
            plot_binding_mismatch_heatmap(
                julia_binding,
                f"Julia {model.upper()} OPF vs PowerModels: binding-constraint mismatches by case and scenario",
                args.output_dir / f"julia_{model}_binding_mismatch_heatmap.png", args.dpi,
            )

    julia_ac_tables = load_detail_tables(args.julia_detail_dir, "ac")
    julia_dc_tables = load_detail_tables(args.julia_detail_dir, "dc")
    if julia_ac_tables:
        plot_element_diff_table(
            julia_ac_tables, AC_VARIABLE_SOURCES, "Julia AC OPF vs PowerModels: per-element diff (spot-check sample)",
            args.output_dir / "julia_ac_element_diff_table.png", args.dpi,
        )
        mismatch_tables.append(binding_class_table_from_details(julia_ac_tables, "ac", "julia"))
        julia_ac_long = build_element_diff_table(julia_ac_tables, AC_VARIABLE_SOURCES)
        for case_name, scenario in find_spotlight_scenarios(julia_ac_long):
            plot_scenario_spotlight(
                julia_ac_tables, case_name, scenario, julia_ac_subset, "Julia AC OPF vs PowerModels",
                args.output_dir / f"julia_ac_spotlight_{case_name}__{scenario}.png", args.dpi,
            )
    if julia_dc_tables:
        plot_element_diff_table(
            julia_dc_tables, DC_VARIABLE_SOURCES, "Julia DC OPF vs PowerModels: per-element diff (spot-check sample)",
            args.output_dir / "julia_dc_element_diff_table.png", args.dpi,
        )
        mismatch_tables.append(binding_class_table_from_details(julia_dc_tables, "dc", "julia"))
        julia_dc_long = build_element_diff_table(julia_dc_tables, DC_VARIABLE_SOURCES)
        for case_name, scenario in find_spotlight_scenarios(julia_dc_long):
            plot_scenario_spotlight(
                julia_dc_tables, case_name, scenario, julia_dc_subset, "Julia DC OPF vs PowerModels",
                args.output_dir / f"julia_dc_spotlight_{case_name}__{scenario}.png", args.dpi,
            )

    if summary_tables:
        pd.concat(summary_tables, ignore_index=True).to_csv(
            args.output_dir / "variable_diff_summary_table.csv", index=False
        )
    if binding_tables:
        pd.concat(binding_tables, ignore_index=True).to_csv(
            args.output_dir / "binding_set_comparison.csv", index=False
        )
    if mismatch_tables:
        combined = pd.concat(mismatch_tables, ignore_index=True)
        combined.to_csv(args.output_dir / "binding_mismatch_elements.csv", index=False)
        print(f"Element-level binding mismatches (only_own/only_pm) found: {len(combined)}")

    for label, tables in [("python_ac", python_ac_tables), ("julia_ac", julia_ac_tables)]:
        if "branches" not in tables:
            continue
        convention_path = (
            args.python_detail_dir if label.startswith("python") else args.julia_detail_dir
        ) / "ac_branch_flow_convention_check.csv"
        if convention_path.is_file():
            convention = pd.read_csv(convention_path)
            convention.to_csv(args.output_dir / f"{label}_branch_flow_convention_check.csv", index=False)

    print(f"Wrote per-variable diff plots and tables to {args.output_dir}")


if __name__ == "__main__":
    main()
