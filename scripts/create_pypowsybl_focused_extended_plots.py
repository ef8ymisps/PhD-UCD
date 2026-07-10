"""Create focused presentation plots for the extended PyPowSyBl analysis."""

from __future__ import annotations

import csv
import textwrap
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = (
    PROJECT_ROOT
    / "results"
    / "presentable"
    / "pypowsybl_lf_replacement_sweep"
    / "extended_configuration_analysis"
)
FIGURE_DIR = ANALYSIS_DIR / "figures"
SUMMARY_CSV = ANALYSIS_DIR / "extended_configuration_case_config_summary.csv"

CASE_ORDER = [
    ("pglib_opf_case300_ieee", "300 IEEE"),
    ("pglib_opf_case1888_rte", "1888 RTE"),
    ("pglib_opf_case6470_rte", "6470 RTE"),
    ("pglib_opf_case13659_pegase", "13659 PEGASE"),
]

SLACK_CONFIGS = [
    ("matched_single_slack", "Matched\nsingle slack"),
    ("explicit_original_slack", "Explicit\noriginal slack"),
    ("largest_generator_slack", "Largest-generator\nslack"),
    ("distributed_pmax", "Distributed\nby Pmax"),
    ("distributed_remaining_margin", "Distributed\nby margin"),
]

REACTIVE_OPERATIONAL_CONFIGS = [
    ("matched_single_slack", "Matched\nsingle slack"),
    ("q_limits", "Q limits"),
    ("q_limits_more_switches", "Q limits\nmore switches"),
    ("q_target_clamped", "Clamp\ntarget Q"),
    ("disable_bad_voltage_controls", "Disable bad\nV controls"),
    ("reactive_dispatch_k", "Reactive\ndispatch K"),
    ("transformer_voltage_control", "Transformer\nV control"),
    ("shunt_voltage_control", "Shunt\nV control"),
    ("phase_control", "Phase\ncontrol"),
    ("full_openloadflow_operational", "Full\noperational"),
]

