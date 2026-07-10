"""Diagnose PG/QG plus VS versus PG/QG plus VG convergence behavior."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SWEEP_ROWS = (
    PROJECT_ROOT
    / "results"
    / "presentable"
    / "powermodels_lf_replacement_sweep"
    / "base_full"
    / "powermodels_ac_pf_sweep_rows.csv"
)
OUTPUT_DIR = PROJECT_ROOT / "results" / "presentable" / "focused_pf_convergence_analysis"
FIGURE_DIR = OUTPUT_DIR / "figures"

FOCUS_CASES = [
    "case300_ieee",
    "case1888_rte",
    "case6470_rte",
    "case13659_pegase",
]

CASE_LABELS = {
    "case300_ieee": "300 IEEE",
    "case1888_rte": "1888 RTE",
    "case6470_rte": "6470 RTE",
    "case13659_pegase": "13659 PEGASE",
}

VARIANT_ORDER = [
    "G_PGQG_from",
    "G_PGQG_from_plus_BV_from",
    "G_PGQG_from_plus_GVG_from",
]

VARIANT_LABELS = {
    "G_PGQG_from": "Gen P/Q",
    "G_PGQG_from_plus_BV_from": "Gen P/Q + VS",
    "G_PGQG_from_plus_GVG_from": "Gen P/Q + VG",
}

SEMANTICS = {
    "G_PGQG_from": "Generator active/reactive powers are replaced; default PF starts are then created from the resulting data.",
    "G_PGQG_from_plus_BV_from": "Generator active/reactive powers are replaced, default starts are created, then bus vm_start/va_start are overwritten from voltage-setpoint notes. VS changes initialization only.",
    "G_PGQG_from_plus_GVG_from": "Generator active/reactive powers and generator vg targets are replaced, then default starts are created. VG changes generator voltage setpoints/control targets.",
}


def bool_text(value: object) -> str:
    return "Y" if str(value).lower() == "true" else "N"


def classify_case(rows: dict[str, pd.Series]) -> str:
    pg = bool(rows["G_PGQG_from"].converged)
    bv = bool(rows["G_PGQG_from_plus_BV_from"].converged)
    vg = bool(rows["G_PGQG_from_plus_GVG_from"].converged)
    if pg and bv and vg:
        return "all three converge; voltage starts/targets do not change the outcome"
    if (not pg) and bv and (not vg):
        return "VS start recovers Newton convergence; VG target alone does not"
    if pg and (not bv) and vg:
        return "VS start harms convergence; PG/QG and VG target converge"
    if not pg and not bv and not vg:
        return "PG/QG operating point remains difficult; neither VS starts nor VG targets recover direct PF"
    if pg and not bv and not vg:
        return "PG/QG converges, but both voltage modifications destabilize direct PF"
    if not pg and not bv and vg:
        return "VG target recovers convergence; VS start does not"
    if not pg and bv and vg:
        return "both voltage information paths recover convergence"
    return "mixed behavior; inspect case-specific voltage/reactive controls"


def create_diagnostics(df: pd.DataFrame) -> pd.DataFrame:
    focus = df[df["case_name"].isin(FOCUS_CASES) & df["variant_name"].isin(VARIANT_ORDER)].copy()
    by_key = {(row.case_name, row.variant_name): row for row in focus.itertuples(index=False)}
    rows: list[dict[str, object]] = []

    for case in FOCUS_CASES:
        case_rows = {variant: by_key[(case, variant)] for variant in VARIANT_ORDER}
        classification = classify_case(case_rows)
        pg = case_rows["G_PGQG_from"]
        bv = case_rows["G_PGQG_from_plus_BV_from"]
        vg = case_rows["G_PGQG_from_plus_GVG_from"]
        for variant in VARIANT_ORDER:
            row = case_rows[variant]
            rows.append(
                {
                    "case_name": case,
                    "case_label": CASE_LABELS.get(case, case),
                    "variant_name": variant,
                    "variant_label": VARIANT_LABELS[variant],
                    "converged": bool(row.converged),
                    "termination_status": bool(row.termination_status),
                    "n_applied_gen_pg": int(row.n_applied_gen_pg),
                    "n_applied_gen_qg": int(row.n_applied_gen_qg),
                    "n_applied_bus_voltage": int(row.n_applied_bus_voltage),
                    "n_applied_gen_vg": int(row.n_applied_gen_vg),
                    "gen_minus_load_mw_after": row.gen_minus_load_mw_after,
                    "slack_bus": row.slack_bus,
                    "slack_pg": row.slack_pg,
                    "min_vm": row.min_vm,
                    "max_vm": row.max_vm,
                    "max_abs_va_deg": row.max_abs_va_deg,
                    "solve_time_sec": row.solve_time_sec,
                    "change_relative_to_gen_pq": "baseline"
                    if variant == "G_PGQG_from"
                    else (
                        "VS changes bus vm_start/va_start only"
                        if variant == "G_PGQG_from_plus_BV_from"
                        else "VG changes generator voltage setpoints"
                    ),
                    "case_classification": classification,
                    "pgqg_converged": bool(pg.converged),
                    "pgqg_plus_vs_converged": bool(bv.converged),
                    "pgqg_plus_vg_converged": bool(vg.converged),
                }
            )
    return pd.DataFrame(rows)


def create_case_summary(diagnostics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for case, case_df in diagnostics.groupby("case_name", sort=False):
        wide = {row.variant_name: row for row in case_df.itertuples(index=False)}
        rows.append(
            {
                "case_name": case,
                "case_label": CASE_LABELS.get(case, case),
                "gen_pq": bool_text(wide["G_PGQG_from"].converged),
                "gen_pq_plus_vs": bool_text(wide["G_PGQG_from_plus_BV_from"].converged),
                "gen_pq_plus_vg": bool_text(wide["G_PGQG_from_plus_GVG_from"].converged),
                "n_applied_bus_voltage_in_vs": int(wide["G_PGQG_from_plus_BV_from"].n_applied_bus_voltage),
                "n_applied_gen_vg_in_vg": int(wide["G_PGQG_from_plus_GVG_from"].n_applied_gen_vg),
                "case_classification": wide["G_PGQG_from"].case_classification,
            }
        )
    return pd.DataFrame(rows)


def plot_heatmap(diagnostics: pd.DataFrame) -> None:
    values = []
    y_labels = []
    for case in FOCUS_CASES:
        case_df = diagnostics[diagnostics["case_name"].eq(case)]
        y_labels.append(CASE_LABELS.get(case, case))
        values.append(
            [
                1 if bool(case_df[case_df["variant_name"].eq(variant)]["converged"].iloc[0]) else 0
                for variant in VARIANT_ORDER
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
    ax.set_title("Gen P/Q with voltage starts vs generator-voltage targets", fontsize=14, pad=10)
    ax.set_xticks(range(len(VARIANT_ORDER)))
    ax.set_xticklabels([VARIANT_LABELS[variant] for variant in VARIANT_ORDER], rotation=20, ha="right")
    ax.set_yticks(range(len(y_labels)))
    ax.set_yticklabels(y_labels)
    ax.tick_params(length=0)

    for y, row in enumerate(values):
        for x, value in enumerate(row):
            ax.text(x, y, "Y" if value else "N", ha="center", va="center", color="white", fontsize=11)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.2)

    fig.tight_layout()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_DIR / "pgqg_vs_and_vg_convergence_heatmap.png", dpi=180)
    plt.close(fig)


def write_notes(case_summary: pd.DataFrame) -> None:
    lines = [
        "# PG/QG + VS versus PG/QG + VG diagnostic",
        "",
        "The two voltage variants are not equivalent in the current PowerModels workflow.",
        "",
        "- `Gen P/Q + VS`: applies generator PG/QG, creates default AC PF starts, then overwrites bus `vm_start` and `va_start` from voltage-setpoint notes. These are Newton initialization values, not enforced voltage-control targets.",
        "- `Gen P/Q + VG`: applies generator PG/QG and generator `vg`, then creates default starts. This changes generator voltage setpoints/control targets, not the bus angle warm start.",
        "",
        "Therefore different convergence outcomes are expected: VS can move Newton into or out of a basin of attraction, while VG changes the PV-bus voltage target and reactive-control problem.",
        "",
        "## Case summary",
        "",
    ]
    for row in case_summary.itertuples(index=False):
        lines.append(
            f"- {row.case_label}: Gen P/Q={row.gen_pq}, Gen P/Q + VS={row.gen_pq_plus_vs}, "
            f"Gen P/Q + VG={row.gen_pq_plus_vg}. {row.case_classification}."
        )
    (OUTPUT_DIR / "pgqg_vs_and_vg_interpretation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(SWEEP_ROWS)
    diagnostics = create_diagnostics(df)
    case_summary = create_case_summary(diagnostics)
    diagnostics.to_csv(OUTPUT_DIR / "pgqg_bv_vs_vg_variant_diagnostics.csv", index=False)
    case_summary.to_csv(OUTPUT_DIR / "pgqg_bv_vs_vg_case_summary.csv", index=False)
    plot_heatmap(diagnostics)
    write_notes(case_summary)
    print(f"Wrote PG/QG voltage variant diagnostics to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
