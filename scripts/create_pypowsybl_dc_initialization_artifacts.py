from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INTERACTIVE_DIR = PROJECT_ROOT / "results" / "interactive" / "pypowsybl_lf_four_config_playground"
OUT_DIR = (
    PROJECT_ROOT
    / "results"
    / "presentable"
    / "pypowsybl_lf_replacement_sweep"
    / "dc_initialization_analysis"
)
FIGURE_DIR = OUT_DIR / "figures"

SUMMARY_CSV = INTERACTIVE_DIR / "base_case_four_config_lf_summary.csv"
ROWS_CSV = INTERACTIVE_DIR / "base_case_four_config_lf_rows.csv"

CASE_LABELS = {
    "pglib_opf_case300_ieee": "300 IEEE",
    "pglib_opf_case1354_pegase": "1354 PEGASE",
    "pglib_opf_case1888_rte": "1888 RTE",
    "pglib_opf_case6470_rte": "6470 RTE",
    "pglib_opf_case9241_pegase": "9241 PEGASE",
    "pglib_opf_case13659_pegase": "13659 PEGASE",
    "pglib_opf_case24464_goc": "24464 GOC",
}

CASE_ORDER = [
    "pglib_opf_case300_ieee",
    "pglib_opf_case1354_pegase",
    "pglib_opf_case1888_rte",
    "pglib_opf_case6470_rte",
    "pglib_opf_case9241_pegase",
    "pglib_opf_case13659_pegase",
    "pglib_opf_case24464_goc",
]

CONFIG_LABELS = {
    "strict_pm_like": "Single slack\nQ off Ops off",
    "q_limits_only": "Single slack\nQ on Ops off",
    "ops_only": "Single slack\nQ off Ops on",
    "ops_and_q_limits": "Single slack\nQ on Ops on",
}

CONFIG_ORDER = ["strict_pm_like", "q_limits_only", "ops_only", "ops_and_q_limits"]

