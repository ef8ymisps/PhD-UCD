"""Run focused advanced PyPowSyBl/OpenLoadFlow configuration experiments.

The baseline matrix intentionally keeps the PowerModels comparison clean. This
script starts the next layer: numerical robustness and initialization variants
for the problematic/base diagnostic cases, without writing the very large
mapping audit table.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pypowsybl_replacement_experiment_matrix import (  # noqa: E402
    DEFAULT_MAT_DIR,
    DEFAULT_M_DIR,
    VARIANT_LABELS,
    ParameterScenario,
    ReplacementVariant,
    _balance,
    _case_paths,
    _feasibility,
    _is_converged_status,
    _previous_values_available,
    _pypowsybl_version,
    apply_replacement_variant,
    load_case_network,
    make_replacement_variants,
    parse_replacement_notes,
    require_pypowsybl,
    run_openloadflow,
    verify_replacement_mapping,
)


OUTPUT_DIR = (
    PROJECT_ROOT
    / "results"
    / "presentable"
    / "pypowsybl_lf_replacement_sweep"
    / "advanced_configuration_analysis"
)
FIGURE_DIR = OUTPUT_DIR / "figures"
BASE_CASE_MAT_DIR = DEFAULT_MAT_DIR / "base_case"
BASE_CASE_M_DIR = DEFAULT_M_DIR / "base_case"
CONFIG_MATRIX = (
    PROJECT_ROOT
    / "results"
    / "presentable"
    / "pypowsybl_lf_replacement_sweep"
    / "baseline_problem_case_analysis"
    / "advanced_configuration_matrix.csv"
)

CASE_NAMES = [
    "pglib_opf_case300_ieee",
    "pglib_opf_case1888_rte",
    "pglib_opf_case6470_rte",
    "pglib_opf_case13659_pegase",
]

CASE_LABELS = {
    "pglib_opf_case300_ieee": "300 IEEE",
    "pglib_opf_case1888_rte": "1888 RTE",
    "pglib_opf_case6470_rte": "6470 RTE",
    "pglib_opf_case13659_pegase": "13659 PEGASE",
}

VARIANT_ORDER = [
    "original",
    "BV_from_warmstart",
    "G_PGQG_from",
    "G_PGQG_from_plus_BV_from",
    "G_PGQG_from_plus_GVG_from",
]

GENERATOR_VARIANTS = [
    "G_PGQG_from",
    "G_PGQG_from_plus_BV_from",
    "G_PGQG_from_plus_GVG_from",
]

DEFAULT_CONFIGS = [
    "nr_baseline",
    "nr_line_search",
    "nr_max_voltage_change",
    "nr_per_equation_stop",
    "newton_krylov",
    "flat_start",
    "dc_start",
]

CONFIG_LABELS = {
    "nr_baseline": "NR baseline",
    "nr_line_search": "NR line search",
    "nr_max_voltage_change": "NR max voltage change",
    "nr_per_equation_stop": "NR per-equation stop",
    "newton_krylov": "Newton-Krylov",
    "flat_start": "Flat start",
    "dc_start": "DC start",
}


def parse_provider_parameters(text: str | float | None) -> dict[str, str]:
    if text is None or pd.isna(text) or str(text).strip() == "":
        return {}
    result: dict[str, str] = {}
    for item in str(text).split(";"):
        if not item.strip():
            continue
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def enum_name(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "name", value)).split(".")[-1]


def build_scenario(config: pd.Series) -> ParameterScenario:
    pp = require_pypowsybl()
    lf = pp.loadflow
    provider_parameters = parse_provider_parameters(config.get("provider_parameters"))

    voltage_init_mode = lf.VoltageInitMode.UNIFORM_VALUES
    if str(config["config_name"]) == "dc_start":
        voltage_init_mode = lf.VoltageInitMode.DC_VALUES
    elif str(config["config_name"]) in {"previous_voltage_magnitude", "previous_full_voltage"}:
        voltage_init_mode = lf.VoltageInitMode.PREVIOUS_VALUES

    kwargs: dict[str, Any] = {
        "voltage_init_mode": voltage_init_mode,
        "distributed_slack": False,
        "use_reactive_limits": False,
        "transformer_voltage_control_on": False,
        "shunt_compensator_voltage_control_on": False,
        "phase_shifter_regulation_on": False,
        "read_slack_bus": True,
        "write_slack_bus": True,
        "component_mode": lf.ComponentMode.MAIN_CONNECTED,
        "provider_parameters": provider_parameters,
    }

    params = lf.Parameters(**kwargs)
    settings = {
        "advanced_stage": int(config["stage"]),
        "advanced_config_name": str(config["config_name"]),
        "advanced_config_purpose": str(config.get("purpose", "")),
        "voltage_init_mode": enum_name(voltage_init_mode),
        "requested_voltage_init_mode": enum_name(voltage_init_mode),
        "use_reactive_limits": False,
        "distributed_slack": False,
        "balance_type": None,
        "transformer_voltage_control_on": False,
        "shunt_compensator_voltage_control_on": False,
        "phase_shifter_regulation_on": False,
        "read_slack_bus": True,
        "write_slack_bus": True,
        "component_mode": "MAIN_CONNECTED",
    }
    return ParameterScenario(
        str(config["config_name"]),
        params,
        provider="OpenLoadFlow",
        settings=settings,
        provider_parameters=provider_parameters,
    )


def force_previous_values(scenario: ParameterScenario) -> ParameterScenario:
    pp = require_pypowsybl()
    lf = pp.loadflow
    settings = dict(scenario.settings)
    settings["voltage_init_mode"] = "PREVIOUS_VALUES"
    kwargs = {
        "voltage_init_mode": lf.VoltageInitMode.PREVIOUS_VALUES,
        "distributed_slack": settings.get("distributed_slack"),
        "use_reactive_limits": settings.get("use_reactive_limits"),
        "transformer_voltage_control_on": settings.get("transformer_voltage_control_on"),
        "shunt_compensator_voltage_control_on": settings.get("shunt_compensator_voltage_control_on"),
        "phase_shifter_regulation_on": settings.get("phase_shifter_regulation_on"),
        "read_slack_bus": settings.get("read_slack_bus"),
        "write_slack_bus": settings.get("write_slack_bus"),
        "component_mode": lf.ComponentMode.MAIN_CONNECTED,
        "provider_parameters": scenario.provider_parameters,
    }
    return ParameterScenario(
        scenario.name,
        lf.Parameters(**kwargs),
        provider=scenario.provider,
        settings=settings,
        provider_parameters=scenario.provider_parameters,
    )


def selected_variants(case_name: str, config_name: str) -> list[str]:
    if case_name == "pglib_opf_case300_ieee":
        return VARIANT_ORDER
    if config_name in {"flat_start", "dc_start", "previous_voltage_magnitude", "previous_full_voltage"}:
        return VARIANT_ORDER
    return GENERATOR_VARIANTS


def row_key(row: pd.Series | dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row["case_name"]),
        str(row["variant_name"]),
        str(row["advanced_config_name"]),
    )


def load_existing(path: Path) -> pd.DataFrame:
    if path.exists() and path.stat().st_size:
        return pd.read_csv(path)
    return pd.DataFrame()


def save_dataframe_with_retry(frame: pd.DataFrame, path: Path, attempts: int = 20) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    last_exc: PermissionError | None = None
    for attempt in range(attempts):
        try:
            frame.to_csv(path, index=False)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(min(0.5 * (attempt + 1), 5.0))
    if last_exc is not None:
        raise last_exc


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def run_one(
    case_name: str,
    variant: ReplacementVariant,
    scenario: ParameterScenario,
    *,
    mat_dir: Path,
    m_dir: Path,
) -> dict[str, Any]:
    mat_path, m_path = _case_paths(case_name, mat_dir, m_dir)
    notes = parse_replacement_notes(m_path)
    run_scenario = force_previous_values(scenario) if variant.voltage_init == "PREVIOUS_VALUES" else scenario

    row: dict[str, Any] = {
        "case_name": case_name,
        "case_label": CASE_LABELS.get(case_name, case_name),
        "variant_name": variant.name,
        "variant_label": VARIANT_LABELS.get(variant.name, variant.name),
        "advanced_stage": scenario.settings.get("advanced_stage"),
        "advanced_config_name": scenario.settings.get("advanced_config_name", scenario.name),
        "advanced_config_label": CONFIG_LABELS.get(scenario.name, scenario.name),
        "advanced_config_purpose": scenario.settings.get("advanced_config_purpose", ""),
        "replacement_groups": "+".join(variant.groups),
        "direction": variant.direction,
        "provider": run_scenario.provider,
        "pypowsybl_version": _pypowsybl_version(),
        "requested_voltage_init_mode": scenario.settings.get("requested_voltage_init_mode"),
        "actual_voltage_init_mode": run_scenario.settings.get("voltage_init_mode"),
        "use_reactive_limits": run_scenario.settings.get("use_reactive_limits"),
        "distributed_slack": run_scenario.settings.get("distributed_slack"),
        "transformer_voltage_control_on": run_scenario.settings.get("transformer_voltage_control_on"),
        "shunt_compensator_voltage_control_on": run_scenario.settings.get("shunt_compensator_voltage_control_on"),
        "phase_shifter_regulation_on": run_scenario.settings.get("phase_shifter_regulation_on"),
        "component_mode": run_scenario.settings.get("component_mode"),
        "provider_parameters": ";".join(f"{key}={value}" for key, value in sorted(run_scenario.provider_parameters.items())),
        "mapping_success": False,
        "n_notes_applied": 0,
        "n_notes_unmatched": 0,
        "n_applied_gen_pg": 0,
        "n_applied_gen_qg": 0,
        "n_applied_gen_vg": 0,
        "n_applied_bus_voltage": 0,
        "n_applied_loads": 0,
        "gen_minus_load_before": None,
        "gen_minus_load_after": None,
        "total_generation_after": None,
        "total_load_after": None,
        "total_max_generation_after": None,
        "total_headroom_after": None,
        "number_of_generators_with_target_p_above_max_p": None,
        "number_of_generators_with_target_q_outside_limits": None,
        "status": "NOT_RUN",
        "status_text": None,
        "converged": False,
        "all_components_converged": False,
        "valid_converged": False,
        "component_count": 0,
        "main_component_status": None,
        "iteration_count": None,
        "reference_bus_id": None,
        "slack_bus_id": None,
        "slack_active_power_mismatch": None,
        "distributed_active_power": None,
        "elapsed_seconds_single": None,
        "exception": None,
    }

    try:
        if variant.voltage_init == "PREVIOUS_VALUES" and not _previous_values_available():
            row["status"] = "SKIPPED"
            row["exception"] = "VoltageInitMode.PREVIOUS_VALUES not available"
            return row

        network = load_case_network(mat_path)
        before = _balance(network)
        audit, counts = apply_replacement_variant(
            network,
            notes,
            variant,
            case_name=case_name,
            parameter_scenario=scenario.name,
            strict=False,
        )
        after = _balance(network)
        feasibility = _feasibility(network)
        unmatched = audit[
            audit["matched_object_id"].isna()
            & audit["note_type"].isin(["load", "bus_voltage", "gen_pgqg", "gen_vg"])
        ]
        applied_fields = int(audit["applied"].fillna(False).sum()) if "applied" in audit else 0
        row.update(
            {
                **counts,
                "n_notes_applied": applied_fields,
                "n_notes_unmatched": int(len(unmatched)),
                "mapping_success": int(len(unmatched)) == 0,
                "gen_minus_load_before": before["gen_minus_load"],
                "gen_minus_load_after": after["gen_minus_load"],
                "total_generation_after": after["total_generation"],
                "total_load_after": after["total_load"],
                **feasibility,
            }
        )
        result = run_openloadflow(network, run_scenario.params, provider=run_scenario.provider)
        row.update({key: value for key, value in result.items() if key not in {"component_results", "raw_results", "elapsed_seconds"}})
        row["elapsed_seconds_single"] = result.get("elapsed_seconds_single")
        row["valid_converged"] = (
            bool(row.get("converged"))
            and bool(row.get("all_components_converged"))
            and _is_converged_status(row.get("status"))
        )
    except Exception as exc:
        row["status"] = "EXCEPTION"
        row["exception"] = repr(exc)
        try:
            network = load_case_network(mat_path)
            audit = verify_replacement_mapping(
                network,
                notes,
                case_name=case_name,
                variant_name=variant.name,
                parameter_scenario=scenario.name,
            )
            unmatched = audit[audit["matched_object_id"].isna()]
            row["n_notes_unmatched"] = int(len(unmatched))
            row["mapping_success"] = int(len(unmatched)) == 0
        except Exception:
            pass
    return row


def build_case_config_summary(rows: pd.DataFrame) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if rows.empty:
        return result
    for (case_name, config_name), group in rows.groupby(["case_name", "advanced_config_name"]):
        converged = group[group["valid_converged"].astype(str).str.lower().eq("true") | group["valid_converged"].eq(True)]
        result.append(
            {
                "case_name": case_name,
                "case_label": CASE_LABELS.get(case_name, case_name),
                "advanced_config_name": config_name,
                "advanced_config_label": CONFIG_LABELS.get(config_name, config_name),
                "n_variants_run": int(len(group)),
                "n_variants_converged": int(len(converged)),
                "converged_variants": "; ".join(converged["variant_label"].astype(str).tolist()),
                "max_iteration_count": group["iteration_count"].max(),
                "max_elapsed_seconds_single": group["elapsed_seconds_single"].max(),
            }
        )
    return result


def build_recovery_summary(rows: pd.DataFrame) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if rows.empty:
        return result
    # Use flat_start as the five-variant baseline when available. The
    # nr_baseline config intentionally runs only generator variants for the
    # larger cases, while flat_start covers Original and VS init too.
    baseline = rows[rows["advanced_config_name"].eq("flat_start")]
    if baseline.empty:
        baseline = rows[rows["advanced_config_name"].eq("nr_baseline")]
    baseline_map = {
        (row["case_name"], row["variant_name"]): bool(row["valid_converged"])
        for _, row in baseline.iterrows()
    }
    for (case_name, variant_name), group in rows.groupby(["case_name", "variant_name"]):
        base = baseline_map.get((case_name, variant_name), False)
        recovered = group[
            (group["advanced_config_name"] != "nr_baseline")
            & (group["valid_converged"].astype(str).str.lower().eq("true") | group["valid_converged"].eq(True))
        ]
        result.append(
            {
                "case_name": case_name,
                "case_label": CASE_LABELS.get(case_name, case_name),
                "variant_name": variant_name,
                "variant_label": VARIANT_LABELS.get(variant_name, variant_name),
                "baseline_converged": bool(base),
                "n_advanced_configs_converged": int(len(recovered)),
                "advanced_configs_converged": "; ".join(recovered["advanced_config_label"].astype(str).tolist()),
                "newly_recovered": bool((not base) and len(recovered) > 0),
            }
        )
    return result


def plot_case_config_heatmap(summary: pd.DataFrame) -> None:
    if summary.empty:
        return
    config_order = [name for name in DEFAULT_CONFIGS if name in set(summary["advanced_config_name"])]
    case_order = [case for case in CASE_NAMES if case in set(summary["case_name"])]
    by_key = {(row["case_name"], row["advanced_config_name"]): row for _, row in summary.iterrows()}
    values: list[list[float]] = []
    annotations: list[list[str]] = []
    for case in case_order:
        value_row = []
        annotation_row = []
        for config in config_order:
            row = by_key.get((case, config))
            if row is None:
                value_row.append(float("nan"))
                annotation_row.append("")
            else:
                total = int(row["n_variants_run"])
                ok = int(row["n_variants_converged"])
                value_row.append(ok / total if total else 0.0)
                annotation_row.append(f"{ok}/{total}")
        values.append(value_row)
        annotations.append(annotation_row)

    fig, ax = plt.subplots(figsize=(12.0, 4.8))
    ax.imshow(values, cmap="RdYlGn", vmin=0.0, vmax=1.0, aspect="auto")
    ax.set_xticks(range(len(config_order)))
    ax.set_xticklabels([CONFIG_LABELS.get(name, name) for name in config_order], rotation=30, ha="right")
    ax.set_yticks(range(len(case_order)))
    ax.set_yticklabels([CASE_LABELS.get(case, case) for case in case_order])
    ax.set_title("Advanced OpenLoadFlow convergence by case and configuration")
    for i, row in enumerate(annotations):
        for j, text in enumerate(row):
            ax.text(j, i, text, ha="center", va="center", fontsize=9)
    fig.tight_layout()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_DIR / "advanced_configuration_case_config_heatmap.png", dpi=180)
    plt.close(fig)


def plot_recovery_heatmap(rows: pd.DataFrame) -> None:
    if rows.empty:
        return
    config_order = [name for name in DEFAULT_CONFIGS if name in set(rows["advanced_config_name"])]
    row_labels: list[str] = []
    values: list[list[int]] = []
    for case in CASE_NAMES:
        case_rows = rows[rows["case_name"].eq(case)]
        for variant in VARIANT_ORDER:
            variant_rows = case_rows[case_rows["variant_name"].eq(variant)]
            if variant_rows.empty:
                continue
            row_labels.append(f"{CASE_LABELS.get(case, case)} | {VARIANT_LABELS.get(variant, variant)}")
            values.append(
                [
                    1
                    if bool(
                        variant_rows[variant_rows["advanced_config_name"].eq(config)]["valid_converged"]
                        .astype(str)
                        .str.lower()
                        .eq("true")
                        .any()
                    )
                    else 0
                    for config in config_order
                ]
            )

    fig, ax = plt.subplots(figsize=(12.0, max(5.0, 0.35 * len(row_labels))))
    ax.imshow(
        values,
        cmap=matplotlib.colors.ListedColormap(["#b94b48", "#4f7f62"]),
        vmin=0,
        vmax=1,
        aspect="auto",
    )
    ax.set_xticks(range(len(config_order)))
    ax.set_xticklabels([CONFIG_LABELS.get(name, name) for name in config_order], rotation=30, ha="right")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=8)
    ax.set_title("Advanced OpenLoadFlow convergence by variant")
    for i, row in enumerate(values):
        for j, value in enumerate(row):
            ax.text(j, i, "Y" if value else "N", ha="center", va="center", color="white", fontsize=8)
    fig.tight_layout()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_DIR / "advanced_configuration_variant_heatmap.png", dpi=180)
    plt.close(fig)


def write_interpretation(rows: pd.DataFrame, recovery: pd.DataFrame) -> None:
    lines = [
        "# Advanced PyPowSyBl Configuration Analysis",
        "",
        "This analysis extends the matched single-slack baseline with numerical robustness and initialization configurations.",
        "",
        "Scope:",
        "",
        "- cases: `300`, `1888`, `6470`, `13659`",
        "- no `78484` run in this layer",
        "- stages run: numerical robustness and initialization",
        "- controls that change the physical LF model, such as Q limits, distributed slack, transformer controls, shunt controls, and phase-shifter regulation, remain staged for the next pass.",
        "",
        "## Newly Recovered Baseline Failures",
        "",
        "| Case | Variant | Advanced configurations that converged |",
        "|---|---|---|",
    ]
    if recovery.empty or not bool(recovery["newly_recovered"].any()):
        lines.append("| none | none | none |")
    else:
        for _, row in recovery[recovery["newly_recovered"]].iterrows():
            lines.append(
                f"| {row['case_label']} | {row['variant_label']} | {row['advanced_configs_converged']} |"
            )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "If a baseline-failing variant converges under `nr_line_search`, `nr_max_voltage_change`, or `newton_krylov`, the issue is likely numerical path sensitivity rather than the operating point itself.",
            "",
            "If a baseline-failing variant converges under `dc_start`, the issue is primarily initialization sensitivity.",
            "",
            "Rows involving `VS init` or `Gen P/Q + VS` use `PREVIOUS_VALUES` as the actual voltage initialization mode, because the voltage-setpoint replacement is meant to feed the initial voltage state.",
        ]
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "advanced_configuration_interpretation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_artifacts(summary_csv: Path) -> None:
    rows = load_existing(summary_csv)
    if rows.empty:
        return
    rows = rows.copy()
    if "variant_name" in rows:
        variant_labels = rows["variant_name"].astype(str).map(VARIANT_LABELS)
        rows["variant_label"] = variant_labels.fillna(rows.get("variant_label", rows["variant_name"]))
    if "advanced_config_name" in rows:
        config_labels = rows["advanced_config_name"].astype(str).map(CONFIG_LABELS)
        rows["advanced_config_label"] = config_labels.fillna(rows.get("advanced_config_label", rows["advanced_config_name"]))
    presentation_rows = rows[rows["advanced_config_name"].isin(DEFAULT_CONFIGS)].copy()
    case_config_rows = build_case_config_summary(presentation_rows)
    recovery_rows = build_recovery_summary(presentation_rows)
    write_csv(OUTPUT_DIR / "advanced_configuration_case_config_summary.csv", case_config_rows)
    write_csv(OUTPUT_DIR / "advanced_configuration_recovery_summary.csv", recovery_rows)
    case_config = pd.DataFrame(case_config_rows)
    recovery = pd.DataFrame(recovery_rows)
    plot_case_config_heatmap(case_config)
    plot_recovery_heatmap(presentation_rows)
    write_interpretation(presentation_rows, recovery)


def run_matrix(args: argparse.Namespace) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    summary_csv = OUTPUT_DIR / "advanced_configuration_rows.csv"

    matrix = pd.read_csv(CONFIG_MATRIX)
    if args.config:
        matrix = matrix[matrix["config_name"].isin(args.config)]
    else:
        matrix = matrix[matrix["config_name"].isin(DEFAULT_CONFIGS)]
    if args.stage:
        matrix = matrix[matrix["stage"].isin(args.stage)]
    if matrix.empty:
        raise ValueError("No advanced configurations selected.")

    variants = make_replacement_variants(include_bus_voltage=True)
    variants = {name: variants[name] for name in VARIANT_ORDER}
    cases = args.case or CASE_NAMES

    existing = load_existing(summary_csv)
    completed = {row_key(row) for _, row in existing.iterrows()} if not existing.empty else set()
    rows = existing.to_dict("records") if not existing.empty else []

    n_run = 0
    for _, config in matrix.iterrows():
        scenario = build_scenario(config)
        for case_name in cases:
            for variant_name in selected_variants(case_name, scenario.name):
                variant = variants[variant_name]
                key = (case_name, variant_name, scenario.name)
                if args.resume and key in completed:
                    print(f"Skipping completed {case_name} | {variant_name} | {scenario.name}")
                    continue
                print(f"\n=== {case_name} | {variant_name} | {scenario.name} ===")
                row = run_one(case_name, variant, scenario, mat_dir=Path(args.mat_dir), m_dir=Path(args.m_dir))
                rows.append(row)
                completed.add(key)
                n_run += 1
                save_dataframe_with_retry(pd.DataFrame(rows), summary_csv)
                print(
                    f"status={row['status']} valid={row['valid_converged']} "
                    f"iterations={row['iteration_count']} init={row['actual_voltage_init_mode']}"
                )
                if args.max_rows and n_run >= args.max_rows:
                    create_artifacts(summary_csv)
                    print(f"Stopped after --max-rows={args.max_rows}")
                    return

    create_artifacts(summary_csv)
    print(f"Wrote advanced configuration artifacts to {OUTPUT_DIR}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run focused advanced PyPowSyBl/OpenLoadFlow configuration analysis.")
    parser.add_argument("--case", action="append", choices=CASE_NAMES, help="Case to include; may be repeated.")
    parser.add_argument("--config", action="append", help="Advanced config name to include; may be repeated.")
    parser.add_argument("--stage", action="append", type=int, help="Advanced config stage to include; may be repeated.")
    parser.add_argument("--mat-dir", type=Path, default=BASE_CASE_MAT_DIR)
    parser.add_argument("--m-dir", type=Path, default=BASE_CASE_M_DIR)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--artifacts-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.artifacts_only:
        create_artifacts(OUTPUT_DIR / "advanced_configuration_rows.csv")
        print(f"Wrote advanced configuration artifacts to {OUTPUT_DIR}")
        return
    run_matrix(args)


if __name__ == "__main__":
    main()
