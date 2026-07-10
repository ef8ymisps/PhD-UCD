"""Create focused PowerModels/PyPowSyBl PF convergence comparison artifacts.

This script reads existing base-case sweep CSVs only. It does not rerun
PowerModels or PyPowSyBl load flows.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "results" / "presentable" / "focused_pf_convergence_analysis"
FIGURE_DIR = OUTPUT_DIR / "figures"
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
    / "presentation_artifacts"
    / "base_case"
    / "presentable_summary_rows.csv"
)

FOCUS_CASES = [
    "case300_ieee",
    "case1888_rte",
    "case6470_rte",
    "case13659_pegase",
    "case78484_epigrids",
]

CASE_LABELS = {
    "case300_ieee": "300 IEEE",
    "case1888_rte": "1888 RTE",
    "case6470_rte": "6470 RTE",
    "case13659_pegase": "13659 PEGASE",
    "case78484_epigrids": "78484 EpiGrid",
}

PM_VARIANTS = [
    "original",
    "BV_from_warmstart",
    "G_PGQG_from",
    "G_PGQG_from_plus_BV_from",
    "G_PGQG_from_plus_GVG_from",
]

SHARED_VARIANTS = [
    "original",
    "BV_from_warmstart",
    "G_PGQG_from_plus_GVG_from",
]

VARIANT_LABELS = {
    "original": "Original",
    "BV_from_warmstart": "VS setpoints",
    "G_PGQG_from": "Gen power",
    "G_PGQG_from_plus_BV_from": "Gen power + VS",
    "G_PGQG_from_plus_GVG_from": "Gen P/Q + VG",
}


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def as_bool(value: object) -> bool:
    return str(value).strip().lower() == "true"


def pp_case_name(pm_case: str) -> str:
    return f"pglib_opf_{pm_case}"


def classify_pm(row: pd.Series) -> str:
    if bool(row.get("converged", False)) and bool(row.get("termination_status", False)):
        if row.get("variant_name") in {"G_PGQG_from", "G_PGQG_from_plus_BV_from", "G_PGQG_from_plus_GVG_from"}:
            return "converged_after_generator_setpoints"
        if row.get("variant_name") == "BV_from_warmstart":
            return "converged_after_voltage_warmstart"
        return "converged_original"
    return "non_converged"


def create_powermodels_diagnosis(pm: pd.DataFrame) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    focus = pm[pm["case_name"].isin(FOCUS_CASES)].copy()
    for _, row in focus.sort_values(["case_name", "variant_name"]).iterrows():
        rows.append(
            {
                "tool": "PowerModels.compute_ac_pf",
                "case_name": row["case_name"],
                "case_label": CASE_LABELS.get(row["case_name"], row["case_name"]),
                "variant_name": row["variant_name"],
                "variant_label": VARIANT_LABELS.get(row["variant_name"], row["variant_name"]),
                "converged": bool(row.get("converged", False)),
                "termination_status": bool(row.get("termination_status", False)),
                "classification": classify_pm(row),
                "gen_minus_load_mw_before": row.get("gen_minus_load_mw_before", ""),
                "gen_minus_load_mw_after": row.get("gen_minus_load_mw_after", ""),
                "slack_bus": row.get("slack_bus", ""),
                "slack_pg": row.get("slack_pg", ""),
                "slack_qg": row.get("slack_qg", ""),
                "min_vm": row.get("min_vm", ""),
                "max_vm": row.get("max_vm", ""),
                "max_branch_loading_ratio": row.get("max_branch_loading_ratio", ""),
                "n_applied_gen_pg": row.get("n_applied_gen_pg", ""),
                "n_applied_gen_qg": row.get("n_applied_gen_qg", ""),
                "n_applied_gen_vg": row.get("n_applied_gen_vg", ""),
                "solve_time_sec": row.get("solve_time_sec", ""),
            }
        )
    return rows


def create_matched_single_slack(pm: pd.DataFrame, pp: pd.DataFrame) -> list[dict[str, object]]:
    pp_single = pp[
        pp["base_case_name"].isin([pp_case_name(case) for case in FOCUS_CASES])
        & pp["parameter_scenario"].eq("strict_single_slack")
        & pp["variant_name"].isin(SHARED_VARIANTS)
    ].copy()
    pm_shared = pm[pm["case_name"].isin(FOCUS_CASES) & pm["variant_name"].isin(SHARED_VARIANTS)].copy()
    pp_by_key = {(row["base_case_name"], row["variant_name"]): row for _, row in pp_single.iterrows()}
    pm_by_key = {(row["case_name"], row["variant_name"]): row for _, row in pm_shared.iterrows()}

    rows: list[dict[str, object]] = []
    for case in FOCUS_CASES:
        for variant in SHARED_VARIANTS:
            pm_row = pm_by_key.get((case, variant))
            pp_row = pp_by_key.get((pp_case_name(case), variant))
            rows.append(
                {
                    "case_name": case,
                    "case_label": CASE_LABELS.get(case, case),
                    "variant_name": variant,
                    "variant_label": VARIANT_LABELS.get(variant, variant),
                    "powermodels_converged": "" if pm_row is None else bool(pm_row.get("converged", False)),
                    "pypowsybl_single_slack_converged": "" if pp_row is None else bool(pp_row.get("valid_converged", False)),
                    "agreement": ""
                    if pm_row is None or pp_row is None
                    else bool(pm_row.get("converged", False)) == bool(pp_row.get("valid_converged", False)),
                    "powermodels_gen_minus_load_mw_after": "" if pm_row is None else pm_row.get("gen_minus_load_mw_after", ""),
                    "pypowsybl_gen_minus_load_after": "" if pp_row is None else pp_row.get("gen_minus_load_after", ""),
                    "powermodels_slack_bus": "" if pm_row is None else pm_row.get("slack_bus", ""),
                    "pypowsybl_slack_bus_id": "" if pp_row is None else pp_row.get("slack_bus_id", ""),
                    "powermodels_slack_pg": "" if pm_row is None else pm_row.get("slack_pg", ""),
                    "pypowsybl_slack_active_power_mismatch": "" if pp_row is None else pp_row.get("slack_active_power_mismatch", ""),
                    "pypowsybl_status_text": "" if pp_row is None else pp_row.get("status_text", ""),
                    "pypowsybl_iteration_count": "" if pp_row is None else pp_row.get("iteration_count", ""),
                    "pypowsybl_target_q_outside_limits": ""
                    if pp_row is None
                    else pp_row.get("number_of_generators_with_target_q_outside_limits", ""),
                }
            )
    return rows


def create_case_summary(pm: pd.DataFrame, pp: pd.DataFrame) -> list[dict[str, object]]:
    matched = pd.DataFrame(create_matched_single_slack(pm, pp))
    pm_focus = pm[pm["case_name"].isin(FOCUS_CASES)]
    rows: list[dict[str, object]] = []
    for case in FOCUS_CASES:
        pm_case = pm_focus[pm_focus["case_name"].eq(case)]
        matched_case = matched[matched["case_name"].eq(case)]
        rows.append(
            {
                "case_name": case,
                "case_label": CASE_LABELS.get(case, case),
                "powermodels_converged_variants": "; ".join(
                    row["variant_name"] for _, row in pm_case.iterrows() if bool(row.get("converged", False))
                ),
                "powermodels_n_converged_of_5": int(pm_case["converged"].fillna(False).astype(bool).sum()),
                "pypowsybl_single_slack_converged_variants": "; ".join(
                    row["variant_name"]
                    for _, row in matched_case.iterrows()
                    if str(row["pypowsybl_single_slack_converged"]).lower() == "true"
                ),
                "pypowsybl_n_converged_of_3": int(
                    sum(str(value).lower() == "true" for value in matched_case["pypowsybl_single_slack_converged"])
                ),
                "matched_agreement_rows_of_3": int(sum(str(value).lower() == "true" for value in matched_case["agreement"])),
            }
        )
    return rows


def plot_matched_heatmap(rows: list[dict[str, object]]) -> None:
    labels = []
    values = []
    for case in FOCUS_CASES:
        for tool_key, tool_label in (
            ("powermodels_converged", "PowerModels"),
            ("pypowsybl_single_slack_converged", "PyPowSyBl single slack"),
        ):
            labels.append(f"{CASE_LABELS.get(case, case)} | {tool_label}")
            row_values = []
            for variant in SHARED_VARIANTS:
                match = next(row for row in rows if row["case_name"] == case and row["variant_name"] == variant)
                row_values.append(1 if str(match[tool_key]).lower() == "true" else 0)
            values.append(row_values)

    fig, ax = plt.subplots(figsize=(8.8, max(4.8, 0.48 * len(labels))))
    ax.imshow(
        values,
        cmap=matplotlib.colors.ListedColormap(["#b94b48", "#4f7f62"]),
        vmin=0,
        vmax=1,
        aspect="auto",
    )
    ax.set_xticks(range(len(SHARED_VARIANTS)))
    ax.set_xticklabels([VARIANT_LABELS[variant] for variant in SHARED_VARIANTS], rotation=25, ha="right")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_title("Focused base-case AC PF convergence: matched variants")
    for y, row in enumerate(values):
        for x, value in enumerate(row):
            ax.text(x, y, "Y" if value else "N", ha="center", va="center", color="white", fontsize=8)
    fig.tight_layout()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_DIR / "matched_powermodels_pypowsybl_single_slack_heatmap.png", dpi=160)
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    pm = pd.read_csv(PM_ROWS)
    pp = pd.read_csv(PP_ROWS)

    pm_diagnosis = create_powermodels_diagnosis(pm)
    matched = create_matched_single_slack(pm, pp)
    summary = create_case_summary(pm, pp)

    write_csv(OUTPUT_DIR / "powermodels_base_failure_diagnosis.csv", pm_diagnosis)
    write_csv(OUTPUT_DIR / "pypowsybl_matched_single_slack_comparison.csv", matched)
    write_csv(OUTPUT_DIR / "case300_1888_6470_13659_78484_convergence_summary.csv", summary)
    write_csv(OUTPUT_DIR / "case300_1888_6470_13659_convergence_summary.csv", summary)
    plot_matched_heatmap(matched)

    print(f"Wrote focused PF convergence artifacts to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
