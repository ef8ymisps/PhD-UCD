"""DC network matrices and coefficients shared by PF and OPF code."""

from __future__ import annotations

import numpy as np

from own_models.src.data.case import PowerCase


def dc_branch_flow_coefficient(branch) -> float:
    """Return the DC branch-flow coefficient used for angle-difference flows."""
    # Imaginary part of series admittance: x / (r^2 + x^2). This keeps the
    # DC approximation aligned with cases where resistance is retained.
    denom = branch.r**2 + branch.x**2
    if denom < 1e-18:
        raise ValueError(f"Branch {branch.id} has near-zero impedance.")
    return branch.x / denom


def dc_branch_conductance(branch) -> float:
    """Return the conductance term used by quadratic-loss DC approximations."""
    denom = branch.r**2 + branch.x**2
    if denom < 1e-18:
        raise ValueError(f"Branch {branch.id} has near-zero impedance.")
    return branch.r / denom


def build_bbus(case: PowerCase) -> np.ndarray:
    """Build the DC bus matrix used in ``P_injection = Bbus * theta``."""
    # Laplacian-style B matrix: active branch coefficients add to endpoint
    # diagonals and subtract from endpoint off-diagonals.
    nb = len(case.buses)
    bbus = np.zeros((nb, nb), dtype=float)

    for branch in case.branches:
        if branch.status == 0:
            continue
        i = case.bus_id_to_index[branch.from_bus]
        j = case.bus_id_to_index[branch.to_bus]
        b = dc_branch_flow_coefficient(branch)
        bbus[i, i] += b
        bbus[j, j] += b
        bbus[i, j] -= b
        bbus[j, i] -= b

    return bbus


def build_bf(case: PowerCase) -> tuple[np.ndarray, list[str]]:
    """Build the DC branch-flow matrix used in ``Pf = Bf * theta``.

    Rows follow the returned active branch-id list. Columns follow
    ``case.buses`` and ``case.bus_id_to_index``. Positive flow is measured in
    each branch's stored orientation, from ``from_bus`` to ``to_bus``.
    """
    active_branches = [branch for branch in case.branches if branch.status == 1]
    bf = np.zeros((len(active_branches), len(case.buses)), dtype=float)

    for row, branch in enumerate(active_branches):
        i = case.bus_id_to_index[branch.from_bus]
        j = case.bus_id_to_index[branch.to_bus]
        b = dc_branch_flow_coefficient(branch)
        bf[row, i] = b
        bf[row, j] = -b

    return bf, [branch.id for branch in active_branches]


def build_net_active_injection(case: PowerCase) -> np.ndarray:
    """Build fixed net active injection vector for DC power flow."""
    # Positive sign means net injection into the network. Loads and shunt
    # conductance are therefore subtracted from generator active power.
    p = np.zeros(len(case.buses), dtype=float)

    for gen in case.generators:
        if gen.status == 1:
            p[case.bus_id_to_index[gen.bus]] += gen.pg0

    for load in case.loads:
        if load.status == 1:
            p[case.bus_id_to_index[load.bus]] -= load.pd

    for shunt in case.shunts:
        if shunt.status == 1:
            p[case.bus_id_to_index[shunt.bus]] -= shunt.gs

    return p


def slack_index(case: PowerCase) -> int:
    """Return the zero-based array index of the case slack bus."""
    return case.bus_id_to_index[case.slack_bus_id]
