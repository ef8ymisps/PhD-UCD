"""Validate Python AC-OPF endpoints with PyPowSyBl/OpenLoadFlow.

This is the Python-side counterpart to the Julia endpoint load-flow diagnostic,
but the external load-flow engine is PyPowSyBl/OpenLoadFlow.  For each selected
objective/variable-mismatch scenario, the script:

1. reads the exported Python-vs-PowerModels endpoint tables;
2. builds the same scenario as a PyPowSyBl network;
3. warm-starts/configures OpenLoadFlow once from the Python OPF endpoint and
   once from the PowerModels OPF endpoint;
4. runs AC load flow through PyPowSyBl; and
5. evaluates inequality levels after LF against the PowerModels OPF levels.

Pg checks and generator/PV/slack voltage checks are flagged separately because
they are not fully independent LF evidence: active generator dispatch is an LF
input except for balancing/slack treatment, and generator-bus voltage magnitudes
are setpoint-like.  PQ-bus voltage magnitudes, Qg, branch thermal loading, and
angle levels are the stronger re-derived checks.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.io import savemat

PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from own_models.src.adapters.powermodels_csv_adapter import (  # noqa: E402
    load_powercase_from_powermodels_json,
)
from own_models.src.analysis.compare_ac_with_powermodels import (  # noqa: E402
    classify_powermodels_ac_outcome,
    load_powermodels_ac_reference,
)
from own_models.src.analysis.pypowsybl_security_validation import (  # noqa: E402
    _lf_converged,
    _require_pypowsybl,
    _solved_slack_bus,
    _status_text,
)
from own_models.src.data.case import PQ, PV, SLACK, PowerCase  # noqa: E402
from pypowsybl_replacement_experiment_matrix import (  # noqa: E402
    _match_generator,
    build_pypowsybl_mapping,
    get_table,
    load_case_network,
)


REFERENCE_DIR = REPO_ROOT / "results" / "reference" / "powermodels" / "ac_opf"
RESULTS_ROOT = REPO_ROOT / "results" / "own_vs_powermodels"
PYTHON_CURRENT_ROOT = RESULTS_ROOT / "python"
PYTHON_PM_LIKE_ROOT = RESULTS_ROOT / "python_pm_like"
ORGANIZED_ROOT = RESULTS_ROOT / "_organized_for_meeting" / "08_python_ac_opf_debugging"

SUMMARY_PATH = RESULTS_ROOT / "python_endpoint_pypowsybl_loadflow_validation.csv"
VARIABLES_PATH = RESULTS_ROOT / "python_endpoint_pypowsybl_lf_variable_comparison.csv"
LEVELS_PATH = RESULTS_ROOT / "python_endpoint_pypowsybl_lf_inequality_levels.csv"
BINDING_SUMMARY_PATH = RESULTS_ROOT / "python_endpoint_pypowsybl_lf_binding_summary.csv"
VIOLATIONS_PATH = RESULTS_ROOT / "python_endpoint_pypowsybl_lf_violations.csv"
README_PATH = RESULTS_ROOT / "python_endpoint_pypowsybl_lf_README.md"
TMP_MATPOWER_DIR = RESULTS_ROOT / "_tmp_pypowsybl_endpoint_cases"

SOLVED_STATUSES = {"OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED"}


@dataclass(frozen=True)
class VariantConfig:
    key: str
    label: str
    root: Path
    summary_path: Path


@dataclass
class EndpointState:
    pg: dict[str, float]
    qg: dict[str, float]
    vm: dict[str, float]
    va: dict[str, float]
    branch_flows: dict[str, dict[str, float]]


@dataclass
class LoadFlowState:
    status: str
    converged: bool
    elapsed_sec: float
    iteration_count: float
    slack_bus_id: str
    slack_active_power_mismatch_mw: float
    used_provider_keyword: bool
    bus_update_success: bool
    pg: dict[str, float]
    qg: dict[str, float]
    vm: dict[str, float]
    va: dict[str, float]
    branch_flows: dict[str, dict[str, float]]
    n_applied_bus_vm: int
    n_applied_bus_va: int
    n_applied_gen_pg: int
    n_applied_gen_qg: int
    n_applied_gen_target_v: int


VARIANTS = {
    "current": VariantConfig(
        key="current",
        label="python_current",
        root=PYTHON_CURRENT_ROOT,
        summary_path=PYTHON_CURRENT_ROOT / "summary.csv",
    ),
    "pm_like": VariantConfig(
        key="pm_like",
        label="python_pm_like_zero",
        root=PYTHON_PM_LIKE_ROOT,
        summary_path=PYTHON_PM_LIKE_ROOT / "summary.csv",
    ),
}


def disk_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    resolved = path.resolve()
    text = str(resolved)
    return Path(text if text.startswith("\\\\?\\") else "\\\\?\\" + text)


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def safe_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def is_python_solved(row: pd.Series) -> bool:
    if "own_success" in row and str(row["own_success"]) != "":
        return as_bool(row["own_success"])
    return str(row.get("own_status", "")).strip().upper() in SOLVED_STATUSES or str(row.get("own_status", "")).strip().lower() == "optimal"


def is_powermodels_solved(row: pd.Series) -> bool:
    if "pm_success" in row and str(row["pm_success"]) != "":
        return as_bool(row["pm_success"])
    return str(row.get("pm_status", "")).strip().upper() in SOLVED_STATUSES


def material_reasons(row: pd.Series, args: argparse.Namespace) -> list[str]:
    reasons: list[str] = []
    if abs(safe_float(row.get("objective_diff"))) > args.objective_abs_tol:
        reasons.append("objective_abs")
    if abs(safe_float(row.get("objective_rel_diff"))) > args.objective_rel_tol:
        reasons.append("objective_rel")
    for col, tol in (
        ("max_pg_diff", args.pg_tol),
        ("max_qg_diff", args.qg_tol),
        ("max_vm_diff", args.vm_tol),
        ("max_va_diff", args.va_tol),
        ("max_pf_diff", args.flow_tol),
        ("max_qf_diff", args.flow_tol),
    ):
        if safe_float(row.get(col)) > tol:
            reasons.append(col)
    return reasons


def find_reference_files(case_label: str, scenario: str) -> tuple[Path, Path, str]:
    stem = f"pglib_opf_case{case_label}"
    direct_case = REFERENCE_DIR / f"{stem}_{scenario}_case.json"
    direct_ref = REFERENCE_DIR / f"{stem}_{scenario}_reference.json"
    if disk_path(direct_case).exists() and disk_path(direct_ref).exists():
        return direct_case, direct_ref, scenario

    for reference_path in sorted(REFERENCE_DIR.glob(f"{stem}_*_reference.json")):
        suffix = reference_path.name[len(stem) + 1 : -len("_reference.json")]
        case_path = REFERENCE_DIR / f"{stem}_{suffix}_case.json"
        if not disk_path(case_path).exists():
            continue
        try:
            reference = load_powermodels_ac_reference(disk_path(reference_path))
        except OSError:
            continue
        scenario_candidates = {
            suffix,
            str(reference.get("scenario", "")),
            suffix.replace("p", "."),
        }
        if scenario in scenario_candidates:
            return case_path, reference_path, suffix

    raise FileNotFoundError(f"No PowerModels JSON case/reference files found for {case_label}/{scenario}.")


def endpoint_dirs_by_scenario(root: Path, case_label: str) -> dict[str, Path]:
    case_dir = root / case_label
    if not disk_path(case_dir).exists():
        return {}
    mapping: dict[str, Path] = {}
    for path in sorted(case_dir.iterdir()):
        if not path.is_dir():
            continue
        label_path = path / "scenario_label.txt"
        scenario = label_path.read_text(encoding="utf-8").strip() if label_path.exists() else path.name
        mapping[scenario] = path
    return mapping


def endpoint_dir_for(config: VariantConfig, case_label: str, scenario: str) -> Path:
    mapping = endpoint_dirs_by_scenario(config.root, case_label)
    if scenario in mapping:
        return mapping[scenario]
    direct = config.root / case_label / scenario
    if disk_path(direct).exists():
        return direct
    raise FileNotFoundError(f"No exported endpoint table folder found for {config.label}/{case_label}/{scenario}.")


def read_endpoint_state(out_dir: Path, prefix: str) -> EndpointState:
    buses = pd.read_csv(disk_path(out_dir / "buses.csv"), dtype={"bus_id": str})
    generators = pd.read_csv(disk_path(out_dir / "generators.csv"), dtype={"gen_id": str})
    branches = pd.read_csv(disk_path(out_dir / "branches.csv"), dtype={"branch_id": str})

    def mapping(frame: pd.DataFrame, id_col: str, value_col: str) -> dict[str, float]:
        if value_col not in frame.columns:
            return {}
        values: dict[str, float] = {}
        for _, row in frame.iterrows():
            value = safe_float(row[value_col])
            if math.isfinite(value):
                values[str(row[id_col])] = value
        return values

    branch_flows: dict[str, dict[str, float]] = {}
    for _, row in branches.iterrows():
        branch_id = str(row["branch_id"])
        values = {
            "pf": safe_float(row.get(f"{prefix}_pf")),
            "qf": safe_float(row.get(f"{prefix}_qf")),
            "pt": safe_float(row.get(f"{prefix}_pt")),
            "qt": safe_float(row.get(f"{prefix}_qt")),
        }
        branch_flows[branch_id] = {key: value for key, value in values.items() if math.isfinite(value)}

    return EndpointState(
        pg=mapping(generators, "gen_id", f"{prefix}_pg"),
        qg=mapping(generators, "gen_id", f"{prefix}_qg"),
        vm=mapping(buses, "bus_id", f"{prefix}_vm"),
        va=mapping(buses, "bus_id", f"{prefix}_va"),
        branch_flows=branch_flows,
    )


def reference_endpoint_state(reference: dict) -> EndpointState:
    pg = {
        str(gen_id): float(values["pg"])
        for gen_id, values in reference.get("generators", {}).items()
        if values.get("pg") is not None
    }
    qg = {
        str(gen_id): float(values["qg"])
        for gen_id, values in reference.get("generators", {}).items()
        if values.get("qg") is not None
    }
    vm = {
        str(bus_id): float(values["vm"])
        for bus_id, values in reference.get("buses", {}).items()
        if values.get("vm") is not None
    }
    va = {
        str(bus_id): float(values["va"])
        for bus_id, values in reference.get("buses", {}).items()
        if values.get("va") is not None
    }
    branch_flows = {
        str(branch_id): {
            key: float(values[key])
            for key in ("pf", "qf", "pt", "qt")
            if values.get(key) is not None
        }
        for branch_id, values in reference.get("branches", {}).items()
    }
    return EndpointState(pg=pg, qg=qg, vm=vm, va=va, branch_flows=branch_flows)


def safe_case_token(case_label: str, scenario: str) -> str:
    raw = f"{case_label}_{scenario}"
    safe = "".join(ch if ch.isalnum() or ch in "._=-" else "_" for ch in raw).strip("_")
    if len(safe) <= 96:
        return safe
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{safe[:72].rstrip('_')}__{digest}"


def matpower_bus_number(bus_id: str) -> int:
    return int(round(float(bus_id)))


def powercase_to_mpc(case: PowerCase) -> dict[str, Any]:
    pd_by_bus = {bus.id: 0.0 for bus in case.buses}
    qd_by_bus = {bus.id: 0.0 for bus in case.buses}
    gs_by_bus = {bus.id: 0.0 for bus in case.buses}
    bs_by_bus = {bus.id: 0.0 for bus in case.buses}

    for load in case.loads:
        if load.status == 1:
            pd_by_bus[load.bus] += load.pd * case.base_mva
            qd_by_bus[load.bus] += load.qd * case.base_mva
    for shunt in case.shunts:
        if shunt.status == 1:
            gs_by_bus[shunt.bus] += shunt.gs * case.base_mva
            bs_by_bus[shunt.bus] += shunt.bs * case.base_mva

    bus = np.array(
        [
            [
                matpower_bus_number(item.id),
                item.bus_type,
                pd_by_bus[item.id],
                qd_by_bus[item.id],
                gs_by_bus[item.id],
                bs_by_bus[item.id],
                1.0,
                item.vm0,
                math.degrees(item.va0),
                item.nominal_v_kv if item.nominal_v_kv > 0.0 else 1.0,
                1.0,
                item.vmax,
                item.vmin,
            ]
            for item in case.buses
        ],
        dtype=float,
    )

    gen = np.array(
        [
            [
                matpower_bus_number(item.bus),
                item.pg0 * case.base_mva,
                item.qg0 * case.base_mva,
                item.qmax * case.base_mva,
                item.qmin * case.base_mva,
                case.buses[case.bus_id_to_index[item.bus]].vm0,
                case.base_mva,
                item.status,
                item.pmax * case.base_mva,
                item.pmin * case.base_mva,
            ]
            for item in case.generators
        ],
        dtype=float,
    )

    branch = np.array(
        [
            [
                matpower_bus_number(item.from_bus),
                matpower_bus_number(item.to_bus),
                item.r,
                item.x,
                item.b,
                item.rate_a * case.base_mva if item.rate_a > 0.0 else 0.0,
                item.rate_a * case.base_mva if item.rate_a > 0.0 else 0.0,
                item.rate_a * case.base_mva if item.rate_a > 0.0 else 0.0,
                item.tap,
                math.degrees(item.shift),
                item.status,
                math.degrees(item.angmin),
                math.degrees(item.angmax),
            ]
            for item in case.branches
        ],
        dtype=float,
    )

    gencost = np.array(
        [
            [2.0, 0.0, 0.0, 3.0, item.cost_c2, item.cost_c1, item.cost_c0]
            for item in case.generators
        ],
        dtype=float,
    )

    return {
        "version": "2",
        "baseMVA": float(case.base_mva),
        "bus": bus,
        "gen": gen,
        "branch": branch,
        "gencost": gencost,
    }


def write_scenario_matpower(case: PowerCase, case_label: str, scenario: str) -> Path:
    TMP_MATPOWER_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_MATPOWER_DIR / f"pglib_opf_case{safe_case_token(case_label, scenario)}.mat"
    savemat(str(disk_path(path)), {"mpc": powercase_to_mpc(case)}, do_compression=True)
    return path


def generator_ids_by_own_id(case: PowerCase, mapping: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    gen_ids: dict[str, str] = {}
    methods: dict[str, str] = {}
    order_by_bus: dict[str, int] = {}
    order_at_bus: dict[str, int] = {}
    for gen in case.generators:
        order_by_bus[gen.bus] = order_by_bus.get(gen.bus, 0) + 1
        order_at_bus[gen.id] = order_by_bus[gen.bus]

    consumed: set[str] = set()
    for gen in case.generators:
        if gen.status != 1:
            continue
        try:
            pp_gen, method, reason = _match_generator(
                matpower_bus_number(gen.bus),
                order_at_bus[gen.id],
                mapping,
                consumed,
            )
        except Exception as exc:  # noqa: BLE001 - keep partial mapping available
            pp_gen, method, reason = None, "", str(exc)
        if pp_gen is None:
            methods[gen.id] = f"unmatched:{reason}"
            continue
        pp_id = str(pp_gen["id"])
        consumed.add(pp_id)
        gen_ids[gen.id] = pp_id
        methods[gen.id] = method
    return gen_ids, methods


def branch_ids_by_own_id(case: PowerCase, network: Any) -> dict[str, str]:
    branch_rows: list[dict[str, Any]] = []
    for table in (get_table(network, "get_lines"), get_table(network, "get_2_windings_transformers")):
        if table.empty or not {"id", "voltage_level1_id", "voltage_level2_id"}.issubset(table.columns):
            continue
        for _, row in table.iterrows():
            nums_1 = re_digits(str(row["voltage_level1_id"]))
            nums_2 = re_digits(str(row["voltage_level2_id"]))
            if not nums_1 or not nums_2:
                continue
            branch_rows.append(
                {
                    "id": str(row["id"]),
                    "key": tuple(sorted((nums_1[0], nums_2[0]))),
                }
            )

    by_pair: dict[tuple[int, int], list[str]] = {}
    for row in sorted(branch_rows, key=lambda item: item["id"]):
        by_pair.setdefault(row["key"], []).append(row["id"])

    counters: dict[tuple[int, int], int] = {}
    mapping: dict[str, str] = {}
    for branch in case.branches:
        if branch.status != 1:
            continue
        key = tuple(sorted((matpower_bus_number(branch.from_bus), matpower_bus_number(branch.to_bus))))
        position = counters.get(key, 0)
        candidates = by_pair.get(key, [])
        if position < len(candidates):
            mapping[branch.id] = candidates[position]
        counters[key] = position + 1
    return mapping


def re_digits(value: str) -> list[int]:
    return [int(match) for match in re.findall(r"\d+", value)]


def apply_endpoint_to_network(network: Any, case: PowerCase, endpoint: EndpointState, mapping: dict[str, Any]) -> tuple[bool, dict[str, int], dict[str, str], dict[str, str]]:
    gen_id_map, gen_mapping_methods = generator_ids_by_own_id(case, mapping)
    counts = {
        "n_applied_bus_vm": 0,
        "n_applied_bus_va": 0,
        "n_applied_gen_pg": 0,
        "n_applied_gen_qg": 0,
        "n_applied_gen_target_v": 0,
    }

    for gen in case.generators:
        pp_id = gen_id_map.get(gen.id)
        if gen.status != 1 or pp_id is None or gen.id not in endpoint.pg:
            continue
        update = {
            "id": [pp_id],
            "target_p": [endpoint.pg[gen.id] * case.base_mva],
            "target_q": [endpoint.qg.get(gen.id, gen.qg0) * case.base_mva],
            "target_v": [endpoint.vm.get(gen.bus, case.buses[case.bus_id_to_index[gen.bus]].vm0)],
        }
        network.update_generators(**update)
        counts["n_applied_gen_pg"] += int(gen.id in endpoint.pg)
        counts["n_applied_gen_qg"] += int(gen.id in endpoint.qg)
        counts["n_applied_gen_target_v"] += int(gen.bus in endpoint.vm)

    bus_update_success = False
    try:
        bus_id_by_matpower_bus = mapping.get("bus_id_by_matpower_bus", {})
        for bus in case.buses:
            pp_bus_id = bus_id_by_matpower_bus.get(matpower_bus_number(bus.id))
            if pp_bus_id is None or (bus.id not in endpoint.vm and bus.id not in endpoint.va):
                continue
            network.update_buses(
                id=[pp_bus_id],
                v_mag=[endpoint.vm.get(bus.id, bus.vm0)],
                v_angle=[math.degrees(endpoint.va.get(bus.id, bus.va0))],
            )
            counts["n_applied_bus_vm"] += int(bus.id in endpoint.vm)
            counts["n_applied_bus_va"] += int(bus.id in endpoint.va)
        bus_update_success = counts["n_applied_bus_vm"] > 0 or counts["n_applied_bus_va"] > 0
    except Exception:
        bus_update_success = False

    return bus_update_success, counts, gen_id_map, gen_mapping_methods


def component_result_value(result: Any, name: str) -> float:
    values = result if isinstance(result, list) else [result]
    extracted = []
    for item in values:
        if hasattr(item, name):
            extracted.append(safe_float(getattr(item, name)))
    finite = [value for value in extracted if math.isfinite(value)]
    if not finite:
        return math.nan
    return max(finite) if name == "iteration_count" else finite[0]


def endpoint_loadflow_parameters(max_iterations: int):
    pp = _require_pypowsybl()
    provider_parameters = {
        "acSolverType": "NEWTON_RAPHSON",
        "maxNewtonRaphsonIterations": str(int(max_iterations)),
        "reactiveLimitsMaxPqPvSwitch": "20",
    }
    kwargs: dict[str, Any] = {
        "distributed_slack": False,
        "read_slack_bus": True,
        "write_slack_bus": True,
        "use_reactive_limits": True,
        "transformer_voltage_control_on": False,
        "phase_shifter_regulation_on": False,
        "shunt_compensator_voltage_control_on": False,
        "provider_parameters": provider_parameters,
    }
    if hasattr(pp.lf, "VoltageInitMode") and hasattr(pp.lf.VoltageInitMode, "PREVIOUS_VALUES"):
        kwargs["voltage_init_mode"] = pp.lf.VoltageInitMode.PREVIOUS_VALUES
    if hasattr(pp.lf, "ComponentMode") and hasattr(pp.lf.ComponentMode, "MAIN_CONNECTED"):
        kwargs["component_mode"] = pp.lf.ComponentMode.MAIN_CONNECTED
    return pp.lf.Parameters(**kwargs)


def parameter_summary(max_iterations: int) -> str:
    return (
        "voltage_init_mode=PREVIOUS_VALUES;"
        "distributed_slack=false;"
        "read_slack_bus=true;"
        "write_slack_bus=true;"
        "use_reactive_limits=true;"
        "controls_off;"
        "acSolverType=NEWTON_RAPHSON;"
        f"maxNewtonRaphsonIterations={int(max_iterations)}"
    )


def combined_branch_table(network: Any) -> pd.DataFrame:
    frames = []
    lines = network.get_lines().reset_index()
    if not lines.empty:
        lines["equipment_type"] = "line"
        frames.append(lines)
    transformers = network.get_2_windings_transformers().reset_index()
    if not transformers.empty:
        transformers["equipment_type"] = "two_windings_transformer"
        frames.append(transformers)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def extract_lf_state(
    network: Any,
    case: PowerCase,
    lf_result: Any,
    elapsed_sec: float,
    used_provider_keyword: bool,
    bus_update_success: bool,
    counts: dict[str, int],
    pypowsybl_mapping: dict[str, Any],
    gen_id_map: dict[str, str],
) -> LoadFlowState:
    status = _status_text(lf_result)
    buses_pp = get_table(network, "get_buses")
    gens_pp = get_table(network, "get_generators")
    branches_pp = combined_branch_table(network)

    buses_by_id = buses_pp.set_index("id") if "id" in buses_pp.columns else pd.DataFrame()
    gens_by_id = gens_pp.set_index("id") if "id" in gens_pp.columns else pd.DataFrame()
    branches_by_id = branches_pp.set_index("id") if "id" in branches_pp.columns and not branches_pp.empty else pd.DataFrame()
    bus_id_by_matpower_bus = pypowsybl_mapping.get("bus_id_by_matpower_bus", {})
    branch_id_map = branch_ids_by_own_id(case, network)

    vm: dict[str, float] = {}
    va: dict[str, float] = {}
    for bus in case.buses:
        pp_bus_id = bus_id_by_matpower_bus.get(matpower_bus_number(bus.id))
        if pp_bus_id is None or buses_by_id.empty or pp_bus_id not in buses_by_id.index:
            continue
        row = buses_by_id.loc[pp_bus_id]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        vm[bus.id] = safe_float(row.get("v_mag"))
        va[bus.id] = math.radians(safe_float(row.get("v_angle")))

    pg: dict[str, float] = {}
    qg: dict[str, float] = {}
    for gen in case.generators:
        pp_id = gen_id_map.get(gen.id)
        if gen.status != 1 or pp_id not in gens_by_id.index:
            continue
        row = gens_by_id.loc[pp_id]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        p = safe_float(row.get("p"))
        q = safe_float(row.get("q"))
        if math.isfinite(p):
            pg[gen.id] = -p / case.base_mva
        if math.isfinite(q):
            qg[gen.id] = -q / case.base_mva

    branch_flows: dict[str, dict[str, float]] = {}
    for branch in case.branches:
        pp_branch_id = branch_id_map.get(branch.id)
        if branch.status != 1 or pp_branch_id is None or pp_branch_id not in branches_by_id.index:
            continue
        row = branches_by_id.loc[pp_branch_id]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        values = {
            "pf": safe_float(row.get("p1")) / case.base_mva,
            "qf": safe_float(row.get("q1")) / case.base_mva,
            "pt": safe_float(row.get("p2")) / case.base_mva,
            "qt": safe_float(row.get("q2")) / case.base_mva,
        }
        branch_flows[branch.id] = {key: value for key, value in values.items() if math.isfinite(value)}

    return LoadFlowState(
        status=status,
        converged=_lf_converged(status),
        elapsed_sec=elapsed_sec,
        iteration_count=component_result_value(lf_result, "iteration_count"),
        slack_bus_id=_solved_slack_bus(lf_result),
        slack_active_power_mismatch_mw=component_result_value(lf_result, "slack_bus_active_power_mismatch"),
        used_provider_keyword=used_provider_keyword,
        bus_update_success=bus_update_success,
        pg=pg,
        qg=qg,
        vm=vm,
        va=va,
        branch_flows=branch_flows,
        **counts,
    )


def run_pypowsybl_lf(
    case: PowerCase,
    case_label: str,
    scenario: str,
    endpoint: EndpointState,
    *,
    provider: str,
    max_iterations: int,
) -> LoadFlowState:
    pp = _require_pypowsybl()
    mat_path = write_scenario_matpower(case, case_label, scenario)
    network = load_case_network(mat_path)
    pypowsybl_mapping = build_pypowsybl_mapping(network)
    bus_update_success, counts, gen_id_map, _ = apply_endpoint_to_network(network, case, endpoint, pypowsybl_mapping)
    parameters = endpoint_loadflow_parameters(max_iterations)

    start = time.perf_counter()
    try:
        raw_result = pp.lf.run_ac(network, parameters=parameters, provider=provider)
        used_provider_keyword = True
    except TypeError:
        raw_result = pp.lf.run_ac(network, parameters=parameters)
        used_provider_keyword = False
    elapsed = time.perf_counter() - start

    return extract_lf_state(
        network,
        case,
        raw_result,
        elapsed,
        used_provider_keyword,
        bus_update_success,
        counts,
        pypowsybl_mapping,
        gen_id_map,
    )


def bus_type_label(bus_type: int) -> str:
    if bus_type == PQ:
        return "PQ"
    if bus_type == PV:
        return "PV"
    if bus_type == SLACK:
        return "slack"
    return "unknown"


def bus_class(case: PowerCase, bus_id: str) -> tuple[str, bool]:
    if bus_id not in case.bus_id_to_index:
        return "unknown", False
    bus = case.buses[case.bus_id_to_index[bus_id]]
    has_active_gen = any(gen.status == 1 and gen.bus == bus_id for gen in case.generators)
    return bus_type_label(bus.bus_type), has_active_gen


def independence_note(case: PowerCase, constraint_type: str, element_id: str) -> tuple[bool, str, str, bool]:
    if constraint_type.startswith("pg_"):
        gen = next((item for item in case.generators if item.id == element_id), None)
        gen_bus = gen.bus if gen is not None else ""
        bus_type, has_active_gen = bus_class(case, gen_bus) if gen_bus else ("unknown", False)
        return False, "specified_dispatch_or_slack_balance", bus_type, has_active_gen

    if constraint_type.startswith("qg_"):
        gen = next((item for item in case.generators if item.id == element_id), None)
        gen_bus = gen.bus if gen is not None else ""
        bus_type, has_active_gen = bus_class(case, gen_bus) if gen_bus else ("unknown", False)
        return True, "reactive_power_rederived_by_openloadflow", bus_type, has_active_gen

    if constraint_type.startswith("vm_"):
        bus_type, has_active_gen = bus_class(case, element_id)
        if bus_type == "PQ" and not has_active_gen:
            return True, "pq_bus_voltage_rederived_by_openloadflow", bus_type, has_active_gen
        return False, "generator_or_slack_voltage_setpoint_like", bus_type, has_active_gen

    if constraint_type.startswith("thermal_"):
        return True, "branch_flow_rederived_by_openloadflow", "", False

    if constraint_type.startswith("angle_"):
        return True, "bus_angle_difference_rederived_by_openloadflow", "", False

    return False, "unclassified", "", False


def add_level(rows: list[dict[str, object]], *, constraint_type: str, element_id: str, bound: float, value: float, margin: float, units: str, source: str, tol: float) -> None:
    rows.append(
        {
            "source": source,
            "constraint_type": constraint_type,
            "element_id": str(element_id),
            "bound": float(bound),
            "value": float(value),
            "margin": float(margin),
            "violation": max(0.0, -float(margin)),
            "is_binding": abs(float(margin)) <= tol,
            "level_units": units,
        }
    )


def inequality_level_rows(case: PowerCase, state: EndpointState | LoadFlowState, *, source: str, tol: float) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for gen in case.generators:
        if gen.status != 1 or gen.id not in state.pg or gen.id not in state.qg:
            continue
        pg = state.pg[gen.id]
        qg = state.qg[gen.id]
        add_level(rows, constraint_type="pg_min", element_id=gen.id, bound=gen.pmin, value=pg, margin=pg - gen.pmin, units="pu", source=source, tol=tol)
        add_level(rows, constraint_type="pg_max", element_id=gen.id, bound=gen.pmax, value=pg, margin=gen.pmax - pg, units="pu", source=source, tol=tol)
        add_level(rows, constraint_type="qg_min", element_id=gen.id, bound=gen.qmin, value=qg, margin=qg - gen.qmin, units="pu", source=source, tol=tol)
        add_level(rows, constraint_type="qg_max", element_id=gen.id, bound=gen.qmax, value=qg, margin=gen.qmax - qg, units="pu", source=source, tol=tol)

    for bus in case.buses:
        if bus.id not in state.vm:
            continue
        vm = state.vm[bus.id]
        add_level(rows, constraint_type="vm_min", element_id=bus.id, bound=bus.vmin, value=vm, margin=vm - bus.vmin, units="pu", source=source, tol=tol)
        add_level(rows, constraint_type="vm_max", element_id=bus.id, bound=bus.vmax, value=vm, margin=bus.vmax - vm, units="pu", source=source, tol=tol)

    for branch in case.branches:
        if branch.status != 1:
            continue
        if branch.from_bus in state.va and branch.to_bus in state.va:
            angle = float(state.va[branch.from_bus] - state.va[branch.to_bus])
            add_level(rows, constraint_type="angle_min", element_id=branch.id, bound=branch.angmin, value=angle, margin=angle - branch.angmin, units="radian", source=source, tol=tol)
            add_level(rows, constraint_type="angle_max", element_id=branch.id, bound=branch.angmax, value=angle, margin=branch.angmax - angle, units="radian", source=source, tol=tol)

        flow = state.branch_flows.get(branch.id, {})
        if branch.rate_a > 0.0 and {"pf", "qf", "pt", "qt"}.issubset(flow):
            sf2 = flow["pf"] ** 2 + flow["qf"] ** 2
            st2 = flow["pt"] ** 2 + flow["qt"] ** 2
            limit = branch.rate_a**2
            add_level(rows, constraint_type="thermal_from", element_id=branch.id, bound=limit, value=sf2, margin=limit - sf2, units="pu_squared", source=source, tol=tol)
            add_level(rows, constraint_type="thermal_to", element_id=branch.id, bound=limit, value=st2, margin=limit - st2, units="pu_squared", source=source, tol=tol)

    return rows


def compare_levels(case: PowerCase, pm_rows: list[dict[str, object]], lf_rows: list[dict[str, object]], *, violation_tol: float) -> list[dict[str, object]]:
    pm_by_key = {(row["constraint_type"], row["element_id"]): row for row in pm_rows}
    lf_by_key = {(row["constraint_type"], row["element_id"]): row for row in lf_rows}
    compared: list[dict[str, object]] = []
    for key in sorted(set(pm_by_key) | set(lf_by_key)):
        constraint_type, element_id = key
        pm = pm_by_key.get(key, {})
        lf = lf_by_key.get(key, {})
        pm_value = safe_float(pm.get("value"))
        lf_value = safe_float(lf.get("value"))
        pm_margin = safe_float(pm.get("margin"))
        lf_margin = safe_float(lf.get("margin"))
        pm_binding = bool(pm.get("is_binding", False))
        lf_binding = bool(lf.get("is_binding", False))
        pm_violation = safe_float(pm.get("violation"))
        lf_violation = safe_float(lf.get("violation"))
        independent, note, bus_type, has_active_gen = independence_note(case, constraint_type, element_id)
        compared.append(
            {
                "constraint_type": constraint_type,
                "element_id": element_id,
                "bound": safe_float(pm.get("bound", lf.get("bound", math.nan))),
                "lf_value": lf_value,
                "pm_opf_value": pm_value,
                "value_diff_lf_minus_pm_opf": lf_value - pm_value if math.isfinite(lf_value) and math.isfinite(pm_value) else math.nan,
                "abs_value_diff": abs(lf_value - pm_value) if math.isfinite(lf_value) and math.isfinite(pm_value) else math.nan,
                "lf_margin": lf_margin,
                "pm_opf_margin": pm_margin,
                "margin_diff_lf_minus_pm_opf": lf_margin - pm_margin if math.isfinite(lf_margin) and math.isfinite(pm_margin) else math.nan,
                "abs_margin_diff": abs(lf_margin - pm_margin) if math.isfinite(lf_margin) and math.isfinite(pm_margin) else math.nan,
                "lf_violation": lf_violation,
                "pm_opf_violation": pm_violation,
                "lf_binding": lf_binding,
                "pm_opf_binding": pm_binding,
                "lf_violates_limit": math.isfinite(lf_violation) and lf_violation > violation_tol,
                "pm_opf_violates_limit": math.isfinite(pm_violation) and pm_violation > violation_tol,
                "binding_relation": "same" if lf_binding == pm_binding else "different",
                "independently_rederived_after_lf": independent,
                "independence_note": note,
                "bus_type": bus_type,
                "bus_has_active_generator": has_active_gen,
                "level_units": str(pm.get("level_units", lf.get("level_units", ""))),
                "limit_violation_tolerance": violation_tol,
            }
        )
    return compared


def family_of(constraint_type: str) -> str:
    if constraint_type.startswith("pg_"):
        return "pg"
    if constraint_type.startswith("qg_"):
        return "qg"
    if constraint_type.startswith("vm_"):
        return "vm"
    if constraint_type.startswith("thermal_"):
        return "thermal"
    if constraint_type.startswith("angle_"):
        return "angle"
    return constraint_type


def max_abs_change(endpoint: dict[str, float], solved: dict[str, float]) -> float:
    values = [
        abs(float(solved[key]) - float(endpoint[key]))
        for key in set(endpoint) & set(solved)
        if math.isfinite(float(endpoint[key])) and math.isfinite(float(solved[key]))
    ]
    return max(values) if values else math.nan


def align_angles_to_endpoint(case: PowerCase, endpoint_va: dict[str, float], solved_va: dict[str, float]) -> dict[str, float]:
    if case.slack_bus_id not in endpoint_va or case.slack_bus_id not in solved_va:
        return dict(solved_va)
    offset = solved_va[case.slack_bus_id] - endpoint_va[case.slack_bus_id]
    return {bus_id: value - offset for bus_id, value in solved_va.items()}


def branch_metric(endpoint: EndpointState, solved: LoadFlowState, metric: str) -> float:
    values = []
    for branch_id, endpoint_flow in endpoint.branch_flows.items():
        solved_flow = solved.branch_flows.get(branch_id, {})
        if metric in endpoint_flow and metric in solved_flow:
            values.append(abs(solved_flow[metric] - endpoint_flow[metric]))
    return max(values) if values else math.nan


def variable_rows(base: dict[str, object], case: PowerCase, endpoint: EndpointState, solved: LoadFlowState) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    solved_va_aligned = align_angles_to_endpoint(case, endpoint.va, solved.va)
    variable_maps = [
        ("pg", endpoint.pg, solved.pg, "generator"),
        ("qg", endpoint.qg, solved.qg, "generator"),
        ("vm", endpoint.vm, solved.vm, "bus"),
        ("va", endpoint.va, solved_va_aligned, "bus"),
    ]
    for variable, endpoint_values, solved_values, element_type in variable_maps:
        for element_id in sorted(set(endpoint_values) & set(solved_values), key=lambda value: int(value) if str(value).isdigit() else str(value)):
            endpoint_value = endpoint_values[element_id]
            lf_value = solved_values[element_id]
            rows.append(
                {
                    **base,
                    "variable": variable,
                    "element_type": element_type,
                    "element_id": element_id,
                    "opf_endpoint_value": endpoint_value,
                    "pypowsybl_lf_value": lf_value,
                    "difference_lf_minus_endpoint": lf_value - endpoint_value,
                    "abs_difference": abs(lf_value - endpoint_value),
                    "angle_aligned_to_endpoint_slack": variable == "va",
                }
            )

    for branch_id in sorted(set(endpoint.branch_flows) & set(solved.branch_flows), key=lambda value: int(value) if str(value).isdigit() else str(value)):
        for variable in ("pf", "qf", "pt", "qt"):
            endpoint_value = endpoint.branch_flows[branch_id].get(variable)
            lf_value = solved.branch_flows[branch_id].get(variable)
            if endpoint_value is None or lf_value is None:
                continue
            rows.append(
                {
                    **base,
                    "variable": variable,
                    "element_type": "branch",
                    "element_id": branch_id,
                    "opf_endpoint_value": endpoint_value,
                    "pypowsybl_lf_value": lf_value,
                    "difference_lf_minus_endpoint": lf_value - endpoint_value,
                    "abs_difference": abs(lf_value - endpoint_value),
                    "angle_aligned_to_endpoint_slack": False,
                }
            )

    return rows


def binding_summary_rows(base: dict[str, object], compared_levels: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for family in ["all", *sorted({family_of(str(row["constraint_type"])) for row in compared_levels})]:
        subset = compared_levels if family == "all" else [
            row for row in compared_levels if family_of(str(row["constraint_type"])) == family
        ]
        if not subset:
            continue
        for independence_scope, scope_subset in (
            ("all_constraints", subset),
            ("independently_rederived_only", [row for row in subset if bool(row["independently_rederived_after_lf"])]),
            ("specified_or_pinned_only", [row for row in subset if not bool(row["independently_rederived_after_lf"])]),
        ):
            if not scope_subset:
                continue
            rows.append(
                {
                    **base,
                    "constraint_family": family,
                    "independence_scope": independence_scope,
                    "n_constraints": len(scope_subset),
                    "n_binding_mismatches": sum(str(row["binding_relation"]) == "different" for row in scope_subset),
                    "n_only_lf_binding": sum(bool(row["lf_binding"]) and not bool(row["pm_opf_binding"]) for row in scope_subset),
                    "n_only_pm_opf_binding": sum(bool(row["pm_opf_binding"]) and not bool(row["lf_binding"]) for row in scope_subset),
                    "n_lf_violations": sum(bool(row["lf_violates_limit"]) for row in scope_subset),
                    "n_pm_opf_violations": sum(bool(row["pm_opf_violates_limit"]) for row in scope_subset),
                    "max_lf_violation": max(safe_float(row["lf_violation"]) for row in scope_subset),
                    "max_abs_value_diff": max(safe_float(row["abs_value_diff"]) for row in scope_subset),
                    "max_abs_margin_diff": max(safe_float(row["abs_margin_diff"]) for row in scope_subset),
                }
            )
    return rows


def select_targets(config: VariantConfig, args: argparse.Namespace) -> list[dict[str, object]]:
    if not disk_path(config.summary_path).exists():
        return []
    frame = pd.read_csv(disk_path(config.summary_path), dtype=str, keep_default_na=False)
    if args.cases:
        frame = frame[frame["case"].isin(set(args.cases))]
    if args.scenarios:
        frame = frame[frame["scenario"].isin(set(args.scenarios))]

    selected: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        if not is_python_solved(row) or not is_powermodels_solved(row):
            continue
        reasons = material_reasons(row, args)
        if not args.all_solved and not reasons:
            continue
        record = row.to_dict()
        record["selected_reasons"] = "all_solved" if args.all_solved and not reasons else ";".join(reasons)
        record["model_variant"] = config.label
        selected.append(record)

    selected.sort(
        key=lambda row: (
            str(row["model_variant"]),
            -abs(safe_float(row.get("objective_rel_diff"))),
            -abs(safe_float(row.get("objective_diff"))),
            str(row.get("case")),
            str(row.get("scenario")),
        )
    )
    if args.max_targets is not None:
        selected = selected[: args.max_targets]
    return selected


def endpoint_sources_from_args(args: argparse.Namespace) -> list[tuple[str, str]]:
    sources = []
    if "own" in args.endpoint_sources:
        sources.append(("own_opf_solution", "own"))
    if "pm" in args.endpoint_sources:
        sources.append(("powermodels_opf_solution", "pm"))
    return sources


def run_one(config: VariantConfig, row: dict[str, object], args: argparse.Namespace) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    case_label = str(row["case"])
    scenario = str(row["scenario"])
    case_path, reference_path, reference_suffix = find_reference_files(case_label, scenario)
    out_dir = endpoint_dir_for(config, case_label, scenario)

    case = load_powercase_from_powermodels_json(disk_path(case_path))
    reference = load_powermodels_ac_reference(disk_path(reference_path))
    pm_state = reference_endpoint_state(reference)
    pm_level_rows = inequality_level_rows(case, pm_state, source="powermodels_opf_reference", tol=args.binding_tol)
    endpoint_states = {
        "own": read_endpoint_state(out_dir, "own"),
        "pm": read_endpoint_state(out_dir, "pm"),
    }

    summary_rows: list[dict[str, object]] = []
    variable_out: list[dict[str, object]] = []
    level_out: list[dict[str, object]] = []
    binding_out: list[dict[str, object]] = []
    violation_out: list[dict[str, object]] = []

    for endpoint_source, prefix in endpoint_sources_from_args(args):
        endpoint = endpoint_states[prefix]
        base = {
            "case": case_label,
            "scenario": scenario,
            "reference_suffix": reference_suffix,
            "model_variant": config.label,
            "endpoint_source": endpoint_source,
            "lf_engine": "PyPowSyBl/OpenLoadFlow",
            "provider": args.provider,
            "parameters": parameter_summary(args.max_lf_iterations),
            "target_source": "powermodels_reference",
            "selected_reasons": str(row.get("selected_reasons", "")),
            "pm_status": reference.get("termination_status", ""),
            "pm_outcome_class": classify_powermodels_ac_outcome(reference),
        }
        try:
            solved = run_pypowsybl_lf(
                case,
                case_label,
                scenario,
                endpoint,
                provider=args.provider,
                max_iterations=args.max_lf_iterations,
            )
            lf_rows = inequality_level_rows(case, solved, source="pypowsybl_lf_endpoint", tol=args.binding_tol)
            compared = compare_levels(case, pm_level_rows, lf_rows, violation_tol=args.limit_violation_tol)
            for level in compared:
                level.update(base)
            summary_base = {
                **base,
                "lf_status": solved.status,
                "lf_converged": solved.converged,
                "lf_elapsed_sec": solved.elapsed_sec,
                "lf_iteration_count": solved.iteration_count,
                "lf_slack_bus_id": solved.slack_bus_id,
                "lf_slack_active_power_mismatch_mw": solved.slack_active_power_mismatch_mw,
                "used_provider_keyword": solved.used_provider_keyword,
                "bus_update_success": solved.bus_update_success,
                "n_applied_bus_vm": solved.n_applied_bus_vm,
                "n_applied_bus_va": solved.n_applied_bus_va,
                "n_applied_gen_pg": solved.n_applied_gen_pg,
                "n_applied_gen_qg": solved.n_applied_gen_qg,
                "n_applied_gen_target_v": solved.n_applied_gen_target_v,
                "max_abs_pg_change_endpoint_to_lf": max_abs_change(endpoint.pg, solved.pg),
                "max_abs_qg_change_endpoint_to_lf": max_abs_change(endpoint.qg, solved.qg),
                "max_abs_vm_change_endpoint_to_lf": max_abs_change(endpoint.vm, solved.vm),
                "max_abs_va_change_endpoint_to_lf_aligned": max_abs_change(
                    endpoint.va,
                    align_angles_to_endpoint(case, endpoint.va, solved.va),
                ),
                "max_abs_pf_change_endpoint_to_lf": branch_metric(endpoint, solved, "pf"),
                "max_abs_qf_change_endpoint_to_lf": branch_metric(endpoint, solved, "qf"),
                "n_lf_violations_all_constraints": sum(bool(level["lf_violates_limit"]) for level in compared),
                "n_lf_violations_independently_rederived": sum(
                    bool(level["lf_violates_limit"]) and bool(level["independently_rederived_after_lf"])
                    for level in compared
                ),
                "n_binding_mismatches_all_constraints": sum(str(level["binding_relation"]) == "different" for level in compared),
                "n_binding_mismatches_independently_rederived": sum(
                    str(level["binding_relation"]) == "different" and bool(level["independently_rederived_after_lf"])
                    for level in compared
                ),
                "max_lf_violation_all_constraints": max((safe_float(level["lf_violation"]) for level in compared), default=math.nan),
                "max_lf_violation_independently_rederived": max(
                    (
                        safe_float(level["lf_violation"])
                        for level in compared
                        if bool(level["independently_rederived_after_lf"])
                    ),
                    default=math.nan,
                ),
                "error": "",
            }
            summary_rows.append(summary_base)
            variable_out.extend(variable_rows(base, case, endpoint, solved))
            level_out.extend(compared)
            binding_out.extend(binding_summary_rows(base, compared))
            violation_out.extend(
                {
                    **base,
                    **level,
                }
                for level in compared
                if bool(level["lf_violates_limit"])
            )
        except Exception as exc:  # noqa: BLE001 - keep sweeping across scenarios
            summary_rows.append(
                {
                    **base,
                    "lf_status": "ERROR",
                    "lf_converged": False,
                    "lf_elapsed_sec": math.nan,
                    "lf_iteration_count": math.nan,
                    "lf_slack_bus_id": "",
                    "lf_slack_active_power_mismatch_mw": math.nan,
                    "used_provider_keyword": False,
                    "bus_update_success": False,
                    "n_applied_bus_vm": 0,
                    "n_applied_bus_va": 0,
                    "n_applied_gen_pg": 0,
                    "n_applied_gen_qg": 0,
                    "n_applied_gen_target_v": 0,
                    "max_abs_pg_change_endpoint_to_lf": math.nan,
                    "max_abs_qg_change_endpoint_to_lf": math.nan,
                    "max_abs_vm_change_endpoint_to_lf": math.nan,
                    "max_abs_va_change_endpoint_to_lf_aligned": math.nan,
                    "max_abs_pf_change_endpoint_to_lf": math.nan,
                    "max_abs_qf_change_endpoint_to_lf": math.nan,
                    "n_lf_violations_all_constraints": math.nan,
                    "n_lf_violations_independently_rederived": math.nan,
                    "n_binding_mismatches_all_constraints": math.nan,
                    "n_binding_mismatches_independently_rederived": math.nan,
                    "max_lf_violation_all_constraints": math.nan,
                    "max_lf_violation_independently_rederived": math.nan,
                    "error": str(exc),
                }
            )

    return summary_rows, variable_out, level_out, binding_out, violation_out


def write_frame(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(disk_path(path), index=False)


def copy_outputs(paths: list[Path]) -> None:
    ORGANIZED_ROOT.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if path.exists():
            shutil.copy2(disk_path(path), ORGANIZED_ROOT / path.name)


def write_readme() -> None:
    text = """# Python Endpoint Load-Flow Validation With PyPowSyBl

