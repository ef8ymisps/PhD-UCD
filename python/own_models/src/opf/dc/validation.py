"""Lightweight matrix residual checks for solved DC OPF outputs."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from own_models.src.data.case import PowerCase
from own_models.src.network.dc import build_bbus


@dataclass(frozen=True)
class DCOPFValidationReport:
    """Residual summary for the DC OPF matrix equation."""
    max_bbus_residual: float
    bbus_ok: bool


def validate_bbus_consistency(
    case: PowerCase,
    *,
    pg: dict[str, float],
    theta: dict[str, float],
    tolerance: float = 1e-6,
) -> DCOPFValidationReport:
    """Check a solved DC OPF result against the matrix equation.

    Mathematical check:
        ``Bbus * theta ~= P_injection``

    ``P_injection`` uses optimized ``Pg`` and fixed demand, not the initial
    generator dispatch stored in the case.
    """
    bbus = build_bbus(case)

    theta_vec = np.zeros(len(case.buses), dtype=float)
    injection = np.zeros(len(case.buses), dtype=float)

    for bus in case.buses:
        theta_vec[case.bus_id_to_index[bus.id]] = theta[bus.id]

    for gen in case.generators:
        if gen.status == 1:
            injection[case.bus_id_to_index[gen.bus]] += pg[gen.id]

    for load in case.loads:
        if load.status == 1:
            injection[case.bus_id_to_index[load.bus]] -= load.pd

    for shunt in case.shunts:
        if shunt.status == 1:
            injection[case.bus_id_to_index[shunt.bus]] -= shunt.gs

    residual = bbus @ theta_vec - injection
    max_residual = float(np.max(np.abs(residual)))

    return DCOPFValidationReport(
        max_bbus_residual=max_residual,
        bbus_ok=max_residual <= tolerance,
    )
