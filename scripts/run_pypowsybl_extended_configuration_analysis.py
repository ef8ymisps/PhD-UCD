"""Run extended PyPowSyBl/OpenLoadFlow configuration experiments.

This layer intentionally moves beyond the matched PowerModels comparison. It
tests model and control mechanisms that can change the solved load-flow problem:
slack modeling, reactive limits, voltage controls, and operational controls.
"""

from __future__ import annotations

import argparse
import csv
import inspect
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
    / "extended_configuration_analysis"
)
FIGURE_DIR = OUTPUT_DIR / "figures"
BASE_CASE_MAT_DIR = DEFAULT_MAT_DIR / "base_case"
BASE_CASE_M_DIR = DEFAULT_M_DIR / "base_case"
BASELINE_ROWS = (
    PROJECT_ROOT
    / "results"
    / "presentable"
    / "pypowsybl_lf_replacement_sweep"
    / "pypowsybl_openloadflow_sensitivity_rows.csv"
)

CASE_NAMES = [
    "pglib_opf_case300_ieee",
    "pglib_opf_case1354_pegase",
    "pglib_opf_case1888_rte",
    "pglib_opf_case6470_rte",
    "pglib_opf_case9241_pegase",
    "pglib_opf_case13659_pegase",
]

CASE_LABELS = {
    "pglib_opf_case300_ieee": "300 IEEE",
    "pglib_opf_case1354_pegase": "1354 PEGASE",
    "pglib_opf_case1888_rte": "1888 RTE",
    "pglib_opf_case6470_rte": "6470 RTE",
    "pglib_opf_case9241_pegase": "9241 PEGASE",
    "pglib_opf_case13659_pegase": "13659 PEGASE",
}

VARIANT_ORDER = [
    "original",
    "BV_from_warmstart",
    "G_PGQG_from",
    "G_PGQG_from_plus_BV_from",
    "G_PGQG_from_plus_GVG_from",
]

STAGE_ORDER = ["reference", "slack", "reactive", "operational"]