Generated by `python/own_models/scripts/38_run_python_opf_endpoint_pypowsybl_loadflow_validation.py`.

Workflow:

1. read Python OPF endpoint tables from `results/own_vs_powermodels/python` and/or `results/own_vs_powermodels/python_pm_like`;
2. serialize the matching scenario JSON/PowerCase to a temporary MATPOWER `.mat` file and import it with PyPowSyBl;
3. warm-start/configure OpenLoadFlow once from the Python OPF endpoint and once from the PowerModels OPF endpoint;
4. evaluate generator, voltage, branch thermal, and angle inequality levels after LF;
5. compare those levels against the PowerModels OPF inequality levels.

OpenLoadFlow configuration:

- `voltage_init_mode = PREVIOUS_VALUES`;
- `distributed_slack = false`;
- `read_slack_bus = true`;
- `write_slack_bus = true`;
- `use_reactive_limits = true`;
- transformer, phase-shifter, and shunt controls off;
- `acSolverType = NEWTON_RAPHSON`;
- `maxNewtonRaphsonIterations = 800` by default.

Important interpretation:

- Pg limit rows are dispatch/slack-balance checks, not fully independent LF evidence.
- Generator, PV, and slack bus voltage magnitudes are setpoint-like in LF.
- PQ-bus voltage magnitudes, Qg, branch thermal levels, and angle levels are re-derived after OpenLoadFlow.
- Use `independently_rederived_after_lf` and `independence_note` before turning a zero-violation headline into a modelling claim.
"""
    README_PATH.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Python OPF endpoint LF validation through PyPowSyBl/OpenLoadFlow.")
    parser.add_argument("--model-variants", nargs="*", default=["current", "pm_like"], choices=sorted(VARIANTS))
    parser.add_argument("--endpoint-sources", nargs="*", default=["own", "pm"], choices=["own", "pm"])
    parser.add_argument("--cases", nargs="*", default=None)
    parser.add_argument("--scenarios", nargs="*", default=None)
    parser.add_argument("--all-solved", action="store_true", help="Run every solved scenario instead of material mismatch scenarios only.")
    parser.add_argument("--max-targets", type=int, default=None)
    parser.add_argument("--provider", default="OpenLoadFlow")
    parser.add_argument("--max-lf-iterations", type=int, default=800)
    parser.add_argument("--binding-tol", type=float, default=1e-5)
    parser.add_argument("--limit-violation-tol", type=float, default=1e-7)
    parser.add_argument("--objective-abs-tol", type=float, default=1e-4)
    parser.add_argument("--objective-rel-tol", type=float, default=1e-6)
    parser.add_argument("--pg-tol", type=float, default=1e-4)
    parser.add_argument("--qg-tol", type=float, default=1e-4)
    parser.add_argument("--vm-tol", type=float, default=1e-5)
    parser.add_argument("--va-tol", type=float, default=1e-5)
    parser.add_argument("--flow-tol", type=float, default=1e-4)
    parser.add_argument("--no-copy-organized", action="store_true")
    args = parser.parse_args()

    all_summary_rows: list[dict[str, object]] = []
    all_variable_rows: list[dict[str, object]] = []
    all_level_rows: list[dict[str, object]] = []
    all_binding_rows: list[dict[str, object]] = []
    all_violation_rows: list[dict[str, object]] = []

    for variant_key in args.model_variants:
        config = VARIANTS[variant_key]
        targets = select_targets(config, args)
        print(f"{config.label}: selected {len(targets)} target scenarios", flush=True)
        for row in targets:
            print(f"  {row['case']} / {row['scenario']}", flush=True)
            summary_rows, variable_rows_out, level_rows, binding_rows_out, violation_rows_out = run_one(config, row, args)
            all_summary_rows.extend(summary_rows)
            all_variable_rows.extend(variable_rows_out)
            all_level_rows.extend(level_rows)
            all_binding_rows.extend(binding_rows_out)
            all_violation_rows.extend(violation_rows_out)

    outputs = [
        SUMMARY_PATH,
        VARIABLES_PATH,
        LEVELS_PATH,
        BINDING_SUMMARY_PATH,
        VIOLATIONS_PATH,
        README_PATH,
    ]
    write_frame(SUMMARY_PATH, all_summary_rows)
    write_frame(VARIABLES_PATH, all_variable_rows)
    write_frame(LEVELS_PATH, all_level_rows)
    write_frame(BINDING_SUMMARY_PATH, all_binding_rows)
    write_frame(VIOLATIONS_PATH, all_violation_rows)
    write_readme()
    if not args.no_copy_organized:
        copy_outputs(outputs)

    print(f"Wrote {SUMMARY_PATH}")
    print(f"Wrote {VARIABLES_PATH}")
    print(f"Wrote {LEVELS_PATH}")
    print(f"Wrote {BINDING_SUMMARY_PATH}")
    print(f"Wrote {VIOLATIONS_PATH}")


if __name__ == "__main__":
    main()
