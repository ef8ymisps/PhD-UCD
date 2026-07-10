from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
PRESENTABLE = ROOT / "results" / "presentable" / "pypowsybl_lf_replacement_sweep"
INTERACTIVE = ROOT / "results" / "interactive" / "pypowsybl_lf_four_config_playground"
OUT_DIR = PRESENTABLE / "focused_presentation_heatmaps"
FIG_DIR = OUT_DIR / "figures"

EXT_ROWS = PRESENTABLE / "extended_configuration_analysis" / "extended_configuration_rows.csv"
FACTORIAL_ROWS = PRESENTABLE / "factorial_control_analysis" / "factorial_lf_rows.csv"
INTERACTIVE_ROWS = INTERACTIVE / "base_case_four_config_lf_rows.csv"
SENSITIVITY_ROWS = PRESENTABLE / "pypowsybl_openloadflow_sensitivity_rows.csv"

CASE_LABELS = {
    "pglib_opf_case300_ieee": "300 IEEE",
    "pglib_opf_case1354_pegase": "1354 PEGASE",
    "pglib_opf_case1888_rte": "1888 RTE",
    "pglib_opf_case6470_rte": "6470 RTE",
    "pglib_opf_case9241_pegase": "9241 PEGASE",
    "pglib_opf_case13659_pegase": "13659 PEGASE",
}

ATTACHED_CASE_ORDER = [
    "pglib_opf_case300_ieee",
    "pglib_opf_case1354_pegase",
    "pglib_opf_case1888_rte",
    "pglib_opf_case6470_rte",
    "pglib_opf_case9241_pegase",
    "pglib_opf_case13659_pegase",
]

SLACK_CASE_ORDER = [
    "pglib_opf_case300_ieee",
    "pglib_opf_case1888_rte",
    "pglib_opf_case6470_rte",
    "pglib_opf_case13659_pegase",
]

VARIANT_ORDER = [
    "original",
    "BV_from_warmstart",
    "G_PGQG_from",
    "G_PGQG_from_plus_BV_from",
    "G_PGQG_from_plus_GVG_from",
]

SLACK_CONFIGS = [
    ("matched_single_slack", "Single original\nslack bus"),
    ("largest_generator_slack", "Largest-generator\nslack"),
    ("distributed_pmax", "Distributed\nby Pmax"),
    ("distributed_remaining_margin", "Distributed\nby margin"),
]

CONTROL_CONFIGS = [
    ("strict_pm_like", "Q off\nOps off"),
    ("q_limits_only", "Q on\nOps off"),
    ("ops_only", "Q off\nOps on"),
    ("ops_and_q_limits", "Q on\nOps on"),
]

FACTORIAL_CONTROL_CONFIGS = [
    ("ds_off__pvpq_off__ops_off", "Q off\nOps off"),
    ("ds_off__pvpq_on__ops_off", "Q on\nOps off"),
    ("ds_off__pvpq_off__ops_on", "Q off\nOps on"),
    ("ds_off__pvpq_on__ops_on", "Q on\nOps on"),
]

COUNT_COLORS = {
    0: "#bc4b49",
    1: "#df684e",
    2: "#efb46d",
    3: "#d9ef82",
    4: "#89b879",
    5: "#4f7f62",
}

DELTA_COLORS = {
    -5: "#7f1d1d",
    -4: "#9f2f2f",
    -3: "#bc4b49",
    -2: "#d96f5f",
    -1: "#e7774f",
    0: "#f2f2f2",
    1: "#cfe8b8",
    2: "#a8d28a",
    3: "#78b66a",
    4: "#4f9856",
    5: "#2f7d44",
}

TRANSITION_COLORS = {
    "all_fail": "#bc4b49",
    "all_converge": "#2f6f4e",
    "full_recovery": "#2f7d44",
    "partial_recovery": "#78b66a",
    "partial_unchanged": "#d9ef82",
    "loss": "#e7774f",
    "mixed_loss": "#efb46d",
}

WHITE = "#ffffff"
BLACK = "#111111"


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
    fill: str = BLACK,
) -> None:
    left, top, right, bottom = xyxy
    width, height = text_size(draw, text, text_font)
    x = left + (right - left - width) / 2
    y = top + (bottom - top - height) / 2
    draw.multiline_text((x, y), text, font=text_font, fill=fill, spacing=4, align="center")