COUNT_COLORS = {
    0: "#b94b48",
    1: "#ee704b",
    2: "#f5dc8e",
    3: "#d9ef82",
    4: "#89b879",
    5: "#4f7f62",
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
    spacing: int = 4,
) -> None:
    left, top, right, bottom = xyxy
    width, height = text_size(draw, text, text_font)
    x = left + (right - left - width) / 2
    y = top + (bottom - top - height) / 2
    draw.multiline_text((x, y), text, font=text_font, fill=fill, spacing=spacing, align="center")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def build_rows(summary: pd.DataFrame, configs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    by_key = {
        (str(row["case_name"]), str(row["extended_config_name"])): row
        for _, row in summary.iterrows()
    }
    rows: list[dict[str, Any]] = []
    for case_name, case_label in CASE_ORDER:
        for config_name, config_label in configs:
            row = by_key[(case_name, config_name)]
            rows.append(
                {
                    "case_name": case_name,
                    "case_label": case_label,
                    "extended_config_name": config_name,
                    "extended_config_label": config_label.replace("\n", " "),
                    "n_variants_converged": int(row["n_variants_converged"]),
                    "n_variants_run": int(row["n_variants_run"]),
                    "converged_variants": row.get("converged_variants", ""),
                }
            )
    return rows


def draw_heatmap(
    rows: list[dict[str, Any]],
    configs: list[tuple[str, str]],
    path: Path,
    *,
    title: str,
    subtitle: str,
    group_spans: list[tuple[str, int, int]],
) -> None:
    title_font = font(30, bold=True)
    subtitle_font = font(18)
    header_font = font(17, bold=True)
    group_font = font(17, bold=True)
    case_font = font(21)
    cell_font = font(20, bold=True)
    note_font = font(15)

    left_margin = 175
    right_margin = 35
    top_margin = 120
    group_h = 32
    header_h = 78
    row_h = 74
    legend_h = 58
    cell_w = 150 if len(configs) <= 6 else 135
    width = left_margin + right_margin + cell_w * len(configs)
    height = top_margin + group_h + header_h + row_h * len(CASE_ORDER) + legend_h

    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((left_margin, 24), title, font=title_font, fill="#111111")
    draw.text((left_margin, 64), subtitle, font=subtitle_font, fill="#333333")

    grid_top = top_margin + group_h + header_h
    grid_left = left_margin

    for group_label, start, end in group_spans:
        x0 = grid_left + start * cell_w
        x1 = grid_left + (end + 1) * cell_w
        draw.rectangle((x0, top_margin, x1, top_margin + group_h), fill="#f4f4f4", outline="#333333", width=1)
        centered_text(draw, (x0, top_margin, x1, top_margin + group_h), group_label, group_font)

    for col, (_, label) in enumerate(configs):
        x0 = grid_left + col * cell_w
        x1 = x0 + cell_w
        draw.rectangle((x0, top_margin + group_h, x1, grid_top), fill="#ffffff", outline="#333333", width=1)
        centered_text(draw, (x0 + 4, top_margin + group_h + 4, x1 - 4, grid_top - 4), label, header_font)

    by_cell = {
        (row["case_name"], row["extended_config_name"]): row
        for row in rows
    }
    for row_idx, (case_name, case_label) in enumerate(CASE_ORDER):
        y0 = grid_top + row_idx * row_h
        y1 = y0 + row_h
        draw.rectangle((0, y0, grid_left, y1), fill="#ffffff")
        centered_text(draw, (10, y0, grid_left - 12, y1), case_label, case_font)
        for col, (config_name, _) in enumerate(configs):
            cell = by_cell[(case_name, config_name)]
            ok = int(cell["n_variants_converged"])
            total = int(cell["n_variants_run"])
            x0 = grid_left + col * cell_w
            x1 = x0 + cell_w
            draw.rectangle((x0, y0, x1, y1), fill=COUNT_COLORS.get(ok, "#dddddd"), outline="#ffffff", width=2)
            centered_text(draw, (x0, y0, x1, y1), f"{ok}/{total}", cell_font)

    for _, start, _ in group_spans[1:]:
        x = grid_left + start * cell_w
        draw.line((x, top_margin, x, grid_top + row_h * len(CASE_ORDER)), fill="#222222", width=3)

    legend_top = grid_top + row_h * len(CASE_ORDER) + 20
    draw.text((grid_left, legend_top), "Cells show converged replacement variants out of 5", font=note_font, fill="#333333")
    x = grid_left + 420
    for value in range(0, 6):
        draw.rectangle((x, legend_top - 2, x + 24, legend_top + 22), fill=COUNT_COLORS[value], outline="#ffffff")
        draw.text((x + 30, legend_top), str(value), font=note_font, fill="#222222")
        x += 62

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def write_notes() -> None:
    notes = [
        "# Focused Extended Configuration Plots",
        "",
        "These two figures replace the overwhelming all-configuration heatmap with a cleaner story.",
        "",
        "## Slack Modelling",
        "",
        "The slack-modelling plot shows that the number of converged variants is unchanged across matched single slack, explicit original slack, largest-generator slack, distributed by Pmax, and distributed by remaining margin.",
        "",
        "This means slack modelling does not explain the non-converged starting points in this focused PyPowSyBl/OpenLoadFlow set.",
        "",
        "## Reactive Limits And Operational Controls",
        "",
        "The reactive/operational plot keeps the matched single-slack baseline as the first column, then separates reactive-limit settings from operational controls.",
        "",
        "The main visual finding is that these controls do not recover extra failing variants. For 300 IEEE, enabling Q-limit/control modes removes the three baseline convergences. For 1888 RTE, 6470 RTE, and 13659 PEGASE, the convergence counts remain the same as the baseline.",
        "",
        "These controls should therefore be presented as a changed OpenLoadFlow problem, not as a direct PowerModels-matched baseline.",
        "",
    ]
    (ANALYSIS_DIR / "focused_configuration_plot_notes.md").write_text("\n".join(notes), encoding="utf-8")


def main() -> None:
    summary = pd.read_csv(SUMMARY_CSV)
    slack_rows = build_rows(summary, SLACK_CONFIGS)
    reactive_operational_rows = build_rows(summary, REACTIVE_OPERATIONAL_CONFIGS)

    write_csv(ANALYSIS_DIR / "focused_slack_modelling_summary.csv", slack_rows)
    write_csv(ANALYSIS_DIR / "focused_reactive_operational_summary.csv", reactive_operational_rows)

    draw_heatmap(
        slack_rows,
        SLACK_CONFIGS,
        FIGURE_DIR / "focused_slack_modelling_convergence.png",
        title="Slack modelling has no effect on convergence",
        subtitle="Focused base-case replacement variants; alternative slack choices recover no extra cases.",
        group_spans=[("Slack modelling", 0, len(SLACK_CONFIGS) - 1)],
    )
    draw_heatmap(
        reactive_operational_rows,
        REACTIVE_OPERATIONAL_CONFIGS,
        FIGURE_DIR / "focused_reactive_operational_convergence.png",
        title="Reactive limits and operational controls",
        subtitle="Compared with matched single slack, these controls do not recover extra failing variants.",
        group_spans=[
            ("Baseline", 0, 0),
            ("Reactive limits and voltage controls", 1, 5),
            ("Operational controls", 6, 9),
        ],
    )
    write_notes()
    print(f"Wrote focused plots to {FIGURE_DIR}")


if __name__ == "__main__":
    main()
