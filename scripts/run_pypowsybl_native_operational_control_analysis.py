from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "results" / "presentable" / "pypowsybl_lf_replacement_sweep" / "native_operational_control_analysis"
FIGURE_DIR = OUTPUT_DIR / "figures"

NETWORKS = [
    "create_eurostag_tutorial_example1_network",
    "create_eurostag_tutorial_example1_with_power_limits_network",
    "create_four_substations_node_breaker_network",
    "create_four_substations_node_breaker_network_with_extensions",
    "create_ieee300",
    "create_metrix_tutorial_six_buses_network",
    "create_micro_grid_be_network",
    "create_micro_grid_nl_network",
]

NETWORK_LABELS = {
    "create_eurostag_tutorial_example1_network": "Eurostag tutorial",
    "create_eurostag_tutorial_example1_with_power_limits_network": "Eurostag + P limits",
    "create_four_substations_node_breaker_network": "4-substation node-breaker",
    "create_four_substations_node_breaker_network_with_extensions": "4-substation + extensions",
    "create_ieee300": "IEEE 300 native",
    "create_metrix_tutorial_six_buses_network": "Metrix 6-bus",
    "create_micro_grid_be_network": "Microgrid BE",
    "create_micro_grid_nl_network": "Microgrid NL",
}

CONFIGS = [
    {
        "name": "single_no_q_no_ops",
        "label": "Single slack\nQ limits off\nOps off",
        "distributed_slack": False,
        "use_reactive_limits": False,
        "operational_controls": False,
    },
    {
        "name": "single_no_q_ops",
        "label": "Single slack\nQ limits off\nOps on",
        "distributed_slack": False,
        "use_reactive_limits": False,
        "operational_controls": True,
    },
    {
        "name": "distributed_no_q_no_ops",
        "label": "Distributed\nQ limits off\nOps off",
        "distributed_slack": True,
        "use_reactive_limits": False,
        "operational_controls": False,
    },
    {
        "name": "distributed_no_q_ops",
        "label": "Distributed\nQ limits off\nOps on",
        "distributed_slack": True,
        "use_reactive_limits": False,
        "operational_controls": True,
    },
    {
        "name": "distributed_q_no_ops",
        "label": "Distributed\nQ limits on\nOps off",
        "distributed_slack": True,
        "use_reactive_limits": True,
        "operational_controls": False,
    },
    {
        "name": "distributed_q_ops",
        "label": "Distributed\nQ limits on\nOps on",
        "distributed_slack": True,
        "use_reactive_limits": True,
        "operational_controls": True,
    },
]


def require_pypowsybl():
    import pypowsybl as pp

    return pp


def get_table(network: Any, method_name: str) -> pd.DataFrame:
    method = getattr(network, method_name, None)
    if method is None:
        return pd.DataFrame()
    try:
        table = method()
    except Exception:
        return pd.DataFrame()
    if table is None:
        return pd.DataFrame()
    return table.reset_index() if isinstance(table, pd.DataFrame) else pd.DataFrame(table)


def numeric(series: pd.Series | None) -> pd.Series:
    if series is None:
        return pd.Series(dtype=float)
    return pd.to_numeric(series, errors="coerce")


def bool_count(series: pd.Series | None) -> int:
    if series is None:
        return 0
    return int(series.fillna(False).astype(bool).sum())


def count_gt_one(series: pd.Series | None) -> int:
    if series is None:
        return 0
    return int(numeric(series).gt(1).sum())


def audit_network(network_name: str) -> dict[str, Any]:
    pp = require_pypowsybl()
    network = getattr(pp.network, network_name)()
    buses = get_table(network, "get_buses")
    generators = get_table(network, "get_generators")
    ratio_taps = get_table(network, "get_ratio_tap_changers")
    phase_taps = get_table(network, "get_phase_tap_changers")
    shunts = get_table(network, "get_shunt_compensators")
    svcs = get_table(network, "get_static_var_compensators")
    row = {
        "network_name": network_name,
        "n_buses": len(buses),
        "n_generators": len(generators),
        "n_generators_voltage_regulator_on": bool_count(generators.get("voltage_regulator_on")),
        "n_ratio_tap_changers": len(ratio_taps),
        "n_ratio_tap_changers_regulating": bool_count(ratio_taps.get("regulating")),
        "n_ratio_tap_changers_with_steps": count_gt_one(ratio_taps.get("step_count")),
        "n_phase_tap_changers": len(phase_taps),
        "n_phase_tap_changers_regulating": bool_count(phase_taps.get("regulating")),
        "n_phase_tap_changers_with_steps": count_gt_one(phase_taps.get("step_count")),
        "n_shunts": len(shunts),
        "n_shunts_voltage_regulation_on": bool_count(shunts.get("voltage_regulation_on")),
        "n_shunts_with_multiple_sections": count_gt_one(shunts.get("max_section_count")),
        "n_static_var_compensators": len(svcs),
    }
    row["supports_operational_controls"] = int(
        row["n_ratio_tap_changers_regulating"] > 0
        or row["n_ratio_tap_changers_with_steps"] > 0
        or row["n_phase_tap_changers_regulating"] > 0
        or row["n_phase_tap_changers_with_steps"] > 0
        or row["n_shunts_voltage_regulation_on"] > 0
        or row["n_shunts_with_multiple_sections"] > 0
        or row["n_static_var_compensators"] > 0
    )
    return row


