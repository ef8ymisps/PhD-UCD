from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PRESENTABLE = ROOT / "results" / "presentable" / "pypowsybl_lf_replacement_sweep"
INTERACTIVE = ROOT / "results" / "interactive" / "pypowsybl_lf_four_config_playground"
OUT_DIR = PRESENTABLE / "presentation_findings"


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def read_csv(path: Path) -> pd.DataFrame:
    if path.exists() and path.stat().st_size:
        return pd.read_csv(path)
    return pd.DataFrame()


def figure_inventory() -> pd.DataFrame:
    rows = []
    for path in sorted(PRESENTABLE.rglob("*.png")):
        rows.append(
            {
                "figure": rel(path),
                "group": rel(path.parent),
                "size_kb": round(path.stat().st_size / 1024, 1),
                "recommended_use": classify_figure(path),
            }
        )
    return pd.DataFrame(rows)


def classify_figure(path: Path) -> str:
    name = path.name
    parts = set(path.parts)
    if name == "pypowsybl_base_large_single_slack_ac_convergence_heatmap.png":
        return "baseline matched PowerModels-style comparison"
    if name == "matched_single_slack_convergence_heatmap.png":
        return "PowerModels vs PyPowSyBl matched single-slack overview"
    if name == "pypowsybl_base_large_ac_convergence_with_strict_heatmap.png":
        return "large-case PyPowSyBl convergence including strict single slack"
    if name == "pypowsybl_case300_1888_ac_convergence_with_strict_heatmap.png":
        return "focused 300/1888 case discussion"
    if name == "slack_modeling_heatmap.png":
        return "slack modelling effect"
    if name == "reactive_operational_controls_heatmap.png":
        return "reactive limits and operational controls effect"
    if name == "factorial_control_case_config_heatmap.png":
        return "full factorial config overview"
    if name == "native_operational_controls_heatmap.png":
        return "native network operational-control capability"
    if name == "focused_slack_modelling_convergence.png":
        return "older slack modelling focused plot"
    if name == "focused_reactive_operational_convergence.png":
        return "older reactive/operational focused plot"
    if name in {"large_ac_convergence_heatmap.png", "speedup_vs_original.png", "valid_ac_solve_time_vs_buses.png"}:
        return "general PyPowSyBl replacement sweep figure"
    if "advanced_configuration" in name or "extended_configuration" in name:
        return "wide configuration sweep; use as backup, not main slide"
    return "supporting figure"


def interactive_findings() -> tuple[pd.DataFrame, list[str]]:
    summary_path = INTERACTIVE / "base_case_four_config_lf_summary.csv"
    summary = read_csv(summary_path)
    bullets: list[str] = []
    if summary.empty:
        return summary, ["Interactive four-configuration summary was not found."]

    for case in sorted(summary["case_name"].unique()):
        case_df = summary[summary["case_name"] == case]
        modes = sorted(case_df["voltage_init_mode"].dropna().unique())
        if set(modes) >= {"UNIFORM_VALUES", "DC_VALUES"}:
            uniform = case_df[case_df["voltage_init_mode"] == "UNIFORM_VALUES"]
            dc = case_df[case_df["voltage_init_mode"] == "DC_VALUES"]
            u_total = int(uniform["converged_variants"].sum())
            d_total = int(dc["converged_variants"].sum())
            max_total = int(dc["total_variants"].sum())
            if d_total != u_total:
                bullets.append(
                    f"- `{case}`: DC initialization changes convergence from {u_total}/{max_total} to {d_total}/{max_total} "
                    "across the four configs and five variants."
                )

    if not bullets:
        bullets.append("- DC initialization did not change aggregate convergence in the currently selected interactive summary.")
    return summary, bullets


