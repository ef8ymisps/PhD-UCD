from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from own_models.src.adapters.powermodels_csv_adapter import load_powercase_from_powermodels_json
from own_models.src.analysis.balancing_policy import apply_balancing_policy
from own_models.src.analysis.contingencies import apply_contingency, generate_generator_contingencies
from own_models.src.analysis.post_contingency import case_with_base_dispatch
from own_models.src.analysis.topology import island_diagnostics
from own_models.src.data.case import PQ, PowerCase
from own_models.src.opf.ac import solve_ac_opf_pyomo


VOLTAGE_TOLERANCE_KV = 1e-6
TAP_TOLERANCE = 1e-8
SHIFT_TOLERANCE = 1e-10
VM_TOL_PU = 1e-4
VA_TOL_DEG = 1e-2
FLOW_TOL_MVA = 1e-2
PG_TOL_MW = 1e-3


class PyPowSyBlSecurityValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PyPowSyBlBuildResult:
    network: Any
    branch_map: pd.DataFrame
    unit_validation: pd.DataFrame
    voltage_limit_validation: pd.DataFrame
    branch_limit_validation: pd.DataFrame


def _require_pypowsybl():
    try:
        import pypowsybl.loadflow as lf
        import pypowsybl.network as pn
        import pypowsybl.security as security
    except ImportError as exc:
        raise PyPowSyBlSecurityValidationError(
            "pypowsybl is required for PyPowSyBl security validation."
        ) from exc
    return SimpleNamespace(lf=lf, pn=pn, security=security)


def _z_base(bus_nominal_kv: float, base_mva: float) -> float:
    if bus_nominal_kv <= 0.0:
        raise PyPowSyBlSecurityValidationError("Nominal voltage must be positive.")
    return bus_nominal_kv**2 / base_mva


def _pu_to_ohm(value_pu: float, bus_nominal_kv: float, base_mva: float) -> float:
    return value_pu * _z_base(bus_nominal_kv, base_mva)


def _pu_to_siemens(value_pu: float, bus_nominal_kv: float, base_mva: float) -> float:
    return value_pu / _z_base(bus_nominal_kv, base_mva)


def _branch_equipment_type(case: PowerCase, branch) -> str:
    f = case.buses[case.bus_id_to_index[branch.from_bus]]
    t = case.buses[case.bus_id_to_index[branch.to_bus]]
    is_transformer = (
        abs(f.nominal_v_kv - t.nominal_v_kv) > VOLTAGE_TOLERANCE_KV
        or abs(branch.tap - 1.0) > TAP_TOLERANCE
        or abs(branch.shift) > SHIFT_TOLERANCE
    )
    return "two_windings_transformer" if is_transformer else "line"


def _vl_id(bus_id: str) -> str:
    return f"VL::{bus_id}"


def _pp_bus_id(bus_id: str) -> str:
    return f"bus::{bus_id}"


def _pp_gen_id(gen_id: str) -> str:
    return f"gen::{gen_id}"


def _pp_load_id(load_id: str) -> str:
    return f"load::{load_id}"


def _pp_shunt_id(shunt_id: str) -> str:
    return f"shunt::{shunt_id}"


def _substation_id(case: PowerCase) -> str:
    return f"SUB_{case.name}"


def _mw(value_pu: float, base_mva: float) -> float:
    return value_pu * base_mva


def _bus_by_id(case: PowerCase) -> dict[str, Any]:
    return {bus.id: bus for bus in case.buses}


def _active_generator_buses(case: PowerCase) -> set[str]:
    return {gen.bus for gen in case.generators if gen.status == 1}


def _bus_regulates_voltage(case: PowerCase, bus_id: str) -> bool:
    bus = case.buses[case.bus_id_to_index[bus_id]]
    return bus.bus_type != PQ and bus_id in _active_generator_buses(case)


def _dataframe_from_records(records: list[dict], *, index: str = "id") -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame.from_records(records)
    for col in df.columns:
        if col.endswith("_id") or col in {"id", "element_id", "side", "name", "type", "group_name"}:
            df[col] = df[col].astype(str)
    return df.set_index(index, drop=True)