def bool_value(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def label_for_count(count: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{count}/{total}"


def transition_label(uniform: int, dc: int, total: int) -> str:
    if uniform == 0 and dc == 0:
        return "N -> N"
    if uniform == 0 and dc == total:
        return "N -> Y"
    if uniform == total and dc == 0:
        return "Y -> N"
    if uniform == total and dc == total:
        return "Y -> Y"
    return f"{uniform}/{total} -> {dc}/{total}"


def transition_class(uniform: int, dc: int, total: int) -> str:
    if uniform == 0 and dc == 0:
        return "all_fail"
    if uniform == total and dc == total:
        return "all_converge"
    if uniform == 0 and dc == total:
        return "full_recovery"
    if dc > uniform:
        return "partial_recovery"
    if dc < uniform:
        return "loss" if dc == 0 else "mixed_loss"
    return "partial_unchanged"


def draw_count_heatmap(
    matrix: list[dict[str, Any]],
    cases: list[str],
    path: Path,
    *,
    title: str,
) -> None:
    title_font = font(30)
    header_font = font(17)
    row_font = font(18)
    cell_font = font(21)

    left = 250
    top = 88
    header_h = 84
    row_h = 70
    cell_w = 160
    width = left + cell_w * len(cases) + 40
    height = top + header_h + row_h * len(matrix) + 42

    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)
    draw.text((left, 24), title, font=title_font, fill=BLACK)

    grid_top = top + header_h
    for col_idx, case in enumerate(cases):
        x0 = left + col_idx * cell_w
        x1 = x0 + cell_w
        draw.rectangle((x0, top, x1, grid_top), fill=WHITE, outline="#333333")
        centered_text(draw, (x0 + 5, top + 5, x1 - 5, grid_top - 5), CASE_LABELS.get(case, case), header_font)

    for row_idx, row in enumerate(matrix):
        y0 = grid_top + row_idx * row_h
        y1 = y0 + row_h
        centered_text(draw, (6, y0, left - 12, y1), str(row["row_label"]), row_font)
        for col_idx, case in enumerate(cases):
            cell = row[case]
            count = int(cell["count"])
            total = int(cell["total"])
            x0 = left + col_idx * cell_w
            x1 = x0 + cell_w
            fill = "#e6e6e6" if total == 0 else COUNT_COLORS.get(count, "#dddddd")
            text_fill = "#555555" if total == 0 else WHITE
            draw.rectangle((x0, y0, x1, y1), fill=fill, outline=WHITE, width=2)
            centered_text(draw, (x0, y0, x1, y1), label_for_count(count, total), cell_font, fill=text_fill)

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def draw_transition_heatmap(
    matrix: list[dict[str, Any]],
    cases: list[str],
    path: Path,
    *,
    title: str,
) -> None:
    title_font = font(30)
    header_font = font(17)
    row_font = font(18)
    cell_font = font(18)

    left = 250
    top = 88
    header_h = 84
    row_h = 76
    cell_w = 160
    width = left + cell_w * len(cases) + 40
    height = top + header_h + row_h * len(matrix) + 42

    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)
    draw.text((left, 24), title, font=title_font, fill=BLACK)

    grid_top = top + header_h
    for col_idx, case in enumerate(cases):
        x0 = left + col_idx * cell_w
        x1 = x0 + cell_w
        draw.rectangle((x0, top, x1, grid_top), fill=WHITE, outline="#333333")
        centered_text(draw, (x0 + 5, top + 5, x1 - 5, grid_top - 5), CASE_LABELS.get(case, case), header_font)

    for row_idx, row in enumerate(matrix):
        y0 = grid_top + row_idx * row_h
        y1 = y0 + row_h
        centered_text(draw, (6, y0, left - 12, y1), str(row["row_label"]), row_font)
        for col_idx, case in enumerate(cases):
            cell = row[case]
            uniform = int(cell["uniform"])
            dc = int(cell["dc"])
            total = int(cell["total"])
            x0 = left + col_idx * cell_w
            x1 = x0 + cell_w
            cls = transition_class(uniform, dc, total)
            fill = TRANSITION_COLORS.get(cls, "#dddddd")
            text_fill = WHITE if cls in {"all_fail", "all_converge", "full_recovery", "loss"} else BLACK
            draw.rectangle((x0, y0, x1, y1), fill=fill, outline=WHITE, width=2)
            centered_text(draw, (x0, y0, x1, y1), transition_label(uniform, dc, total), cell_font, fill=text_fill)

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def count_variants(
    rows: pd.DataFrame,
    config_col: str,
    config_name: str,
    case_name: str,
    converged_col: str,
    *,
    variants: list[str] | None = None,
) -> dict[str, int]:
    subset = rows[(rows[config_col] == config_name) & (rows["case_name"] == case_name)]
    variant_order = VARIANT_ORDER if variants is None else variants
    subset = subset[subset["variant_name"].isin(variant_order)]
    total = int(subset["variant_name"].nunique())
    count = int(subset.drop_duplicates("variant_name")[converged_col].map(bool_value).sum())
    return {"count": count, "total": total}