CONFIGS: list[dict[str, Any]] = [
    {
        "stage": "reference",
        "config_name": "matched_single_slack",
        "label": "Matched single slack",
        "purpose": "Reference: single slack, no Q limits, no distributed slack, controls off",
        "read_slack_bus": True,
        "distributed_slack": False,
        "use_reactive_limits": False,
        "provider_parameters": {"acSolverType": "NEWTON_RAPHSON", "maxNewtonRaphsonIterations": "1000"},
    },
    {
        "stage": "slack",
        "config_name": "explicit_original_slack",
        "label": "Explicit original slack",
        "purpose": "Force the slack bus observed in the matched baseline",
        "read_slack_bus": False,
        "distributed_slack": False,
        "use_reactive_limits": False,
        "slack_mode": "NAME",
        "provider_parameters": {"acSolverType": "NEWTON_RAPHSON", "maxNewtonRaphsonIterations": "1000"},
    },
    {
        "stage": "slack",
        "config_name": "largest_generator_slack",
        "label": "Largest-generator slack",
        "purpose": "Select the largest generator as single slack",
        "read_slack_bus": False,
        "distributed_slack": False,
        "use_reactive_limits": False,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
            "slackBusSelectionMode": "LARGEST_GENERATOR",
        },
    },
    {
        "stage": "slack",
        "config_name": "distributed_pmax",
        "label": "Distributed by Pmax",
        "purpose": "Distribute active-power mismatch by generator Pmax with active limits",
        "read_slack_bus": False,
        "distributed_slack": True,
        "balance_type": "PROPORTIONAL_TO_GENERATION_P_MAX",
        "use_reactive_limits": False,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
            "slackBusSelectionMode": "LARGEST_GENERATOR",
            "slackDistributionFailureBehavior": "FAIL",
            "useActiveLimits": "true",
        },
    },
    {
        "stage": "slack",
        "config_name": "distributed_remaining_margin",
        "label": "Distributed by margin",
        "purpose": "Distribute active-power mismatch by remaining generator headroom",
        "read_slack_bus": False,
        "distributed_slack": True,
        "balance_type": "PROPORTIONAL_TO_GENERATION_REMAINING_MARGIN",
        "use_reactive_limits": False,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
            "slackBusSelectionMode": "LARGEST_GENERATOR",
            "slackDistributionFailureBehavior": "FAIL",
            "useActiveLimits": "true",
        },
    },
    {
        "stage": "reactive",
        "config_name": "q_limits",
        "label": "Q limits",
        "purpose": "Enable PV/PQ switching from reactive limits",
        "read_slack_bus": True,
        "distributed_slack": False,
        "use_reactive_limits": True,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
            "reactiveLimitsMaxPqPvSwitch": "3",
        },
    },
    {
        "stage": "reactive",
        "config_name": "q_limits_more_switches",
        "label": "Q limits more switches",
        "purpose": "Allow more PV/PQ switching attempts",
        "read_slack_bus": True,
        "distributed_slack": False,
        "use_reactive_limits": True,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
            "reactiveLimitsMaxPqPvSwitch": "10",
        },
    },
    {
        "stage": "reactive",
        "config_name": "q_target_clamped",
        "label": "Clamp target Q",
        "purpose": "Clamp target Q values into reactive limits",
        "read_slack_bus": True,
        "distributed_slack": False,
        "use_reactive_limits": True,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
            "reactiveLimitsMaxPqPvSwitch": "10",
            "forceTargetQInReactiveLimits": "true",
        },
    },
    {
        "stage": "reactive",
        "config_name": "disable_bad_voltage_controls",
        "label": "Disable bad V controls",
        "purpose": "Disable inconsistent voltage controls and generators outside active bounds",
        "read_slack_bus": True,
        "distributed_slack": False,
        "use_reactive_limits": True,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
            "disableInconsistentVoltageControls": "true",
            "disableVoltageControlOfGeneratorsOutsideActivePowerLimits": "true",
        },
    },
    {
        "stage": "reactive",
        "config_name": "reactive_dispatch_k",
        "label": "Reactive dispatch K",
        "purpose": "Use K-equal-proportion reactive dispatch",
        "read_slack_bus": True,
        "distributed_slack": False,
        "use_reactive_limits": True,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
            "reactivePowerDispatchMode": "K_EQUAL_PROPORTION",
        },
    },
    {
        "stage": "operational",
        "config_name": "transformer_voltage_control",
        "label": "Transformer V control",
        "purpose": "Enable transformer tap-changing voltage controls",
        "read_slack_bus": True,
        "distributed_slack": False,
        "use_reactive_limits": True,
        "transformer_voltage_control_on": True,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
            "transformerVoltageControlMode": "INCREMENTAL_VOLTAGE_CONTROL",
        },
    },
    {
        "stage": "operational",
        "config_name": "shunt_voltage_control",
        "label": "Shunt V control",
        "purpose": "Enable shunt voltage controls",
        "read_slack_bus": True,
        "distributed_slack": False,
        "use_reactive_limits": True,
        "shunt_compensator_voltage_control_on": True,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
            "shuntVoltageControlMode": "WITH_GENERATOR_VOLTAGE_CONTROL",
        },
    },
    {
        "stage": "operational",
        "config_name": "phase_control",
        "label": "Phase control",
        "purpose": "Enable phase-shifter regulation",
        "read_slack_bus": True,
        "distributed_slack": False,
        "use_reactive_limits": True,
        "phase_shifter_regulation_on": True,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
            "phaseShifterControlMode": "CONTINUOUS_WITH_DISCRETISATION",
        },
    },
    {
        "stage": "operational",
        "config_name": "full_openloadflow_operational",
        "label": "Full operational",
        "purpose": "Distributed slack, Q limits, transformer/shunt/phase controls enabled",
        "read_slack_bus": False,
        "distributed_slack": True,
        "balance_type": "PROPORTIONAL_TO_GENERATION_P_MAX",
        "use_reactive_limits": True,
        "transformer_voltage_control_on": True,
        "shunt_compensator_voltage_control_on": True,
        "phase_shifter_regulation_on": True,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
            "slackBusSelectionMode": "LARGEST_GENERATOR",
            "slackDistributionFailureBehavior": "FAIL",
            "useActiveLimits": "true",
            "transformerVoltageControlMode": "INCREMENTAL_VOLTAGE_CONTROL",
            "shuntVoltageControlMode": "WITH_GENERATOR_VOLTAGE_CONTROL",
            "phaseShifterControlMode": "CONTINUOUS_WITH_DISCRETISATION",
        },
    },
]

CONFIG_BY_NAME = {str(config["config_name"]): config for config in CONFIGS}


def enum_name(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "name", value)).split(".")[-1]


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