def _pypowsybl_dataframe(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame.from_records(records)
    for col in df.columns:
        if col.endswith("_id") or col in {"id", "element_id", "side", "name", "type", "group_name"}:
            df[col] = df[col].astype(str)
    return df


def _create_network_skeleton(case: PowerCase):
    pp = _require_pypowsybl()
    network = pp.pn.create_empty()
    sub_id = _substation_id(case)
    network.create_substations(id=[sub_id], name=[sub_id])
    network.create_voltage_levels(
        id=[_vl_id(bus.id) for bus in case.buses],
        substation_id=[sub_id for _ in case.buses],
        topology_kind=["BUS_BREAKER" for _ in case.buses],
        nominal_v=[bus.nominal_v_kv for bus in case.buses],
        low_voltage_limit=[bus.vmin * bus.nominal_v_kv for bus in case.buses],
        high_voltage_limit=[bus.vmax * bus.nominal_v_kv for bus in case.buses],
    )
    network.create_buses(
        id=[_pp_bus_id(bus.id) for bus in case.buses],
        voltage_level_id=[_vl_id(bus.id) for bus in case.buses],
        name=[bus.id for bus in case.buses],
    )
    return network


def _create_branches(network, case: PowerCase) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    buses = _bus_by_id(case)
    line_records: list[dict] = []
    transformer_records: list[dict] = []
    limit_records: list[dict] = []
    map_rows: list[dict] = []
    unit_rows: list[dict] = []
    limit_rows: list[dict] = []

    for branch in case.branches:
        f = buses[branch.from_bus]
        t = buses[branch.to_bus]
        equipment_type = _branch_equipment_type(case, branch)
        contingency_id = f"outage::{branch.id}"
        status = branch.status == 1
        rate_mva = _mw(branch.rate_a, case.base_mva) if branch.rate_a > 0.0 else math.nan

        if equipment_type == "line":
            r_ohm = _pu_to_ohm(branch.r, f.nominal_v_kv, case.base_mva)
            x_ohm = _pu_to_ohm(branch.x, f.nominal_v_kv, case.base_mva)
            g1 = _pu_to_siemens(branch.g_fr, f.nominal_v_kv, case.base_mva)
            b1 = _pu_to_siemens(branch.b_fr, f.nominal_v_kv, case.base_mva)
            g2 = _pu_to_siemens(branch.g_to, t.nominal_v_kv, case.base_mva)
            b2 = _pu_to_siemens(branch.b_to, t.nominal_v_kv, case.base_mva)
            line_records.append(
                {
                    "id": branch.id,
                    "voltage_level1_id": _vl_id(f.id),
                    "bus1_id": _pp_bus_id(f.id),
                    "connectable_bus1_id": _pp_bus_id(f.id),
                    "voltage_level2_id": _vl_id(t.id),
                    "bus2_id": _pp_bus_id(t.id),
                    "connectable_bus2_id": _pp_bus_id(t.id),
                    "r": r_ohm,
                    "x": x_ohm,
                    "g1": g1,
                    "b1": b1,
                    "g2": g2,
                    "b2": b2,
                }
            )
        else:
            if abs(branch.shift) > SHIFT_TOLERANCE:
                raise PyPowSyBlSecurityValidationError(
                    f"Branch {branch.id} has non-zero phase shift; v1 builder cannot represent it faithfully."
                )
            r_ohm = _pu_to_ohm(branch.r, t.nominal_v_kv, case.base_mva)
            x_ohm = _pu_to_ohm(branch.x, t.nominal_v_kv, case.base_mva)
            g = _pu_to_siemens(branch.g_fr, t.nominal_v_kv, case.base_mva)
            b = _pu_to_siemens(branch.b_fr, t.nominal_v_kv, case.base_mva)
            transformer_records.append(
                {
                    "id": branch.id,
                    "voltage_level1_id": _vl_id(f.id),
                    "bus1_id": _pp_bus_id(f.id),
                    "connectable_bus1_id": _pp_bus_id(f.id),
                    "voltage_level2_id": _vl_id(t.id),
                    "bus2_id": _pp_bus_id(t.id),
                    "connectable_bus2_id": _pp_bus_id(t.id),
                    "rated_u1": branch.tap * f.nominal_v_kv,
                    "rated_u2": t.nominal_v_kv,
                    "rated_s": _mw(branch.rate_a, case.base_mva) if branch.rate_a > 0.0 else case.base_mva,
                    "r": r_ohm,
                    "x": x_ohm,
                    "g": g,
                    "b": b,
                }
            )

        if branch.rate_a > 0.0:
            for side in ("ONE", "TWO"):
                limit_records.append(
                    {
                        "element_id": branch.id,
                        "side": side,
                        "name": "RATE_A",
                        "type": "APPARENT_POWER",
                        "value": rate_mva,
                        "acceptable_duration": -1,
                        "fictitious": False,
                        "group_name": "DEFAULT",
                    }
                )

        map_rows.append(
            {
                "case_name": case.name,
                "own_branch_id": branch.id,
                "pypowsybl_equipment_id": branch.id,
                "contingency_id": contingency_id,
                "equipment_type": equipment_type,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "pypowsybl_from_bus_id": _pp_bus_id(branch.from_bus),
                "pypowsybl_to_bus_id": _pp_bus_id(branch.to_bus),
                "status": branch.status,
                "represented": True,
                "id_note": "branch_id_preserved;non_branch_equipment_ids_namespaced_for_iidm_uniqueness",
            }
        )
        unit_rows.append(
            {
                "case_name": case.name,
                "bus_id": f.id,
                "nominal_voltage_kv": f.nominal_v_kv,
                "branch_id": branch.id,
                "equipment_type": equipment_type,
                "r_pu": branch.r,
                "x_pu": branch.x,
                "r_ohm": r_ohm,
                "x_ohm": x_ohm,
                "tap": branch.tap,
                "phase_shift": branch.shift,
            }
        )
        limit_rows.append(
            {
                "case_name": case.name,
                "branch_id": branch.id,
                "equipment_type": equipment_type,
                "rate_a_pu": branch.rate_a,
                "rate_a_mva": rate_mva,
                "side_one_limit_present": branch.rate_a > 0.0,
                "side_two_limit_present": branch.rate_a > 0.0,
            }
        )

    if line_records:
        network.create_lines(_dataframe_from_records(line_records))
    if transformer_records:
        network.create_2_windings_transformers(_dataframe_from_records(transformer_records))
    if limit_records:
        network.create_operational_limits(
            element_id=[str(row["element_id"]) for row in limit_records],
            side=[str(row["side"]) for row in limit_records],
            name=[str(row["name"]) for row in limit_records],
            type=[str(row["type"]) for row in limit_records],
            value=[float(row["value"]) for row in limit_records],
            acceptable_duration=[int(row["acceptable_duration"]) for row in limit_records],
            fictitious=[bool(row["fictitious"]) for row in limit_records],
            group_name=[str(row["group_name"]) for row in limit_records],
        )

    for branch in case.branches:
        if branch.status == 0:
            if _branch_equipment_type(case, branch) == "line":
                network.update_lines(id=branch.id, connected1=False, connected2=False)
            else:
                network.update_2_windings_transformers(id=branch.id, connected1=False, connected2=False)

    return pd.DataFrame(map_rows), pd.DataFrame(unit_rows), pd.DataFrame(limit_rows)


def _create_injections(network, case: PowerCase) -> None:
    buses = _bus_by_id(case)

    generators = [gen for gen in case.generators if gen.status == 1]
    if generators:
        network.create_generators(
            id=[_pp_gen_id(gen.id) for gen in generators],
            voltage_level_id=[_vl_id(gen.bus) for gen in generators],
            bus_id=[_pp_bus_id(gen.bus) for gen in generators],
            connectable_bus_id=[_pp_bus_id(gen.bus) for gen in generators],
            energy_source=["OTHER" for _ in generators],
            condenser=[False for _ in generators],
            min_p=[_mw(gen.pmin, case.base_mva) for gen in generators],
            max_p=[_mw(gen.pmax, case.base_mva) for gen in generators],
            target_p=[_mw(gen.pg0, case.base_mva) for gen in generators],
            target_q=[_mw(gen.qg0, case.base_mva) for gen in generators],
            target_v=[buses[gen.bus].vm0 * buses[gen.bus].nominal_v_kv for gen in generators],
            voltage_regulator_on=[_bus_regulates_voltage(case, gen.bus) for gen in generators],
            rated_s=[max(abs(_mw(gen.pmax, case.base_mva)), case.base_mva) for gen in generators],
        )
        network.create_minmax_reactive_limits(
            id=[_pp_gen_id(gen.id) for gen in generators],
            min_q=[_mw(gen.qmin, case.base_mva) for gen in generators],
            max_q=[_mw(gen.qmax, case.base_mva) for gen in generators],
        )

    loads = [load for load in case.loads if load.status == 1]
    if loads:
        network.create_loads(
            id=[_pp_load_id(load.id) for load in loads],
            voltage_level_id=[_vl_id(load.bus) for load in loads],
            bus_id=[_pp_bus_id(load.bus) for load in loads],
            connectable_bus_id=[_pp_bus_id(load.bus) for load in loads],
            p0=[_mw(load.pd, case.base_mva) for load in loads],
            q0=[_mw(load.qd, case.base_mva) for load in loads],
        )

    shunts = [shunt for shunt in case.shunts if shunt.status == 1]
    if shunts:
        shunt_df = pd.DataFrame.from_records(
            [
                {
                    "id": _pp_shunt_id(shunt.id),
                    "voltage_level_id": _vl_id(shunt.bus),
                    "bus_id": _pp_bus_id(shunt.bus),
                    "connectable_bus_id": _pp_bus_id(shunt.bus),
                    "model_type": "LINEAR",
                    "section_count": 1,
                }
                for shunt in shunts
            ]
        ).set_index("id")
        model_df = pd.DataFrame.from_records(
            [
                {
                    "id": _pp_shunt_id(shunt.id),
                    "g_per_section": _pu_to_siemens(
                        shunt.gs,
                        buses[shunt.bus].nominal_v_kv,
                        case.base_mva,
                    ),
                    "b_per_section": _pu_to_siemens(
                        shunt.bs,
                        buses[shunt.bus].nominal_v_kv,
                        case.base_mva,
                    ),
                    "max_section_count": 1,
                }
                for shunt in shunts
            ]
        ).set_index("id")
        network.create_shunt_compensators(shunt_df, model_df)


def _set_slack_terminal(network, case: PowerCase) -> str:
    slack_gens = [
        gen for gen in case.generators if gen.status == 1 and gen.bus == case.slack_bus_id
    ]
    if not slack_gens:
        raise PyPowSyBlSecurityValidationError(
            f"No active generator is connected to slack bus {case.slack_bus_id}."
        )
    slack_gen = sorted(slack_gens, key=lambda gen: (gen.index, gen.id))[0]
    slack_df = pd.DataFrame.from_records(
        [{"voltage_level_id": _vl_id(case.slack_bus_id), "element_id": _pp_gen_id(slack_gen.id)}]
    ).set_index("voltage_level_id")
    network.create_extensions("slackTerminal", slack_df)
    ext = network.get_extension("slackTerminal")
    if ext.empty or str(ext.iloc[0]["element_id"]) != _pp_gen_id(slack_gen.id):
        raise PyPowSyBlSecurityValidationError("Failed to store requested slack bus in PyPowSyBl network.")
    return slack_gen.id


def build_pypowsybl_network_from_powercase(case: PowerCase) -> PyPowSyBlBuildResult:
    network = _create_network_skeleton(case)
    branch_map, unit_validation, branch_limit_validation = _create_branches(network, case)
    _create_injections(network, case)
    _set_slack_terminal(network, case)

    voltage_rows = []
    voltage_levels = network.get_voltage_levels().reset_index()
    by_id = voltage_levels.set_index("id")
    for bus in case.buses:
        vl = by_id.loc[_vl_id(bus.id)]
        low = float(vl["low_voltage_limit"])
        high = float(vl["high_voltage_limit"])
        voltage_rows.append(
            {
                "case_name": case.name,
                "bus_id": bus.id,
                "own_vmin_pu": bus.vmin,
                "own_vmax_pu": bus.vmax,
                "pypowsybl_low_limit_kv": low,
                "pypowsybl_high_limit_kv": high,
                "converted_low_limit_pu": low / bus.nominal_v_kv,
                "converted_high_limit_pu": high / bus.nominal_v_kv,
            }
        )

    return PyPowSyBlBuildResult(
        network=network,
        branch_map=branch_map,
        unit_validation=unit_validation,
        voltage_limit_validation=pd.DataFrame(voltage_rows),
        branch_limit_validation=branch_limit_validation,
    )


def transfer_ac_opf_dispatch(network, case: PowerCase, ac_opf_result) -> None:
    buses = _bus_by_id(case)
    generators = [gen for gen in case.generators if gen.status == 1]
    network.update_generators(
        id=[_pp_gen_id(gen.id) for gen in generators],
        target_p=[_mw(ac_opf_result.pg.get(gen.id, gen.pg0), case.base_mva) for gen in generators],
        target_q=[_mw(ac_opf_result.qg.get(gen.id, gen.qg0), case.base_mva) for gen in generators],
        target_v=[
            ac_opf_result.vm.get(gen.bus, buses[gen.bus].vm0) * buses[gen.bus].nominal_v_kv
            for gen in generators
        ],
        voltage_regulator_on=[_bus_regulates_voltage(case, gen.bus) for gen in generators],
    )
    try:
        network.update_buses(
            id=[_pp_bus_id(bus.id) for bus in case.buses],
            v_mag=[ac_opf_result.vm.get(bus.id, bus.vm0) * bus.nominal_v_kv for bus in case.buses],
            v_angle=[math.degrees(ac_opf_result.va.get(bus.id, bus.va0)) for bus in case.buses],
        )
    except Exception:
        # Some PyPowSyBl topologies expose computed buses only after a load-flow
        # initialization pass. The generator targets are the mandatory dispatch
        # transfer; bus values are an initial guess and are checked after LF.
        pass


def transfer_case_dispatch(network, case: PowerCase) -> None:
    """Push a PowerCase's own generator pg0/qg0 into a pypowsybl network.

    Unlike transfer_ac_opf_dispatch (which reads Pg/Qg from a separate
    ac_opf_result and only falls back to case.pg0 for generators missing from
    that result -- correct for a plain base-case transfer, where case.pg0 is
    just the raw input file's starting guess), this is for cases whose own
    pg0/qg0 fields ARE the authoritative dispatch to use, e.g. a
    balancing_policy-redispatched case, where the redispatched values live in
    case.generators[i].pg0/qg0 and ac_opf_result would still hold the stale
    pre-contingency dispatch.
    """
    buses = _bus_by_id(case)
    generators = [gen for gen in case.generators if gen.status == 1]
    network.update_generators(
        id=[_pp_gen_id(gen.id) for gen in generators],
        target_p=[_mw(gen.pg0, case.base_mva) for gen in generators],
        target_q=[_mw(gen.qg0, case.base_mva) for gen in generators],
        target_v=[buses[gen.bus].vm0 * buses[gen.bus].nominal_v_kv for gen in generators],
        voltage_regulator_on=[_bus_regulates_voltage(case, gen.bus) for gen in generators],
    )


def _loadflow_parameters():
    pp = _require_pypowsybl()
    return pp.lf.Parameters(
        distributed_slack=False,
        read_slack_bus=True,
        write_slack_bus=True,
        use_reactive_limits=True,
        transformer_voltage_control_on=False,
        phase_shifter_regulation_on=False,
        shunt_compensator_voltage_control_on=False,
    )


def _status_text(result) -> str:
    if isinstance(result, list):
        if not result:
            return "UNKNOWN"
        statuses = [str(getattr(item, "status", "UNKNOWN")) for item in result]
        return ";".join(statuses)
    return str(getattr(result, "status", result))


def _lf_converged(status: str) -> bool:
    return "CONVERGED" in status.upper()


def _solved_slack_bus(result) -> str:
    values = result if isinstance(result, list) else [result]
    for item in values:
        slack_results = getattr(item, "slack_bus_results", None)
        if slack_results:
            return str(slack_results[0].id)
    return ""


def _combined_branch_table(network) -> pd.DataFrame:
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
    return pd.concat(frames, ignore_index=True).set_index("id", drop=False)


def _compare_base_state(case: PowerCase, network, ac_opf_result, lf_result) -> pd.DataFrame:
    status = _status_text(lf_result)
    solved_slack = _solved_slack_bus(lf_result)
    buses_pp = network.get_buses().reset_index()
    buses_by_vl = buses_pp.set_index("voltage_level_id")
    gens_pp = network.get_generators().reset_index().set_index("id")
    branches_pp = _combined_branch_table(network)
    rows = []

    for bus in case.buses:
        pp_bus = buses_by_vl.loc[_vl_id(bus.id)]
        if isinstance(pp_bus, pd.DataFrame):
            pp_bus = pp_bus.iloc[0]
        rows.append(
            {
                "case_name": case.name,
                "component_type": "bus",
                "component_id": bus.id,
                "metric": "vm_pu",
                "own_value": ac_opf_result.vm[bus.id],
                "pypowsybl_value": float(pp_bus["v_mag"]) / bus.nominal_v_kv,
                "diff": ac_opf_result.vm[bus.id] - float(pp_bus["v_mag"]) / bus.nominal_v_kv,
            }
        )
        rows.append(
            {
                "case_name": case.name,
                "component_type": "bus",
                "component_id": bus.id,
                "metric": "va_deg",
                "own_value": math.degrees(ac_opf_result.va[bus.id]),
                "pypowsybl_value": float(pp_bus["v_angle"]),
                "diff": math.degrees(ac_opf_result.va[bus.id]) - float(pp_bus["v_angle"]),
            }
        )

    for gen in case.generators:
        pp_gen_id = _pp_gen_id(gen.id)
        if gen.status != 1 or pp_gen_id not in gens_pp.index:
            continue
        pp_gen = gens_pp.loc[pp_gen_id]
        pp_generation = -float(pp_gen["p"])
        rows.append(
            {
                "case_name": case.name,
                "component_type": "generator",
                "component_id": gen.id,
                "metric": "pg_mw",
                "own_value": _mw(ac_opf_result.pg[gen.id], case.base_mva),
                "pypowsybl_value": pp_generation,
                "diff": _mw(ac_opf_result.pg[gen.id], case.base_mva) - pp_generation,
            }
        )

    for branch in case.branches:
        if branch.status != 1 or branch.id not in branches_pp.index:
            continue
        pp_branch = branches_pp.loc[branch.id]
        own = ac_opf_result.branch_flows[branch.id]
        for own_key, pp_key in (("pf", "p1"), ("qf", "q1"), ("pt", "p2"), ("qt", "q2")):
            own_value = _mw(own[own_key], case.base_mva)
            pp_value = float(pp_branch[pp_key])
            rows.append(
                {
                    "case_name": case.name,
                    "component_type": "branch",
                    "component_id": branch.id,
                    "metric": f"{own_key}_mva_component",
                    "own_value": own_value,
                    "pypowsybl_value": pp_value,
                    "diff": own_value - pp_value,
                }
            )

    frame = pd.DataFrame(rows)
    frame["base_lf_status"] = status
    frame["base_lf_converged"] = _lf_converged(status)
    frame["requested_slack_bus"] = _pp_bus_id(case.slack_bus_id)
    frame["solved_slack_bus"] = solved_slack
    if solved_slack in set(buses_pp["id"].astype(str)):
        solved_slack_vl = str(buses_pp.set_index("id").loc[solved_slack]["voltage_level_id"])
    else:
        solved_slack_vl = ""
    frame["solved_slack_voltage_level_id"] = solved_slack_vl
    frame["slack_bus_match"] = solved_slack_vl == _vl_id(case.slack_bus_id)
    return frame


def _base_gate(base_comparison: pd.DataFrame, case: PowerCase) -> dict[str, Any]:
    if base_comparison.empty:
        return {
            "base_lf_converged": False,
            "slack_bus_match": False,
            "non_slack_pg_match": False,
            "max_vm_diff": math.inf,
            "max_va_diff": math.inf,
            "max_branch_flow_diff": math.inf,
            "base_state_valid": False,
        }

    bus_vm = base_comparison[
        (base_comparison["component_type"] == "bus") & (base_comparison["metric"] == "vm_pu")
    ]
    bus_va = base_comparison[
        (base_comparison["component_type"] == "bus") & (base_comparison["metric"] == "va_deg")
    ]
    branch = base_comparison[base_comparison["component_type"] == "branch"]
    gen = base_comparison[base_comparison["component_type"] == "generator"].copy()
    slack_gen_ids = {
        g.id for g in case.generators if g.status == 1 and g.bus == case.slack_bus_id
    }
    non_slack_gen = gen[~gen["component_id"].isin(slack_gen_ids)]

    max_vm = float(bus_vm["diff"].abs().max()) if not bus_vm.empty else math.inf
    max_va = float(bus_va["diff"].abs().max()) if not bus_va.empty else math.inf
    max_flow = float(branch["diff"].abs().max()) if not branch.empty else math.inf
    max_non_slack_pg = (
        float(non_slack_gen["diff"].abs().max()) if not non_slack_gen.empty else 0.0
    )
    base_lf_converged = bool(base_comparison["base_lf_converged"].iloc[0])
    slack_bus_match = bool(base_comparison["slack_bus_match"].iloc[0])
    non_slack_pg_match = max_non_slack_pg <= PG_TOL_MW
    base_state_valid = (
        base_lf_converged
        and slack_bus_match
        and non_slack_pg_match
        and max_vm <= VM_TOL_PU
        and max_va <= VA_TOL_DEG
        and max_flow <= FLOW_TOL_MVA
    )
    return {
        "base_lf_converged": base_lf_converged,
        "slack_bus_match": slack_bus_match,
        "non_slack_pg_match": non_slack_pg_match,
        "max_vm_diff": max_vm,
        "max_va_diff": max_va,
        "max_branch_flow_diff": max_flow,
        "base_state_valid": base_state_valid,
    }


def _native_secure(post_result, native_violations: pd.DataFrame) -> bool:
    status = str(getattr(post_result, "status", "UNKNOWN"))
    contingency_id = str(getattr(post_result, "contingency_id", ""))
    if not _lf_converged(status):
        return False
    if native_violations.empty:
        return True
    if "contingency_id" in native_violations.columns:
        return native_violations[native_violations["contingency_id"].astype(str) == contingency_id].empty
    return True


def _security_result_frame(result_attr) -> pd.DataFrame:
    if isinstance(result_attr, pd.DataFrame):
        return result_attr.reset_index()
    return pd.DataFrame()


def _run_pypowsybl_security(network, branch_map: pd.DataFrame):
    pp = _require_pypowsybl()
    analysis = pp.security.create_analysis()
    active_map = branch_map[branch_map["status"] == 1]
    equipment_ids = active_map["pypowsybl_equipment_id"].astype(str).tolist()
    contingency_ids = {
        row["pypowsybl_equipment_id"]: row["contingency_id"]
        for _, row in active_map.iterrows()
    }
    for equipment_id in equipment_ids:
        analysis.add_single_element_contingency(
            equipment_id,
            contingency_id=contingency_ids[equipment_id],
        )
    analysis.add_monitored_elements(
        branch_ids=equipment_ids,
        voltage_level_ids=network.get_voltage_levels().index.astype(str).tolist(),
    )
    result = analysis.run_ac(
        network,
        parameters=pp.security.Parameters(load_flow_parameters=_loadflow_parameters()),
    )
    return result


def _extract_security_tables(case: PowerCase, security_result, branch_map: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    native_violations = _security_result_frame(security_result.limit_violations)
    branch_results = _security_result_frame(security_result.branch_results)
    voltage_results = _security_result_frame(security_result.bus_results)

    summary_rows = []
    for contingency_id, post in security_result.post_contingency_results.items():
        summary_rows.append(
            {
                "case_name": case.name,
                "contingency_id": str(contingency_id),
                "pypowsybl_status": str(getattr(post, "status", "UNKNOWN")),
                "pypowsybl_converged": _lf_converged(str(getattr(post, "status", "UNKNOWN"))),
                "pypowsybl_native_secure": _native_secure(post, native_violations),
            }
        )
    summary = pd.DataFrame(summary_rows)
    if not branch_results.empty:
        branch_results["case_name"] = case.name
    if not voltage_results.empty:
        voltage_results["case_name"] = case.name
    return summary, branch_results, voltage_results


def _contingency_column(df: pd.DataFrame) -> str | None:
    for name in ("contingency_id", "contingency", "Contingency"):
        if name in df.columns:
            return name
    return None


def _id_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if name in df.columns:
            return name
    return None


def _harmonized_rows(
    case: PowerCase,
    branch_map: pd.DataFrame,
    security_summary: pd.DataFrame,
    branch_results: pd.DataFrame,
    voltage_results: pd.DataFrame,
) -> pd.DataFrame:
    buses = _bus_by_id(case)
    branches = {branch.id: branch for branch in case.branches}
    rows = []
    branch_cont_col = _contingency_column(branch_results)
    volt_cont_col = _contingency_column(voltage_results)
    branch_id_col = _id_column(branch_results, ("branch_id", "id", "element_id"))
    voltage_id_col = _id_column(voltage_results, ("voltage_level_id", "id", "bus_id"))

    for _, sec in security_summary.iterrows():
        contingency_id = str(sec["contingency_id"])
        map_row = branch_map.loc[branch_map["contingency_id"] == contingency_id]
        if map_row.empty:
            continue
        own_branch_id = str(map_row.iloc[0]["own_branch_id"])

        bframe = branch_results
        if branch_cont_col is not None and not branch_results.empty:
            bframe = branch_results[branch_results[branch_cont_col].astype(str) == contingency_id]
        vframe = voltage_results
        if volt_cont_col is not None and not voltage_results.empty:
            vframe = voltage_results[voltage_results[volt_cont_col].astype(str) == contingency_id]

        max_loading = math.nan
        thermal_violation = False
        if not bframe.empty and branch_id_col is not None:
            for _, br_result in bframe.iterrows():
                branch_id = str(br_result[branch_id_col])
                if branch_id not in branches:
                    continue
                branch = branches[branch_id]
                if branch.rate_a <= 0.0:
                    continue
                s1 = math.hypot(float(br_result.get("p1", math.nan)), float(br_result.get("q1", math.nan)))
                s2 = math.hypot(float(br_result.get("p2", math.nan)), float(br_result.get("q2", math.nan)))
                loading = 100.0 * max(s1, s2) / _mw(branch.rate_a, case.base_mva)
                max_loading = loading if math.isnan(max_loading) else max(max_loading, loading)
                thermal_violation = thermal_violation or loading > 100.0 + 1e-6

        min_vm = math.nan
        voltage_violation = False
        if not vframe.empty:
            for _, volt in vframe.iterrows():
                raw_id = str(volt[voltage_id_col]) if voltage_id_col is not None else ""
                bus_id = raw_id.removeprefix("VL::")
                if bus_id not in buses:
                    continue
                bus = buses[bus_id]
                value = float(volt.get("v_mag", math.nan))
                vm_pu = value / bus.nominal_v_kv if value > 2.5 else value
                min_vm = vm_pu if math.isnan(min_vm) else min(min_vm, vm_pu)
                voltage_violation = voltage_violation or vm_pu < bus.vmin - 1e-6 or vm_pu > bus.vmax + 1e-6

        harmonized_secure = bool(sec["pypowsybl_converged"]) and not voltage_violation and not thermal_violation
        rows.append(
            {
                "case_name": case.name,
                "contingency_id": contingency_id,
                "own_branch_id": own_branch_id,
                "pypowsybl_equipment_id": str(map_row.iloc[0]["pypowsybl_equipment_id"]),
                "equipment_type": str(map_row.iloc[0]["equipment_type"]),
                "pypowsybl_converged": bool(sec["pypowsybl_converged"]),
                "pypowsybl_native_secure": bool(sec["pypowsybl_native_secure"]),
                "pypowsybl_harmonized_secure": harmonized_secure,
                "pypowsybl_harmonized_voltage_violation": voltage_violation,
                "pypowsybl_harmonized_thermal_violation": thermal_violation,
                "pypowsybl_min_vm": min_vm,
                "pypowsybl_harmonized_max_loading_pct": max_loading,
                "pypowsybl_primary_label": "SECURE"
                if harmonized_secure
                else (
                    "PF_DID_NOT_CONVERGE"
                    if not bool(sec["pypowsybl_converged"])
                    else "VOLTAGE_VIOLATION"
                    if voltage_violation
                    else "BRANCH_THERMAL_VIOLATION"
                ),
            }
        )
    return pd.DataFrame(rows)


def _own_metric_columns(own: pd.DataFrame) -> pd.DataFrame:
    own = own.copy()
    own["contingency_id"] = "outage::" + own["outaged_branch_ids"].astype(str)
    own["own_min_vm"] = np.nan
    own["own_max_loading_pct"] = own["normalized_thermal_severity"].astype(float) * 100.0 + 100.0
    own.loc[own["max_thermal_violation"].astype(float) <= 0.0, "own_max_loading_pct"] = np.nan
    return own


def _join_comparison(
    case: PowerCase,
    own_results: pd.DataFrame,
    harmonized: pd.DataFrame,
    gate: dict[str, Any],
) -> pd.DataFrame:
    own = _own_metric_columns(own_results[own_results["case_name"] == case.name])
    joined = own.merge(harmonized, on="contingency_id", how="inner", suffixes=("_own_raw", ""))
    if joined.empty:
        return joined
    joined["case_name"] = case.name
    joined["base_state_valid"] = gate["base_state_valid"]
    joined["own_pf_converged"] = joined["pf_converged"].astype(bool)
    joined["convergence_match"] = joined["own_pf_converged"] == joined["pypowsybl_converged"]
    joined["own_voltage_violation"] = joined["has_voltage_violation"].astype(bool)
    joined["own_thermal_violation"] = joined["has_thermal_violation"].astype(bool)
    joined["harmonized_security_match"] = (
        joined["secure"].astype(bool) == joined["pypowsybl_harmonized_secure"].astype(bool)
    ) & bool(gate["base_state_valid"])
    joined["security_label_match"] = joined["harmonized_security_match"]
    joined["min_vm_diff"] = joined["own_min_vm"] - joined["pypowsybl_min_vm"]
    joined["max_loading_diff"] = joined["own_max_loading_pct"] - joined["pypowsybl_harmonized_max_loading_pct"]
    notes = []
    for _, row in joined.iterrows():
        row_notes = []
        if not gate["base_state_valid"]:
            row_notes.append("base_state_invalid_skipped")
        if row["has_angle_violation"]:
            row_notes.append("own_angle_limit_has_no_v1_pypowsybl_equivalent")
        if row["pypowsybl_native_secure"] != row["pypowsybl_harmonized_secure"]:
            row_notes.append("native_vs_harmonized_label_differs")
        notes.append(";".join(row_notes))
    joined["notes"] = notes
    return joined[
        [
            "case_name",
            "contingency_id",
            "own_branch_id",
            "pypowsybl_equipment_id",
            "equipment_type",
            "base_state_valid",
            "own_pf_converged",
            "pypowsybl_converged",
            "convergence_match",
            "secure",
            "pypowsybl_native_secure",
            "pypowsybl_harmonized_secure",
            "harmonized_security_match",
            "primary_label",
            "pypowsybl_primary_label",
            "own_voltage_violation",
            "pypowsybl_harmonized_voltage_violation",
            "own_thermal_violation",
            "pypowsybl_harmonized_thermal_violation",
            "own_min_vm",
            "pypowsybl_min_vm",
            "min_vm_diff",
            "own_max_loading_pct",
            "pypowsybl_harmonized_max_loading_pct",
            "max_loading_diff",
            "notes",
        ]
    ].rename(columns={"secure": "own_secure", "primary_label": "own_primary_label"})


def run_pypowsybl_branch_security_validation(
    *,
    reference_dir: Path,
    own_branch_results_path: Path,
    pypowsybl_output_dir: Path,
    comparison_output_path: Path,
) -> dict[str, Path]:
    pypowsybl_output_dir.mkdir(parents=True, exist_ok=True)
    comparison_output_path.parent.mkdir(parents=True, exist_ok=True)

    own_results = pd.read_csv(own_branch_results_path)
    all_topology = []
    all_units = []
    all_voltage_limits = []
    all_branch_limits = []
    all_base = []
    all_security_summary = []
    all_branch_flows = []
    all_voltage_results = []
    all_comparisons = []
    case_summary_rows = []

    for case_path in sorted(reference_dir.glob("*_base_case.json")):
        case = load_powercase_from_powermodels_json(case_path)
        ac_opf_result = solve_ac_opf_pyomo(case)
        build = build_pypowsybl_network_from_powercase(case)
        transfer_ac_opf_dispatch(build.network, case, ac_opf_result)

        pp = _require_pypowsybl()
        lf_result = pp.lf.run_ac(build.network, parameters=_loadflow_parameters())
        base_comparison = _compare_base_state(case, build.network, ac_opf_result, lf_result)
        gate = _base_gate(base_comparison, case)

        all_topology.append(build.branch_map)
        all_units.append(build.unit_validation)
        all_voltage_limits.append(build.voltage_limit_validation)
        all_branch_limits.append(build.branch_limit_validation)
        all_base.append(base_comparison)

        builder_valid = (
            bool(build.branch_map["represented"].all())
            and bool(build.unit_validation[["r_ohm", "x_ohm"]].replace([np.inf, -np.inf], np.nan).notna().all().all())
            and bool(build.branch_limit_validation["side_one_limit_present"].equals(build.branch_limit_validation["side_two_limit_present"]))
        )

        comparison = pd.DataFrame()
        n_common = 0
        n_conv_match = 0
        n_sec_match = 0
        if gate["base_state_valid"]:
            security_result = _run_pypowsybl_security(build.network, build.branch_map)
            sec_summary, branch_flows, voltage_results = _extract_security_tables(
                case,
                security_result,
                build.branch_map,
            )
            harmonized = _harmonized_rows(case, build.branch_map, sec_summary, branch_flows, voltage_results)
            comparison = _join_comparison(case, own_results, harmonized, gate)
            all_security_summary.append(sec_summary)
            all_branch_flows.append(branch_flows)
            all_voltage_results.append(voltage_results)
            all_comparisons.append(comparison)
            n_common = len(comparison)
            n_conv_match = int(comparison["convergence_match"].sum()) if not comparison.empty else 0
            n_sec_match = int(comparison["harmonized_security_match"].sum()) if not comparison.empty else 0
        else:
            all_comparisons.append(
                pd.DataFrame(
                    [
                        {
                            "case_name": case.name,
                            "base_state_valid": False,
                            "notes": "base_state_invalid_skipped",
                        }
                    ]
                )
            )

        case_summary_rows.append(
            {
                "case_name": case.name,
                "builder_valid": builder_valid,
                "base_lf_converged": gate["base_lf_converged"],
                "slack_bus_match": gate["slack_bus_match"],
                "base_state_valid": gate["base_state_valid"],
                "n_common_contingencies": n_common,
                "n_convergence_matches": n_conv_match,
                "n_harmonized_security_matches": n_sec_match,
                "convergence_match_rate": n_conv_match / n_common if n_common else math.nan,
                "harmonized_security_match_rate": n_sec_match / n_common if n_common else math.nan,
                "max_vm_diff": gate["max_vm_diff"],
                "max_va_diff": gate["max_va_diff"],
                "max_branch_flow_diff": gate["max_branch_flow_diff"],
            }
        )

    paths = {
        "topology": pypowsybl_output_dir / "pglib_builder_topology_validation.csv",
        "units": pypowsybl_output_dir / "pglib_builder_unit_validation.csv",
        "voltage_limits": pypowsybl_output_dir / "pglib_builder_voltage_limit_validation.csv",
        "branch_limits": pypowsybl_output_dir / "pglib_builder_branch_limit_validation.csv",
        "base": pypowsybl_output_dir / "pglib_base_state_comparison.csv",
        "summary": pypowsybl_output_dir / "pglib_n1_branch_summary.csv",
        "flows": pypowsybl_output_dir / "pglib_n1_branch_flows.csv",
        "voltages": pypowsybl_output_dir / "pglib_n1_monitored_voltage_results.csv",
        "case_summary": pypowsybl_output_dir / "pglib_validation_case_summary.csv",
        "comparison": comparison_output_path,
    }

    pd.concat(all_topology, ignore_index=True).to_csv(paths["topology"], index=False)
    pd.concat(all_units, ignore_index=True).to_csv(paths["units"], index=False)
    pd.concat(all_voltage_limits, ignore_index=True).to_csv(paths["voltage_limits"], index=False)
    pd.concat(all_branch_limits, ignore_index=True).to_csv(paths["branch_limits"], index=False)
    pd.concat(all_base, ignore_index=True).to_csv(paths["base"], index=False)
    (pd.concat(all_security_summary, ignore_index=True) if all_security_summary else pd.DataFrame()).to_csv(paths["summary"], index=False)
    (pd.concat(all_branch_flows, ignore_index=True) if all_branch_flows else pd.DataFrame()).to_csv(paths["flows"], index=False)
    (pd.concat(all_voltage_results, ignore_index=True) if all_voltage_results else pd.DataFrame()).to_csv(paths["voltages"], index=False)
    pd.DataFrame(case_summary_rows).to_csv(paths["case_summary"], index=False)
    pd.concat(all_comparisons, ignore_index=True).to_csv(paths["comparison"], index=False)
    return paths


# ------------------------------------------------------------
# Standalone N-1 screening via pypowsybl + OpenLoadFlow
#
# Unlike run_pypowsybl_branch_security_validation (a cross-check against a
# pre-computed own-model Newton-Raphson N-1 CSV), this runs pypowsybl's
# native security-analysis engine as the primary N-1 screening tool, for
# both branch and generator contingencies. Slack config
# (_loadflow_parameters: distributed_slack=False) and slack-bus pinning
# (_set_slack_terminal, via build_pypowsybl_network_from_powercase) match the
# single-slack convention documented in docs/formulations/security_constraints.md.
# ------------------------------------------------------------

def _element_contingency_map(
    case: PowerCase,
    branch_map: pd.DataFrame,
    *,
    include_branches: bool,
    include_generators: bool,
) -> pd.DataFrame:
    rows = []
    if include_branches:
        active = branch_map[branch_map["status"] == 1]
        for _, row in active.iterrows():
            rows.append(
                {
                    "contingency_id": row["contingency_id"],
                    "pypowsybl_equipment_id": row["pypowsybl_equipment_id"],
                    "element_type": "branch",
                    "own_element_id": row["own_branch_id"],
                }
            )
    if include_generators:
        for gen in case.generators:
            if gen.status != 1:
                continue
            rows.append(
                {
                    # "gen::" prefix avoids colliding with branch contingency_ids
                    # (own branch.id and gen.id are independently numbered, e.g.
                    # both "1" -- a bare "outage::1" would be ambiguous and
                    # pypowsybl's post_contingency_results dict silently keeps
                    # only one of the two when contingency_ids collide).
                    "contingency_id": f"outage::gen::{gen.id}",
                    "pypowsybl_equipment_id": _pp_gen_id(gen.id),
                    "element_type": "generator",
                    "own_element_id": gen.id,
                }
            )
    return pd.DataFrame(rows)


def run_pypowsybl_n1_security_analysis(network, contingency_map: pd.DataFrame):
    pp = _require_pypowsybl()
    analysis = pp.security.create_analysis()
    for _, row in contingency_map.iterrows():
        analysis.add_single_element_contingency(
            str(row["pypowsybl_equipment_id"]), contingency_id=str(row["contingency_id"])
        )
    branch_ids = contingency_map.loc[
        contingency_map["element_type"] == "branch", "pypowsybl_equipment_id"
    ].astype(str).tolist()
    analysis.add_monitored_elements(
        branch_ids=branch_ids,
        voltage_level_ids=network.get_voltage_levels().index.astype(str).tolist(),
    )
    return analysis.run_ac(
        network,
        parameters=pp.security.Parameters(load_flow_parameters=_loadflow_parameters()),
    )


def n1_screening_table(case: PowerCase, security_result, contingency_map: pd.DataFrame) -> pd.DataFrame:
    """One row per contingency: LF status, max/total/squared overload and
    voltage-violation metrics, secure flag, severity score (task 7 + task 8)."""
    branch_results = _security_result_frame(security_result.branch_results)
    voltage_results = _security_result_frame(security_result.bus_results)
    buses = _bus_by_id(case)
    branches = {b.id: b for b in case.branches}

    branch_cont_col = _contingency_column(branch_results)
    volt_cont_col = _contingency_column(voltage_results)
    branch_id_col = _id_column(branch_results, ("branch_id", "id", "element_id"))
    voltage_id_col = _id_column(voltage_results, ("voltage_level_id", "id", "bus_id"))

    rows = []
    for raw_contingency_id, post in security_result.post_contingency_results.items():
        contingency_id = str(raw_contingency_id)
        map_row = contingency_map.loc[contingency_map["contingency_id"] == contingency_id]
        element_type = str(map_row.iloc[0]["element_type"]) if not map_row.empty else "unknown"
        own_element_id = str(map_row.iloc[0]["own_element_id"]) if not map_row.empty else ""
        status = str(getattr(post, "status", "UNKNOWN"))
        converged = _lf_converged(status)

        bframe = branch_results
        if branch_cont_col is not None and not branch_results.empty:
            bframe = branch_results[branch_results[branch_cont_col].astype(str) == contingency_id]
        vframe = voltage_results
        if volt_cont_col is not None and not voltage_results.empty:
            vframe = voltage_results[voltage_results[volt_cont_col].astype(str) == contingency_id]

        max_loading = math.nan
        total_overload_pct = 0.0
        total_overload_pct_sq = 0.0
        n_overloaded = 0
        thermal_violation = False
        if not bframe.empty and branch_id_col is not None:
            for _, br in bframe.iterrows():
                branch_id = str(br[branch_id_col])
                if branch_id not in branches:
                    continue
                branch = branches[branch_id]
                if branch.rate_a <= 0.0:
                    continue
                s1 = math.hypot(float(br.get("p1", math.nan)), float(br.get("q1", math.nan)))
                s2 = math.hypot(float(br.get("p2", math.nan)), float(br.get("q2", math.nan)))
                loading = 100.0 * max(s1, s2) / _mw(branch.rate_a, case.base_mva)
                if math.isnan(loading):
                    continue
                max_loading = loading if math.isnan(max_loading) else max(max_loading, loading)
                if loading > 100.0 + 1e-6:
                    thermal_violation = True
                    n_overloaded += 1
                    overload = loading - 100.0
                    total_overload_pct += overload
                    total_overload_pct_sq += overload * overload

        min_vm = math.nan
        max_vm = math.nan
        voltage_violation = False
        total_voltage_violation_pu = 0.0
        total_voltage_violation_pu_sq = 0.0
        n_voltage_violations = 0
        if not vframe.empty:
            for _, volt in vframe.iterrows():
                raw_id = str(volt[voltage_id_col]) if voltage_id_col is not None else ""
                bus_id = raw_id.removeprefix("VL::")
                if bus_id not in buses:
                    continue
                bus = buses[bus_id]
                value = float(volt.get("v_mag", math.nan))
                if math.isnan(value):
                    continue
                vm_pu = value / bus.nominal_v_kv if value > 2.5 else value
                min_vm = vm_pu if math.isnan(min_vm) else min(min_vm, vm_pu)
                max_vm = vm_pu if math.isnan(max_vm) else max(max_vm, vm_pu)
                deviation = 0.0
                if vm_pu < bus.vmin - 1e-6:
                    deviation = bus.vmin - vm_pu
                elif vm_pu > bus.vmax + 1e-6:
                    deviation = vm_pu - bus.vmax
                if deviation > 0.0:
                    voltage_violation = True
                    n_voltage_violations += 1
                    total_voltage_violation_pu += deviation
                    total_voltage_violation_pu_sq += deviation * deviation

        secure = converged and not thermal_violation and not voltage_violation
        severity_score = max(
            0.0 if math.isnan(max_loading) else max(0.0, max_loading - 100.0) / 100.0,
            total_voltage_violation_pu,
        )
        rows.append(
            {
                "case_name": case.name,
                "contingency_id": contingency_id,
                "element_type": element_type,
                "outaged_element_id": own_element_id,
                "pypowsybl_status": status,
                "converged": converged,
                "max_loading_pct": max_loading,
                "n_overloaded_branches": n_overloaded,
                "total_overload_pct": total_overload_pct,
                "total_overload_pct_sq": total_overload_pct_sq,
                "min_vm_pu": min_vm,
                "max_vm_pu": max_vm,
                "n_voltage_violations": n_voltage_violations,
                "total_voltage_violation_pu": total_voltage_violation_pu,
                "total_voltage_violation_pu_sq": total_voltage_violation_pu_sq,
                "thermal_violation": thermal_violation,
                "voltage_violation": voltage_violation,
                "secure": secure,
                "severity_score": severity_score,
            }
        )
    return pd.DataFrame(rows)


def _network_state_row(
    case: PowerCase,
    network,
    *,
    contingency_id: str,
    element_type: str,
    own_element_id: str,
    status: str,
) -> dict:
    """Same metrics as n1_screening_table's per-row logic, read from a solved
    network's current state rather than a batch security-analysis result --
    used for the per-contingency redispatch-aware generator loop, since each
    generator contingency has a different (redispatched) network."""
    converged = _lf_converged(status)
    buses = _bus_by_id(case)
    branches_pp = _combined_branch_table(network)
    buses_pp = network.get_buses().reset_index()
    buses_by_vl = buses_pp.set_index("voltage_level_id")

    max_loading = math.nan
    total_overload_pct = 0.0
    total_overload_pct_sq = 0.0
    n_overloaded = 0
    thermal_violation = False
    for branch in case.branches:
        if branch.status != 1 or branch.rate_a <= 0.0 or branch.id not in branches_pp.index:
            continue
        pp_branch = branches_pp.loc[branch.id]
        s1 = math.hypot(float(pp_branch.get("p1", math.nan)), float(pp_branch.get("q1", math.nan)))
        s2 = math.hypot(float(pp_branch.get("p2", math.nan)), float(pp_branch.get("q2", math.nan)))
        loading = 100.0 * max(s1, s2) / _mw(branch.rate_a, case.base_mva)
        if math.isnan(loading):
            continue
        max_loading = loading if math.isnan(max_loading) else max(max_loading, loading)
        if loading > 100.0 + 1e-6:
            thermal_violation = True
            n_overloaded += 1
            overload = loading - 100.0
            total_overload_pct += overload
            total_overload_pct_sq += overload * overload

    min_vm = math.nan
    max_vm = math.nan
    voltage_violation = False
    total_voltage_violation_pu = 0.0
    total_voltage_violation_pu_sq = 0.0
    n_voltage_violations = 0
    for bus in case.buses:
        try:
            pp_bus = buses_by_vl.loc[_vl_id(bus.id)]
        except KeyError:
            continue
        if isinstance(pp_bus, pd.DataFrame):
            pp_bus = pp_bus.iloc[0]
        value = float(pp_bus.get("v_mag", math.nan))
        if math.isnan(value):
            continue
        vm_pu = value / bus.nominal_v_kv
        min_vm = vm_pu if math.isnan(min_vm) else min(min_vm, vm_pu)
        max_vm = vm_pu if math.isnan(max_vm) else max(max_vm, vm_pu)
        deviation = 0.0
        if vm_pu < bus.vmin - 1e-6:
            deviation = bus.vmin - vm_pu
        elif vm_pu > bus.vmax + 1e-6:
            deviation = vm_pu - bus.vmax
        if deviation > 0.0:
            voltage_violation = True
            n_voltage_violations += 1
            total_voltage_violation_pu += deviation
            total_voltage_violation_pu_sq += deviation * deviation

    secure = converged and not thermal_violation and not voltage_violation
    severity_score = max(
        0.0 if math.isnan(max_loading) else max(0.0, max_loading - 100.0) / 100.0,
        total_voltage_violation_pu,
    )
    return {
        "case_name": case.name,
        "contingency_id": contingency_id,
        "element_type": element_type,
        "outaged_element_id": own_element_id,
        "pypowsybl_status": status,
        "converged": converged,
        "max_loading_pct": max_loading,
        "n_overloaded_branches": n_overloaded,
        "total_overload_pct": total_overload_pct,
        "total_overload_pct_sq": total_overload_pct_sq,
        "min_vm_pu": min_vm,
        "max_vm_pu": max_vm,
        "n_voltage_violations": n_voltage_violations,
        "total_voltage_violation_pu": total_voltage_violation_pu,
        "total_voltage_violation_pu_sq": total_voltage_violation_pu_sq,
        "thermal_violation": thermal_violation,
        "voltage_violation": voltage_violation,
        "secure": secure,
        "severity_score": severity_score,
    }


def run_pypowsybl_generator_n1_with_redispatch(
    case: PowerCase,
    ac_opf_result,
    *,
    balancing_policy: str = "proportional_reserve",
) -> pd.DataFrame:
    """Generator N-1 via pypowsybl/OpenLoadFlow, with the same static
    proportional-reserve redispatch policy the own model applies
    (balancing_policy.py), so both tools test the same post-contingency
    dispatch assumption (see docs/formulations/security_constraints.md).

    Each generator outage gets a differently-redispatched case, so this
    solves one pypowsybl network per contingency rather than pypowsybl's
    batched security.create_analysis() (which assumes one shared base state).
    """
    base_dispatch_case = case_with_base_dispatch(case, ac_opf_result)
    rows = []
    for contingency in generate_generator_contingencies(base_dispatch_case):
        gen_id = contingency.outaged_generator_ids[0]
        contingency_id = f"outage::gen::{gen_id}"
        contingency_case = apply_contingency(base_dispatch_case, contingency)
        diagnostics = island_diagnostics(contingency_case)
        if diagnostics.islanded:
            rows.append(
                {
                    "case_name": case.name,
                    "contingency_id": contingency_id,
                    "element_type": "generator",
                    "outaged_element_id": gen_id,
                    "pypowsybl_status": "ISLANDED",
                    "converged": False,
                    "max_loading_pct": math.nan,
                    "n_overloaded_branches": 0,
                    "total_overload_pct": 0.0,
                    "total_overload_pct_sq": 0.0,
                    "min_vm_pu": math.nan,
                    "max_vm_pu": math.nan,
                    "n_voltage_violations": 0,
                    "total_voltage_violation_pu": 0.0,
                    "total_voltage_violation_pu_sq": 0.0,
                    "thermal_violation": False,
                    "voltage_violation": False,
                    "secure": False,
                    "severity_score": math.nan,
                }
            )
            continue

        balanced_case, balancing_result = apply_balancing_policy(
            contingency_case, ac_opf_result, contingency, policy=balancing_policy
        )
        if not balancing_result.success:
            rows.append(
                {
                    "case_name": case.name,
                    "contingency_id": contingency_id,
                    "element_type": "generator",
                    "outaged_element_id": gen_id,
                    "pypowsybl_status": "GENERATION_IMBALANCE",
                    "converged": False,
                    "max_loading_pct": math.nan,
                    "n_overloaded_branches": 0,
                    "total_overload_pct": 0.0,
                    "total_overload_pct_sq": 0.0,
                    "min_vm_pu": math.nan,
                    "max_vm_pu": math.nan,
                    "n_voltage_violations": 0,
                    "total_voltage_violation_pu": 0.0,
                    "total_voltage_violation_pu_sq": 0.0,
                    "thermal_violation": False,
                    "voltage_violation": False,
                    "secure": False,
                    "severity_score": math.nan,
                }
            )
            continue

        pp = _require_pypowsybl()
        build = build_pypowsybl_network_from_powercase(balanced_case)
        transfer_case_dispatch(build.network, balanced_case)
        lf_result = pp.lf.run_ac(build.network, parameters=_loadflow_parameters())
        status = _status_text(lf_result)
        rows.append(
            _network_state_row(
                balanced_case,
                build.network,
                contingency_id=contingency_id,
                element_type="generator",
                own_element_id=gen_id,
                status=status,
            )
        )
    return pd.DataFrame(rows)


def run_pypowsybl_n1_screening(
    case: PowerCase,
    ac_opf_result,
    *,
    include_branches: bool = True,
    include_generators: bool = True,
    generator_balancing_policy: str = "proportional_reserve",
) -> pd.DataFrame:
    """Run pypowsybl/OpenLoadFlow N-1 security analysis and return one row per
    contingency. Branch outages use pypowsybl's batched security analysis
    (single shared base state, no redispatch -- branch outages don't trigger
    the balancing policy for the own model either). Generator outages apply
    the same proportional-reserve redispatch the own model uses, since each
    generator loss needs a differently-redispatched case."""
    frames = []
    if include_branches:
        build = build_pypowsybl_network_from_powercase(case)
        transfer_ac_opf_dispatch(build.network, case, ac_opf_result)
        contingency_map = _element_contingency_map(
            case, build.branch_map, include_branches=True, include_generators=False
        )
        security_result = run_pypowsybl_n1_security_analysis(build.network, contingency_map)
        frames.append(n1_screening_table(case, security_result, contingency_map))
    if include_generators:
        frames.append(
            run_pypowsybl_generator_n1_with_redispatch(
                case, ac_opf_result, balancing_policy=generator_balancing_policy
            )
        )
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
