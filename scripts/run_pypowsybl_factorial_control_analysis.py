"""Run a compact PyPowSyBl/OpenLoadFlow control-factor analysis.

This is the focused follow-up to the wide extended-configuration sweep. It uses
only three binary factors:

* distributed slack off/on
* reactive-limit PV/PQ switching off/on
* operational controls off/on

The script also audits whether the imported PGLib networks actually contain
the control data needed for those factors to have an effect.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import run_pypowsybl_extended_configuration_analysis as ext  # noqa: E402
from pypowsybl_replacement_experiment_matrix import (  # noqa: E402
    VARIANT_LABELS,
    ReplacementVariant,
    _balance,
    _case_paths,
    _feasibility,
    _is_converged_status,
    _previous_values_available,
    _pypowsybl_version,
    apply_replacement_variant,
    get_table,
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
    / "factorial_control_analysis"
)
FIGURE_DIR = OUTPUT_DIR / "figures"

CASE_NAMES = ext.CASE_NAMES
CASE_LABELS = ext.CASE_LABELS
VARIANT_ORDER = ext.VARIANT_ORDER

ROWS_CSV = OUTPUT_DIR / "factorial_lf_rows.csv"
AUDIT_CSV = OUTPUT_DIR / "control_support_audit.csv"
BUS_STATES_CSV = OUTPUT_DIR / "solved_state_buses.csv"
GEN_STATES_CSV = OUTPUT_DIR / "solved_state_generators.csv"
BRANCH_STATES_CSV = OUTPUT_DIR / "solved_state_branches.csv"
RATIO_TAP_STATES_CSV = OUTPUT_DIR / "solved_state_ratio_tap_changers.csv"
PHASE_TAP_STATES_CSV = OUTPUT_DIR / "solved_state_phase_tap_changers.csv"
SHUNT_STATES_CSV = OUTPUT_DIR / "solved_state_shunt_compensators.csv"
SVC_STATES_CSV = OUTPUT_DIR / "solved_state_static_var_compensators.csv"


def bool_label(value: bool) -> str:
    return "on" if value else "off"


CONFIGS: list[dict[str, Any]] = []
for distributed_slack in (False, True):
    for pv_pq_switching in (False, True):
        for operational_controls in (False, True):
            name = (
                f"ds_{bool_label(distributed_slack)}"
                f"__pvpq_{bool_label(pv_pq_switching)}"
                f"__ops_{bool_label(operational_controls)}"
            )
            label = (
                f"DS {bool_label(distributed_slack)} | "
                f"PV/PQ {bool_label(pv_pq_switching)} | "
                f"Ops {bool_label(operational_controls)}"
            )
            CONFIGS.append(
                {
                    "config_name": name,
                    "config_label": label,
                    "distributed_slack": distributed_slack,
                    "pv_pq_switching": pv_pq_switching,
                    "operational_controls": operational_controls,
                }
            )

CONFIG_BY_NAME = {config["config_name"]: config for config in CONFIGS}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


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


def append_frame(path: Path, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, mode="a", index=False, header=not path.exists())


def load_existing(path: Path) -> pd.DataFrame:
    if path.exists() and path.stat().st_size:
        return pd.read_csv(path)
    return pd.DataFrame()


def enum_name(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "name", value)).split(".")[-1]


def bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().eq("true") | series.eq(True)


def row_key(row: pd.Series | dict[str, Any]) -> tuple[str, str, str]:
    return (str(row["case_name"]), str(row["variant_name"]), str(row["factorial_config_name"]))


def state_signature(network: Any, getter_name: str, columns: tuple[str, ...]) -> dict[str, tuple[Any, ...]]:
    table = get_table(network, getter_name)
    if table.empty or "id" not in table:
        return {}
    existing = [column for column in columns if column in table.columns]
    if not existing:
        return {}
    return {
        str(row["id"]): tuple(row.get(column) for column in existing)
        for _, row in table.iterrows()
    }


def count_state_changes(before: dict[str, tuple[Any, ...]], after: dict[str, tuple[Any, ...]]) -> int:
    count = 0
    for key, after_value in after.items():
        if key in before and before[key] != after_value:
            count += 1
    return count


def finite_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def q_limit_summary(network: Any) -> dict[str, Any]:
    generators = get_table(network, "get_generators")
    result = {
        "n_generators_q_below_min_after": None,
        "n_generators_q_above_max_after": None,
        "n_generators_q_at_limit_after": None,
        "n_generators_voltage_regulator_on_after": None,
    }
    if generators.empty:
        return result
    if "voltage_regulator_on" in generators:
        result["n_generators_voltage_regulator_on_after"] = int(generators["voltage_regulator_on"].fillna(False).astype(bool).sum())
    required = {"q", "min_q", "max_q"}
    if not required.issubset(generators.columns):
        return result
    q = finite_numeric(generators["q"])
    min_q = finite_numeric(generators["min_q"])
    max_q = finite_numeric(generators["max_q"])
    known = q.notna() & min_q.notna() & max_q.notna()
    eps = 1e-4
    result["n_generators_q_below_min_after"] = int((known & (q < min_q - eps)).sum())
    result["n_generators_q_above_max_after"] = int((known & (q > max_q + eps)).sum())
    result["n_generators_q_at_limit_after"] = int((known & ((q - min_q).abs().le(eps) | (q - max_q).abs().le(eps))).sum())
    return result


def branch_loading_frame(network: Any) -> pd.DataFrame:
    branches = get_table(network, "get_branches").copy()
    if branches.empty:
        return branches
    for col in ("p1", "q1", "p2", "q2"):
        if col in branches:
            branches[col] = finite_numeric(branches[col])
    if {"p1", "q1"}.issubset(branches.columns):
        branches["s1_mva"] = (branches["p1"].pow(2) + branches["q1"].pow(2)).pow(0.5)
    if {"p2", "q2"}.issubset(branches.columns):
        branches["s2_mva"] = (branches["p2"].pow(2) + branches["q2"].pow(2)).pow(0.5)
    s_cols = [col for col in ("s1_mva", "s2_mva") if col in branches]
    if s_cols:
        branches["s_max_mva"] = branches[s_cols].max(axis=1)

    limits = get_table(network, "get_operational_limits")
    if not limits.empty and {"element_id", "side", "type", "acceptable_duration", "value"}.issubset(limits.columns):
        apparent = limits[
            limits["type"].astype(str).eq("APPARENT_POWER")
            & finite_numeric(limits["acceptable_duration"]).eq(-1)
        ].copy()
        apparent["value"] = finite_numeric(apparent["value"])
        for side, col in (("ONE", "limit1_mva"), ("TWO", "limit2_mva")):
            side_limits = apparent[apparent["side"].astype(str).eq(side)]
            if not side_limits.empty:
                mapping = side_limits.groupby("element_id")["value"].min().to_dict()
                branches[col] = branches["id"].map(mapping)
        if "limit1_mva" in branches and "s1_mva" in branches:
            branches["loading1_pct"] = 100.0 * branches["s1_mva"] / finite_numeric(branches["limit1_mva"])
        if "limit2_mva" in branches and "s2_mva" in branches:
            branches["loading2_pct"] = 100.0 * branches["s2_mva"] / finite_numeric(branches["limit2_mva"])
        loading_cols = [col for col in ("loading1_pct", "loading2_pct") if col in branches]
        if loading_cols:
            branches["loading_max_pct"] = branches[loading_cols].max(axis=1)
    return branches


def branch_loading_summary(network: Any) -> dict[str, Any]:
    branches = branch_loading_frame(network)
    result = {
        "n_branches_with_limits": None,
        "max_branch_s_mva": None,
        "max_branch_loading_pct": None,
        "n_branches_loading_above_100_pct": None,
    }
    if branches.empty:
        return result
    if "s_max_mva" in branches:
        result["max_branch_s_mva"] = finite_numeric(branches["s_max_mva"]).max()
    if "loading_max_pct" in branches:
        loading = finite_numeric(branches["loading_max_pct"])
        result["max_branch_loading_pct"] = loading.max()
        result["n_branches_loading_above_100_pct"] = int(loading.gt(100.0).sum())
    limit_cols = [col for col in ("limit1_mva", "limit2_mva") if col in branches]
    if limit_cols:
        result["n_branches_with_limits"] = int(branches[limit_cols].notna().any(axis=1).sum())
    return result


def voltage_summary(network: Any) -> dict[str, Any]:
    buses = get_table(network, "get_buses")
    result = {"bus_voltage_min": None, "bus_voltage_max": None, "bus_angle_min": None, "bus_angle_max": None}
    if buses.empty:
        return result
    if "v_mag" in buses:
        values = finite_numeric(buses["v_mag"])
        result["bus_voltage_min"] = values.min()
        result["bus_voltage_max"] = values.max()
    if "v_angle" in buses:
        values = finite_numeric(buses["v_angle"])
        result["bus_angle_min"] = values.min()
        result["bus_angle_max"] = values.max()
    return result


def control_support_audit(case_name: str) -> dict[str, Any]:
    mat_path, _ = _case_paths(case_name, ext.BASE_CASE_MAT_DIR, ext.BASE_CASE_M_DIR)
    network = load_case_network(mat_path)
    generators = get_table(network, "get_generators")
    buses = get_table(network, "get_buses")
    branches = get_table(network, "get_branches")
    lines = get_table(network, "get_lines")
    transformers = get_table(network, "get_2_windings_transformers")
    ratio_taps = get_table(network, "get_ratio_tap_changers")
    phase_taps = get_table(network, "get_phase_tap_changers")
    shunts = get_table(network, "get_shunt_compensators")
    svcs = get_table(network, "get_static_var_compensators")
    limits = get_table(network, "get_operational_limits")

    row: dict[str, Any] = {
        "case_name": case_name,
        "case_label": CASE_LABELS.get(case_name, case_name),
        "n_buses": int(len(buses)),
        "n_generators": int(len(generators)),
        "n_branches": int(len(branches)),
        "n_lines": int(len(lines)),
        "n_2w_transformers": int(len(transformers)),
        "n_ratio_tap_changers": int(len(ratio_taps)),
        "n_phase_tap_changers": int(len(phase_taps)),
        "n_shunts": int(len(shunts)),
        "n_svcs": int(len(svcs)),
        "n_operational_limits": int(len(limits)),
        "supports_distributed_slack": int(not generators.empty),
        "supports_reactive_limits": 0,
        "supports_operational_controls": 0,
    }
    if not generators.empty:
        for col in ("min_q", "max_q", "target_q", "target_v", "min_p", "max_p", "target_p"):
            if col in generators:
                row[f"n_generators_with_{col}"] = int(generators[col].notna().sum())
        if {"min_q", "max_q"}.issubset(generators.columns):
            min_q = finite_numeric(generators["min_q"])
            max_q = finite_numeric(generators["max_q"])
            row["n_generators_with_q_range"] = int((min_q.notna() & max_q.notna() & (max_q > min_q)).sum())
            row["supports_reactive_limits"] = int(row["n_generators_with_q_range"] > 0)
        if "voltage_regulator_on" in generators:
            row["n_generators_voltage_regulator_on"] = int(generators["voltage_regulator_on"].fillna(False).astype(bool).sum())
    if not ratio_taps.empty:
        row["n_ratio_tap_changers_regulating"] = int(ratio_taps.get("regulating", pd.Series(dtype=bool)).fillna(False).astype(bool).sum())
        row["n_ratio_tap_changers_with_steps"] = int(finite_numeric(ratio_taps.get("step_count", pd.Series(dtype=float))).gt(1).sum())
    if not phase_taps.empty:
        row["n_phase_tap_changers_regulating"] = int(phase_taps.get("regulating", pd.Series(dtype=bool)).fillna(False).astype(bool).sum())
        row["n_phase_tap_changers_with_steps"] = int(finite_numeric(phase_taps.get("step_count", pd.Series(dtype=float))).gt(1).sum())
    if not shunts.empty:
        row["n_shunts_voltage_regulation_on"] = int(shunts.get("voltage_regulation_on", pd.Series(dtype=bool)).fillna(False).astype(bool).sum())
        row["n_shunts_with_multiple_sections"] = int(finite_numeric(shunts.get("max_section_count", pd.Series(dtype=float))).gt(1).sum())
    if not svcs.empty:
        row["n_svcs_regulating"] = int(svcs.get("regulating", pd.Series(dtype=bool)).fillna(False).astype(bool).sum())

    row["supports_operational_controls"] = int(
        row.get("n_ratio_tap_changers_regulating", 0) > 0
        or row.get("n_ratio_tap_changers_with_steps", 0) > 0
        or row.get("n_phase_tap_changers_regulating", 0) > 0
        or row.get("n_phase_tap_changers_with_steps", 0) > 0
        or row.get("n_shunts_voltage_regulation_on", 0) > 0
        or row.get("n_shunts_with_multiple_sections", 0) > 0
        or row.get("n_svcs_regulating", 0) > 0
    )
    return row


def build_scenario(config: dict[str, Any], case_name: str) -> ext.ParameterScenario:
    pp = require_pypowsybl()
    lf = pp.loadflow
    provider_parameters = {
        "acSolverType": "NEWTON_RAPHSON",
        "maxNewtonRaphsonIterations": "1000",
        "reactiveLimitsMaxPqPvSwitch": "3",
        "slackDistributionFailureBehavior": "FAIL",
        "useActiveLimits": "true",
        "disableInconsistentVoltageControls": "false",
        "transformerVoltageControlMode": "INCREMENTAL_VOLTAGE_CONTROL",
        "shuntVoltageControlMode": "WITH_GENERATOR_VOLTAGE_CONTROL",
        "phaseShifterControlMode": "CONTINUOUS_WITH_DISCRETISATION",
    }
    balance_type = lf.BalanceType.PROPORTIONAL_TO_GENERATION_P_MAX if config["distributed_slack"] else None
    params = lf.Parameters(
        voltage_init_mode=lf.VoltageInitMode.UNIFORM_VALUES,
        distributed_slack=bool(config["distributed_slack"]),
        balance_type=balance_type,
        use_reactive_limits=bool(config["pv_pq_switching"]),
        transformer_voltage_control_on=bool(config["operational_controls"]),
        shunt_compensator_voltage_control_on=bool(config["operational_controls"]),
        phase_shifter_regulation_on=bool(config["operational_controls"]),
        read_slack_bus=True,
        write_slack_bus=True,
        component_mode=lf.ComponentMode.MAIN_CONNECTED,
        provider_parameters=provider_parameters,
    )
    settings = {
        "factorial_config_name": config["config_name"],
        "factorial_config_label": config["config_label"],
        "distributed_slack": bool(config["distributed_slack"]),
        "pv_pq_switching": bool(config["pv_pq_switching"]),
        "operational_controls": bool(config["operational_controls"]),
        "use_reactive_limits": bool(config["pv_pq_switching"]),
        "transformer_voltage_control_on": bool(config["operational_controls"]),
        "shunt_compensator_voltage_control_on": bool(config["operational_controls"]),
        "phase_shifter_regulation_on": bool(config["operational_controls"]),
        "read_slack_bus": True,
        "write_slack_bus": True,
        "component_mode": "MAIN_CONNECTED",
        "balance_type": enum_name(balance_type),
        "voltage_init_mode": "UNIFORM_VALUES",
    }
    return ext.ParameterScenario(
        str(config["config_name"]),
        params,
        provider="OpenLoadFlow",
        settings=settings,
        provider_parameters=provider_parameters,
    )


def run_validation_summary(network: Any, params: Any, *, enabled: bool) -> dict[str, Any]:
    return ext.run_validation_summary(network, params, enabled=enabled)


def run_metadata(row: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "case_name",
        "case_label",
        "variant_name",
        "variant_label",
        "factorial_config_name",
        "factorial_config_label",
        "distributed_slack",
        "pv_pq_switching",
        "operational_controls",
    ]
    return {key: row.get(key) for key in keys}


def with_metadata(table: pd.DataFrame, row: dict[str, Any], table_name: str) -> pd.DataFrame:
    if table.empty:
        return table
    table = table.copy()
    meta = run_metadata(row)
    for key, value in reversed(list(meta.items())):
        table.insert(0, key, value)
    table.insert(0, "state_table", table_name)
    return table


def export_solved_states(network: Any, row: dict[str, Any]) -> None:
    buses = get_table(network, "get_buses")
    generators = get_table(network, "get_generators")
    branches = branch_loading_frame(network)
    append_frame(BUS_STATES_CSV, with_metadata(buses, row, "buses"))
    append_frame(GEN_STATES_CSV, with_metadata(generators, row, "generators"))
    append_frame(BRANCH_STATES_CSV, with_metadata(branches, row, "branches"))

    append_frame(
        RATIO_TAP_STATES_CSV,
        with_metadata(get_table(network, "get_ratio_tap_changers"), row, "ratio_tap_changers"),
    )
    append_frame(
        PHASE_TAP_STATES_CSV,
        with_metadata(get_table(network, "get_phase_tap_changers"), row, "phase_tap_changers"),
    )
    append_frame(
        SHUNT_STATES_CSV,
        with_metadata(get_table(network, "get_shunt_compensators"), row, "shunt_compensators"),
    )
    append_frame(
        SVC_STATES_CSV,
        with_metadata(get_table(network, "get_static_var_compensators"), row, "static_var_compensators"),
    )


def run_one(
    case_name: str,
    variant: ReplacementVariant,
    scenario: ext.ParameterScenario,
    *,
    validate: bool,
    export_states: bool,
) -> dict[str, Any]:
    mat_path, m_path = _case_paths(case_name, ext.BASE_CASE_MAT_DIR, ext.BASE_CASE_M_DIR)
    notes = parse_replacement_notes(m_path)
    run_scenario = ext.force_previous_values(scenario) if variant.voltage_init == "PREVIOUS_VALUES" else scenario

    row: dict[str, Any] = {
        "case_name": case_name,
        "case_label": CASE_LABELS.get(case_name, case_name),
        "variant_name": variant.name,
        "variant_label": VARIANT_LABELS.get(variant.name, variant.name),
        "factorial_config_name": scenario.settings["factorial_config_name"],
        "factorial_config_label": scenario.settings["factorial_config_label"],
        "distributed_slack": run_scenario.settings.get("distributed_slack"),
        "pv_pq_switching": scenario.settings.get("pv_pq_switching"),
        "operational_controls": scenario.settings.get("operational_controls"),
        "balance_type": run_scenario.settings.get("balance_type"),
        "provider": run_scenario.provider,
        "pypowsybl_version": _pypowsybl_version(),
        "requested_voltage_init_mode": scenario.settings.get("voltage_init_mode"),
        "actual_voltage_init_mode": run_scenario.settings.get("voltage_init_mode"),
        "provider_parameters": ";".join(f"{key}={value}" for key, value in sorted(run_scenario.provider_parameters.items())),
        "mapping_success": False,
        "n_notes_applied": 0,
        "n_notes_unmatched": 0,
        "n_applied_gen_pg": 0,
        "n_applied_gen_qg": 0,
        "n_applied_gen_vg": 0,
        "n_applied_bus_voltage": 0,
        "gen_minus_load_before": None,
        "gen_minus_load_after": None,
        "total_generation_after": None,
        "total_load_after": None,
        "number_of_generators_with_target_p_above_max_p": None,
        "number_of_generators_with_target_q_outside_limits": None,
        "status": "NOT_RUN",
        "status_text": None,
        "converged": False,
        "all_components_converged": False,
        "valid_converged": False,
        "component_count": 0,
        "iteration_count": None,
        "reference_bus_id": None,
        "slack_bus_id": None,
        "slack_active_power_mismatch": None,
        "distributed_active_power": None,
        "elapsed_seconds_single": None,
        "exception": None,
        "n_generator_vreg_state_changes": None,
        "n_ratio_tap_state_changes": None,
        "n_phase_tap_state_changes": None,
        "n_shunt_state_changes": None,
    }
    row.update(run_validation_summary(None, None, enabled=False))

    try:
        if variant.voltage_init == "PREVIOUS_VALUES" and not _previous_values_available():
            row["status"] = "SKIPPED"
            row["exception"] = "VoltageInitMode.PREVIOUS_VALUES not available"
            return row

        network = load_case_network(mat_path)
        before_balance = _balance(network)
        audit, counts = apply_replacement_variant(
            network,
            notes,
            variant,
            case_name=case_name,
            parameter_scenario=scenario.name,
            strict=False,
        )
        after_balance = _balance(network)
        feasibility = _feasibility(network)
        unmatched = audit[
            audit["matched_object_id"].isna()
            & audit["note_type"].isin(["load", "bus_voltage", "gen_pgqg", "gen_vg"])
        ]
        row.update(
            {
                **counts,
                "n_notes_applied": int(audit["applied"].fillna(False).sum()) if "applied" in audit else 0,
                "n_notes_unmatched": int(len(unmatched)),
                "mapping_success": int(len(unmatched)) == 0,
                "gen_minus_load_before": before_balance["gen_minus_load"],
                "gen_minus_load_after": after_balance["gen_minus_load"],
                "total_generation_after": after_balance["total_generation"],
                "total_load_after": after_balance["total_load"],
                **feasibility,
            }
        )

        vreg_before = state_signature(network, "get_generators", ("voltage_regulator_on",))
        ratio_before = state_signature(network, "get_ratio_tap_changers", ("tap", "solved_tap_position", "regulating"))
        phase_before = state_signature(network, "get_phase_tap_changers", ("tap", "solved_tap_position", "regulating"))
        shunt_before = state_signature(network, "get_shunt_compensators", ("section_count", "solved_section_count", "voltage_regulation_on"))

        result = run_openloadflow(network, run_scenario.params, provider=run_scenario.provider)
        row.update({key: value for key, value in result.items() if key not in {"component_results", "raw_results", "elapsed_seconds"}})
        row["elapsed_seconds_single"] = result.get("elapsed_seconds_single")
        row["valid_converged"] = (
            bool(row.get("converged"))
            and bool(row.get("all_components_converged"))
            and _is_converged_status(row.get("status"))
        )
        row.update(voltage_summary(network))
        row.update(q_limit_summary(network))
        row.update(branch_loading_summary(network))
        row["n_generator_vreg_state_changes"] = count_state_changes(
            vreg_before,
            state_signature(network, "get_generators", ("voltage_regulator_on",)),
        )
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
        if row["valid_converged"]:
            row.update(run_validation_summary(network, run_scenario.params, enabled=validate))
            if export_states:
                export_solved_states(network, row)
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
    if rows.empty:
        return []
    result: list[dict[str, Any]] = []
    for (case_name, config_name), group in rows.groupby(["case_name", "factorial_config_name"], sort=False):
        converged = group[bool_series(group["valid_converged"])]
        result.append(
            {
                "case_name": case_name,
                "case_label": CASE_LABELS.get(case_name, case_name),
                "factorial_config_name": config_name,
                "factorial_config_label": group["factorial_config_label"].iloc[0],
                "distributed_slack": group["distributed_slack"].iloc[0],
                "pv_pq_switching": group["pv_pq_switching"].iloc[0],
                "operational_controls": group["operational_controls"].iloc[0],
                "n_variants_run": int(len(group)),
                "n_variants_converged": int(len(converged)),
                "converged_variants": "; ".join(converged["variant_label"].astype(str).tolist()),
                "max_iteration_count": group["iteration_count"].max(),
                "max_abs_slack_mismatch": group["slack_active_power_mismatch"].abs().max(),
                "max_distributed_active_power": group["distributed_active_power"].abs().max(),
                "max_branch_loading_pct": group["max_branch_loading_pct"].max(),
                "max_q_below_min_after": group["n_generators_q_below_min_after"].max(),
                "max_q_above_max_after": group["n_generators_q_above_max_after"].max(),
                "max_generator_vreg_state_changes": group["n_generator_vreg_state_changes"].max(),
                "max_ratio_tap_state_changes": group["n_ratio_tap_state_changes"].max(),
                "max_phase_tap_state_changes": group["n_phase_tap_state_changes"].max(),
                "max_shunt_state_changes": group["n_shunt_state_changes"].max(),
            }
        )
    return result


def build_recovery_summary(rows: pd.DataFrame) -> list[dict[str, Any]]:
    if rows.empty:
        return []
    baseline_name = "ds_off__pvpq_off__ops_off"
    baseline = rows[rows["factorial_config_name"].eq(baseline_name)]
    baseline_map = {
        (row["case_name"], row["variant_name"]): bool(row["valid_converged"])
        for _, row in baseline.iterrows()
    }
    result: list[dict[str, Any]] = []
    for (case_name, variant_name), group in rows.groupby(["case_name", "variant_name"], sort=False):
        base = baseline_map.get((case_name, variant_name), False)
        recovered = group[
            group["factorial_config_name"].ne(baseline_name)
            & bool_series(group["valid_converged"])
        ]
        result.append(
            {
                "case_name": case_name,
                "case_label": CASE_LABELS.get(case_name, case_name),
                "variant_name": variant_name,
                "variant_label": VARIANT_LABELS.get(variant_name, variant_name),
                "baseline_converged": bool(base),
                "n_factorial_configs_converged": int(len(recovered)),
                "factorial_configs_converged": "; ".join(recovered["factorial_config_label"].astype(str).tolist()),
                "newly_recovered": bool((not base) and len(recovered) > 0),
            }
        )
    return result


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def centered_text(draw: ImageDraw.ImageDraw, xyxy: tuple[int, int, int, int], text: str, text_font: ImageFont.ImageFont) -> None:
    left, top, right, bottom = xyxy
    box = draw.multiline_textbbox((0, 0), text, font=text_font, spacing=4, align="center")
    width = box[2] - box[0]
    height = box[3] - box[1]
    draw.multiline_text(
        (left + (right - left - width) / 2, top + (bottom - top - height) / 2),
        text,
        font=text_font,
        fill="#111111",
        spacing=4,
        align="center",
    )


def plot_case_config_heatmap(summary: pd.DataFrame) -> None:
    if summary.empty:
        return
    colors = {0: "#b94b48", 1: "#ee704b", 2: "#f5dc8e", 3: "#d9ef82", 4: "#89b879", 5: "#4f7f62"}
    configs = [config["config_name"] for config in CONFIGS]
    labels = {config["config_name"]: config["config_label"].replace(" | ", "\n") for config in CONFIGS}
    by_key = {(row["case_name"], row["factorial_config_name"]): row for _, row in summary.iterrows()}
    left_margin = 170
    top_margin = 120
    header_h = 95
    row_h = 72
    cell_w = 150
    width = left_margin + cell_w * len(configs) + 30
    height = top_margin + header_h + row_h * len(CASE_NAMES) + 60
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((left_margin, 26), "Compact OpenLoadFlow control-factor matrix", font=font(28, True), fill="#111111")
    draw.text((left_margin, 64), "Cells show converged replacement variants out of 5.", font=font(17), fill="#333333")
    grid_top = top_margin + header_h
    for col, config_name in enumerate(configs):
        x0 = left_margin + col * cell_w
        x1 = x0 + cell_w
        draw.rectangle((x0, top_margin, x1, grid_top), fill="#ffffff", outline="#333333")
        centered_text(draw, (x0 + 4, top_margin + 4, x1 - 4, grid_top - 4), labels[config_name], font(14, True))
    for row_idx, case_name in enumerate(CASE_NAMES):
        y0 = grid_top + row_idx * row_h
        y1 = y0 + row_h
        centered_text(draw, (0, y0, left_margin - 10, y1), CASE_LABELS.get(case_name, case_name), font(20))
        for col, config_name in enumerate(configs):
            row = by_key.get((case_name, config_name))
            ok = int(row["n_variants_converged"]) if row is not None else 0
            total = int(row["n_variants_run"]) if row is not None else 5
            x0 = left_margin + col * cell_w
            x1 = x0 + cell_w
            draw.rectangle((x0, y0, x1, y1), fill=colors.get(ok, "#dddddd"), outline="#ffffff", width=2)
            centered_text(draw, (x0, y0, x1, y1), f"{ok}/{total}", font(19, True))
    legend_top = grid_top + row_h * len(CASE_NAMES) + 22
    draw.text((left_margin, legend_top), "Converged variants:", font=font(14), fill="#333333")
    x = left_margin + 145
    for value in range(6):
        draw.rectangle((x, legend_top - 2, x + 22, legend_top + 20), fill=colors[value])
        draw.text((x + 28, legend_top), str(value), font=font(14), fill="#222222")
        x += 58
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    image.save(FIGURE_DIR / "factorial_control_case_config_heatmap.png")


def plot_focused_case_config_heatmap(
    summary: pd.DataFrame,
    config_names: list[str],
    title: str,
    subtitle: str,
    filename: str,
    label_overrides: dict[str, str] | None = None,
) -> None:
    if summary.empty:
        return
    colors = {0: "#b94b48", 1: "#ee704b", 2: "#f5dc8e", 3: "#d9ef82", 4: "#89b879", 5: "#4f7f62"}
    labels = {config["config_name"]: config["config_label"].replace(" | ", "\n") for config in CONFIGS}
    if label_overrides:
        labels.update(label_overrides)
    by_key = {(row["case_name"], row["factorial_config_name"]): row for _, row in summary.iterrows()}
    left_margin = 170
    top_margin = 120
    header_h = 95
    row_h = 72
    cell_w = 180
    width = max(left_margin + cell_w * len(config_names) + 30, 980)
    height = top_margin + header_h + row_h * len(CASE_NAMES) + 60
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((left_margin, 26), title, font=font(28, True), fill="#111111")
    draw.text((left_margin, 64), subtitle, font=font(17), fill="#333333")
    grid_top = top_margin + header_h
    for col, config_name in enumerate(config_names):
        x0 = left_margin + col * cell_w
        x1 = x0 + cell_w
        draw.rectangle((x0, top_margin, x1, grid_top), fill="#ffffff", outline="#333333")
        centered_text(draw, (x0 + 4, top_margin + 4, x1 - 4, grid_top - 4), labels[config_name], font(14, True))
    for row_idx, case_name in enumerate(CASE_NAMES):
        y0 = grid_top + row_idx * row_h
        y1 = y0 + row_h
        centered_text(draw, (0, y0, left_margin - 10, y1), CASE_LABELS.get(case_name, case_name), font(20))
        for col, config_name in enumerate(config_names):
            row = by_key.get((case_name, config_name))
            ok = int(row["n_variants_converged"]) if row is not None else 0
            total = int(row["n_variants_run"]) if row is not None else 5
            x0 = left_margin + col * cell_w
            x1 = x0 + cell_w
            draw.rectangle((x0, y0, x1, y1), fill=colors.get(ok, "#dddddd"), outline="#ffffff", width=2)
            centered_text(draw, (x0, y0, x1, y1), f"{ok}/{total}", font(19, True))
    legend_top = grid_top + row_h * len(CASE_NAMES) + 22
    draw.text((left_margin, legend_top), "Converged variants:", font=font(14), fill="#333333")
    x = left_margin + 145
    for value in range(6):
        draw.rectangle((x, legend_top - 2, x + 22, legend_top + 20), fill=colors[value])
        draw.text((x + 28, legend_top), str(value), font=font(14), fill="#222222")
        x += 58
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    image.save(FIGURE_DIR / filename)


def write_interpretation(rows: pd.DataFrame, audit: pd.DataFrame, recovery: pd.DataFrame) -> None:
    lines = [
        "# PyPowSyBl Compact Control-Factor Analysis",
        "",
        "This pass tests the efficient 2 x 2 x 2 matrix requested after the wide configuration sweep:",
        "",
        "- distributed slack: off/on",
        "- PV/PQ switching through reactive limits: off/on",
        "- operational controls: off/on",
        "",
        "The audit table should be read before interpreting the control results, because imported MATPOWER/PGLib cases may not expose active transformer, shunt, or phase-shifter controls.",
        "",
        "## Scope",
        "",
        f"- cases: {', '.join(CASE_LABELS.get(case, case) for case in CASE_NAMES)}",
        "- variants: Original, VS init, Gen P/Q, Gen P/Q + VS, Gen P/Q + VG",
        f"- total LF rows: {len(rows)}",
        "",
        "## Newly Recovered Baseline Failures",
        "",
        "| Case | Variant | Factorial configs that converged |",
        "|---|---|---|",
    ]
    newly = recovery[bool_series(recovery["newly_recovered"])] if not recovery.empty else pd.DataFrame()
    if newly.empty:
        lines.append("| none | none | none |")
    else:
        for _, row in newly.iterrows():
            lines.append(f"| {row['case_label']} | {row['variant_label']} | {row['factorial_configs_converged']} |")
    lines.extend(
        [
            "",
            "## Control-Support Reading",
            "",
            "Use `control_support_audit.csv` to distinguish two explanations:",
            "",
            "- controls are enabled but do not help convergence",
            "- controls are enabled but the imported network has little or no active control data for OpenLoadFlow to use",
            "",
            "Solved-state exports are written for converged rows so the next step can compare full bus, generator, and branch states rather than only convergence.",
            "",
            "Focused figures:",
            "",
            "- `figures/slack_modeling_heatmap.png` isolates single-slack vs distributed-slack behaviour with PV/PQ switching and operational controls disabled.",
            "- `figures/reactive_operational_controls_heatmap.png` isolates reactive-limit switching and operational controls with distributed slack enabled.",
        ]
    )
    (OUTPUT_DIR / "factorial_control_interpretation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_artifacts() -> None:
    rows = load_existing(ROWS_CSV)
    audit = load_existing(AUDIT_CSV)
    if rows.empty:
        return
    case_config_summary = pd.DataFrame(build_case_config_summary(rows))
    recovery = pd.DataFrame(build_recovery_summary(rows))
    write_csv(OUTPUT_DIR / "factorial_lf_case_config_summary.csv", case_config_summary.to_dict("records"))
    write_csv(OUTPUT_DIR / "factorial_lf_recovery_summary.csv", recovery.to_dict("records"))
    plot_case_config_heatmap(case_config_summary)
    plot_focused_case_config_heatmap(
        case_config_summary,
        ["ds_off__pvpq_off__ops_off", "ds_on__pvpq_off__ops_off"],
        "OpenLoadFlow slack-modelling check",
        "PV/PQ switching and operational controls off. Cells show converged variants out of 5.",
        "slack_modeling_heatmap.png",
        {
            "ds_off__pvpq_off__ops_off": "Single slack\nPV/PQ off\nOps off",
            "ds_on__pvpq_off__ops_off": "Distributed slack\nPV/PQ off\nOps off",
        },
    )
    plot_focused_case_config_heatmap(
        case_config_summary,
        [
            "ds_on__pvpq_off__ops_off",
            "ds_on__pvpq_off__ops_on",
            "ds_on__pvpq_on__ops_off",
            "ds_on__pvpq_on__ops_on",
        ],
        "OpenLoadFlow reactive and operational controls",
        "Distributed slack on. Cells show converged variants out of 5.",
        "reactive_operational_controls_heatmap.png",
        None,
    )
    write_interpretation(rows, audit, recovery)


def clear_outputs() -> None:
    for path in (
        ROWS_CSV,
        AUDIT_CSV,
        BUS_STATES_CSV,
        GEN_STATES_CSV,
        BRANCH_STATES_CSV,
        RATIO_TAP_STATES_CSV,
        PHASE_TAP_STATES_CSV,
        SHUNT_STATES_CSV,
        SVC_STATES_CSV,
        OUTPUT_DIR / "factorial_lf_case_config_summary.csv",
        OUTPUT_DIR / "factorial_lf_recovery_summary.csv",
        OUTPUT_DIR / "factorial_control_interpretation.md",
        FIGURE_DIR / "factorial_control_case_config_heatmap.png",
        FIGURE_DIR / "slack_modeling_heatmap.png",
        FIGURE_DIR / "reactive_operational_controls_heatmap.png",
    ):
        if path.exists():
            path.unlink()


def run_matrix(args: argparse.Namespace) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    if args.fresh:
        clear_outputs()

    cases = args.case or CASE_NAMES
    variants = make_replacement_variants(include_bus_voltage=True)
    variants = {name: variants[name] for name in VARIANT_ORDER}
    if args.variant:
        variants = {name: variant for name, variant in variants.items() if name in set(args.variant)}
    configs = CONFIGS
    if args.config:
        configs = [config for config in configs if config["config_name"] in set(args.config)]

    audit_rows = [control_support_audit(case_name) for case_name in cases]
    write_csv(AUDIT_CSV, audit_rows)

    existing = load_existing(ROWS_CSV)
    completed = {row_key(row) for _, row in existing.iterrows()} if not existing.empty else set()
    rows = existing.to_dict("records") if not existing.empty else []
    n_run = 0
    for case_name in cases:
        for config in configs:
            scenario = build_scenario(config, case_name)
            for variant_name, variant in variants.items():
                key = (case_name, variant_name, scenario.name)
                if args.resume and key in completed:
                    print(f"Skipping completed {case_name} | {variant_name} | {scenario.name}")
                    continue
                print(f"\n=== {case_name} | {variant_name} | {scenario.name} ===")
                row = run_one(
                    case_name,
                    variant,
                    scenario,
                    validate=not args.no_validation,
                    export_states=not args.no_state_exports,
                )
                rows.append(row)
                completed.add(key)
                n_run += 1
                save_dataframe_with_retry(pd.DataFrame(rows), ROWS_CSV)
                print(
                    f"status={row['status']} valid={row['valid_converged']} "
                    f"iters={row['iteration_count']} slack={row['slack_bus_id']} "
                    f"branch_loading={row.get('max_branch_loading_pct')}"
                )
                if args.max_rows and n_run >= args.max_rows:
                    create_artifacts()
                    print(f"Stopped after --max-rows={args.max_rows}")
                    return
    create_artifacts()
    print(f"Wrote factorial control artifacts to {OUTPUT_DIR}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run compact PyPowSyBl LF control-factor analysis.")
    parser.add_argument("--case", action="append", choices=CASE_NAMES)
    parser.add_argument("--variant", action="append", choices=VARIANT_ORDER)
    parser.add_argument("--config", action="append", choices=sorted(CONFIG_BY_NAME))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--no-validation", action="store_true")
    parser.add_argument("--no-state-exports", action="store_true")
    parser.add_argument("--artifacts-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.artifacts_only:
        create_artifacts()
        print(f"Wrote factorial control artifacts to {OUTPUT_DIR}")
        return
    run_matrix(args)


if __name__ == "__main__":
    main()
