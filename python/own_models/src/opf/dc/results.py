"""Result containers and extraction helpers for solved DC OPF models."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyomo.environ as pyo


@dataclass(frozen=True)
class DCOPFResult:
    """Solved DC OPF values extracted from a Pyomo model."""
    status: str
    termination_condition: str
    objective: float
    pg: dict[str, float]
    theta: dict[str, float]
    branch_flow: dict[str, float]
    total_generation: float
    total_load: float
    max_power_balance_residual: float
    solve_iterations: float = float("nan")

    @property
    def branch_flows(self) -> dict[str, float]:
        """Backward-compatible plural alias for active branch flows."""
        return self.branch_flow

    @property
    def success(self) -> bool:
        """Compatibility flag used by analysis helpers."""
        return self.termination_condition.lower() in {"optimal", "locallyoptimal", "feasible"}

    @property
    def message(self) -> str:
        """Human-readable solve message for summary tables."""
        return self.termination_condition

    @property
    def max_power_balance_violation(self) -> float:
        """Compatibility alias for the matrix balance residual."""
        return self.max_power_balance_residual


def extract_dc_opf_result(
    model: pyo.ConcreteModel,
    *,
    status: str = "",
    termination_condition: str = "",
    solve_iterations: float = float("nan"),
) -> DCOPFResult:
    """Extract objective, dispatch, angles, flows, and matrix residuals."""
    pg = {g: float(pyo.value(model.Pg[g])) for g in model.GEN}
    theta = {i: float(pyo.value(model.theta[i])) for i in model.BUS}
    branch_flow = {l: float(pyo.value(model.Pf[l])) for l in model.BRANCH}

    objective = float(pyo.value(model.objective))
    total_generation = sum(pg.values())
    total_load = sum(model._pd_by_bus.values())
    max_residual = _max_matrix_balance_residual(model, pg, theta)

    return DCOPFResult(
        status=status,
        termination_condition=termination_condition,
        objective=objective,
        pg=pg,
        theta=theta,
        branch_flow=branch_flow,
        total_generation=total_generation,
        total_load=total_load,
        max_power_balance_residual=max_residual,
        solve_iterations=solve_iterations,
    )


def _max_matrix_balance_residual(
    model: pyo.ConcreteModel,
    pg: dict[str, float],
    theta: dict[str, float],
) -> float:
    """Return max absolute residual of ``Pg - demand - Bbus * theta``."""
    bus_ids = list(model._bus_ids)
    theta_vec = np.array([theta[bus_id] for bus_id in bus_ids], dtype=float)
    network_injection = model._bbus @ theta_vec

    max_residual = 0.0
    for row, bus_id in enumerate(bus_ids):
        generation = sum(pg[g] for g in model._gens_at_bus[bus_id])
        fixed_demand = model._pd_by_bus[bus_id]
        residual = generation - fixed_demand - network_injection[row]
        max_residual = max(max_residual, abs(float(residual)))

    return max_residual
