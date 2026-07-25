"""Pyomo constraints and expressions for AC OPF."""

from __future__ import annotations

import cmath

import numpy as np
import pyomo.environ as pyo

from own_models.src.network.admittance import (
    calc_ac_branch_flows,
    calc_bus_power_injections,
)


def _generator_vectors(case, data, pg: dict[str, float], qg: dict[str, float]):
    """Aggregate active/reactive generator decision variables by bus."""
    pgen = np.zeros(len(case.buses), dtype=float)
    qgen = np.zeros(len(case.buses), dtype=float)
    for gen in data.generators:
        idx = case.bus_id_to_index[gen.bus]
        pgen[idx] += pg[gen.id]
        qgen[idx] += qg[gen.id]
    return pgen, qgen


def evaluate_ac_opf_constraint_violations(
    case,
    data,
    pg: dict[str, float],
    qg: dict[str, float],
    vm: np.ndarray,
    va: np.ndarray,
    *,
    load_curtailment: dict[str, float] | None = None,
    loadability_factor: float = 1.0,
) -> dict[str, float]:
    """Compute max AC OPF balance, bound, thermal, and angle violations."""
    pgen, qgen = _generator_vectors(case, data, pg, qg)
    load_curtailment = load_curtailment or {}
    pd = np.array(
        [
            sum(
                loadability_factor * data.load_by_id[load_id].pd * (1.0 - load_curtailment.get(load_id, 0.0))
                for load_id in data.loads_at_bus[bus.id]
            )
            for bus in data.buses
        ],
        dtype=float,
    )
    qd = np.array(
        [
            sum(
                loadability_factor * data.load_by_id[load_id].qd * (1.0 - load_curtailment.get(load_id, 0.0))
                for load_id in data.loads_at_bus[bus.id]
            )
            for bus in data.buses
        ],
        dtype=float,
    )
    pcalc, qcalc = calc_bus_power_injections(data.ybus, vm, va)
    p_balance = pgen - pd - pcalc
    q_balance = qgen - qd - qcalc

    pg_limit = 0.0
    qg_limit = 0.0
    # Post-solve diagnostics make solver status distinct from practical
    # feasibility under the repository tolerance.
    for gen in data.generators:
        pg_limit = max(pg_limit, gen.pmin - pg[gen.id], pg[gen.id] - gen.pmax)
        qg_limit = max(qg_limit, gen.qmin - qg[gen.id], qg[gen.id] - gen.qmax)

    voltage = 0.0
    for value, bus in zip(vm, case.buses, strict=True):
        voltage = max(
            voltage,
            data.vmin_by_bus[bus.id] - value,
            value - data.vmax_by_bus[bus.id],
        )

    flows = calc_ac_branch_flows(case, vm, va)
    thermal = 0.0
    angle = 0.0
    for branch in data.branches:
        if branch.rate_a > 0.0 and branch.id in flows:
            flow = flows[branch.id]
            thermal = max(
                thermal,
                flow["pf"] ** 2 + flow["qf"] ** 2 - branch.rate_a**2,
                flow["pt"] ** 2 + flow["qt"] ** 2 - branch.rate_a**2,
            )
        angle_diff = va[case.bus_id_to_index[branch.from_bus]] - va[case.bus_id_to_index[branch.to_bus]]
        angle = max(angle, branch.angmin - angle_diff, angle_diff - branch.angmax)

    return {
        "max_p_balance_violation": float(np.linalg.norm(p_balance, ord=np.inf)),
        "max_q_balance_violation": float(np.linalg.norm(q_balance, ord=np.inf)),
        "max_pg_limit_violation": float(max(0.0, pg_limit)),
        "max_qg_limit_violation": float(max(0.0, qg_limit)),
        "max_voltage_violation": float(max(0.0, voltage)),
        "max_thermal_violation": float(max(0.0, thermal)),
        "max_angle_violation": float(max(0.0, angle)),
    }


def add_reference_angle_constraint(model, slack_bus_id: str) -> None:
    """Fix the voltage angle reference."""
    model.slack_angle = pyo.Constraint(expr=model.va[slack_bus_id] == 0.0)


def branch_admittance_terms(branch):
    """Return branch terminal admittance terms matching network.admittance."""
    y = 1.0 / complex(branch.r, branch.x)
    y_fr = complex(branch.g_fr, branch.b_fr)
    y_to = complex(branch.g_to, branch.b_to)
    tap_mag = branch.tap if branch.tap != 0.0 else 1.0
    tap = tap_mag * cmath.exp(1j * branch.shift)
    return {
        "yff": (y + y_fr) / abs(tap) ** 2,
        "yft": -y / tap.conjugate(),
        "ytf": -y / tap,
        "ytt": y + y_to,
    }


def branch_flow_exprs(model, branch):
    """Return AC branch terminal flow expressions ``pf, qf, pt, qt``."""
    terms = branch_admittance_terms(branch)
    vf = model.vm[branch.from_bus]
    vt = model.vm[branch.to_bus]
    af = model.va[branch.from_bus]
    at = model.va[branch.to_bus]
    angle_ft = af - at
    angle_tf = at - af

    yff = terms["yff"]
    yft = terms["yft"]
    ytf = terms["ytf"]
    ytt = terms["ytt"]

    pf = yff.real * vf**2 + vf * vt * (
        yft.real * pyo.cos(angle_ft) + yft.imag * pyo.sin(angle_ft)
    )
    qf = -yff.imag * vf**2 + vf * vt * (
        yft.real * pyo.sin(angle_ft) - yft.imag * pyo.cos(angle_ft)
    )
    pt = ytt.real * vt**2 + vt * vf * (
        ytf.real * pyo.cos(angle_tf) + ytf.imag * pyo.sin(angle_tf)
    )
    qt = -ytt.imag * vt**2 + vt * vf * (
        ytf.real * pyo.sin(angle_tf) - ytf.imag * pyo.cos(angle_tf)
    )
    return pf, qf, pt, qt