def first_component_result(results: Any) -> Any | None:
    if results is None:
        return None
    if isinstance(results, list):
        return results[0] if results else None
    try:
        return results[0]
    except Exception:
        return results


def attr_value(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def status_text(component: Any) -> str:
    status = attr_value(component, "status", "")
    return str(getattr(status, "name", status))


def is_converged(component: Any) -> bool:
    return "CONVERGED" in status_text(component).upper()


def slack_summary(component: Any) -> dict[str, Any]:
    slack_results = attr_value(component, "slack_bus_results", None)
    if slack_results is None:
        return {"slack_bus_id": "", "slack_active_power_mismatch": math.nan}
    try:
        first = slack_results[0]
    except Exception:
        return {"slack_bus_id": "", "slack_active_power_mismatch": math.nan}
    return {
        "slack_bus_id": attr_value(first, "id", ""),
        "slack_active_power_mismatch": attr_value(first, "active_power_mismatch", math.nan),
    }


def make_parameters(config: dict[str, Any]):
    pp = require_pypowsybl()
    lf = pp.loadflow
    ops = bool(config["operational_controls"])
    provider_parameters = {
        "acSolverType": "NEWTON_RAPHSON",
        "maxNewtonRaphsonIterations": "200",
        "disableInconsistentVoltageControls": "false",
        "fixVoltageTargets": "true",
        "phaseShifterControlMode": "CONTINUOUS_WITH_DISCRETISATION",
        "shuntVoltageControlMode": "WITH_GENERATOR_VOLTAGE_CONTROL",
        "transformerVoltageControlMode": "INCREMENTAL_VOLTAGE_CONTROL",
        "slackBusSelectionMode": "LARGEST_GENERATOR",
    }
    kwargs = {
        "voltage_init_mode": lf.VoltageInitMode.DC_VALUES if config["distributed_slack"] else lf.VoltageInitMode.UNIFORM_VALUES,
        "distributed_slack": bool(config["distributed_slack"]),
        "use_reactive_limits": bool(config["use_reactive_limits"]),
        "transformer_voltage_control_on": ops,
        "shunt_compensator_voltage_control_on": ops,
        "phase_shifter_regulation_on": ops,
        "read_slack_bus": True,
        "write_slack_bus": True,
        "component_mode": lf.ComponentMode.MAIN_CONNECTED,
        "provider_parameters": provider_parameters,
    }
    if config["distributed_slack"]:
        kwargs["balance_type"] = lf.BalanceType.PROPORTIONAL_TO_GENERATION_P_MAX
    return lf.Parameters(**kwargs), provider_parameters


def state_signature(network: Any, table_method: str, columns: tuple[str, ...]) -> dict[str, tuple[Any, ...]]:
    table = get_table(network, table_method)
    if table.empty or "id" not in table:
        return {}
    sig: dict[str, tuple[Any, ...]] = {}
    for _, row in table.iterrows():
        values = []
        for col in columns:
            value = row[col] if col in row else None
            if pd.isna(value):
                value = None
            values.append(value)
        sig[str(row["id"])] = tuple(values)
    return sig


def count_state_changes(before: dict[str, tuple[Any, ...]], after: dict[str, tuple[Any, ...]]) -> int:
    keys = set(before) | set(after)
    return sum(1 for key in keys if before.get(key) != after.get(key))


def network_quantities(network: Any) -> dict[str, Any]:
    buses = get_table(network, "get_buses")
    generators = get_table(network, "get_generators")
    branches = pd.concat(
        [
            get_table(network, "get_lines").assign(branch_type="line"),
            get_table(network, "get_2_windings_transformers").assign(branch_type="2wt"),
        ],
        ignore_index=True,
        sort=False,
    )
    row: dict[str, Any] = {}
    if not buses.empty:
        v_mag = numeric(buses.get("v_mag"))
        v_angle = numeric(buses.get("v_angle"))
        row.update(
            {
                "v_mag_min": float(v_mag.min(skipna=True)),
                "v_mag_max": float(v_mag.max(skipna=True)),
                "v_angle_min": float(v_angle.min(skipna=True)),
                "v_angle_max": float(v_angle.max(skipna=True)),
            }
        )
    if not generators.empty:
        target_p = numeric(generators.get("target_p")).fillna(0.0)
        target_q = numeric(generators.get("target_q")).fillna(0.0)
        row.update({"total_gen_p": float(target_p.sum()), "total_gen_q": float(target_q.sum())})
        q = numeric(generators.get("q"))
        min_q = numeric(generators.get("min_q"))
        max_q = numeric(generators.get("max_q"))
        row["q_below_min_after"] = int((q < min_q).fillna(False).sum())
        row["q_above_max_after"] = int((q > max_q).fillna(False).sum())
    if not branches.empty:
        p1 = numeric(branches.get("p1"))
        q1 = numeric(branches.get("q1"))
        p2 = numeric(branches.get("p2"))
        q2 = numeric(branches.get("q2"))
        s1 = (p1.pow(2) + q1.pow(2)).pow(0.5)
        s2 = (p2.pow(2) + q2.pow(2)).pow(0.5)
        row.update({"branch_s_max": float(pd.concat([s1, s2]).max(skipna=True))})
    return row


def run_one(network_name: str, config: dict[str, Any]) -> dict[str, Any]:
    pp = require_pypowsybl()
    network = getattr(pp.network, network_name)()
    params, provider_parameters = make_parameters(config)
    ratio_before = state_signature(network, "get_ratio_tap_changers", ("tap", "solved_tap_position", "regulating"))
    phase_before = state_signature(network, "get_phase_tap_changers", ("tap", "solved_tap_position", "regulating"))
    shunt_before = state_signature(network, "get_shunt_compensators", ("section_count", "solved_section_count", "voltage_regulation_on"))
    row = {
        "network_name": network_name,
        "config_name": config["name"],
        "config_label": config["label"].replace("\n", " | "),
        "distributed_slack": bool(config["distributed_slack"]),
        "use_reactive_limits": bool(config["use_reactive_limits"]),
        "operational_controls": bool(config["operational_controls"]),
        "provider": "OpenLoadFlow",
        "provider_parameters": ";".join(f"{key}={value}" for key, value in sorted(provider_parameters.items())),
    }
    try:
        results = pp.loadflow.run_ac(network, parameters=params)
        component = first_component_result(results)
        row.update(
            {
                "status": status_text(component),
                "status_text": attr_value(component, "status_text", ""),
                "valid_converged": is_converged(component),
                "iteration_count": attr_value(component, "iteration_count", math.nan),
                "distributed_active_power": attr_value(component, "distributed_active_power", math.nan),
            }
        )
        row.update(slack_summary(component))
        row.update(network_quantities(network))
        row["n_ratio_tap_state_changes"] = count_state_changes(
            ratio_before,
            state_signature(network, "get_ratio_tap_changers", ("tap", "solved_tap_position", "regulating")),
        )
        row["n_phase_tap_state_changes"] = count_state_changes(
            phase_before,
            state_signature(network, "get_phase_tap_changers", ("tap", "solved_tap_position", "regulating")),
        )
        row["n_shunt_state_changes"] = count_state_changes(
            shunt_before,
            state_signature(network, "get_shunt_compensators", ("section_count", "solved_section_count", "voltage_regulation_on")),
        )
    except Exception as exc:
        row.update(
            {
                "status": "EXCEPTION",
                "status_text": f"{type(exc).__name__}: {exc}",
                "valid_converged": False,
                "iteration_count": math.nan,
                "distributed_active_power": math.nan,
                "slack_bus_id": "",
                "slack_active_power_mismatch": math.nan,
                "n_ratio_tap_state_changes": math.nan,
                "n_phase_tap_state_changes": math.nan,
                "n_shunt_state_changes": math.nan,
            }
        )
    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def safe_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font: ImageFont.ImageFont) -> None:
    left, top, right, bottom = box
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=4, align="center")
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.multiline_text(
        (left + (right - left - width) / 2, top + (bottom - top - height) / 2),
        text,
        font=font,
        fill="#111111",
        spacing=4,
        align="center",
    )


