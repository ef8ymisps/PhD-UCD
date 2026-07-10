"""Objective functions for AC OPF."""

from __future__ import annotations

from typing import Any, cast

import pyomo.environ as pyo


def cost_power_expression(pg_pu, base_mva: float, cost_power_units: str):
    """Convert Pg into the units expected by the cost coefficients."""
    if cost_power_units == "mw":
        return pg_pu * base_mva
    if cost_power_units == "pu":
        return pg_pu
    raise ValueError(f"Unsupported cost_power_units={cost_power_units!r}.")


def generation_cost_expression(model, case, generators, cost_power_units: str):
    """Return the standard quadratic active-generation production cost."""
    return sum(
        float(gen.cost_c2) * cost_power_expression(model.pg[gen.id], case.base_mva, cost_power_units) ** 2
        + float(gen.cost_c1) * cost_power_expression(model.pg[gen.id], case.base_mva, cost_power_units)
        + float(gen.cost_c0)
        for gen in generators
    )


def active_losses_expression(model, data):
    """Return total active generation minus fixed active demand.

    With fixed loads and AC balance equations, minimizing this expression is
    equivalent to minimizing active network losses plus active shunt effects
    represented in Ybus.
    """
    fixed_pd = sum(load.pd for load in data.loads)
    return sum(model.pg[gen.id] for gen in data.generators) - fixed_pd


def pg_deviation_expression(model, data, options: dict[str, object]):
    """Return least-squares Pg deviation from initial or supplied reference Pg."""
    reference_pg = cast(dict[str, object], options.get("reference_pg", {}))
    return sum(
        (model.pg[gen.id] - float(cast(float | int | str, reference_pg.get(gen.id, gen.pg0)))) ** 2
        for gen in data.generators
    )


def load_curtailment_expression(model, data, options: dict[str, object]):
    """Return weighted active-load curtailment."""
    if not hasattr(model, "load_curtailment_fraction"):
        raise ValueError("objective_type='load_curtailment' requires load curtailment variables.")
    weights = cast(dict[str, object], options.get("curtailment_weights", {}))
    penalty = float(cast(float | int | str, options.get("curtailment_penalty", 1.0)))
    return penalty * sum(
        float(cast(float | int | str, weights.get(load.id, 1.0))) * load.pd * model.load_curtailment_fraction[load.id]
        for load in data.loads
    )

def add_ac_opf_objective(
    model,
    case,
    *,
    objective_type: str = "generation_cost",
    cost_power_units: str = "mw",
    objective_options: dict[str, object] | None = None,
) -> None:
    """Attach the selected AC OPF objective to the Pyomo model."""
    options = objective_options or {}
    objective_type = objective_type.lower()

    if objective_type == "generation_cost":
        expr = generation_cost_expression(model, case, model._ac_data.generators, cost_power_units)
    elif objective_type == "active_losses":
        expr = active_losses_expression(model, model._ac_data)
    elif objective_type == "pg_deviation":
        expr = pg_deviation_expression(model, model._ac_data, options)
    elif objective_type == "load_curtailment":
        expr = load_curtailment_expression(model, model._ac_data, options)
    elif objective_type == "max_loadability":
        if not hasattr(model, "loadability_factor"):
            raise ValueError("objective_type='max_loadability' requires model.loadability_factor.")
        expr = -model.loadability_factor
    else:
        raise ValueError(f"Unsupported AC OPF objective_type={objective_type!r}.")

    model.objective = pyo.Objective(expr=expr, sense=pyo.minimize)
    model._objective_type = objective_type

