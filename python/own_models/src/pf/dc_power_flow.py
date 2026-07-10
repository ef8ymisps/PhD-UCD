from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from own_models.src.data.case import PowerCase
from own_models.src.network.dc import (
    build_bbus,
    build_net_active_injection,
    dc_branch_flow_coefficient,
    slack_index,
)


@dataclass(frozen=True)
class DCPowerFlowResult:
    """Container for DC PF convergence, angles, flows, and slack balance."""
    converged: bool
    theta: np.ndarray
    branch_flows: dict[str, float]
    slack_injection: float
    mismatch_norm: float


def solve_dc_power_flow(case: PowerCase) -> DCPowerFlowResult:
    """Solve DC power flow for bus angles, branch flows, and slack injection."""
    bbus = build_bbus(case)
    p = build_net_active_injection(case)
    slack = slack_index(case)
    # Remove the reference bus equation so the angle system is nonsingular.
    # The slack angle is implicitly zero in the initialized theta vector.
    keep = [idx for idx in range(len(case.buses)) if idx != slack]

    theta = np.zeros(len(case.buses), dtype=float)
    theta[keep] = np.linalg.solve(bbus[np.ix_(keep, keep)], p[keep])

    branch_flows: dict[str, float] = {}
    for branch in case.branches:
        if branch.status == 0:
            continue
        i = case.bus_id_to_index[branch.from_bus]
        j = case.bus_id_to_index[branch.to_bus]
        branch_flows[branch.id] = float(dc_branch_flow_coefficient(branch) * (theta[i] - theta[j]))

    calculated = bbus @ theta
    mismatch = calculated - p
    # The slack mismatch is the balancing injection implied by the solved
    # network, so convergence is judged on non-slack equations only.
    slack_injection = float(calculated[slack])
    mismatch_non_slack = mismatch[keep] if keep else np.array([0.0])

    return DCPowerFlowResult(
        converged=bool(np.linalg.norm(mismatch_non_slack, ord=np.inf) <= 1e-8),
        theta=theta,
        branch_flows=branch_flows,
        slack_injection=slack_injection,
        mismatch_norm=float(np.linalg.norm(mismatch_non_slack, ord=np.inf)),
    )
