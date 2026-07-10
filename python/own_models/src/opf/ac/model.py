"""Top-level assembly of the AC OPF Pyomo model."""

from __future__ import annotations

import pyomo.environ as pyo

from own_models.src.data.case import PowerCase
from own_models.src.opf.ac.constraints import (
    add_ac_power_balance_constraints,
    add_branch_angle_constraints,
    add_branch_thermal_constraints,
    add_reference_angle_constraint,
)
from own_models.src.opf.ac.data import prepare_ac_opf_data
from own_models.src.opf.ac.objective import add_ac_opf_objective
from own_models.src.opf.ac.variables import (
    add_ac_objective_variables,
    add_ac_opf_variables,
    set_ac_opf_initial_values,
)


def build_ac_opf_pyomo_model(
    case: PowerCase,
    *,
    cost_power_units: str = "mw",
    objective_type: str = "generation_cost",
    objective_options: dict[str, object] | None = None,
    voltage_bounds: tuple[float, float] | dict[str, tuple[float, float]] | None = None,
    include_branch_thermal: bool = True,
    include_branch_angles: bool = True,
    fix_reference_angle: bool = True,
    warm_start: str = "case",
    stored_solution=None,
    load_flow_provider=None,
    model_name: str | None = None,
) -> pyo.ConcreteModel:
    """Build the nonlinear AC OPF Pyomo model."""
    data = prepare_ac_opf_data(case, voltage_bounds=voltage_bounds)

    model = pyo.ConcreteModel(name=model_name or f"{case.name}_ac_opf")
    model.BUS = pyo.Set(initialize=data.bus_ids, ordered=True)
    model.GEN = pyo.Set(initialize=data.gen_ids, ordered=True)
    model.BRANCH = pyo.Set(initialize=data.branch_ids, ordered=True)
    model.LOAD = pyo.Set(initialize=data.load_ids, ordered=True)

    add_ac_opf_variables(model, case, data)
    add_ac_objective_variables(
        model,
        data,
        objective_type=objective_type,
        objective_options=objective_options,
    )
    set_ac_opf_initial_values(
        model,
        case,
        data,
        warm_start=warm_start,
        stored_solution=stored_solution,
        load_flow_provider=load_flow_provider,
    )
    model._case = case
    model._ac_data = data # for objective.py , results.py , diagnostics
    add_ac_opf_objective(
        model,
        case,
        objective_type=objective_type,
        cost_power_units=cost_power_units,
        objective_options=objective_options,
    )
    add_ac_power_balance_constraints(model, case, data)

    if fix_reference_angle:
        add_reference_angle_constraint(model, case.slack_bus_id)
    if include_branch_angles:
        add_branch_angle_constraints(model, data)
    if include_branch_thermal:
        add_branch_thermal_constraints(model, data)

    model._cost_power_units = cost_power_units
    model._objective_options = objective_options or {}
    model._voltage_bounds = voltage_bounds
    model._fix_reference_angle = fix_reference_angle
    model._include_branch_thermal = include_branch_thermal
    model._include_branch_angles = include_branch_angles
    model._warm_start = warm_start

    return model
