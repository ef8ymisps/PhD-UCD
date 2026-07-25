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


def active_losses_expression(model):
    """Return total active branch loss: sum of each arc's own `p` flow
    variable over the full directed arc set (`model.ARC` already contains
    both directions per branch, so this equals `sum(pf + pt)` per branch --
    exactly Julia's `add_ac_active_losses_objective!` and the custom
    PowerModels loss-objective reference used to validate it).

    A prior version of this function used `sum(Pg) - fixed_Pd` instead
    (generation minus load) -- by the AC power-balance equations this
    differs from branch-only losses by the total ACTIVE bus-shunt
    conductance draw (`sum(Gs_i * Vm_i^2)`), since that consumption is
    counted in the generation/load balance but is NOT part of any branch's
    own arc flow. That discrepancy is negligible on cases with zero/near-zero
    shunt conductance, but real and reproducible (~0.3-0.5% of total losses,
    confirmed via a three-way PowerModels/Julia/Python comparison across the
    full validation grid) on `case300_ieee`, which has non-trivial active
    shunt conductance -- Julia and PowerModels (both true branch-only losses)
    agreed with each other while Python (branch losses + shunt draw)
    consistently landed on a different value, robust across every warm-start
    mode tried. Using the same arc-sum definition as Julia removes the
    discrepancy at its source rather than patching around it.
    """
    return sum(model.p[arc] for arc in model.ARC)


def pg_deviation_expression(model, data, options: dict[str, object]):
    """Return least-squares Pg deviation from initial or supplied reference Pg."""
    reference_pg = cast(dict[str, object], options.get("reference_pg", {}))
    return sum(
        (model.pg[gen.id] - float(cast(float | int | str, reference_pg.get(gen.id, gen.pg0)))) ** 2
        for gen in data.generators
    )


def target_deviation_expression(model, data, options: dict[str, object]):
    """L1 total deviation from a target point via signed slacks -- sum of
    every dpg_plus/dpg_minus/dqg_plus/... pair that exists on the model.
    `add_ac_target_deviation_variables` (variables.py) only builds a group's
    slack pair when that group is included (gated by `include_pg`/
    `include_qg`/`include_vm`/`include_va` in options, default True), not by
    whether its target dict happens to be present -- target dicts are now
    always passed for every group. Mirrors Julia's
    add_ac_target_deviation_objective!'s sum(plus[id] + minus[id] ...)."""
    terms = []
    if options.get("include_pg", True):
        terms.append(sum(model.dpg_plus[g] + model.dpg_minus[g] for g in model.GEN))
    if options.get("include_qg", True):
        terms.append(sum(model.dqg_plus[g] + model.dqg_minus[g] for g in model.GEN))
    if options.get("include_vm", True):
        terms.append(sum(model.dvm_plus[b] + model.dvm_minus[b] for b in model.BUS))
    if options.get("include_va", True):
        terms.append(sum(model.dva_plus[b] + model.dva_minus[b] for b in model.BUS))
    if not terms:
        raise ValueError("objective_type='target_deviation' requires at least one included group.")
    return sum(terms)


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
        expr = active_losses_expression(model)
    elif objective_type == "pg_deviation":
        expr = pg_deviation_expression(model, model._ac_data, options)
    elif objective_type == "target_deviation":
        expr = target_deviation_expression(model, model._ac_data, options)
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