def table_to_markdown(df: pd.DataFrame, columns: list[str], max_rows: int | None = None) -> str:
    if df.empty:
        return "_No data available._"
    use = df[columns].copy()
    if max_rows is not None:
        use = use.head(max_rows)
    headers = [str(column) for column in use.columns]

    def fmt(value: object) -> str:
        if pd.isna(value):
            text = ""
        elif isinstance(value, float):
            text = f"{value:.6g}"
        else:
            text = str(value)
        return text.replace("|", "\\|").replace("\n", " ")

    rows = ["| " + " | ".join(headers) + " |"]
    rows.append("| " + " | ".join("---" for _ in headers) + " |")
    for _, row in use.iterrows():
        rows.append("| " + " | ".join(fmt(row[column]) for column in use.columns) + " |")
    return "\n".join(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    figures = figure_inventory()
    figures.to_csv(OUT_DIR / "pypowsybl_lf_figure_inventory.csv", index=False)

    baseline_summary = read_csv(PRESENTABLE / "baseline_problem_case_analysis" / "matched_single_slack_problem_case_summary.csv")
    baseline_comparison = read_csv(PRESENTABLE / "baseline_problem_case_analysis" / "matched_single_slack_problem_case_comparison.csv")
    factorial_summary = read_csv(PRESENTABLE / "factorial_control_analysis" / "factorial_lf_case_config_summary.csv")
    factorial_recovery = read_csv(PRESENTABLE / "factorial_control_analysis" / "factorial_lf_recovery_summary.csv")
    support_audit = read_csv(PRESENTABLE / "factorial_control_analysis" / "control_support_audit.csv")
    note_assessment = read_csv(PRESENTABLE / "pglib_translation_note_audit" / "pglib_operational_control_note_assessment.csv")
    native_rows = read_csv(PRESENTABLE / "native_operational_control_analysis" / "native_operational_control_rows.csv")
    native_support = read_csv(PRESENTABLE / "native_operational_control_analysis" / "native_operational_control_support_audit.csv")
    interactive_summary, dc_bullets = interactive_findings()

    main_figures = figures[
        figures["recommended_use"].isin(
            [
                "baseline matched PowerModels-style comparison",
                "PowerModels vs PyPowSyBl matched single-slack overview",
                "large-case PyPowSyBl convergence including strict single slack",
                "focused 300/1888 case discussion",
                "slack modelling effect",
                "reactive limits and operational controls effect",
                "full factorial config overview",
                "native network operational-control capability",
                "general PyPowSyBl replacement sweep figure",
            ]
        )
    ]

    lines: list[str] = []
    lines.extend(
        [
            "# PyPowSyBl Load-Flow Convergence Findings",
            "",
            "This note aggregates the PyPowSyBl/OpenLoadFlow load-flow evidence produced in the repository. "
            "It is written as a presentation guide: first the central message, then the figures and the supporting artifacts.",
            "",
            "## Central Message",
            "",
            "- PyPowSyBl is useful as a second load-flow implementation, but convergence depends strongly on initialization and on whether the operating point is internally consistent.",
            "- Under the matched PowerModels-style setup, the fair baseline is single slack, Newton-Raphson, no distributed slack, no reactive limits, and no transformer/shunt/phase controls.",
            "- For PGLib/MATPOWER imports, enabling operational-control flags does not activate meaningful transformer/shunt/phase regulation because the imported data do not contain the required control metadata.",
            "- Reactive-limit PV/PQ switching is not automatically beneficial: in several cases it worsens convergence or leaves it unchanged.",
            "- DC angle initialization is important: in the latest interactive sweep, `1888_rte` recovers completely under `DC_VALUES`, while uniform initialization fails for all variants/configs.",
            "- Native PyPowSyBl/IIDM-style networks are the correct place to demonstrate operational controls, because they contain regulating tap/shunt/phase metadata.",
            "",
            "## Latest Interactive Finding: DC Initialization",
            "",
            *dc_bullets,
            "",
        ]
    )

    if not interactive_summary.empty:
        compact = interactive_summary[
            [
                "case_name",
                "voltage_init_mode",
                "config_name",
                "converged_variants",
                "total_variants",
                "median_iterations",
                "supports_operational_controls",
            ]
        ].copy()
        lines.extend(
            [
                "The interactive summary is stored at `results/interactive/pypowsybl_lf_four_config_playground/base_case_four_config_lf_summary.csv`.",
                "",
                table_to_markdown(compact, list(compact.columns), max_rows=80),
                "",
            ]
        )

    lines.extend(
        [
            "## Main Figures To Present",
            "",
            table_to_markdown(main_figures, ["figure", "recommended_use"], max_rows=50),
            "",
            "Recommended slide order:",
            "",
            "1. Baseline convergence heatmap: show the matched single-slack comparison first.",
            "2. Focused 300/1888 plot: explain that convergence alone is not the solved-state story.",
            "3. DC initialization result: use the interactive summary table to show 1888/6470 sensitivity to initialization.",
            "4. Slack modelling heatmap: show that slack modelling was not the main recovery mechanism in the tested PGLib setup.",
            "5. Reactive/operational heatmap: show that PV/PQ switching and operational flags do not generally recover the failed PGLib variants.",
            "6. Translation-note audit: explain why PGLib notes are useful, but not enough for real operational controls.",
            "7. Native operational-control heatmap: show operational controls working on native PyPowSyBl networks with actual control metadata.",
            "",
            "## Baseline Matched Comparison",
            "",
        ]
    )

    if not baseline_summary.empty:
        lines.extend(
            [
                "Problem-case summary:",
                "",
                table_to_markdown(baseline_summary, list(baseline_summary.columns), max_rows=20),
                "",
            ]
        )
    if not baseline_comparison.empty:
        cols = [col for col in [
            "case_name",
            "variant_label",
            "powermodels_converged",
            "pypowsybl_single_slack_converged",
            "pypowsybl_iteration_count",
            "pypowsybl_slack_bus_id",
            "pypowsybl_target_q_outside_limits",
        ] if col in baseline_comparison.columns]
        if cols:
            lines.extend(
                [
                    "Matched single-slack detail:",
                    "",
                    table_to_markdown(baseline_comparison, cols, max_rows=40),
                    "",
                ]
            )

    lines.extend(
        [
            "Takeaway: compare PyPowSyBl to PowerModels only under the matched baseline first. "
            "Do not interpret PyPowSyBl Q-limit or operational-control modes as equivalent to `compute_ac_pf`.",
            "",
            "## Factorial Controls",
            "",
        ]
    )

    if not factorial_summary.empty:
        cols = [col for col in [
            "case_name",
            "factorial_config_name",
            "distributed_slack",
            "pv_pq_switching",
            "operational_controls",
            "n_converged",
            "n_total",
            "median_iterations",
            "supports_operational_controls",
        ] if col in factorial_summary.columns]
        if cols:
            lines.extend([table_to_markdown(factorial_summary, cols, max_rows=80), ""])

    if not factorial_recovery.empty:
        lines.extend(
            [
                "Recovery summary:",
                "",
                table_to_markdown(factorial_recovery, list(factorial_recovery.columns), max_rows=30),
                "",
            ]
        )

    lines.extend(
        [
            "Takeaway: in the PGLib/MATPOWER-imported cases, slack modelling was not the dominant driver. "
            "PV/PQ switching often leaves convergence unchanged or worse, and operational controls do not help because the network does not expose active control metadata.",
            "",
            "## Data Support For Controls",
            "",
        ]
    )

    if not support_audit.empty:
        cols = [col for col in [
            "case_name",
            "n_generators",
            "n_generators_with_q_range",
            "supports_reactive_limits",
            "n_ratio_tap_changers_regulating",
            "n_phase_tap_changers_regulating",
            "n_shunts_voltage_regulation_on",
            "supports_operational_controls",
        ] if col in support_audit.columns]
        if cols:
            lines.extend(["PGLib import control-support audit:", "", table_to_markdown(support_audit, cols, max_rows=20), ""])

    if not note_assessment.empty:
        cols = [col for col in [
            "case_name",
            "transformer_static_tap_ratio_replacement",
            "thermal_rating_replacement",
            "voltage_setpoint_replacement",
            "generator_pq_vg_setpoint_replacement",
            "control_metadata_keyword_hits",
            "operational_control_assessment",
        ] if col in note_assessment.columns]
        if cols:
            lines.extend(["PGLib translation-note audit:", "", table_to_markdown(note_assessment, cols, max_rows=20), ""])

    lines.extend(
        [
            "Takeaway: the PGLib note blocks are valuable and should be cited. They support static replacement/provenance analysis "
            "for transformer tap replacements, thermal ratings, voltage setpoints, and generator P/Q/Vg setpoints. "
            "They do not provide controlled bus, target voltage, deadband, tap step curves, or regulation modes.",
            "",
            "## Native PyPowSyBl Operational Controls",
            "",
        ]
    )

    if not native_support.empty:
        lines.extend(["Native network control-support audit:", "", table_to_markdown(native_support, list(native_support.columns), max_rows=20), ""])
    if not native_rows.empty:
        cols = [col for col in [
            "network_name",
            "config_name",
            "converged",
            "iteration_count",
            "distributed_slack",
            "use_reactive_limits",
            "operational_controls",
        ] if col in native_rows.columns]
        if cols:
            lines.extend(["Native operational-control rows:", "", table_to_markdown(native_rows, cols, max_rows=40), ""])

    lines.extend(
        [
            "Takeaway: native PyPowSyBl networks are the clean demonstration that operational controls can be exercised when the network format contains the right metadata.",
            "",
            "## Presentation Conclusions",
            "",
            "- For your thesis/presentation, separate three layers: matched LF comparison, initialization sensitivity, and PyPowSyBl-specific advanced controls.",
            "- The strongest new result is that `DC_VALUES` can recover cases that fail from a uniform start, especially `1888_rte` in the interactive four-configuration sweep.",
            "- Do not claim operational controls fail in PyPowSyBl. The better statement is that the PGLib/MATPOWER data do not support meaningful operational-control activation.",
            "- For PGLib cases, the useful PyPowSyBl evidence is convergence status, iteration count, slack mismatch, solved-state voltages, generator P/Q outputs, Q-limit pressure, and branch flow/loading proxies.",
            "- For operational controls, use native/IIDM/CGMES/PSS/E-style networks as a separate capability study.",
            "",
            "## Artifact Index",
            "",
            f"- Figure inventory: `{rel(OUT_DIR / 'pypowsybl_lf_figure_inventory.csv')}`",
            f"- This summary: `{rel(OUT_DIR / 'pypowsybl_lf_convergence_findings_for_presentation.md')}`",
            "- Interactive four-config rows: `results/interactive/pypowsybl_lf_four_config_playground/base_case_four_config_lf_rows.csv`",
            "- Interactive four-config summary: `results/interactive/pypowsybl_lf_four_config_playground/base_case_four_config_lf_summary.csv`",
        ]
    )

    out_path = OUT_DIR / "pypowsybl_lf_convergence_findings_for_presentation.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {rel(out_path)}")
    print(f"Wrote {rel(OUT_DIR / 'pypowsybl_lf_figure_inventory.csv')}")


if __name__ == "__main__":
    main()
