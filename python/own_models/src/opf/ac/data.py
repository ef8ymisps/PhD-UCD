"""Data preparation for the AC OPF Pyomo model."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from own_models.src.data.case import PowerCase
from own_models.src.network.admittance import build_admittance_matrix


class ACOPFError(RuntimeError):
    """Raised when the AC OPF model cannot be built or solved."""


@dataclass(frozen=True)
class ACOPFData:
    """Indexed AC OPF data prepared for Pyomo model construction."""
    buses: list
    generators: list
    branches: list
    loads: list
    bus_ids: list[str]
    gen_ids: list[str]
    branch_ids: list[str]
    load_ids: list[str]
    gen_by_id: dict[str, object]
    branch_by_id: dict[str, object]
    load_by_id: dict[str, object]
    vmin_by_bus: dict[str, float]
    vmax_by_bus: dict[str, float]
    pd_by_bus: dict[str, float]
    qd_by_bus: dict[str, float]
    gens_at_bus: dict[str, list[str]]
    loads_at_bus: dict[str, list[str]]
    ybus: np.ndarray
    ybus_terms_by_bus: dict[str, list[tuple[str, float, float]]]


def active_generators(case: PowerCase):
    """Return online generators whose P/Q dispatch is optimized."""
    return [gen for gen in case.generators if gen.status == 1]


def active_branches(case: PowerCase):
    """Return online branches included in AC network constraints."""
    return [branch for branch in case.branches if branch.status == 1]


def active_loads(case: PowerCase):
    """Return online loads included in AC balance constraints."""
    return [load for load in case.loads if load.status == 1]


def load_by_bus(case: PowerCase) -> tuple[dict[str, float], dict[str, float]]:
    """Aggregate online active and reactive load by bus id."""
    pd = {bus.id: 0.0 for bus in case.buses}
    qd = {bus.id: 0.0 for bus in case.buses}
    for load in case.loads:
        if load.status == 1:
            pd[load.bus] += float(load.pd)
            qd[load.bus] += float(load.qd)
    return pd, qd


def loads_at_bus(case: PowerCase, loads) -> dict[str, list[str]]:
    """Map bus id to online load ids connected at that bus."""
    result = {bus.id: [] for bus in case.buses}
    for load in loads:
        if load.bus not in result:
            raise ACOPFError(f"Load {load.id} references unknown bus {load.bus}.")
        result[load.bus].append(load.id)
    return result


def generators_at_bus(case: PowerCase, generators) -> dict[str, list[str]]:
    """Map bus id to online generator ids connected at that bus."""
    result = {bus.id: [] for bus in case.buses}
    for gen in generators:
        if gen.bus not in result:
            raise ACOPFError(f"Generator {gen.id} references unknown bus {gen.bus}.")
        result[gen.bus].append(gen.id)
    return result


def validate_case_for_ac_opf(case: PowerCase) -> None:
    """Check basic data requirements before building AC OPF."""
    if not case.buses:
        raise ACOPFError("AC OPF requires at least one bus.")
    if not active_generators(case):
        raise ACOPFError("AC OPF requires at least one active generator.")
    if case.slack_bus_id not in case.bus_id_to_index:
        raise ACOPFError(f"Slack bus {case.slack_bus_id!r} is not in the bus map.")


def build_ybus_terms_by_bus(case: PowerCase) -> dict[str, list[tuple[str, float, float]]]:
    """Build sparse Ybus row terms as ``(other_bus_id, Gij, Bij)``."""
    ybus = build_admittance_matrix(case)
    terms: dict[str, list[tuple[str, float, float]]] = {bus.id: [] for bus in case.buses}
    for i, bus in enumerate(case.buses):
        nz = np.flatnonzero(np.abs(ybus[i, :]) > 1e-14)
        terms[bus.id] = [
            (case.buses[j].id, float(ybus[i, j].real), float(ybus[i, j].imag))
            for j in nz
        ]
    return terms


def voltage_limit_maps(
    case: PowerCase,
    voltage_bounds: tuple[float, float] | dict[str, tuple[float, float]] | None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Build bus voltage bounds from case data or an explicit override."""
    if voltage_bounds is None:
        vmin = {bus.id: float(bus.vmin) for bus in case.buses}
        vmax = {bus.id: float(bus.vmax) for bus in case.buses}
        _validate_voltage_limit_maps(vmin, vmax)
        return vmin, vmax

    if isinstance(voltage_bounds, dict):
        vmin = {}
        vmax = {}
        for bus in case.buses:
            lower, upper = voltage_bounds.get(bus.id, (bus.vmin, bus.vmax))
            vmin[bus.id] = float(lower)
            vmax[bus.id] = float(upper)
        _validate_voltage_limit_maps(vmin, vmax)
        return vmin, vmax

    lower, upper = voltage_bounds
    vmin = {bus.id: float(lower) for bus in case.buses}
    vmax = {bus.id: float(upper) for bus in case.buses}
    _validate_voltage_limit_maps(vmin, vmax)
    return vmin, vmax


def _validate_voltage_limit_maps(vmin: dict[str, float], vmax: dict[str, float]) -> None:
    """Reject empty voltage intervals before Pyomo variables are constructed."""
    invalid = [
        (bus_id, lower, vmax[bus_id])
        for bus_id, lower in vmin.items()
        if lower > vmax[bus_id]
    ]
    if invalid:
        preview = ", ".join(
            f"{bus_id}: lower={lower:g}, upper={upper:g}"
            for bus_id, lower, upper in invalid[:5]
        )
        extra = "" if len(invalid) <= 5 else f", ... ({len(invalid)} buses total)"
        raise ACOPFError(f"Empty voltage bound interval after tightening: {preview}{extra}")


def prepare_ac_opf_data(
    case: PowerCase,
    *,
    voltage_bounds: tuple[float, float] | dict[str, tuple[float, float]] | None = (0.95, 1.05),
) -> ACOPFData:
    """Prepare ordered records, mappings, loads, and Ybus for AC OPF."""
    validate_case_for_ac_opf(case)

    buses = list(case.buses)
    generators = active_generators(case)
    branches = active_branches(case)
    loads = active_loads(case)
    pd, qd = load_by_bus(case)
    vmin, vmax = voltage_limit_maps(case, voltage_bounds)

    ybus = build_admittance_matrix(case)

    return ACOPFData(
        buses=buses,
        generators=generators,
        branches=branches,
        loads=loads,
        bus_ids=[bus.id for bus in buses],
        gen_ids=[gen.id for gen in generators],
        branch_ids=[branch.id for branch in branches],
        load_ids=[load.id for load in loads],
        gen_by_id={gen.id: gen for gen in generators},
        branch_by_id={branch.id: branch for branch in branches},
        load_by_id={load.id: load for load in loads},
        vmin_by_bus=vmin,
        vmax_by_bus=vmax,
        pd_by_bus=pd,
        qd_by_bus=qd,
        gens_at_bus=generators_at_bus(case, generators),
        loads_at_bus=loads_at_bus(case, loads),
        ybus=ybus,
        ybus_terms_by_bus={
            bus.id: [
                (case.buses[j].id, float(ybus[i, j].real), float(ybus[i, j].imag))
                for j in np.flatnonzero(np.abs(ybus[i, :]) > 1e-14)
            ]
            for i, bus in enumerate(buses)
        },
    )
