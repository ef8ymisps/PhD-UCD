"""Create presentable artifacts from PowerModels OPF-to-PF consistency rows."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = (
    PROJECT_ROOT
    / "results"
    / "presentable"
    / "powermodels_lf_replacement_sweep"
    / "opf_to_pf_consistency"
)
SUMMARY_PATH = OUTPUT_DIR / "opf_to_pf_consistency_summary.csv"
DIRECT_PF_PATH = (
    PROJECT_ROOT
    / "results"
    / "presentable"
    / "powermodels_lf_replacement_sweep"
    / "base_full"
    / "powermodels_ac_pf_sweep_rows.csv"
)
COMPACT_PATH = OUTPUT_DIR / "opf_to_pf_consistency_compact.csv"
DIRECT_FAILURE_PATH = OUTPUT_DIR / "focus_direct_pf_nonconverged_variants.csv"
RECOVERY_PATH = OUTPUT_DIR / "opf_fed_recovery_from_direct_failures.csv"
LEGACY_RECOVERY_PATH = OUTPUT_DIR / "opf_seeded_recovery_from_direct_failures.csv"
CASE_RECOVERY_PATH = OUTPUT_DIR / "opf_fed_case_recovery_summary.csv"
ATTEMPT_LOG_PATH = OUTPUT_DIR / "opf_to_pf_attempt_log.csv"
FIGURE_PATH = OUTPUT_DIR / "opf_to_pf_state_difference_heatmap.png"
RECOVERY_FIGURE_PATH = OUTPUT_DIR / "direct_pf_vs_opf_fed_compute_ac_pf_heatmap.png"
LEGACY_RECOVERY_FIGURE_PATH = OUTPUT_DIR / "direct_pf_vs_opf_seeded_recovery_heatmap.png"
CASE_RECOVERY_FIGURE_PATH = OUTPUT_DIR / "opf_fed_case_recovery_summary_heatmap.png"

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

VARIANT_LABELS = {
    "original": "Original",
    "BV_from_warmstart": "VS setpoints",
    "G_PGQG_from": "Gen power",
    "G_PGQG_from_plus_BV_from": "Gen power + VS",
    "G_PGQG_from_plus_GVG_from": "Gen P/Q + VG",
}

DIFF_COLUMNS = [
    ("max_abs_vm_diff", "max |Vm|"),
    ("max_abs_va_rad_diff", "max |Va| rad"),
    ("max_abs_pg_diff_mw", "max |PG| MW"),
    ("max_abs_qg_diff_mvar", "max |QG| MVAr"),
    ("max_abs_branch_pf_diff_mw", "max |Pflow| MW"),
    ("max_abs_branch_qf_diff_mvar", "max |Qflow| MVAr"),
]

DIRECT_CONTEXT_COLUMNS = [
    "case_name",
    "case_label",
    "variant_name",
    "variant_label",
    "direct_pf_converged",
    "direct_pf_termination_status",
    "direct_pf_solve_time_sec",
    "direct_pf_gen_minus_load_mw_after",
    "direct_pf_slack_bus",
    "direct_pf_slack_pg",
]


def display_number(value: float) -> str:
    if pd.isna(value):
        return ""
    if abs(value) < 1e-14:
        return "0"
    if abs(value) < 1e-3 or abs(value) >= 1e3:
        return f"{value:.1e}"
    return f"{value:.3g}"


def create_compact_summary(df: pd.DataFrame) -> pd.DataFrame:
    compact = df.copy()
    compact.insert(1, "case_label", compact["case_name"].map(CASE_LABELS).fillna(compact["case_name"]))
    compact.insert(3, "variant_label", compact["variant_name"].map(VARIANT_LABELS).fillna(compact["variant_name"]))
    columns = [
        "case_name",
        "case_label",
        "variant_name",
        "variant_label",
        "opf_solver",
        "opf_print_level",
        "opf_max_cpu_time_sec",
        "opf_max_wall_time_sec",
        "opf_max_iter",
        "detail_output_mode",
        "opf_status",
        "opf_solved",
        "pf_status",
        "pf_converged",
        "opf_objective",
        "opf_solve_time_sec",
        "pf_solve_time_sec",
        "max_abs_vm_diff",
        "max_abs_va_rad_diff",
        "max_abs_pg_diff_mw",
        "max_abs_qg_diff_mvar",
        "max_abs_branch_pf_diff_mw",
        "max_abs_branch_qf_diff_mvar",
        "n_pf_q_limit_violations",
    ]
    return compact[[column for column in columns if column in compact.columns]]


def create_direct_failure_context(direct_pf: pd.DataFrame) -> pd.DataFrame:
    focus = direct_pf[direct_pf["case_name"].isin(FOCUS_CASES)].copy()
    failures = focus[~focus["converged"].astype(bool)].copy()
    failures["case_label"] = failures["case_name"].map(CASE_LABELS).fillna(failures["case_name"])
    failures["variant_label"] = failures["variant_name"].map(VARIANT_LABELS).fillna(failures["variant_name"])
    failures["direct_pf_converged"] = failures["converged"].astype(bool)
    failures["direct_pf_termination_status"] = failures["termination_status"].astype(bool)
    failures["direct_pf_solve_time_sec"] = failures.get("solve_time_sec", pd.NA)
    failures["direct_pf_gen_minus_load_mw_after"] = failures.get("gen_minus_load_mw_after", pd.NA)
    failures["direct_pf_slack_bus"] = failures.get("slack_bus", pd.NA)
    failures["direct_pf_slack_pg"] = failures.get("slack_pg", pd.NA)
    return failures[DIRECT_CONTEXT_COLUMNS].sort_values(["case_name", "variant_name"]).reset_index(drop=True)


def create_recovery_table(
    direct_failures: pd.DataFrame,
    compact: pd.DataFrame,
    attempt_log: pd.DataFrame,
) -> pd.DataFrame:
    attempt_log = attempt_log.rename(
        columns={
            "opf_seeded_attempt_status": "opf_fed_attempt_status",
            "opf_seeded_attempt_note": "opf_fed_attempt_note",
        }
    )
    recovery = direct_failures.merge(
        compact.drop(columns=["case_label", "variant_label"], errors="ignore"),
        how="left",
        on=["case_name", "variant_name"],
    )
    if not attempt_log.empty:
        recovery = recovery.merge(attempt_log, how="left", on=["case_name", "variant_name"])
    else:
        recovery["opf_fed_attempt_status"] = pd.NA
        recovery["opf_fed_attempt_note"] = pd.NA
    recovery["opf_fed_run_available"] = recovery["opf_status"].notna()
    if "opf_fed_attempt_status" in recovery.columns:
        recovery.loc[recovery["opf_fed_run_available"], ["opf_fed_attempt_status", "opf_fed_attempt_note"]] = pd.NA
    recovery["opf_fed_recovered_convergence"] = recovery["pf_converged"].fillna(False).astype(bool)
    recovery["solved_state_note"] = recovery.apply(
        lambda row: "not_run"
        if not row["opf_fed_run_available"] and pd.isna(row.get("opf_fed_attempt_status"))
        else row.get("opf_fed_attempt_status")
        if not row["opf_fed_run_available"]
        else (
            "pf_not_converged"
            if not bool(row["pf_converged"])
            else (
                "network_state_matches; inspect_reactive_sharing"
                if pd.notna(row.get("max_abs_qg_diff_mvar")) and row["max_abs_qg_diff_mvar"] > 1e-3
                else "network_and_generator_state_match"
            )
        ),
        axis=1,
    )
    for column in ["opf_solver", "opf_print_level", "opf_max_cpu_time_sec", "opf_max_wall_time_sec", "opf_max_iter", "detail_output_mode"]:
        if column not in recovery.columns:
            recovery[column] = pd.NA
    ordered = DIRECT_CONTEXT_COLUMNS + [
        "opf_fed_run_available",
        "opf_status",
        "opf_solved",
        "pf_status",
        "pf_converged",
        "opf_fed_recovered_convergence",
        "opf_solver",
        "opf_print_level",
        "opf_max_cpu_time_sec",
        "opf_max_wall_time_sec",
        "opf_max_iter",
        "detail_output_mode",
        "opf_objective",
        "opf_solve_time_sec",
        "pf_solve_time_sec",
        "max_abs_vm_diff",
        "max_abs_va_rad_diff",
        "max_abs_pg_diff_mw",
        "max_abs_qg_diff_mvar",
        "max_abs_branch_pf_diff_mw",
        "max_abs_branch_qf_diff_mvar",
        "n_pf_q_limit_violations",
        "opf_fed_attempt_status",
        "opf_fed_attempt_note",
        "solved_state_note",
    ]
    return recovery[ordered]


def create_case_recovery_summary(recovery: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for case_name in FOCUS_CASES:
        case = recovery[recovery["case_name"].eq(case_name)].copy()
        if case.empty:
            continue
        run_mask = case["opf_fed_run_available"].fillna(False).astype(bool)
        recovered_mask = case["opf_fed_recovered_convergence"].fillna(False).astype(bool)
        qg_values = pd.to_numeric(case.loc[run_mask, "max_abs_qg_diff_mvar"], errors="coerce")
        vm_values = pd.to_numeric(case.loc[run_mask, "max_abs_vm_diff"], errors="coerce")
        pg_values = pd.to_numeric(case.loc[run_mask, "max_abs_pg_diff_mw"], errors="coerce")
        branch_values = pd.to_numeric(case.loc[run_mask, "max_abs_branch_pf_diff_mw"], errors="coerce")
        attempt_statuses = sorted(str(value) for value in case["opf_fed_attempt_status"].dropna().unique())

        if recovered_mask.sum() == len(case):
            max_qg = qg_values.max()
            solved_state_summary = (
                "LF converged; network state matches OPF; inspect reactive sharing"
                if pd.notna(max_qg) and max_qg > 1e-3
                else "LF converged; solved state matches OPF"
            )
        elif recovered_mask.sum() > 0:
            solved_state_summary = "partial OPF-fed recovery"
        elif attempt_statuses:
            solved_state_summary = "; ".join(attempt_statuses)
        else:
            solved_state_summary = "not_run"

        rows.append(
            {
                "case_name": case_name,
                "case_label": CASE_LABELS.get(case_name, case_name),
                "direct_pf_failed_variants": "; ".join(case["variant_name"]),
                "n_direct_pf_failures": int(len(case)),
                "n_opf_fed_runs": int(run_mask.sum()),
                "n_opf_fed_pf_converged": int(recovered_mask.sum()),
                "all_completed_opf_fed_runs_converged": bool(run_mask.sum() > 0 and (~run_mask | recovered_mask).all()),
                "max_abs_vm_diff": vm_values.max() if not vm_values.empty else pd.NA,
                "max_abs_pg_diff_mw": pg_values.max() if not pg_values.empty else pd.NA,
                "max_abs_qg_diff_mvar": qg_values.max() if not qg_values.empty else pd.NA,
                "max_abs_branch_pf_diff_mw": branch_values.max() if not branch_values.empty else pd.NA,
                "attempt_status": "; ".join(attempt_statuses),
                "solved_state_summary": solved_state_summary,
            }
        )
    return pd.DataFrame(rows)


def plot_state_difference_heatmap(compact: pd.DataFrame) -> None:
    if compact.empty:
        return

    rows = compact.sort_values(["case_name", "variant_name"]).reset_index(drop=True)
    labels = [f"{row.case_label} | {row.variant_label}" for row in rows.itertuples()]
    values = rows[[name for name, _ in DIFF_COLUMNS]].astype(float).to_numpy()
    clipped = np.where(np.isnan(values), np.nan, np.maximum(values, 1e-12))
    color_values = np.log10(clipped)

    fig_height = max(3.2, 0.46 * len(rows) + 1.8)
    fig, ax = plt.subplots(figsize=(13.2, fig_height))
    im = ax.imshow(color_values, aspect="auto", cmap="YlOrRd", vmin=-12, vmax=1)

    ax.set_title("OPF-fed compute_ac_pf solved-state differences", fontsize=18, pad=12)
    ax.set_xticks(range(len(DIFF_COLUMNS)))
    ax.set_xticklabels([label for _, label in DIFF_COLUMNS], rotation=25, ha="right", fontsize=12)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=12)
    ax.tick_params(length=0)

    for row_idx in range(values.shape[0]):
        for col_idx in range(values.shape[1]):
            value = values[row_idx, col_idx]
            text = display_number(value)
            color = "white" if not pd.isna(value) and value > 0.1 else "black"
            ax.text(col_idx, row_idx, text, ha="center", va="center", fontsize=11, color=color)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.2)

    cbar = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    cbar.set_label("log10 absolute difference", fontsize=11)
    cbar.ax.tick_params(labelsize=10)
    fig.tight_layout()
    FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_PATH, dpi=180)
    plt.close(fig)


def plot_recovery_heatmap(recovery: pd.DataFrame) -> None:
    if recovery.empty:
        return

    rows = recovery.sort_values(["case_name", "variant_name"]).reset_index(drop=True)
    labels = [f"{row.case_label} | {row.variant_label}" for row in rows.itertuples()]
    values = np.column_stack(
        [
            rows["direct_pf_converged"].fillna(False).astype(bool).astype(int).to_numpy(),
            rows["opf_fed_recovered_convergence"].fillna(False).astype(bool).astype(int).to_numpy(),
        ]
    )

    fig_height = max(5.0, 0.34 * len(rows) + 1.8)
    fig, ax = plt.subplots(figsize=(7.6, fig_height))
    ax.imshow(
        values,
        cmap=matplotlib.colors.ListedColormap(["#b94b48", "#4f7f62"]),
        vmin=0,
        vmax=1,
        aspect="auto",
    )
    ax.set_title("Direct PF failures vs OPF-fed compute_ac_pf", fontsize=16, pad=12)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["Direct PF", "OPF-fed PF"], rotation=20, ha="right", fontsize=11)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.tick_params(length=0)

    for y, row in enumerate(values):
        for x, value in enumerate(row):
            if x == 1 and not bool(rows.loc[y, "opf_fed_run_available"]):
                text = "-"
                color = "black"
            else:
                text = "Y" if value else "N"
                color = "white"
            ax.text(x, y, text, ha="center", va="center", fontsize=10, color=color)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.2)

    fig.tight_layout()
    RECOVERY_FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(RECOVERY_FIGURE_PATH, dpi=180)
    fig.savefig(LEGACY_RECOVERY_FIGURE_PATH, dpi=180)
    plt.close(fig)


def plot_case_recovery_heatmap(case_summary: pd.DataFrame) -> None:
    if case_summary.empty:
        return

    rows = case_summary.reset_index(drop=True)
    labels = rows["case_label"].tolist()
    direct_values = np.zeros(len(rows))
    opf_values = np.where(
        rows["n_opf_fed_runs"].astype(int).to_numpy() == 0,
        0.5,
        (
            rows["n_opf_fed_pf_converged"].astype(int).to_numpy()
            == rows["n_opf_fed_runs"].astype(int).to_numpy()
        ).astype(float),
    )
    values = np.column_stack([direct_values, opf_values])

    fig, ax = plt.subplots(figsize=(7.4, max(3.8, 0.54 * len(rows) + 1.8)))
    ax.imshow(
        values,
        cmap=matplotlib.colors.ListedColormap(["#b94b48", "#d7d2bf", "#4f7f62"]),
        vmin=0,
        vmax=1,
        aspect="auto",
    )
    ax.set_title("OPF-fed compute_ac_pf convergence for difficult cases", fontsize=16, pad=12)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["Direct PF failures", "OPF-fed PF"], rotation=20, ha="right", fontsize=11)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=11)
    ax.tick_params(length=0)

    for y, row in rows.iterrows():
        texts = [
            f"{int(row.n_direct_pf_failures)} fail",
            "-"
            if int(row.n_opf_fed_runs) == 0
            else f"{int(row.n_opf_fed_pf_converged)}/{int(row.n_opf_fed_runs)}",
        ]
        for x, text in enumerate(texts):
            ax.text(x, y, text, ha="center", va="center", fontsize=10, color="white" if values[y, x] in (0, 1) else "black")

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.2)

    fig.tight_layout()
    CASE_RECOVERY_FIGURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(CASE_RECOVERY_FIGURE_PATH, dpi=180)
    plt.close(fig)


def main() -> None:
    if not SUMMARY_PATH.exists():
        raise FileNotFoundError(f"Missing OPF-to-PF summary CSV: {SUMMARY_PATH}")
    if not DIRECT_PF_PATH.exists():
        raise FileNotFoundError(f"Missing direct PF sweep CSV: {DIRECT_PF_PATH}")
    df = pd.read_csv(SUMMARY_PATH)
    direct_pf = pd.read_csv(DIRECT_PF_PATH)
    attempt_log = (
        pd.read_csv(ATTEMPT_LOG_PATH)
        if ATTEMPT_LOG_PATH.exists()
        else pd.DataFrame(columns=["case_name", "variant_name", "opf_seeded_attempt_status", "opf_seeded_attempt_note"])
    )
    compact = create_compact_summary(df)
    direct_failures = create_direct_failure_context(direct_pf)
    recovery = create_recovery_table(direct_failures, compact, attempt_log)
    case_recovery = create_case_recovery_summary(recovery)
    COMPACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    compact.to_csv(COMPACT_PATH, index=False)
    direct_failures.to_csv(DIRECT_FAILURE_PATH, index=False)
    recovery.to_csv(RECOVERY_PATH, index=False)
    recovery.to_csv(LEGACY_RECOVERY_PATH, index=False)
    case_recovery.to_csv(CASE_RECOVERY_PATH, index=False)
    plot_state_difference_heatmap(compact)
    plot_recovery_heatmap(recovery)
    plot_case_recovery_heatmap(case_recovery)
    print(f"Wrote OPF-to-PF presentable artifacts to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