def plot_heatmap(rows: pd.DataFrame) -> None:
    if rows.empty:
        return
    configs = [config["name"] for config in CONFIGS]
    config_labels = {config["name"]: config["label"] for config in CONFIGS}
    networks = NETWORKS
    by_key = {(row["network_name"], row["config_name"]): row for _, row in rows.iterrows()}
    left_margin = 310
    top_margin = 120
    header_h = 95
    row_h = 54
    cell_w = 145
    width = left_margin + cell_w * len(configs) + 40
    height = top_margin + header_h + row_h * len(networks) + 70
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((left_margin, 28), "Native PyPowSyBl operational-control check", font=safe_font(27, True), fill="#111111")
    draw.text((left_margin, 65), "Native networks with real control metadata. Cells show convergence and iterations.", font=safe_font(16), fill="#333333")
    grid_top = top_margin + header_h
    for col, config_name in enumerate(configs):
        x0 = left_margin + col * cell_w
        x1 = x0 + cell_w
        draw.rectangle((x0, top_margin, x1, grid_top), fill="#ffffff", outline="#333333")
        centered_text(draw, (x0 + 4, top_margin + 4, x1 - 4, grid_top - 4), config_labels[config_name], safe_font(13, True))
    for row_idx, network_name in enumerate(networks):
        y0 = grid_top + row_idx * row_h
        y1 = y0 + row_h
        centered_text(draw, (0, y0, left_margin - 12, y1), NETWORK_LABELS.get(network_name, network_name), safe_font(14))
        for col, config_name in enumerate(configs):
            row = by_key.get((network_name, config_name))
            ok = bool(row["valid_converged"]) if row is not None else False
            iters = "" if row is None or pd.isna(row.get("iteration_count")) else str(int(float(row["iteration_count"])))
            text = "Y" if ok else "N"
            if iters:
                text = f"{text}\n{iters} it"
            color = "#4f7f62" if ok else "#b94b48"
            x0 = left_margin + col * cell_w
            x1 = x0 + cell_w
            draw.rectangle((x0, y0, x1, y1), fill=color, outline="#ffffff", width=2)
            centered_text(draw, (x0, y0, x1, y1), text, safe_font(14, True))
    legend_top = grid_top + row_h * len(networks) + 22
    draw.rectangle((left_margin, legend_top - 2, left_margin + 22, legend_top + 20), fill="#4f7f62")
    draw.text((left_margin + 30, legend_top), "Converged", font=safe_font(14), fill="#222222")
    draw.rectangle((left_margin + 140, legend_top - 2, left_margin + 162, legend_top + 20), fill="#b94b48")
    draw.text((left_margin + 170, legend_top), "Failed", font=safe_font(14), fill="#222222")
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    image.save(FIGURE_DIR / "native_operational_controls_heatmap.png")


