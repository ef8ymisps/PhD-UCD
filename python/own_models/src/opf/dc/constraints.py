"""Pyomo expressions and constraints for the matrix-form DC OPF model."""

from __future__ import annotations

import numpy as np
import pyomo.environ as pyo


def add_branch_flow_expressions(model, *, bf: np.ndarray, bus_ids: list[str]) -> None:
    """Add matrix-based DC branch-flow expressions.

    Mathematical role:
        ``Pf = Bf * theta``

    Positive ``Pf[l]`` follows the branch orientation stored in the case data.
    """
    row_terms = {
        row: [
            (bus_ids[col], float(bf[row, col]))
            for col in np.flatnonzero(bf[row, :])
        ]
        for row in range(bf.shape[0])
    }

    def branch_flow_rule(_model, branch_id):
        row = _model._branch_position[branch_id]
        return sum(
            coeff * _model.theta[bus_id]
            for bus_id, coeff in row_terms[row]
        )

    model.Pf = pyo.Expression(model.BRANCH, rule=branch_flow_rule)


def add_power_balance_constraints(
    model,
    *,
    pd_by_bus: dict[str, float],
    gens_at_bus: dict[str, list[str]],
    bbus: np.ndarray,
    bus_ids: list[str],
) -> None:
    """Add matrix-based nodal active-power balance.

    Mathematical role:
        ``Pg_at_bus - fixed_demand = Bbus * theta``

    Positive injection means power entering the network. Fixed demand includes
    active load and active shunt conductance.
    """
    bus_position = {bus_id: pos for pos, bus_id in enumerate(bus_ids)}
    row_terms = {
        row: [
            (bus_ids[col], float(bbus[row, col]))
            for col in np.flatnonzero(bbus[row, :])
        ]
        for row in range(bbus.shape[0])
    }

    def power_balance_rule(_model, bus_id):
        row = bus_position[bus_id]
        generation = sum(_model.Pg[g] for g in gens_at_bus[bus_id])
        demand = pd_by_bus[bus_id]
        network_injection = sum(
            coeff * _model.theta[col_bus_id]
            for col_bus_id, coeff in row_terms[row]
        )

        return generation - demand == network_injection

    model.power_balance = pyo.Constraint(model.BUS, rule=power_balance_rule)


def add_reference_angle_constraint(model, slack_bus_id: str) -> None:
    """Fix the reference angle.

    Mathematical role:
        ``theta[slack] = 0`` removes the singular angle reference.
    """
    model.reference_angle = pyo.Constraint(expr=model.theta[slack_bus_id] == 0.0)


def add_branch_flow_limit_constraints(model, branch_by_id) -> None:
    """Add active branch-flow limits for branches with valid ``rate_a``.

    Mathematical role:
        ``-rate_a <= Pf[l] <= rate_a``
    """

    def branch_flow_limit_rule(_model, branch_id):
        rate = float(branch_by_id[branch_id].rate_a)
        return -rate, _model.Pf[branch_id], rate

    model.branch_flow_limit = pyo.Constraint(
        model.BRANCH_WITH_RATE,
        rule=branch_flow_limit_rule,
    )


def add_angle_difference_limit_constraints(model, branch_by_id) -> None:
    """Add branch angle-difference limits.

    Mathematical role:
        ``angmin[l] <= theta[from] - theta[to] <= angmax[l]``
    """

    def angle_limit_rule(_model, branch_id):
        branch = branch_by_id[branch_id]
        angle_difference = (
            _model.theta[branch.from_bus]
            - _model.theta[branch.to_bus]
        )
        return float(branch.angmin), angle_difference, float(branch.angmax)

    model.angle_difference_limit = pyo.Constraint(
        model.BRANCH_WITH_ANGLE_LIMIT,
        rule=angle_limit_rule,
    )