def original_slack_bus_id(case_name: str) -> str:
    if not BASELINE_ROWS.exists():
        return ""
    rows = pd.read_csv(BASELINE_ROWS)
    subset = rows[
        rows["scenario_folder"].eq("base_case")
        & rows["case_name"].eq(case_name)
        & rows["variant_name"].eq("original")
        & rows["parameter_scenario"].eq("strict_single_slack")
    ]
    if subset.empty:
        subset = rows[
            rows["scenario_folder"].eq("base_case")
            & rows["case_name"].eq(case_name)
            & rows["parameter_scenario"].eq("strict_single_slack")
        ]
    if subset.empty or "slack_bus_id" not in subset:
        return ""
    value = subset.iloc[0].get("slack_bus_id", "")
    return "" if pd.isna(value) else str(value)


def build_scenario(config: dict[str, Any], case_name: str) -> ParameterScenario:
    pp = require_pypowsybl()
    lf = pp.loadflow
    provider_parameters = {str(k): str(v) for k, v in dict(config.get("provider_parameters", {})).items()}
    if config.get("slack_mode") == "NAME":
        slack_id = original_slack_bus_id(case_name)
        if slack_id:
            provider_parameters["slackBusSelectionMode"] = "NAME"
            provider_parameters["slackBusesIds"] = slack_id
        else:
            provider_parameters["slackBusSelectionMode"] = "FIRST"

    balance_type = None
    if config.get("balance_type"):
        balance_type = getattr(lf.BalanceType, str(config["balance_type"]))

    voltage_init_mode = lf.VoltageInitMode.UNIFORM_VALUES
    kwargs: dict[str, Any] = {
        "voltage_init_mode": voltage_init_mode,
        "distributed_slack": bool(config.get("distributed_slack", False)),
        "balance_type": balance_type,
        "use_reactive_limits": bool(config.get("use_reactive_limits", False)),
        "transformer_voltage_control_on": bool(config.get("transformer_voltage_control_on", False)),
        "shunt_compensator_voltage_control_on": bool(config.get("shunt_compensator_voltage_control_on", False)),
        "phase_shifter_regulation_on": bool(config.get("phase_shifter_regulation_on", False)),
        "read_slack_bus": bool(config.get("read_slack_bus", True)),
        "write_slack_bus": True,
        "component_mode": lf.ComponentMode.MAIN_CONNECTED,
        "provider_parameters": provider_parameters,
    }

    settings = {
        "extended_stage": str(config["stage"]),
        "extended_config_name": str(config["config_name"]),
        "extended_config_label": str(config["label"]),
        "extended_config_purpose": str(config["purpose"]),
        "requested_voltage_init_mode": enum_name(voltage_init_mode),
        "voltage_init_mode": enum_name(voltage_init_mode),
        "use_reactive_limits": kwargs["use_reactive_limits"],
        "distributed_slack": kwargs["distributed_slack"],
        "balance_type": enum_name(balance_type),
        "transformer_voltage_control_on": kwargs["transformer_voltage_control_on"],
        "shunt_compensator_voltage_control_on": kwargs["shunt_compensator_voltage_control_on"],
        "phase_shifter_regulation_on": kwargs["phase_shifter_regulation_on"],
        "read_slack_bus": kwargs["read_slack_bus"],
        "write_slack_bus": kwargs["write_slack_bus"],
        "component_mode": "MAIN_CONNECTED",
        "explicit_slack_bus_id": provider_parameters.get("slackBusesIds", ""),
    }
    return ParameterScenario(
        str(config["config_name"]),
        lf.Parameters(**kwargs),
        provider="OpenLoadFlow",
        settings=settings,
        provider_parameters=provider_parameters,
    )


def force_previous_values(scenario: ParameterScenario) -> ParameterScenario:
    pp = require_pypowsybl()
    lf = pp.loadflow
    settings = dict(scenario.settings)
    settings["voltage_init_mode"] = "PREVIOUS_VALUES"
    return ParameterScenario(
        scenario.name,
        lf.Parameters(
            voltage_init_mode=lf.VoltageInitMode.PREVIOUS_VALUES,
            distributed_slack=settings.get("distributed_slack"),
            balance_type=getattr(lf.BalanceType, settings["balance_type"]) if settings.get("balance_type") else None,
            use_reactive_limits=settings.get("use_reactive_limits"),
            transformer_voltage_control_on=settings.get("transformer_voltage_control_on"),
            shunt_compensator_voltage_control_on=settings.get("shunt_compensator_voltage_control_on"),
            phase_shifter_regulation_on=settings.get("phase_shifter_regulation_on"),
            read_slack_bus=settings.get("read_slack_bus"),
            write_slack_bus=settings.get("write_slack_bus"),
            component_mode=lf.ComponentMode.MAIN_CONNECTED,
            provider_parameters=scenario.provider_parameters,
        ),
        provider=scenario.provider,
        settings=settings,
        provider_parameters=scenario.provider_parameters,
    )