def make_slack_matrix() -> list[dict[str, Any]]:
    rows = read_csv(EXT_ROWS)
    rows = rows[rows["case_name"].isin(ATTACHED_CASE_ORDER)]
    sensitivity = read_csv(SENSITIVITY_ROWS)
    baseline = sensitivity[
        sensitivity["scenario_folder"].astype(str).eq("base_case")
        & sensitivity["case_name"].isin(ATTACHED_CASE_ORDER)
        & sensitivity["parameter_scenario"].astype(str).eq("strict_single_slack")
    ].copy()
    if "valid_converged" not in baseline.columns:
        baseline["valid_converged"] = baseline["converged"]
    matrix: list[dict[str, Any]] = []
    for config, label in SLACK_CONFIGS:
        item: dict[str, Any] = {"config_name": config, "row_label": label}
        for case in ATTACHED_CASE_ORDER:
            if config == "matched_single_slack":
                item[case] = count_variants(baseline, "parameter_scenario", "strict_single_slack", case, "valid_converged")
            else:
                item[case] = count_variants(rows, "extended_config_name", config, case, "valid_converged")
        matrix.append(item)
    return matrix


def make_control_matrix(voltage_init_mode: str) -> list[dict[str, Any]]:
    rows = read_csv(INTERACTIVE_ROWS)
    rows = rows[rows["voltage_init_mode"].astype(str).eq(voltage_init_mode)]
    matrix: list[dict[str, Any]] = []
    for config, label in CONTROL_CONFIGS:
        item: dict[str, Any] = {"config_name": config, "row_label": label}
        for case in ATTACHED_CASE_ORDER:
            item[case] = count_variants(rows, "config_name", config, case, "converged")
        matrix.append(item)
    return matrix


def make_previous_values_control_matrix() -> list[dict[str, Any]]:
    """Control matrix aligned with the matched single-slack baseline.

    The factorial sweep preserves the variant-specific initialization behavior:
    variants with VS use previous/bus-voltage values, while non-VS variants use
    uniform values. This matches the baseline convergence heatmap.
    """
    rows = read_csv(FACTORIAL_ROWS)
    rows = rows[
        rows["case_name"].isin(SLACK_CASE_ORDER)
        & rows["factorial_config_name"].isin([name for name, _ in FACTORIAL_CONTROL_CONFIGS])
    ]
    matrix: list[dict[str, Any]] = []
    for config, label in FACTORIAL_CONTROL_CONFIGS:
        item: dict[str, Any] = {"config_name": config, "row_label": label}
        for case in SLACK_CASE_ORDER:
            item[case] = count_variants(rows, "factorial_config_name", config, case, "valid_converged")
        matrix.append(item)
    return matrix


def make_dc_transition_matrix() -> list[dict[str, Any]]:
    uniform = make_control_matrix("UNIFORM_VALUES")
    dc = make_control_matrix("DC_VALUES")
    uniform_by_config = {row["config_name"]: row for row in uniform}
    dc_by_config = {row["config_name"]: row for row in dc}
    matrix: list[dict[str, Any]] = []
    for config, label in CONTROL_CONFIGS:
        item: dict[str, Any] = {"config_name": config, "row_label": label}
        for case in ATTACHED_CASE_ORDER:
            u_cell = uniform_by_config[config][case]
            d_cell = dc_by_config[config][case]
            item[case] = {
                "uniform": int(u_cell["count"]),
                "dc": int(d_cell["count"]),
                "total": int(max(u_cell["total"], d_cell["total"])),
            }
        matrix.append(item)
    return matrix


