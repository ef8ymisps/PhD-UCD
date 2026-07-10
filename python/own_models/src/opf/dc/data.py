"""Data preparation and case checks for the matrix-form DC OPF model."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np

from own_models.src.data.case import PowerCase
from own_models.src.network.dc import build_bbus, build_bf


class DCOPFError(RuntimeError):
    """Raised when the DC OPF model cannot be built or solved."""


@dataclass(frozen=True)
class DCOPFData:
    """Indexed DC OPF data prepared for matrix-based Pyomo construction."""
    buses: list[Any]
    generators: list[Any]
    branches: list[Any]
    bus_ids: list[str]
    gen_ids: list[str]
    branch_ids: list[str]
    gen_by_id: dict[str, Any]
    branch_by_id: dict[str, Any]
    pd_by_bus: dict[str, float]
    gens_at_bus: dict[str, list[str]]
    bbus: np.ndarray
    bf: np.ndarray


def active_buses(case: PowerCase):
    """Return buses that participate in the DC OPF model."""
    return list(case.buses)


def active_generators(case: PowerCase):
    """Return online generators whose active dispatch is optimized."""
    return [gen for gen in case.generators if gen.status == 1]


def active_branches(case: PowerCase):
    """Return online branches that define the DC network equations."""
    return [branch for branch in case.branches if branch.status == 1]


def active_loads(case: PowerCase):
    """Return online fixed loads used in active-power balance."""
    return [load for load in case.loads if load.status == 1]


def build_lookup_by_id(items) -> dict[str, Any]:
    """Index case records by their string id."""
    return {item.id: item for item in items}


def active_power_demand_by_bus(case: PowerCase) -> dict[str, float]:
    """Return fixed active demand per bus in p.u.

    The DC balance uses ``Pg - fixed_demand = Bbus * theta``. Fixed demand is
    active load plus active shunt conductance, matching the network helper's
    net-injection sign convention.
    """
    demand = {bus.id: 0.0 for bus in active_buses(case)}

    for load in active_loads(case):
        if load.bus not in demand:
            raise DCOPFError(f"Load {load.id} references unknown bus {load.bus}.")
        demand[load.bus] += float(load.pd)

    for shunt in case.shunts:
        if shunt.status == 0:
            continue
        if shunt.bus not in demand:
            raise DCOPFError(f"Shunt {shunt.id} references unknown bus {shunt.bus}.")
        demand[shunt.bus] += float(shunt.gs)

    return demand


def generators_at_bus(case: PowerCase) -> dict[str, list[str]]:
    """Map each bus id to online generator ids connected to that bus."""
    result = {bus.id: [] for bus in active_buses(case)}

    for gen in active_generators(case):
        if gen.bus not in result:
            raise DCOPFError(f"Generator {gen.id} references unknown bus {gen.bus}.")
        result[gen.bus].append(gen.id)

    return result


def has_branch_rate(branch) -> bool:
    """Return true when a branch has a finite positive active-flow limit."""
    return math.isfinite(float(branch.rate_a)) and float(branch.rate_a) > 0.0


def has_angle_limits(branch) -> bool:
    """Return true when a branch has finite ordered angle-difference limits."""
    return (
        math.isfinite(float(branch.angmin))
        and math.isfinite(float(branch.angmax))
        and float(branch.angmin) <= float(branch.angmax)
    )


def validate_case_for_dc_opf(case: PowerCase) -> None:
    """Check basic data requirements before building the Pyomo model."""
    bus_ids = {bus.id for bus in active_buses(case)}
    gens = active_generators(case)
    branches = active_branches(case)

    if not bus_ids:
        raise DCOPFError("DC OPF requires at least one bus.")
    if not gens:
        raise DCOPFError("DC OPF requires at least one active generator.")
    if not branches:
        raise DCOPFError("DC OPF requires at least one active branch.")
    if case.slack_bus_id not in bus_ids:
        raise DCOPFError(f"Slack bus {case.slack_bus_id!r} is not an active bus.")

    total_load = sum(active_power_demand_by_bus(case).values())
    total_pmin = sum(float(gen.pmin) for gen in gens)
    total_pmax = sum(float(gen.pmax) for gen in gens)

    if total_load < total_pmin - 1e-8:
        raise DCOPFError(
            f"Total load {total_load:.6g} p.u. is below total Pmin "
            f"{total_pmin:.6g} p.u."
        )

    if total_load > total_pmax + 1e-8:
        raise DCOPFError(
            f"Total load {total_load:.6g} p.u. exceeds total Pmax "
            f"{total_pmax:.6g} p.u."
        )


def prepare_dc_opf_data(case: PowerCase) -> DCOPFData:
    """Prepare ordered records, mappings, and matrices for DC OPF assembly."""
    validate_case_for_dc_opf(case)

    buses = active_buses(case)
    generators = active_generators(case)
    branches = active_branches(case)
    bf, bf_branch_ids = build_bf(case)

    branch_ids = [branch.id for branch in branches]
    if bf_branch_ids != branch_ids:
        raise DCOPFError("Bf row order does not match active branch order.")

    return DCOPFData(
        buses=buses,
        generators=generators,
        branches=branches,
        bus_ids=[bus.id for bus in buses],
        gen_ids=[gen.id for gen in generators],
        branch_ids=branch_ids,
        gen_by_id=build_lookup_by_id(generators),
        branch_by_id=build_lookup_by_id(branches),
        pd_by_bus=active_power_demand_by_bus(case),
        gens_at_bus=generators_at_bus(case),
        bbus=build_bbus(case),
        bf=bf,
    )