def selected_variants(case_name: str, config_name: str) -> list[str]:
    # Keep the first implementation complete and symmetric across cases. The
    # CLI allows narrowing by --variant for very slow follow-up runs.
    return VARIANT_ORDER


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


def voltage_summary(network: Any) -> dict[str, Any]:
    buses = get_table(network, "get_buses")
    if buses.empty:
        return {"bus_voltage_min": None, "bus_voltage_max": None, "bus_angle_min": None, "bus_angle_max": None}
    v_col = next((column for column in ("v_mag", "v", "voltage", "voltage_mag") if column in buses.columns), None)
    a_col = next((column for column in ("angle", "v_angle", "theta") if column in buses.columns), None)
    result: dict[str, Any] = {"bus_voltage_min": None, "bus_voltage_max": None, "bus_angle_min": None, "bus_angle_max": None}
    if v_col:
        values = pd.to_numeric(buses[v_col], errors="coerce")
        result["bus_voltage_min"] = values.min()
        result["bus_voltage_max"] = values.max()
    if a_col:
        values = pd.to_numeric(buses[a_col], errors="coerce")
        result["bus_angle_min"] = values.min()
        result["bus_angle_max"] = values.max()
    return result


def run_validation_summary(network: Any, params: Any, *, enabled: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "validation_attempted": False,
        "validation_valid": None,
        "validation_exception": None,
    }
    for name in ("branch_flows", "buses", "generators", "shunts", "svcs", "twts", "t3wts"):
        result[f"validation_{name}_rows"] = None
        result[f"validation_{name}_validated"] = None
    if not enabled:
        return result
    try:
        pp = require_pypowsybl()
        validation_parameters = pp.loadflow.ValidationParameters(
            loadflow_parameters=params,
            check_main_component_only=True,
            ok_missing_values=True,
            no_requirement_if_setpoint_outside_power_bounds=True,
        )
        validation = pp.loadflow.run_validation(network, validation_parameters=validation_parameters)
        result["validation_attempted"] = True
        result["validation_valid"] = bool(getattr(validation, "valid", False))
        for name in ("branch_flows", "buses", "generators", "shunts", "svcs", "twts", "t3wts"):
            table = getattr(validation, name, pd.DataFrame())
            result[f"validation_{name}_rows"] = int(len(table))
            if isinstance(table, pd.DataFrame) and "validated" in table:
                result[f"validation_{name}_validated"] = int(table["validated"].fillna(False).astype(bool).sum())
    except Exception as exc:
        result["validation_attempted"] = True
        result["validation_exception"] = repr(exc)
    return result


