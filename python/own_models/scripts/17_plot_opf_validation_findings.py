"""Create presentation plots for OPF validation findings.

The plots summarize own-model vs PowerModels agreement at three levels:
outcome agreement, objective agreement, and solve-time ratio.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re

import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]

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

SCENARIO_ORDER = [
    "base",
    "relaxed_limits",
    "branch_congestion",
    "tight_voltage_limits",
    "generator_capacity_reduction",
    "generator_capacity_expansion",
    "load_scaling",
    "system_angle_limits",
    "reactive_power_stress",
    "reactive_generation_limits",
    "slack_consistency",
    "branch_outage",
    "generator_outage",
    "slack_without_reference",
]

CLASS_ORDER = [
    "not_run",
    "validated",
    "matched_non_solve",
    "objective_gap",
    "outcome_mismatch",
]

CLASS_TO_VALUE = {name: idx for idx, name in enumerate(CLASS_ORDER)}
CLASS_LABELS = {
    "not_run": "Not run",
    "validated": "Validated",
    "matched_non_solve": "Matched non-solve",
    "objective_gap": "Objective gap",
    "outcome_mismatch": "Outcome mismatch",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ac-python-comparison",
        type=Path,
        default=REPO_ROOT
        / "results"
        / "own_python"
        / "ac_opf_validation"
        / "compact_ac_opf_sweeps_with_no_ref_powermodels_comparison.csv",
    )
    parser.add_argument(
        "--julia-summary",
        type=Path,
        default=REPO_ROOT
        / "results"
        / "own_julia"
        / "opf_validation_sweeps"
        / "compact_opf_validation_summary.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "results" / "presentable" / "opf_validation_findings",
    )
    parser.add_argument("--objective-gap-threshold", type=float, default=1e-3)
    parser.add_argument("--dpi", type=int, default=220)
    return parser.parse_args()


def bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def infer_base_case(case_name: object) -> str:
    text = str(case_name)
    for case in CASE_ORDER:
        if text == case or text.startswith(case + "_"):
            return case
    match = re.match(r"^(\d+_[A-Za-z0-9]+)", text)
    return match.group(1) if match else text


def scenario_label(name: str) -> str:
    return name.replace("_", "\n")


def normalize_ac_python(path: Path, objective_gap_threshold: float) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["base_case"] = frame["case_name"].map(infer_base_case)
    frame["model"] = "ac_python"
    frame["own_status"] = frame["status"].astype(str)
    frame["pm_status"] = frame["pm_status"].astype(str)
    frame["own_solved_norm"] = bool_series(frame["own_solved"])
    frame["pm_solved_norm"] = bool_series(frame["pm_solved_bool"])
    frame["outcome_match_norm"] = bool_series(frame["outcome_match"])
    frame["own_objective_norm"] = numeric_series(frame["objective"])
    frame["pm_objective_norm"] = numeric_series(frame["pm_objective"])
    frame["objective_rel_diff_norm"] = numeric_series(frame["objective_rel_diff_vs_pm"]).abs()
    frame["own_solve_time_norm"] = numeric_series(frame["solve_time_sec"])
    frame["pm_solve_time_norm"] = numeric_series(frame["pm_solve_time_sec"])
    frame["time_ratio_norm"] = numeric_series(frame["solve_time_ratio_vs_pm"])
    return classify_rows(frame, objective_gap_threshold)


def normalize_julia_summary(path: Path, model: str, objective_gap_threshold: float) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame = frame.loc[frame["model"].astype(str).str.lower() == model].copy()
    frame["base_case"] = frame["base_case"].astype(str)
    frame["model"] = f"{model}_julia"
    frame["own_status"] = frame["own_status"].astype(str)
    frame["pm_status"] = frame["pm_status"].astype(str)
    frame["own_solved_norm"] = bool_series(frame["own_solved"])
    frame["pm_solved_norm"] = bool_series(frame["pm_solved"])
    frame["outcome_match_norm"] = bool_series(frame["outcome_match"])
    frame["own_objective_norm"] = numeric_series(frame["own_objective"])
    frame["pm_objective_norm"] = numeric_series(frame["pm_objective"])
    frame["objective_rel_diff_norm"] = numeric_series(frame["objective_rel_diff"]).abs()
    frame["own_solve_time_norm"] = numeric_series(frame["own_solve_time_sec"])
    frame["pm_solve_time_norm"] = numeric_series(frame["pm_solve_time_sec"])
    frame["time_ratio_norm"] = numeric_series(frame["solve_time_ratio_vs_pm"])
    return classify_rows(frame, objective_gap_threshold)


def classify_rows(frame: pd.DataFrame, objective_gap_threshold: float) -> pd.DataFrame:
    both_solved = frame["own_solved_norm"] & frame["pm_solved_norm"]
    objective_gap = both_solved & (frame["objective_rel_diff_norm"] > objective_gap_threshold)
    frame["validation_class"] = np.select(
        [
            ~frame["outcome_match_norm"],
            objective_gap,
            ~both_solved & frame["outcome_match_norm"],
            both_solved,
        ],
        [
            "outcome_mismatch",
            "objective_gap",
            "matched_non_solve",
            "validated",
        ],
        default="not_run",
    )
    frame["validation_severity"] = frame["validation_class"].map(CLASS_TO_VALUE)
    return frame


def ordered_present(values: pd.Series, preferred: list[str]) -> list[str]:
    present = set(values.dropna().astype(str))
    ordered = [item for item in preferred if item in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def plot_outcome_heatmap(frame: pd.DataFrame, title: str, output_path: Path, dpi: int) -> None:
    cases = ordered_present(frame["base_case"], CASE_ORDER)
    scenarios = ordered_present(frame["scenario"], SCENARIO_ORDER)
    pivot = (
        frame.groupby(["base_case", "scenario"], as_index=False)["validation_severity"]
        .max()
        .pivot(index="base_case", columns="scenario", values="validation_severity")
        .reindex(index=cases, columns=scenarios)
    )

    data = pivot.fillna(CLASS_TO_VALUE["not_run"]).to_numpy()
    cmap = ListedColormap(["#d9dee7", "#2f9e44", "#f2c94c", "#f2994a", "#d64545"])
    norm = BoundaryNorm(np.arange(-0.5, len(CLASS_ORDER) + 0.5), cmap.N)

    fig_width = max(9.0, 0.8 * len(scenarios) + 2.2)
    fig_height = max(4.5, 0.48 * len(cases) + 1.8)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.imshow(data, cmap=cmap, norm=norm, aspect="auto")
    ax.set_title(title, fontsize=15, pad=14)
    ax.set_xticks(range(len(scenarios)))
    ax.set_xticklabels([scenario_label(item) for item in scenarios], rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(len(cases)))
    ax.set_yticklabels(cases, fontsize=10)
    ax.set_xlabel("Scenario family")
    ax.set_ylabel("PGLib case")
    ax.set_xticks(np.arange(-0.5, len(scenarios), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(cases), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.2)
    ax.tick_params(which="minor", bottom=False, left=False)

    handles = [
        plt.Line2D([0], [0], marker="s", linestyle="", markersize=9, color=cmap(CLASS_TO_VALUE[name]))
        for name in CLASS_ORDER
    ]
    ax.legend(
        handles,
        [CLASS_LABELS[name] for name in CLASS_ORDER],
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        frameon=False,
    )
    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def plot_objective_scatter(frame: pd.DataFrame, title: str, output_path: Path, dpi: int) -> None:
    solved = frame.loc[frame["own_solved_norm"] & frame["pm_solved_norm"]].copy()
    solved = solved.dropna(subset=["own_objective_norm", "pm_objective_norm"])
    if solved.empty:
        return

    fig, ax = plt.subplots(figsize=(7.2, 5.6))
    cases = ordered_present(solved["base_case"], CASE_ORDER)
    colors = plt.cm.tab10(np.linspace(0, 1, max(len(cases), 1)))
    for color, case in zip(colors, cases):
        subset = solved.loc[solved["base_case"] == case]
        ax.scatter(
            subset["pm_objective_norm"],
            subset["own_objective_norm"],
            s=32,
            alpha=0.78,
            label=case,
            color=color,
            edgecolor="white",
            linewidth=0.4,
        )

    min_value = min(solved["pm_objective_norm"].min(), solved["own_objective_norm"].min())
    max_value = max(solved["pm_objective_norm"].max(), solved["own_objective_norm"].max())
    ax.plot([min_value, max_value], [min_value, max_value], color="#333333", linewidth=1.2)
    ax.set_title(title, fontsize=15, pad=12)
    ax.set_xlabel("PowerModels objective")
    ax.set_ylabel("Own model objective")
    ax.ticklabel_format(axis="both", style="sci", scilimits=(0, 0))
    ax.grid(True, color="#e7e9ee", linewidth=0.8)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False)
    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def solve_time_summary(frame: pd.DataFrame) -> pd.DataFrame:
    solved = frame.loc[frame["own_solved_norm"] & frame["pm_solved_norm"]].copy()
    solved = solved.dropna(subset=["own_solve_time_norm", "pm_solve_time_norm"])
    solved = solved.loc[np.isfinite(solved["own_solve_time_norm"]) & np.isfinite(solved["pm_solve_time_norm"])]
    if solved.empty:
        return pd.DataFrame(columns=["own_avg", "own_min", "own_max", "pm_avg", "pm_min", "pm_max", "own_to_pm"])

    summary = solved.groupby("base_case").agg(
        own_avg=("own_solve_time_norm", "mean"),
        own_min=("own_solve_time_norm", "min"),
        own_max=("own_solve_time_norm", "max"),
        pm_avg=("pm_solve_time_norm", "mean"),
        pm_min=("pm_solve_time_norm", "min"),
        pm_max=("pm_solve_time_norm", "max"),
    )
    summary["own_to_pm"] = summary["own_avg"] / summary["pm_avg"]
    summary = summary.reindex(ordered_present(solved["base_case"], CASE_ORDER))
    return summary


def _min_max_whiskers(avg: pd.Series, lo: pd.Series, hi: pd.Series) -> np.ndarray:
    """Asymmetric errorbar yerr array (2, n) spanning avg->min and avg->max."""
    lower = (avg - lo).clip(lower=0).to_numpy()
    upper = (hi - avg).clip(lower=0).to_numpy()
    return np.vstack([lower, upper])


def plot_solve_time_by_case(frame: pd.DataFrame, title: str, output_path: Path, dpi: int) -> None:
    summary = solve_time_summary(frame)
    if summary.empty:
        return

    fig, ax = plt.subplots(figsize=(8.0, 4.8))
    x = np.arange(len(summary))
    width = 0.36
    errorbar_kwargs = dict(fmt="none", ecolor="#111827", elinewidth=1.0, capsize=3, capthick=1.0)
    ax.bar(x - width / 2, summary["own_avg"], width=width, color="#2563eb", label="Own model avg.")
    ax.errorbar(
        x - width / 2, summary["own_avg"],
        yerr=_min_max_whiskers(summary["own_avg"], summary["own_min"], summary["own_max"]), **errorbar_kwargs,
    )
    ax.bar(x + width / 2, summary["pm_avg"], width=width, color="#f97316", label="PowerModels avg.")
    ax.errorbar(
        x + width / 2, summary["pm_avg"],
        yerr=_min_max_whiskers(summary["pm_avg"], summary["pm_min"], summary["pm_max"]), **errorbar_kwargs,
    )
    ax.set_title(title, fontsize=15, pad=12)
    ax.set_ylabel("Solve time (s): bar = mean, whiskers = min-max")
    ax.set_xticks(x)
    ax.set_xticklabels(summary.index, rotation=30, ha="right")
    if summary[["own_avg", "pm_avg"]].max().max() / max(summary[["own_avg", "pm_avg"]].min().min(), 1e-9) > 50:
        ax.set_yscale("log")
        ax.set_ylabel("Solve time (s, log scale): bar = mean, whiskers = min-max")
    ax.grid(True, axis="y", color="#e7e9ee", linewidth=0.8)
    ax.legend(frameon=False)

    for idx, ratio in enumerate(summary["own_to_pm"]):
        if np.isfinite(ratio):
            y = max(summary.iloc[idx]["own_max"], summary.iloc[idx]["pm_max"])
            ax.annotate(
                f"{ratio:.2f}x",
                xy=(idx, y),
                xytext=(0, 5),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=8.5,
                color="#111827",
            )

    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def write_diagnostics(frame: pd.DataFrame, prefix: str, output_dir: Path) -> None:
    columns = [
        "base_case",
        "case_name",
        "scenario",
        "target_type",
        "target_id",
        "parameter_name",
        "parameter_value",
        "own_status",
        "pm_status",
        "own_objective_norm",
        "pm_objective_norm",
        "objective_rel_diff_norm",
        "own_solve_time_norm",
        "pm_solve_time_norm",
        "time_ratio_norm",
        "validation_class",
    ]
    available = [column for column in columns if column in frame.columns]
    frame.loc[frame["validation_class"] == "outcome_mismatch", available].to_csv(
        output_dir / f"{prefix}_outcome_mismatches.csv",
        index=False,
    )
    frame.loc[frame["validation_class"] == "objective_gap", available].sort_values(
        "objective_rel_diff_norm",
        ascending=False,
    ).to_csv(output_dir / f"{prefix}_objective_gaps.csv", index=False)

    summary = (
        frame.groupby(["base_case", "validation_class"], as_index=False)
        .size()
        .pivot(index="base_case", columns="validation_class", values="size")
        .fillna(0)
        .astype(int)
        .reindex(index=ordered_present(frame["base_case"], CASE_ORDER))
    )
    for name in CLASS_ORDER:
        if name not in summary.columns:
            summary[name] = 0
    summary = summary[CLASS_ORDER]
    summary.to_csv(output_dir / f"{prefix}_class_counts_by_case.csv")

    times = solve_time_summary(frame)
    if not times.empty:
        times.round(
            {"own_avg": 3, "own_min": 3, "own_max": 3, "pm_avg": 3, "pm_min": 3, "pm_max": 3, "own_to_pm": 2}
        ).to_csv(output_dir / f"{prefix}_solve_time_summary_by_case.csv")


def make_plot_set(frame: pd.DataFrame, prefix: str, title_prefix: str, output_dir: Path, dpi: int) -> None:
    plot_outcome_heatmap(
        frame,
        f"{title_prefix}: outcome and objective validation",
        output_dir / f"{prefix}_outcome_heatmap.png",
        dpi,
    )
    plot_objective_scatter(
        frame,
        f"{title_prefix}: objective agreement",
        output_dir / f"{prefix}_objective_scatter.png",
        dpi,
    )
    plot_solve_time_by_case(
        frame,
        f"{title_prefix}: average solve time by case",
        output_dir / f"{prefix}_solve_time_by_case.png",
        dpi,
    )
    write_diagnostics(frame, prefix, output_dir)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.ac_python_comparison.is_file():
        ac_python = normalize_ac_python(args.ac_python_comparison, args.objective_gap_threshold)
        make_plot_set(ac_python, "python_ac_vs_powermodels", "Python AC OPF vs PowerModels", args.output_dir, args.dpi)

    if args.julia_summary.is_file():
        julia = pd.read_csv(args.julia_summary)
        if "model" in julia.columns:
            for model, title in [("ac", "Julia AC OPF vs PowerModels"), ("dc", "Julia DC OPF vs PowerModels")]:
                if (julia["model"].astype(str).str.lower() == model).any():
                    frame = normalize_julia_summary(args.julia_summary, model, args.objective_gap_threshold)
                    make_plot_set(frame, f"julia_{model}_vs_powermodels", title, args.output_dir, args.dpi)

    print(f"Wrote validation plots and diagnostics to {args.output_dir}")


if __name__ == "__main__":
    main()