def flatten_matrix(matrix: list[dict[str, Any]], cases: list[str]) -> pd.DataFrame:
    rows = []
    for row in matrix:
        for case in cases:
            cell = row[case]
            flat = {
                "config_name": row["config_name"],
                "config_label": row["row_label"].replace("\n", " "),
                "case_name": case,
                "case_label": CASE_LABELS.get(case, case),
            }
            flat.update(cell)
            rows.append(flat)
    return pd.DataFrame(rows)


def write_csvs(
    slack: list[dict[str, Any]],
    previous_values_controls: list[dict[str, Any]],
    uniform: list[dict[str, Any]],
    dc_transition: list[dict[str, Any]],
) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    flatten_matrix(slack, ATTACHED_CASE_ORDER).to_csv(OUT_DIR / "slack_modelling_config_case_matrix.csv", index=False)
    flatten_matrix(previous_values_controls, SLACK_CASE_ORDER).to_csv(
        OUT_DIR / "previous_values_q_limits_ops_config_case_matrix.csv",
        index=False,
    )
    flatten_matrix(uniform, ATTACHED_CASE_ORDER).to_csv(OUT_DIR / "uniform_q_limits_ops_config_case_matrix.csv", index=False)
    flatten_matrix(dc_transition, ATTACHED_CASE_ORDER).to_csv(OUT_DIR / "dc_initialization_transition_config_case_matrix.csv", index=False)


def write_notes() -> None:
    lines = [
        "# Focused PyPowSyBl Presentation Heatmaps",
        "",
        "These figures use configurations on the y-axis and cases on the x-axis.",
        "",
        "## Figures",
        "",
        "- `figures/slack_modelling_variant_heatmap.png`: aggregate slack-modelling check across all six cases. `Matched single slack` and `Explicit original slack` were collapsed into `Single original slack bus`; all displayed slack-model cells now come from completed runs.",
        "- `figures/previous_values_q_limits_ops_variant_heatmap.png`: Q-limit and operational-control check for the four-case factorial sweep.",
        "- `figures/uniform_q_limits_ops_variant_heatmap.png`: Q-limit and operational-control check for the six-case four-configuration sweep.",
        "- `figures/dc_initialization_transition_heatmap.png`: transition from `UNIFORM_VALUES` to `DC_VALUES`. A cell such as `N -> Y` means uniform failed all variants and DC solved all variants. Mixed cells use counts, such as `3/5 -> 5/5`. Colors now distinguish full recovery, partial recovery, unchanged partial convergence, convergence loss, and full failure.",
        "",
        "## Key Reading",
        "",
        "- Slack modelling does not materially change the convergence pattern in the focused problematic cases.",
        "- Q limits and operational controls do not explain convergence recovery in these sweeps.",
        "- Cases such as 1888 RTE are sensitive to the starting point; that is why the DC transition plot is a separate initialization diagnostic.",
        "- DC initialization is the strongest recovery factor in this layer, especially for 1888 RTE and 6470 RTE.",
        "",
    ]
    (OUT_DIR / "focused_heatmap_interpretation.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    slack = make_slack_matrix()
    previous_values_controls = make_previous_values_control_matrix()
    uniform = make_control_matrix("UNIFORM_VALUES")
    dc_transition = make_dc_transition_matrix()
    write_csvs(slack, previous_values_controls, uniform, dc_transition)

    draw_count_heatmap(
        slack,
        ATTACHED_CASE_ORDER,
        FIG_DIR / "slack_modelling_variant_heatmap.png",
        title="Slack modelling check",
    )
    draw_count_heatmap(
        previous_values_controls,
        SLACK_CASE_ORDER,
        FIG_DIR / "previous_values_q_limits_ops_variant_heatmap.png",
        title="Q limits and operational controls",
    )
    draw_count_heatmap(
        uniform,
        ATTACHED_CASE_ORDER,
        FIG_DIR / "uniform_q_limits_ops_variant_heatmap.png",
        title="Q limits and operational controls",
    )
    draw_transition_heatmap(
        dc_transition,
        ATTACHED_CASE_ORDER,
        FIG_DIR / "dc_initialization_transition_heatmap.png",
        title="DC initialization improvement",
    )
    write_notes()
    print(f"Wrote focused heatmaps to {FIG_DIR}")


if __name__ == "__main__":
    main()