def run_one(
    case_name: str,
    variant: ReplacementVariant,
    scenario: ParameterScenario,
    *,
    mat_dir: Path,
    m_dir: Path,
    validate: bool,
) -> dict[str, Any]:
    mat_path, m_path = _case_paths(case_name, mat_dir, m_dir)
    notes = parse_replacement_notes(m_path)
    run_scenario = force_previous_values(scenario) if variant.voltage_init == "PREVIOUS_VALUES" else scenario

    row: dict[str, Any] = {
        "case_name": case_name,
        "case_label": CASE_LABELS.get(case_name, case_name),
        "variant_name": variant.name,
        "variant_label": VARIANT_LABELS.get(variant.name, variant.name),
        "extended_stage": scenario.settings.get("extended_stage"),
        "extended_config_name": scenario.settings.get("extended_config_name", scenario.name),
        "extended_config_label": scenario.settings.get("extended_config_label", scenario.name),
        "extended_config_purpose": scenario.settings.get("extended_config_purpose", ""),
        "replacement_groups": "+".join(variant.groups),
        "direction": variant.direction,
        "provider": run_scenario.provider,
        "pypowsybl_version": _pypowsybl_version(),
        "requested_voltage_init_mode": scenario.settings.get("requested_voltage_init_mode"),
        "actual_voltage_init_mode": run_scenario.settings.get("voltage_init_mode"),
        "use_reactive_limits": run_scenario.settings.get("use_reactive_limits"),
        "distributed_slack": run_scenario.settings.get("distributed_slack"),
        "balance_type": run_scenario.settings.get("balance_type"),
        "transformer_voltage_control_on": run_scenario.settings.get("transformer_voltage_control_on"),
        "shunt_compensator_voltage_control_on": run_scenario.settings.get("shunt_compensator_voltage_control_on"),
        "phase_shifter_regulation_on": run_scenario.settings.get("phase_shifter_regulation_on"),
        "read_slack_bus": run_scenario.settings.get("read_slack_bus"),
        "explicit_slack_bus_id": run_scenario.settings.get("explicit_slack_bus_id"),
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
        "n_transformer_state_changes": None,
        "n_shunt_state_changes": None,
        "n_phase_state_changes": None,
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
        applied_fields = int(audit["applied"].fillna(False).sum()) if "applied" in audit else 0
        row.update(
            {
                **counts,
                "n_notes_applied": applied_fields,
                "n_notes_unmatched": int(len(unmatched)),
                "mapping_success": int(len(unmatched)) == 0,
                "gen_minus_load_before": before_balance["gen_minus_load"],
                "gen_minus_load_after": after_balance["gen_minus_load"],
                "total_generation_after": after_balance["total_generation"],
                "total_load_after": after_balance["total_load"],
                **feasibility,
            }
        )
        transformer_before = state_signature(
            network,
            "get_2_windings_transformers",
            ("tap_position", "ratio_tap_position", "phase_tap_position", "rho", "alpha"),
        )
        shunt_before = state_signature(network, "get_shunt_compensators", ("section_count", "current_section_count"))
        phase_before = state_signature(network, "get_2_windings_transformers", ("phase_tap_position", "alpha"))

        result = run_openloadflow(network, run_scenario.params, provider=run_scenario.provider)
        row.update({key: value for key, value in result.items() if key not in {"component_results", "raw_results", "elapsed_seconds"}})
        row["elapsed_seconds_single"] = result.get("elapsed_seconds_single")
        row["valid_converged"] = (
            bool(row.get("converged"))
            and bool(row.get("all_components_converged"))
            and _is_converged_status(row.get("status"))
        )
        row.update(voltage_summary(network))
        row["n_transformer_state_changes"] = count_state_changes(
            transformer_before,
            state_signature(
                network,
                "get_2_windings_transformers",
                ("tap_position", "ratio_tap_position", "phase_tap_position", "rho", "alpha"),
            ),
        )
        row["n_shunt_state_changes"] = count_state_changes(
            shunt_before,
            state_signature(network, "get_shunt_compensators", ("section_count", "current_section_count")),
        )
        row["n_phase_state_changes"] = count_state_changes(
            phase_before,
            state_signature(network, "get_2_windings_transformers", ("phase_tap_position", "alpha")),
        )
        if row["valid_converged"]:
            row.update(run_validation_summary(network, run_scenario.params, enabled=validate))
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


def row_key(row: pd.Series | dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row["case_name"]),
        str(row["variant_name"]),
        str(row["extended_config_name"]),
    )


def bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().eq("true") | series.eq(True)


def build_case_config_summary(rows: pd.DataFrame) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if rows.empty:
        return result
    for (case_name, config_name), group in rows.groupby(["case_name", "extended_config_name"], sort=False):
        converged = group[bool_series(group["valid_converged"])]
        result.append(
            {
                "case_name": case_name,
                "case_label": CASE_LABELS.get(case_name, case_name),
                "extended_stage": group["extended_stage"].iloc[0],
                "extended_config_name": config_name,
                "extended_config_label": group["extended_config_label"].iloc[0],
                "n_variants_run": int(len(group)),
                "n_variants_converged": int(len(converged)),
                "converged_variants": "; ".join(converged["variant_label"].astype(str).tolist()),
                "max_iteration_count": group["iteration_count"].max(),
                "max_elapsed_seconds_single": group["elapsed_seconds_single"].max(),
                "max_target_p_above_max_p": group["number_of_generators_with_target_p_above_max_p"].max(),
                "max_target_q_outside_limits": group["number_of_generators_with_target_q_outside_limits"].max(),
                "max_transformer_state_changes": group["n_transformer_state_changes"].max(),
                "max_shunt_state_changes": group["n_shunt_state_changes"].max(),
                "max_phase_state_changes": group["n_phase_state_changes"].max(),
            }
        )
    return result


