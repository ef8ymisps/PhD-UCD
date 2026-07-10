"""Create focused PyPowSyBl/OpenLoadFlow presentation artifacts.

The existing PyPowSyBl convergence heatmap intentionally drops the strict
single-slack rows. This script adds aligned plots that keep strict single slack
visible, so cases such as case300_ieee show the convergence recovered by the
Gen P/Q + VG replacement.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPOWSYBL_ROOT = PROJECT_ROOT / "results" / "presentable" / "pypowsybl_lf_replacement_sweep"
PYPOWSYBL_BASE_DIR = PYPOWSYBL_ROOT / "presentation_artifacts" / "base_case"
POWERMODELS_ROOT = PROJECT_ROOT / "results" / "presentable" / "powermodels_lf_replacement_sweep"
OUTPUT_DIR = PYPOWSYBL_BASE_DIR / "focused_case_analysis"
FIGURE_DIR = OUTPUT_DIR / "figures"

VARIANT_ORDER = [
    "original",
    "BV_from_warmstart",
    "G_PGQG_from_plus_GVG_from",
]

VARIANT_LABELS = {
    "original": "Original",
    "BV_from_warmstart": "VS init",
    "G_PGQG_from_plus_GVG_from": "Gen P/Q + VG",
}

PF_CONTROL_ORDER = [
    "strict_single_slack",
    "single_slack_q_limits",
    "distributed_q_limits",
    "operational_controls",
]

PF_CONTROL_LABELS = {
    "strict_single_slack": "Single slack",
    "single_slack_q_limits": "Single slack + Q limits",
    "distributed_q_limits": "Distributed + Q limits",
    "operational_controls": "Operational controls",
}

CASE_LABELS = {
    "pglib_opf_case300_ieee": "300 IEEE",
    "pglib_opf_case1354_pegase": "1354 PEGASE",
    "pglib_opf_case1888_rte": "1888 RTE",
    "pglib_opf_case6470_rte": "6470 RTE",
    "pglib_opf_case9241_pegase": "9241 PEGASE",
    "pglib_opf_case13659_pegase": "13659 PEGASE",
    "pglib_opf_case24464_goc": "24464 GOC",
}

LARGE_CASE_ORDER = [
    "pglib_opf_case300_ieee",
    "pglib_opf_case1354_pegase",
    "pglib_opf_case1888_rte",
    "pglib_opf_case6470_rte",
    "pglib_opf_case9241_pegase",
    "pglib_opf_case13659_pegase",
    "pglib_opf_case24464_goc",
]

FOCUS_CASES = [
    "pglib_opf_case300_ieee",
    "pglib_opf_case1888_rte",
]


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def case_label(case_name: str) -> str:
    return CASE_LABELS.get(case_name, case_name.replace("pglib_opf_", ""))


def is_valid(row: pd.Series | None) -> bool:
    if row is None:
        return False
    if "valid_converged" in row:
        return bool(row["valid_converged"])
    return bool(row.get("converged", False))


def plot_convergence_heatmap(summary: pd.DataFrame, cases: list[str], output_path: Path, title: str) -> None:
    by_key = {
        (row.base_case_name, row.parameter_scenario, row.variant_name): row
        for row in summary.itertuples(index=False)
    }
    y_labels: list[str] = []
    values: list[list[int]] = []
    for case in cases:
        if case not in set(summary["base_case_name"]):
            continue
        for control in PF_CONTROL_ORDER:
            y_labels.append(f"{case_label(case)} | {PF_CONTROL_LABELS[control]}")
            row_values = []
            for variant in VARIANT_ORDER:
                row = by_key.get((case, control, variant))
                row_values.append(1 if row is not None and bool(getattr(row, "valid_converged", getattr(row, "converged", False))) else 0)
            values.append(row_values)

    fig, ax = plt.subplots(figsize=(8.8, max(4.8, 0.42 * len(y_labels))))
    ax.imshow(
        values,
        cmap=matplotlib.colors.ListedColormap(["#b94b48", "#4f7f62"]),
        vmin=0,
        vmax=1,
        aspect="auto",
    )
    ax.set_xticks(range(len(VARIANT_ORDER)))
    ax.set_xticklabels([VARIANT_LABELS[variant] for variant in VARIANT_ORDER], rotation=25, ha="right")
    ax.set_yticks(range(len(y_labels)))
    ax.set_yticklabels(y_labels, fontsize=8)
    ax.set_title(title)
    for i, row_values in enumerate(values):
        for j, value in enumerate(row_values):
            ax.text(j, i, "Y" if value else "N", ha="center", va="center", color="white", fontsize=8)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def create_strict_status(summary: pd.DataFrame) -> list[dict[str, object]]:
    strict = summary[
        summary["parameter_scenario"].eq("strict_single_slack")
        & summary["variant_name"].eq("G_PGQG_from_plus_GVG_from")
    ].copy()
    rows: list[dict[str, object]] = []
    for _, row in strict.sort_values("base_case_name").iterrows():
        rows.append(
            {
                "base_case_name": row["base_case_name"],
                "case_label": case_label(str(row["base_case_name"])),
                "variant_name": row["variant_name"],
                "parameter_scenario": row["parameter_scenario"],
                "valid_converged": bool(row["valid_converged"]),
                "status_text": row.get("status_text", ""),
                "iteration_count": row.get("iteration_count", ""),
                "gen_minus_load_after": row.get("gen_minus_load_after", ""),
                "slack_active_power_mismatch": row.get("slack_active_power_mismatch", ""),
                "target_q_outside_limits": row.get("number_of_generators_with_target_q_outside_limits", ""),
            }
        )
    return rows


def create_state_proxy(summary: pd.DataFrame) -> list[dict[str, object]]:
    pm_path = POWERMODELS_ROOT / "base_full" / "powermodels_ac_pf_sweep_rows.csv"
    pm = pd.read_csv(pm_path)
    pp = summary[
        summary["variant_name"].eq("G_PGQG_from_plus_GVG_from")
        & summary["parameter_scenario"].eq("strict_single_slack")
        & summary["base_case_name"].isin(FOCUS_CASES)
    ].copy()

    rows: list[dict[str, object]] = []
    for case in FOCUS_CASES:
        pm_case = case.replace("pglib_opf_", "")
        pm_row = pm[
            pm["case_name"].eq(pm_case)
            & pm["variant_name"].eq("G_PGQG_from_plus_GVG_from")
        ]
        pp_row = pp[pp["base_case_name"].eq(case)]
        if pm_row.empty or pp_row.empty:
            continue
        pm_r = pm_row.iloc[0]
        pp_r = pp_row.iloc[0]
        rows.append(
            {
                "base_case_name": case,
                "case_label": case_label(case),
                "variant_name": "G_PGQG_from_plus_GVG_from",
                "pypowsybl_parameter_scenario": "strict_single_slack",
                "powermodels_converged": bool(pm_r.get("converged", False)),
                "pypowsybl_valid_converged": bool(pp_r.get("valid_converged", False)),
                "powermodels_gen_minus_load_mw_after": pm_r.get("gen_minus_load_mw_after", ""),
                "pypowsybl_gen_minus_load_after": pp_r.get("gen_minus_load_after", ""),
                "powermodels_slack_bus": pm_r.get("slack_bus", ""),
                "pypowsybl_slack_bus_id": pp_r.get("slack_bus_id", ""),
                "powermodels_slack_pg": pm_r.get("slack_pg", ""),
                "pypowsybl_slack_active_power_mismatch": pp_r.get("slack_active_power_mismatch", ""),
                "powermodels_slack_qg": pm_r.get("slack_qg", ""),
                "pypowsybl_target_q_outside_limits": pp_r.get("number_of_generators_with_target_q_outside_limits", ""),
                "powermodels_min_vm": pm_r.get("min_vm", ""),
                "powermodels_max_vm": pm_r.get("max_vm", ""),
                "pypowsybl_status_text": pp_r.get("status_text", ""),
                "pypowsybl_iteration_count": pp_r.get("iteration_count", ""),
                "note": "Aggregate proxy only; bus/generator/branch solved-state tables are not in the current PyPowSyBl summary CSV.",
            }
        )
    return rows


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)

    summary = pd.read_csv(PYPOWSYBL_BASE_DIR / "presentable_summary_rows.csv")

    plot_convergence_heatmap(
        summary,
        LARGE_CASE_ORDER,
        FIGURE_DIR / "pypowsybl_base_large_ac_convergence_with_strict_heatmap.png",
        "Base large-case OpenLoadFlow AC PF convergence",
    )
    plot_convergence_heatmap(
        summary,
        FOCUS_CASES,
        FIGURE_DIR / "pypowsybl_case300_1888_ac_convergence_with_strict_heatmap.png",
        "OpenLoadFlow AC PF convergence focus: 300 IEEE and 1888 RTE",
    )

    write_csv(OUTPUT_DIR / "gen_pq_vg_strict_single_slack_status_by_case.csv", create_strict_status(summary))
    write_csv(OUTPUT_DIR / "case300_1888_powermodels_vs_pypowsybl_state_proxy.csv", create_state_proxy(summary))

    print(f"Wrote focused PyPowSyBl artifacts to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