def write_interpretation(audit: pd.DataFrame, rows: pd.DataFrame) -> None:
    supports = int(audit["supports_operational_controls"].sum()) if not audit.empty else 0
    converged = int(rows["valid_converged"].fillna(False).astype(bool).sum()) if not rows.empty else 0
    total = len(rows)
    lines = [
        "# Native PyPowSyBl Operational-Control Analysis",
        "",
        "This artifact is intentionally separate from the PGLib/MATPOWER runs.",
        "",
        "The PGLib files are useful for matched PowerModels/PyPowSyBl PF comparisons, but they do not encode active transformer, shunt, or phase-shifter control metadata. These native PyPowSyBl networks do encode such metadata, so they are the right place to test OpenLoadFlow operational-control capability.",
        "",
        "## Scope",
        "",
        f"- native networks audited: {len(audit)}",
        f"- native networks with operational-control metadata: {supports}",
        f"- load-flow rows: {total}",
        f"- converged rows: {converged}/{total}",
        "",
        "## Reading",
        "",
        "Use this as a capability/control-metadata benchmark, not as evidence about the PGLib operating points. The useful contrast is:",
        "",
        "- PGLib/MATPOWER import: operational flags can be enabled, but there is no active control metadata to use.",
        "- Native PyPowSyBl networks: operational flags act on real regulating tap changers, phase shifters, shunts, or SVCs.",
        "",
        "The convergence result should be read together with iteration counts and tap/shunt state-change columns in `native_operational_control_rows.csv`.",
    ]
    (OUTPUT_DIR / "native_operational_control_interpretation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run native PyPowSyBl operational-control benchmark.")
    parser.add_argument("--network", action="append", choices=NETWORKS)
    parser.add_argument("--config", action="append", choices=[config["name"] for config in CONFIGS])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    networks = args.network or NETWORKS
    configs = [config for config in CONFIGS if args.config is None or config["name"] in set(args.config)]
    audit_rows = [audit_network(network_name) for network_name in networks]
    write_csv(OUTPUT_DIR / "native_operational_control_support_audit.csv", audit_rows)
    rows = []
    for network_name in networks:
        for config in configs:
            print(f"{network_name} | {config['name']}")
            row = run_one(network_name, config)
            rows.append(row)
            print(f"  {row['status']} converged={row['valid_converged']} iterations={row['iteration_count']}")
    write_csv(OUTPUT_DIR / "native_operational_control_rows.csv", rows)
    audit_df = pd.DataFrame(audit_rows)
    rows_df = pd.DataFrame(rows)
    plot_heatmap(rows_df)
    write_interpretation(audit_df, rows_df)
    print(f"Wrote native operational-control artifacts to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