def build_recovery_summary(rows: pd.DataFrame) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if rows.empty:
        return result
    baseline = rows[rows["extended_config_name"].eq("matched_single_slack")]
    baseline_map = {
        (row["case_name"], row["variant_name"]): bool(row["valid_converged"])
        for _, row in baseline.iterrows()
    }
    for (case_name, variant_name), group in rows.groupby(["case_name", "variant_name"], sort=False):
        base = baseline_map.get((case_name, variant_name), False)
        recovered = group[
            group["extended_config_name"].ne("matched_single_slack")
            & bool_series(group["valid_converged"])
        ]
        result.append(
            {
                "case_name": case_name,
                "case_label": CASE_LABELS.get(case_name, case_name),
                "variant_name": variant_name,
                "variant_label": VARIANT_LABELS.get(variant_name, variant_name),
                "baseline_converged": bool(base),
                "n_extended_configs_converged": int(len(recovered)),
                "extended_configs_converged": "; ".join(recovered["extended_config_label"].astype(str).tolist()),
                "newly_recovered": bool((not base) and len(recovered) > 0),
            }
        )
    return result


def build_validation_summary(rows: pd.DataFrame) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if rows.empty:
        return result
    attempted = rows[bool_series(rows["validation_attempted"])] if "validation_attempted" in rows else rows.iloc[0:0]
    for (case_name, config_name), group in attempted.groupby(["case_name", "extended_config_name"], sort=False):
        result.append(
            {
                "case_name": case_name,
                "case_label": CASE_LABELS.get(case_name, case_name),
                "extended_stage": group["extended_stage"].iloc[0],
                "extended_config_name": config_name,
                "extended_config_label": group["extended_config_label"].iloc[0],
                "n_validation_attempted": int(len(group)),
                "n_validation_valid": int(bool_series(group["validation_valid"]).sum()),
                "n_validation_exceptions": int(group["validation_exception"].notna().sum()),
                "min_bus_validated": group["validation_buses_validated"].min(),
                "min_generator_validated": group["validation_generators_validated"].min(),
                "min_branch_flow_validated": group["validation_branch_flows_validated"].min(),
            }
        )
    return result


def config_order(rows: pd.DataFrame) -> list[str]:
    present = set(rows["extended_config_name"]) if not rows.empty else set()
    ordered = [str(config["config_name"]) for config in CONFIGS if str(config["config_name"]) in present]
    return ordered


def plot_case_config_heatmap(summary: pd.DataFrame) -> None:
    if summary.empty:
        return
    configs = config_order(summary)
    cases = [case for case in CASE_NAMES if case in set(summary["case_name"])]
    labels = {row["extended_config_name"]: row["extended_config_label"] for _, row in summary.iterrows()}
    by_key = {(row["case_name"], row["extended_config_name"]): row for _, row in summary.iterrows()}
    values: list[list[float]] = []
    annotations: list[list[str]] = []
    for case in cases:
        value_row = []
        annotation_row = []
        for config in configs:
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

    fig, ax = plt.subplots(figsize=(15.0, 5.0))
    ax.imshow(values, cmap="RdYlGn", vmin=0.0, vmax=1.0, aspect="auto")
    ax.set_xticks(range(len(configs)))
    ax.set_xticklabels([labels.get(name, name) for name in configs], rotation=30, ha="right")
    ax.set_yticks(range(len(cases)))
    ax.set_yticklabels([CASE_LABELS.get(case, case) for case in cases])
    ax.set_title("Extended OpenLoadFlow convergence by case and configuration")
    for i, row in enumerate(annotations):
        for j, text in enumerate(row):
            ax.text(j, i, text, ha="center", va="center", fontsize=8)
    fig.subplots_adjust(left=0.10, right=0.99, bottom=0.32, top=0.88)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_DIR / "extended_configuration_case_config_heatmap.png", dpi=180)
    plt.close(fig)