VARIANT_ORDER = [
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

COUNT_COLORS = {
    0: "#b94b48",
    1: "#ee704b",
    2: "#f5dc8e",
    3: "#d9ef82",
    4: "#89b879",
    5: "#4f7f62",
}

DELTA_COLORS = {
    -5: "#7f1d1d",
    -4: "#9f2f2f",
    -3: "#b94b48",
    -2: "#d96f5f",
    -1: "#efb09a",
    0: "#f2f2f2",
    1: "#cfe8b8",
    2: "#a8d28a",
    3: "#78b66a",
    4: "#4f9856",
    5: "#2f7d44",
}

BOOL_COLORS = {
    "NN": "#b94b48",
    "NY": "#4f7f62",
    "YN": "#ee704b",
    "YY": "#2f6f4e",
}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.multiline_textbbox((0, 0), text, font=text_font, spacing=4, align="center")
    return int(box[2] - box[0]), int(box[3] - box[1])


def centered_text(
    draw: ImageDraw.ImageDraw,
    xyxy: tuple[int, int, int, int],
    text: str,
    text_font: ImageFont.ImageFont,
    *,
    fill: str = "#111111",
) -> None:
    left, top, right, bottom = xyxy
    width, height = text_size(draw, text, text_font)
    draw.multiline_text(
        (left + (right - left - width) / 2, top + (bottom - top - height) / 2),
        text,
        font=text_font,
        fill=fill,
        spacing=4,
        align="center",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def case_sort_key(case_name: str) -> tuple[int, str]:
    return (CASE_ORDER.index(case_name) if case_name in CASE_ORDER else 999, case_name)


def config_sort_key(config_name: str) -> tuple[int, str]:
    return (CONFIG_ORDER.index(config_name) if config_name in CONFIG_ORDER else 999, config_name)


def variant_sort_key(variant_name: str) -> tuple[int, str]:
    return (VARIANT_ORDER.index(variant_name) if variant_name in VARIANT_ORDER else 999, variant_name)


def build_summary_comparison(summary: pd.DataFrame) -> pd.DataFrame:
    required = {"case_name", "voltage_init_mode", "config_name", "converged_variants", "total_variants", "median_iterations"}
    missing = required - set(summary.columns)
    if missing:
        raise ValueError(f"Summary CSV is missing columns: {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    grouped = summary.groupby(["case_name", "config_name"], dropna=False)
    for (case_name, config_name), group in grouped:
        by_mode = {str(row["voltage_init_mode"]): row for _, row in group.iterrows()}
        if "UNIFORM_VALUES" not in by_mode or "DC_VALUES" not in by_mode:
            continue
        uniform = by_mode["UNIFORM_VALUES"]
        dc = by_mode["DC_VALUES"]
        uniform_conv = int(uniform["converged_variants"])
        dc_conv = int(dc["converged_variants"])
        total = int(dc["total_variants"])
        rows.append(
            {
                "case_name": case_name,
                "case_label": CASE_LABELS.get(case_name, case_name),
                "config_name": config_name,
                "config_label": CONFIG_LABELS.get(config_name, config_name).replace("\n", " "),
                "uniform_converged_variants": uniform_conv,
                "dc_converged_variants": dc_conv,
                "delta_converged_variants": dc_conv - uniform_conv,
                "total_variants": total,
                "uniform_median_iterations": uniform.get("median_iterations", ""),
                "dc_median_iterations": dc.get("median_iterations", ""),
                "delta_median_iterations": (
                    float(dc["median_iterations"]) - float(uniform["median_iterations"])
                    if pd.notna(dc.get("median_iterations")) and pd.notna(uniform.get("median_iterations"))
                    else ""
                ),
                "reading": reading(dc_conv - uniform_conv),
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(
            by=["case_name", "config_name"],
            key=lambda col: col.map(case_sort_key) if col.name == "case_name" else col.map(config_sort_key),
        ).reset_index(drop=True)
    return out


def reading(delta: int) -> str:
    if delta > 0:
        return "dc_initialization_improves_convergence"
    if delta < 0:
        return "dc_initialization_reduces_convergence"
    return "no_aggregate_change"


def build_case_comparison(summary_comparison: pd.DataFrame) -> pd.DataFrame:
    if summary_comparison.empty:
        return pd.DataFrame()
    rows = []
    for case_name, group in summary_comparison.groupby("case_name", sort=False):
        uniform_total = int(group["uniform_converged_variants"].sum())
        dc_total = int(group["dc_converged_variants"].sum())
        total = int(group["total_variants"].sum())
        rows.append(
            {
                "case_name": case_name,
                "case_label": CASE_LABELS.get(case_name, case_name),
                "uniform_converged_runs": uniform_total,
                "dc_converged_runs": dc_total,
                "delta_converged_runs": dc_total - uniform_total,
                "total_runs": total,
                "reading": reading(dc_total - uniform_total),
            }
        )
    return pd.DataFrame(rows).sort_values("case_name", key=lambda col: col.map(case_sort_key)).reset_index(drop=True)


def build_variant_comparison(rows_df: pd.DataFrame) -> pd.DataFrame:
    required = {"case_name", "variant_name", "variant_label", "config_name", "voltage_init_mode", "converged", "iteration_count"}
    missing = required - set(rows_df.columns)
    if missing:
        raise ValueError(f"Rows CSV is missing columns: {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    grouped = rows_df.groupby(["case_name", "variant_name", "config_name"], dropna=False)
    for (case_name, variant_name, config_name), group in grouped:
        by_mode = {str(row["voltage_init_mode"]): row for _, row in group.iterrows()}
        if "UNIFORM_VALUES" not in by_mode or "DC_VALUES" not in by_mode:
            continue
        uniform = by_mode["UNIFORM_VALUES"]
        dc = by_mode["DC_VALUES"]
        uniform_ok = bool_value(uniform["converged"])
        dc_ok = bool_value(dc["converged"])
        code = ("Y" if uniform_ok else "N") + ("Y" if dc_ok else "N")
        rows.append(
            {
                "case_name": case_name,
                "case_label": CASE_LABELS.get(case_name, case_name),
                "variant_name": variant_name,
                "variant_label": uniform.get("variant_label", VARIANT_LABELS.get(variant_name, variant_name)),
                "config_name": config_name,
                "config_label": CONFIG_LABELS.get(config_name, config_name).replace("\n", " "),
                "uniform_converged": uniform_ok,
                "dc_converged": dc_ok,
                "transition": code,
                "transition_label": transition_label(code),
                "uniform_iteration_count": uniform.get("iteration_count", ""),
                "dc_iteration_count": dc.get("iteration_count", ""),
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(
            by=["case_name", "variant_name", "config_name"],
            key=lambda col: (
                col.map(case_sort_key)
                if col.name == "case_name"
                else col.map(variant_sort_key)
                if col.name == "variant_name"
                else col.map(config_sort_key)
            ),
        ).reset_index(drop=True)
    return out


def bool_value(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def transition_label(code: str) -> str:
    return {
        "NN": "fails_both",
        "NY": "dc_recovers",
        "YN": "dc_loses",
        "YY": "converges_both",
    }.get(code, code)


def draw_summary_heatmap(comparison: pd.DataFrame, path: Path) -> None:
    cases = [case for case in CASE_ORDER if case in set(comparison["case_name"])]
    configs = [config for config in CONFIG_ORDER if config in set(comparison["config_name"])]
    title_font = font(30, True)
    subtitle_font = font(18)
    header_font = font(16, True)
    case_font = font(20)
    cell_font = font(20, True)
    note_font = font(15)

    left = 175
    top = 130
    header_h = 82
    row_h = 66
    cell_w = 155
    width = left + cell_w * len(configs) + 40
    height = top + header_h + row_h * len(cases) + 100

    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((left, 26), "DC voltage initialization effect", font=title_font, fill="#111111")
    draw.text((left, 67), "Cells show DC converged variants out of 5; number in parentheses is DC minus uniform.", font=subtitle_font, fill="#333333")

    by_key = {(row["case_name"], row["config_name"]): row for _, row in comparison.iterrows()}
    grid_top = top + header_h
    for col, config_name in enumerate(configs):
        x0 = left + col * cell_w
        x1 = x0 + cell_w
        draw.rectangle((x0, top, x1, grid_top), fill="#ffffff", outline="#333333")
        centered_text(draw, (x0 + 4, top + 4, x1 - 4, grid_top - 4), CONFIG_LABELS[config_name], header_font)

    for row_idx, case_name in enumerate(cases):
        y0 = grid_top + row_idx * row_h
        y1 = y0 + row_h
        centered_text(draw, (8, y0, left - 12, y1), CASE_LABELS.get(case_name, case_name), case_font)
        for col, config_name in enumerate(configs):
            x0 = left + col * cell_w
            x1 = x0 + cell_w
            row = by_key[(case_name, config_name)]
            dc = int(row["dc_converged_variants"])
            delta = int(row["delta_converged_variants"])
            color = COUNT_COLORS.get(dc, "#dddddd")
            draw.rectangle((x0, y0, x1, y1), fill=color, outline="#ffffff", width=2)
            sign = "+" if delta > 0 else ""
            centered_text(draw, (x0, y0, x1, y1), f"{dc}/5\n({sign}{delta})", cell_font)

    legend_y = grid_top + row_h * len(cases) + 22
    draw.text((left, legend_y), "Example: 5/5 (+5) means DC solved all five variants and recovered five versus uniform.", font=note_font, fill="#333333")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def draw_delta_heatmap(comparison: pd.DataFrame, path: Path) -> None:
    cases = [case for case in CASE_ORDER if case in set(comparison["case_name"])]
    configs = [config for config in CONFIG_ORDER if config in set(comparison["config_name"])]
    title_font = font(30, True)
    subtitle_font = font(18)
    header_font = font(16, True)
    case_font = font(20)
    cell_font = font(22, True)

    left = 175
    top = 130
    header_h = 82
    row_h = 66
    cell_w = 155
    width = left + cell_w * len(configs) + 40
    height = top + header_h + row_h * len(cases) + 80
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((left, 26), "DC minus uniform convergence", font=title_font, fill="#111111")
    draw.text((left, 67), "Positive values mean DC initialization recovers additional variants.", font=subtitle_font, fill="#333333")

    by_key = {(row["case_name"], row["config_name"]): row for _, row in comparison.iterrows()}
    grid_top = top + header_h
    for col, config_name in enumerate(configs):
        x0 = left + col * cell_w
        x1 = x0 + cell_w
        draw.rectangle((x0, top, x1, grid_top), fill="#ffffff", outline="#333333")
        centered_text(draw, (x0 + 4, top + 4, x1 - 4, grid_top - 4), CONFIG_LABELS[config_name], header_font)
    for row_idx, case_name in enumerate(cases):
        y0 = grid_top + row_idx * row_h
        y1 = y0 + row_h
        centered_text(draw, (8, y0, left - 12, y1), CASE_LABELS.get(case_name, case_name), case_font)
        for col, config_name in enumerate(configs):
            x0 = left + col * cell_w
            x1 = x0 + cell_w
            delta = int(by_key[(case_name, config_name)]["delta_converged_variants"])
            draw.rectangle((x0, y0, x1, y1), fill=DELTA_COLORS.get(delta, "#dddddd"), outline="#ffffff", width=2)
            sign = "+" if delta > 0 else ""
            centered_text(draw, (x0, y0, x1, y1), f"{sign}{delta}", cell_font)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def draw_variant_transition_heatmap(variant_comparison: pd.DataFrame, path: Path) -> None:
    focus_cases = [case for case in CASE_ORDER if case in set(variant_comparison["case_name"])]
    configs = [config for config in CONFIG_ORDER if config in set(variant_comparison["config_name"])]
    variants = [variant for variant in VARIANT_ORDER if variant in set(variant_comparison["variant_name"])]
    title_font = font(30, True)
    subtitle_font = font(18)
    header_font = font(15, True)
    row_font = font(17)
    cell_font = font(16, True)
    note_font = font(15)

    left = 260
    top = 130
    header_h = 82
    row_h = 35
    cell_w = 135
    rows = [(case, variant) for case in focus_cases for variant in variants]
    width = left + cell_w * len(configs) + 40
    height = top + header_h + row_h * len(rows) + 105
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((left, 26), "Variant-level initialization transitions", font=title_font, fill="#111111")
    draw.text((left, 67), "Cell code is Uniform/DC: NY means DC recovers a variant that failed from uniform start.", font=subtitle_font, fill="#333333")

    by_key = {
        (row["case_name"], row["variant_name"], row["config_name"]): row
        for _, row in variant_comparison.iterrows()
    }
    grid_top = top + header_h
    for col, config_name in enumerate(configs):
        x0 = left + col * cell_w
        x1 = x0 + cell_w
        draw.rectangle((x0, top, x1, grid_top), fill="#ffffff", outline="#333333")
        centered_text(draw, (x0 + 4, top + 4, x1 - 4, grid_top - 4), CONFIG_LABELS[config_name], header_font)

    for idx, (case_name, variant_name) in enumerate(rows):
        y0 = grid_top + idx * row_h
        y1 = y0 + row_h
        label = f"{CASE_LABELS.get(case_name, case_name)} | {VARIANT_LABELS.get(variant_name, variant_name)}"
        draw.text((8, y0 + 8), label, font=row_font, fill="#111111")
        for col, config_name in enumerate(configs):
            x0 = left + col * cell_w
            x1 = x0 + cell_w
            row = by_key.get((case_name, variant_name, config_name))
            code = str(row["transition"]) if row is not None else ""
            draw.rectangle((x0, y0, x1, y1), fill=BOOL_COLORS.get(code, "#dddddd"), outline="#ffffff", width=1)
            centered_text(draw, (x0, y0, x1, y1), code, cell_font)

    legend_y = grid_top + row_h * len(rows) + 20
    legend = "NN fails both | NY DC recovers | YN DC loses | YY converges both"
    draw.text((left, legend_y), legend, font=note_font, fill="#333333")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def write_interpretation(case_comparison: pd.DataFrame, summary_comparison: pd.DataFrame) -> None:
    improved = case_comparison[case_comparison["delta_converged_runs"] > 0]
    worsened = case_comparison[case_comparison["delta_converged_runs"] < 0]
    unchanged = case_comparison[case_comparison["delta_converged_runs"] == 0]

    lines = [
        "# DC Voltage Initialization Analysis",
        "",
        "This artifact compares PyPowSyBl/OpenLoadFlow runs initialized with `UNIFORM_VALUES` against runs initialized with `DC_VALUES`.",
        "",
        "The comparison uses the same cases, replacement variants, and four LF configurations from the interactive playground:",
        "",
        "- single slack, Q limits off, operational controls off",
        "- single slack, Q limits on, operational controls off",
        "- single slack, Q limits off, operational controls on",
        "- single slack, Q limits on, operational controls on",
        "",
        "## Main Finding",
        "",
    ]
    if not improved.empty:
        lines.append("DC initialization improves aggregate convergence for:")
        for _, row in improved.iterrows():
            lines.append(
                f"- {row['case_label']}: {row['uniform_converged_runs']}/{row['total_runs']} with uniform "
                f"to {row['dc_converged_runs']}/{row['total_runs']} with DC "
                f"({int(row['delta_converged_runs']):+d})."
            )
        lines.append("")
    if not worsened.empty:
        lines.append("DC initialization reduces aggregate convergence for:")
        for _, row in worsened.iterrows():
            lines.append(
                f"- {row['case_label']}: {row['uniform_converged_runs']}/{row['total_runs']} with uniform "
                f"to {row['dc_converged_runs']}/{row['total_runs']} with DC "
                f"({int(row['delta_converged_runs']):+d})."
            )
        lines.append("")
    if not unchanged.empty:
        lines.append("DC initialization gives no aggregate change for:")
        lines.append("- " + ", ".join(str(row["case_label"]) for _, row in unchanged.iterrows()))
        lines.append("")

    lines.extend(
        [
            "The strongest result is `1888 RTE`: uniform initialization fails all 20 runs, while DC initialization converges all 20 runs.",
            "",
            "This supports the interpretation that some PyPowSyBl failures are not only due to reactive limits or operational controls; they are also strongly tied to the initial voltage-angle state.",
            "",
            "## Artifacts",
            "",
            "- `dc_vs_uniform_case_config_comparison.csv`: per case/config comparison.",
            "- `dc_vs_uniform_case_summary.csv`: aggregate per case.",
            "- `dc_vs_uniform_variant_transition.csv`: per case/variant/config transition from uniform to DC.",
            "- `figures/dc_initialization_convergence_heatmap.png`: DC convergence count with delta against uniform.",
            "- `figures/dc_minus_uniform_convergence_delta_heatmap.png`: delta-only view.",
            "- `figures/dc_variant_transition_heatmap.png`: variant-level Uniform/DC transitions.",
            "",
            "## Presentation Wording",
            "",
            "Use this as the clean statement:",
            "",
            "> DC angle initialization substantially changes OpenLoadFlow convergence for some PGLib replacement cases. In particular, `1888 RTE` changes from no convergence under uniform initialization to full convergence under DC initialization across all tested variants and configurations. This indicates that initialization, not only LF control options, is a major factor in the observed PyPowSyBl convergence behavior.",
            "",
        ]
    )
    (OUT_DIR / "dc_initialization_interpretation.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    summary = pd.read_csv(SUMMARY_CSV)
    rows_df = pd.read_csv(ROWS_CSV)

    summary_comparison = build_summary_comparison(summary)
    case_comparison = build_case_comparison(summary_comparison)
    variant_comparison = build_variant_comparison(rows_df)

    summary_comparison.to_csv(OUT_DIR / "dc_vs_uniform_case_config_comparison.csv", index=False)
    case_comparison.to_csv(OUT_DIR / "dc_vs_uniform_case_summary.csv", index=False)
    variant_comparison.to_csv(OUT_DIR / "dc_vs_uniform_variant_transition.csv", index=False)

    draw_summary_heatmap(summary_comparison, FIGURE_DIR / "dc_initialization_convergence_heatmap.png")
    draw_delta_heatmap(summary_comparison, FIGURE_DIR / "dc_minus_uniform_convergence_delta_heatmap.png")
    draw_variant_transition_heatmap(variant_comparison, FIGURE_DIR / "dc_variant_transition_heatmap.png")
    write_interpretation(case_comparison, summary_comparison)

    print(f"Wrote DC initialization artifacts to {OUT_DIR}")


if __name__ == "__main__":
    main()
