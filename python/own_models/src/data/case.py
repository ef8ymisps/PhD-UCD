from __future__ import annotations

from dataclasses import dataclass


PQ = 1
PV = 2
SLACK = 3


@dataclass(frozen=True)
class Bus:
    """Bus record containing voltage data, type, and indexing metadata."""
    id: str
    index: int
    bus_type: int
    vm0: float
    va0: float
    vmin: float
    vmax: float
    nominal_v_kv: float


@dataclass(frozen=True)
class Branch:
    """Branch or transformer record used by DC and AC network models."""
    id: str
    index: int
    from_bus: str
    to_bus: str
    r: float
    x: float
    b: float
    b_fr: float
    b_to: float
    g_fr: float
    g_to: float
    rate_a: float
    tap: float
    shift: float
    angmin: float
    angmax: float
    status: int
    branch_type: str = "line"


@dataclass(frozen=True)
class Generator:
    """Generator record with dispatch, limits, status, and cost coefficients."""
    id: str
    index: int
    bus: str
    pg0: float
    qg0: float
    pmin: float
    pmax: float
    qmin: float
    qmax: float
    status: int
    voltage_regulator_on: bool = False
    cost_c2: float = 0.0
    cost_c1: float = 0.0
    cost_c0: float = 0.0


@dataclass(frozen=True)
class Load:
    """Fixed active/reactive demand attached to a bus."""
    id: str
    index: int
    bus: str
    pd: float
    qd: float
    status: int


@dataclass(frozen=True)
class Shunt:
    """Fixed shunt conductance/susceptance attached to a bus."""
    id: str
    index: int
    bus: str
    gs: float
    bs: float
    status: int


@dataclass(frozen=True)
class PowerCase:
    """Complete normalized power-system case used by own-model solvers."""
    name: str
    base_mva: float
    buses: list[Bus]
    branches: list[Branch]
    generators: list[Generator]
    loads: list[Load]
    shunts: list[Shunt]
    slack_bus_id: str
    bus_id_to_index: dict[str, int]


# Backwards-compatible alias for the earlier placeholder API.
NeutralCase = PowerCase