def plot_recovery_heatmap(rows: pd.DataFrame) -> None:
    if rows.empty:
        return
    configs = config_order(rows)
    labels = {row["extended_config_name"]: row["extended_config_label"] for _, row in rows.iterrows()}
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
                    if bool_series(variant_rows[variant_rows["extended_config_name"].eq(config)]["valid_converged"]).any()
                    else 0
                    for config in configs
                ]
            )

    fig, ax = plt.subplots(figsize=(15.0, max(5.0, 0.35 * len(row_labels))))
    ax.imshow(
        values,
        cmap=matplotlib.colors.ListedColormap(["#b94b48", "#4f7f62"]),
        vmin=0,
        vmax=1,
        aspect="auto",
    )
    ax.set_xticks(range(len(configs)))
    ax.set_xticklabels([labels.get(name, name) for name in configs], rotation=30, ha="right")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=8)
    ax.set_title("Extended OpenLoadFlow convergence by variant")
    for i, row in enumerate(values):
        for j, value in enumerate(row):
            ax.text(j, i, "Y" if value else "N", ha="center", va="center", color="white", fontsize=8)
    fig.subplots_adjust(left=0.25, right=0.99, bottom=0.28, top=0.92)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_DIR / "extended_configuration_variant_heatmap.png", dpi=180)
    plt.close(fig)


def write_interpretation(rows: pd.DataFrame, recovery: pd.DataFrame) -> None:
    lines = [
        "# Extended PyPowSyBl/OpenLoadFlow Configuration Analysis",
        "",
        "This layer starts after the matched single-slack and numerical robustness analyses. It intentionally tests OpenLoadFlow model features that can change the solved load-flow problem.",
        "",
        "Scope:",
        "",
        "- cases: `300`, `1888`, `6470`, `13659`",
        "- variants: `Original`, `VS init`, `Gen P/Q`, `Gen P/Q + VS`, `Gen P/Q + VG`",
        "- stages: reference, slack modeling, reactive limits/voltage controls, and operational controls",
        "- no `78484` run in this layer",
        "",
        "## Newly Recovered Baseline Failures",
        "",
        "| Case | Variant | Extended configurations that converged |",
        "|---|---|---|",
    ]
    if recovery.empty or not bool(recovery["newly_recovered"].any()):
        lines.append("| none | none | none |")
    else:
        for _, row in recovery[recovery["newly_recovered"]].iterrows():
            lines.append(
                f"| {row['case_label']} | {row['variant_label']} | {row['extended_configs_converged']} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation Guide",
            "",
            "- Recovery under slack configurations indicates sensitivity to how active-power mismatch is absorbed.",
            "- Recovery under reactive-limit configurations indicates PV/PQ switching, target-Q handling, or voltage-control consistency is important.",
            "- Recovery under operational controls should be presented separately from the matched PowerModels baseline because transformer, shunt, phase, and full operational controls change the modeled LF problem.",
            "- `loadflow.run_validation` is applied to converged rows and summarized separately; strict validation may fail even when load flow converges, so inspect table-level counts before drawing conclusions.",
        ]
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "extended_configuration_interpretation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def utility_capability_rows() -> list[dict[str, Any]]:
    pp = require_pypowsybl()
    rows = []
    utilities = [
        ("loadflow.run_validation", pp.loadflow, "run_validation", "Implemented for every converged extended LF row"),
        ("voltage_initializer.run", pp.voltage_initializer, "run", "Run by run_pypowsybl_advanced_utility_analysis.py on matched-baseline failures; current environment reports missing AMPL module"),
        ("security.create_analysis().run_ac", pp.security, "create_analysis", "Run by run_pypowsybl_advanced_utility_analysis.py as a single-contingency AC probe on matched-baseline solved states"),
        ("sensitivity.create_ac_analysis", pp.sensitivity, "create_ac_analysis", "Run by run_pypowsybl_advanced_utility_analysis.py as a branch-flow/generator-P AC sensitivity probe on matched-baseline solved states"),
    ]
    for label, module, attr, action in utilities:
        available = hasattr(module, attr)
        signature = ""
        if available:
            obj = getattr(module, attr)
            try:
                signature = str(inspect.signature(obj))
            except Exception:
                signature = ""
        rows.append({"utility": label, "available": available, "signature": signature, "implemented_action": action})
    return rows


def create_artifacts(summary_csv: Path, *, make_plots: bool = True) -> None:
    rows = load_existing(summary_csv)
    if rows.empty:
        write_csv(OUTPUT_DIR / "extended_configuration_utility_capabilities.csv", utility_capability_rows())
        return
    rows = rows.copy()
    variant_labels = rows["variant_name"].astype(str).map(VARIANT_LABELS)
    rows["variant_label"] = variant_labels.fillna(rows.get("variant_label", rows["variant_name"]))
    case_config_rows = build_case_config_summary(rows)
    recovery_rows = build_recovery_summary(rows)
    validation_rows = build_validation_summary(rows)
    write_csv(OUTPUT_DIR / "extended_configuration_case_config_summary.csv", case_config_rows)
    write_csv(OUTPUT_DIR / "extended_configuration_recovery_summary.csv", recovery_rows)
    write_csv(OUTPUT_DIR / "extended_configuration_validation_summary.csv", validation_rows)
    write_csv(OUTPUT_DIR / "extended_configuration_utility_capabilities.csv", utility_capability_rows())
    case_config = pd.DataFrame(case_config_rows)
    recovery = pd.DataFrame(recovery_rows)
    if make_plots:
        plot_case_config_heatmap(case_config)
        plot_recovery_heatmap(rows)
    write_interpretation(rows, recovery)


