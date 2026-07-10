"""Create matched PyPowSyBl/OpenLoadFlow baseline artifacts for problem cases.

This script reads existing result CSVs only. It isolates the comparable
single-slack baseline before any PyPowSyBl-specific controls are enabled.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PM_ROWS = (
    PROJECT_ROOT
    / "results"
    / "presentable"
    / "powermodels_lf_replacement_sweep"
    / "base_full"
    / "powermodels_ac_pf_sweep_rows.csv"
)
PP_ROWS = (
    PROJECT_ROOT
    / "results"
    / "presentable"
    / "pypowsybl_lf_replacement_sweep"
    / "presentable_summary_rows.csv"
)

OUTPUT_DIR = (
    PROJECT_ROOT
    / "results"
    / "presentable"
    / "pypowsybl_lf_replacement_sweep"
    / "baseline_problem_case_analysis"
)
FIGURE_DIR = OUTPUT_DIR / "figures"

FOCUS_CASES = [
    "case300_ieee",
    "case1354_pegase",
    "case1888_rte",
    "case6470_rte",
    "case9241_pegase",
    "case13659_pegase",
]

CASE_LABELS = {
    "case300_ieee": "300 IEEE",
    "case1354_pegase": "1354 PEGASE",
    "case1888_rte": "1888 RTE",
    "case6470_rte": "6470 RTE",
    "case9241_pegase": "9241 PEGASE",
    "case13659_pegase": "13659 PEGASE",
}

SHARED_VARIANTS = [
    "original",
    "BV_from_warmstart",
    "G_PGQG_from",
    "G_PGQG_from_plus_BV_from",
    "G_PGQG_from_plus_GVG_from",
]

VARIANT_LABELS = {
    "original": "Original",
    "BV_from_warmstart": "VS init",
    "G_PGQG_from": "Gen P/Q",
    "G_PGQG_from_plus_BV_from": "Gen P/Q + VS",
    "G_PGQG_from_plus_GVG_from": "Gen P/Q + VG",
}

PM_ALL_VARIANTS = SHARED_VARIANTS


def pp_case_name(pm_case_name: str) -> str:
    return f"pglib_opf_{pm_case_name}"


def clean(value: Any) -> Any:
    if pd.isna(value):
        return ""
    return value


def to_bool(value: Any) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def yn(value: Any) -> str:
    return "Y" if to_bool(value) else "N"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not PM_ROWS.exists():
        raise FileNotFoundError(PM_ROWS)
    if not PP_ROWS.exists():
        raise FileNotFoundError(PP_ROWS)
    pm = pd.read_csv(PM_ROWS)
    pp = pd.read_csv(PP_ROWS)
    return pm, pp


def create_comparison_rows(pm: pd.DataFrame, pp: pd.DataFrame) -> list[dict[str, Any]]:
    pm_focus = pm[
        pm["case_name"].isin(FOCUS_CASES)
        & pm["variant_name"].isin(SHARED_VARIANTS)
    ].copy()
    pp_focus = pp[
        pp["scenario_folder"].eq("base_case")
        & pp["base_case_name"].isin([pp_case_name(case) for case in FOCUS_CASES])
        & pp["variant_name"].isin(SHARED_VARIANTS)
        & pp["parameter_scenario"].eq("strict_single_slack")
    ].copy()

    pm_by_key = {(row["case_name"], row["variant_name"]): row for _, row in pm_focus.iterrows()}
    pp_by_key = {(row["base_case_name"], row["variant_name"]): row for _, row in pp_focus.iterrows()}

    rows: list[dict[str, Any]] = []
    for case_name in FOCUS_CASES:
        for variant_name in SHARED_VARIANTS:
            pm_row = pm_by_key.get((case_name, variant_name))
            pp_row = pp_by_key.get((pp_case_name(case_name), variant_name))

            pm_converged = "" if pm_row is None else to_bool(pm_row.get("converged"))
            pp_converged = "" if pp_row is None else to_bool(pp_row.get("valid_converged"))
            agreement = "" if pm_row is None or pp_row is None else pm_converged == pp_converged

            rows.append(
                {
                    "case_name": case_name,
                    "case_label": CASE_LABELS[case_name],
                    "variant_name": variant_name,
                    "variant_label": VARIANT_LABELS[variant_name],
                    "powermodels_converged": pm_converged,
                    "pypowsybl_single_slack_converged": pp_converged,
                    "agreement": agreement,
                    "powermodels_iteration_count": "" if pm_row is None else clean(pm_row.get("iteration_count", "")),
                    "pypowsybl_iteration_count": "" if pp_row is None else clean(pp_row.get("iteration_count", "")),
                    "powermodels_status": "" if pm_row is None else clean(pm_row.get("raw_status", "")),
                    "pypowsybl_status_text": "" if pp_row is None else clean(pp_row.get("status_text", "")),
                    "powermodels_gen_minus_load_mw_before": ""
                    if pm_row is None
                    else clean(pm_row.get("gen_minus_load_mw_before", "")),
                    "powermodels_gen_minus_load_mw_after": ""
                    if pm_row is None
                    else clean(pm_row.get("gen_minus_load_mw_after", "")),
                    "pypowsybl_gen_minus_load_mw_before": ""
                    if pp_row is None
                    else clean(pp_row.get("gen_minus_load_before", "")),
                    "pypowsybl_gen_minus_load_mw_after": ""
                    if pp_row is None
                    else clean(pp_row.get("gen_minus_load_after", "")),
                    "powermodels_slack_bus": "" if pm_row is None else clean(pm_row.get("slack_bus", "")),
                    "pypowsybl_slack_bus_id": "" if pp_row is None else clean(pp_row.get("slack_bus_id", "")),
                    "powermodels_slack_pg_mw": "" if pm_row is None else clean(pm_row.get("slack_pg", "")),
                    "powermodels_slack_qg_mvar": "" if pm_row is None else clean(pm_row.get("slack_qg", "")),
                    "pypowsybl_slack_active_power_mismatch_mw": ""
                    if pp_row is None
                    else clean(pp_row.get("slack_active_power_mismatch", "")),
                    "powermodels_min_vm_pu": "" if pm_row is None else clean(pm_row.get("min_vm", "")),
                    "powermodels_max_vm_pu": "" if pm_row is None else clean(pm_row.get("max_vm", "")),
                    "powermodels_max_power_balance_residual": ""
                    if pm_row is None
                    else clean(pm_row.get("max_power_balance_residual", "")),
                    "pypowsybl_target_q_outside_limits": ""
                    if pp_row is None
                    else clean(pp_row.get("number_of_generators_with_target_q_outside_limits", "")),
                    "pypowsybl_voltage_init_mode": ""
                    if pp_row is None
                    else clean(pp_row.get("voltage_init_mode", "")),
                    "pypowsybl_provider_parameters": ""
                    if pp_row is None
                    else clean(pp_row.get("provider_parameters", "")),
                    "powermodels_solve_time_sec": "" if pm_row is None else clean(pm_row.get("solve_time_sec", "")),
                    "pypowsybl_elapsed_seconds_single": ""
                    if pp_row is None
                    else clean(pp_row.get("elapsed_seconds_single", "")),
                }
            )
    return rows


def create_summary_rows(pm: pd.DataFrame, comparison_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pm_focus = pm[pm["case_name"].isin(FOCUS_CASES) & pm["variant_name"].isin(PM_ALL_VARIANTS)].copy()
    comparison = pd.DataFrame(comparison_rows)

    rows: list[dict[str, Any]] = []
    for case_name in FOCUS_CASES:
        pm_case = pm_focus[pm_focus["case_name"].eq(case_name)]
        comp_case = comparison[comparison["case_name"].eq(case_name)]

        pm_converged_variants = [
            VARIANT_LABELS.get(str(row["variant_name"]), str(row["variant_name"]))
            for _, row in pm_case.iterrows()
            if to_bool(row.get("converged"))
        ]
        pp_converged_variants = [
            str(row["variant_label"])
            for _, row in comp_case.iterrows()
            if to_bool(row["pypowsybl_single_slack_converged"])
        ]
        matched_disagreements = [
            str(row["variant_label"])
            for _, row in comp_case.iterrows()
            if row["agreement"] != "" and not to_bool(row["agreement"])
        ]

        rows.append(
            {
                "case_name": case_name,
                "case_label": CASE_LABELS[case_name],
                "powermodels_direct_converged_variants": "; ".join(pm_converged_variants),
                "powermodels_direct_n_converged_of_5": len(pm_converged_variants),
                "pypowsybl_single_slack_converged_variants": "; ".join(pp_converged_variants),
                "pypowsybl_single_slack_n_converged_of_5": len(pp_converged_variants),
                "matched_agreement_rows_of_5": int(sum(to_bool(value) for value in comp_case["agreement"])),
                "matched_disagreement_variants": "; ".join(matched_disagreements),
                "baseline_reading": classify_case(comp_case),
            }
        )
    return rows


def classify_case(comp_case: pd.DataFrame) -> str:
    pm_any = any(to_bool(value) for value in comp_case["powermodels_converged"])
    pp_any = any(to_bool(value) for value in comp_case["pypowsybl_single_slack_converged"])
    all_agree = all(to_bool(value) for value in comp_case["agreement"])
    if all_agree and not pm_any and not pp_any:
        return "both_tools_fail_all_shared_variants"
    if all_agree:
        return "matched_tools_agree"
    if pp_any and not pm_any:
        return "pypowsybl_recovers_a_shared_variant_that_powermodels_direct_pf_does_not"
    if pm_any and not pp_any:
        return "powermodels_recovers_a_shared_variant_that_pypowsybl_single_slack_does_not"
    return "mixed_tool_sensitivity"


def plot_heatmap(comparison_rows: list[dict[str, Any]]) -> None:
    comparison = pd.DataFrame(comparison_rows)
    by_key = {
        (row["case_name"], row["variant_name"]): row
        for _, row in comparison.iterrows()
    }

    y_labels: list[str] = []
    values: list[list[int]] = []
    for case_name in FOCUS_CASES:
        for tool_label, column in (
            ("PowerModels", "powermodels_converged"),
            ("PyPowSyBl Single slack", "pypowsybl_single_slack_converged"),
        ):
            y_labels.append(f"{CASE_LABELS[case_name]} | {tool_label}")
            values.append(
                [
                    1 if to_bool(by_key[(case_name, variant_name)][column]) else 0
                    for variant_name in SHARED_VARIANTS
                ]
            )

    fig, ax = plt.subplots(figsize=(9.8, 6.4))
    ax.imshow(
        values,
        cmap=matplotlib.colors.ListedColormap(["#b94b48", "#4f7f62"]),
        vmin=0,
        vmax=1,
        aspect="auto",
    )
    ax.set_xticks(range(len(SHARED_VARIANTS)))
    ax.set_xticklabels([VARIANT_LABELS[variant] for variant in SHARED_VARIANTS], rotation=25, ha="right")
    ax.set_yticks(range(len(y_labels)))
    ax.set_yticklabels(y_labels, fontsize=9)
    ax.set_title("Matched single-slack AC PF convergence")
    for i, row_values in enumerate(values):
        for j, value in enumerate(row_values):
            ax.text(j, i, "Y" if value else "N", ha="center", va="center", color="white", fontsize=9)
    fig.tight_layout()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_DIR / "matched_single_slack_convergence_heatmap.png", dpi=180)
    plt.close(fig)


def plot_pypowsybl_large_heatmap(comparison_rows: list[dict[str, Any]]) -> None:
    comparison = pd.DataFrame(comparison_rows)
    by_key = {
        (row["case_name"], row["variant_name"]): row
        for _, row in comparison.iterrows()
    }

    values = []
    y_labels = [CASE_LABELS[case_name] for case_name in FOCUS_CASES]
    for case_name in FOCUS_CASES:
        values.append(
            [
                1 if to_bool(by_key[(case_name, variant_name)]["pypowsybl_single_slack_converged"]) else 0
                for variant_name in SHARED_VARIANTS
            ]
        )

    fig, ax = plt.subplots(figsize=(8.8, 4.8))
    ax.imshow(
        values,
        cmap=matplotlib.colors.ListedColormap(["#b94b48", "#4f7f62"]),
        vmin=0,
        vmax=1,
        aspect="auto",
    )
    ax.set_xticks(range(len(SHARED_VARIANTS)))
    ax.set_xticklabels([VARIANT_LABELS[variant] for variant in SHARED_VARIANTS], rotation=25, ha="right")
    ax.set_yticks(range(len(y_labels)))
    ax.set_yticklabels(y_labels, fontsize=11)
    ax.set_title("Base large-case OpenLoadFlow AC PF convergence")
    for i, row_values in enumerate(values):
        for j, value in enumerate(row_values):
            ax.text(j, i, "Y" if value else "N", ha="center", va="center", color="white", fontsize=10)
    fig.tight_layout()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_DIR / "pypowsybl_base_large_single_slack_ac_convergence_heatmap.png", dpi=180)
    plt.close(fig)


def write_interpretation(summary_rows: list[dict[str, Any]], comparison_rows: list[dict[str, Any]]) -> None:
    summary = pd.DataFrame(summary_rows)
    comparison = pd.DataFrame(comparison_rows)

    lines = [
        "# Matched PyPowSyBl single-slack baseline",
        "",
        "This artifact isolates the first comparable PyPowSyBl layer: PowerModels `compute_ac_pf` versus PyPowSyBl/OpenLoadFlow with single slack, no reactive limits, no distributed slack, and no transformer, shunt, or phase-shifter controls.",
        "",
        "The PyPowSyBl matrix now includes the same five replacement columns used in the PowerModels base large-case plot: `Original`, `VS init`, `Gen P/Q`, `Gen P/Q + VS`, and `Gen P/Q + VG`.",
        "",
        "## Case-level result",
        "",
        "| Case | PowerModels direct | PyPowSyBl single slack | Reading |",
        "|---|---:|---:|---|",
    ]
    for _, row in summary.iterrows():
        lines.append(
            f"| {row['case_label']} | {row['powermodels_direct_n_converged_of_5']}/5 | "
            f"{row['pypowsybl_single_slack_n_converged_of_5']}/5 | {row['baseline_reading']} |"
        )

    lines.extend(
        [
            "",
            "## Shared-variant convergence",
            "",
            "| Case | Variant | PowerModels | PyPowSyBl single slack | Agreement | PyPowSyBl iterations |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for _, row in comparison.iterrows():
        lines.append(
            f"| {row['case_label']} | {row['variant_label']} | {yn(row['powermodels_converged'])} | "
            f"{yn(row['pypowsybl_single_slack_converged'])} | {yn(row['agreement'])} | "
            f"{row['pypowsybl_iteration_count']} |"
        )

    lines.extend(
        [
            "",
            "## What to change if the aligned baseline still fails",
            "",
            "Change one thing at a time, and keep the strict single-slack baseline as the reference.",
            "",
            "1. Align the slack bus explicitly and confirm whether both tools use the same reference/slack element.",
            "2. Try voltage initialization modes before changing controls: flat/uniform values, previous values when bus-voltage starts are available, and DC-derived starts if exposed by the API.",
            "3. Increase or tighten Newton settings only as a numerical test: maximum iterations, stopping tolerance, and solver-specific robustness options.",
            "4. Inspect the slack active-power absorption and generator P bounds separately; single-slack LF balances active-power mismatch at the slack and may push it far from the original dispatch.",
            "5. Inspect Q-limit pressure without enabling Q limits first. If many targets are outside limits, then run a second controlled experiment with reactive limits enabled.",
            "6. Enable distributed slack only after the single-slack result is understood.",
            "7. Enable transformer voltage controls, shunt controls, and phase-shifter regulation last, because those change the model rather than only the numerical route.",
            "",
            "The important interpretation is that disagreement or failure in PyPowSyBl does not automatically mean the operating point is infeasible. It can be a solver basin, initialization, slack-dispatch, reactive-limit, or controller-model difference. The OPF-fed PowerModels experiment is the anchor for testing whether a nearby feasible solved state exists.",
        ]
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "matched_single_slack_problem_case_interpretation.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)

    pm, pp = load_inputs()
    comparison_rows = create_comparison_rows(pm, pp)
    summary_rows = create_summary_rows(pm, comparison_rows)

    write_csv(OUTPUT_DIR / "matched_single_slack_problem_case_comparison.csv", comparison_rows)
    write_csv(OUTPUT_DIR / "matched_single_slack_problem_case_summary.csv", summary_rows)
    plot_heatmap(comparison_rows)
    plot_pypowsybl_large_heatmap(comparison_rows)
    write_interpretation(summary_rows, comparison_rows)

    print(f"Wrote {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