def run_matrix(args: argparse.Namespace) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    summary_csv = OUTPUT_DIR / "extended_configuration_rows.csv"

    selected_configs = CONFIGS
    if args.stage:
        wanted = set(args.stage)
        selected_configs = [config for config in selected_configs if str(config["stage"]) in wanted]
    if args.config:
        wanted = set(args.config)
        selected_configs = [config for config in selected_configs if str(config["config_name"]) in wanted]
    if not selected_configs:
        raise ValueError("No extended configurations selected.")

    variants = make_replacement_variants(include_bus_voltage=True)
    variants = {name: variants[name] for name in VARIANT_ORDER}
    if args.variant:
        wanted_variants = set(args.variant)
        variants = {name: variant for name, variant in variants.items() if name in wanted_variants}
    if not variants:
        raise ValueError("No variants selected.")

    cases = args.case or CASE_NAMES
    existing = load_existing(summary_csv)
    completed = {row_key(row) for _, row in existing.iterrows()} if not existing.empty else set()
    rows = existing.to_dict("records") if not existing.empty else []

    n_run = 0
    for config in selected_configs:
        for case_name in cases:
            scenario = build_scenario(config, case_name)
            for variant_name in selected_variants(case_name, scenario.name):
                if variant_name not in variants:
                    continue
                variant = variants[variant_name]
                key = (case_name, variant_name, scenario.name)
                if args.resume and key in completed:
                    print(f"Skipping completed {case_name} | {variant_name} | {scenario.name}")
                    continue
                print(f"\n=== {case_name} | {variant_name} | {scenario.name} ===")
                row = run_one(
                    case_name,
                    variant,
                    scenario,
                    mat_dir=Path(args.mat_dir),
                    m_dir=Path(args.m_dir),
                    validate=not args.no_validation,
                )
                rows.append(row)
                completed.add(key)
                n_run += 1
                save_dataframe_with_retry(pd.DataFrame(rows), summary_csv)
                print(
                    f"status={row['status']} valid={row['valid_converged']} "
                    f"iterations={row['iteration_count']} slack={row['slack_bus_id']} "
                    f"validation={row.get('validation_valid')}"
                )
                if args.max_rows and n_run >= args.max_rows:
                    create_artifacts(summary_csv, make_plots=not args.no_plots)
                    print(f"Stopped after --max-rows={args.max_rows}")
                    return

    create_artifacts(summary_csv, make_plots=not args.no_plots)
    print(f"Wrote extended configuration artifacts to {OUTPUT_DIR}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run extended PyPowSyBl/OpenLoadFlow configuration analysis.")
    parser.add_argument("--case", action="append", choices=CASE_NAMES, help="Case to include; may be repeated.")
    parser.add_argument("--variant", action="append", choices=VARIANT_ORDER, help="Variant to include; may be repeated.")
    parser.add_argument("--stage", action="append", choices=STAGE_ORDER, help="Extended stage to include; may be repeated.")
    parser.add_argument("--config", action="append", choices=sorted(CONFIG_BY_NAME), help="Config to include; may be repeated.")
    parser.add_argument("--mat-dir", type=Path, default=BASE_CASE_MAT_DIR)
    parser.add_argument("--m-dir", type=Path, default=BASE_CASE_M_DIR)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--no-validation", action="store_true", help="Skip loadflow.run_validation on converged rows.")
    parser.add_argument("--no-plots", action="store_true", help="Regenerate CSV/Markdown artifacts without re-rendering PNG figures.")
    parser.add_argument("--artifacts-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.artifacts_only:
        create_artifacts(OUTPUT_DIR / "extended_configuration_rows.csv", make_plots=not args.no_plots)
        print(f"Wrote extended configuration artifacts to {OUTPUT_DIR}")
        return
    run_matrix(args)


if __name__ == "__main__":
    main()
