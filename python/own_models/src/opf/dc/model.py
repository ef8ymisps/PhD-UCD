"""Top-level assembly of the matrix-form DC OPF Pyomo model."""

from __future__ import annotations

import pyomo.environ as pyo

from own_models.src.data.case import PowerCase
from own_models.src.opf.dc.constraints import (
    add_angle_difference_limit_constraints,
    add_branch_flow_expressions,
    add_branch_flow_limit_constraints,
    add_power_balance_constraints,
    add_reference_angle_constraint,
)
from own_models.src.opf.dc.data import (
    has_angle_limits,
    has_branch_rate,
    prepare_dc_opf_data,
)
from own_models.src.opf.dc.objective import add_generation_cost_objective
from own_models.src.opf.dc.variables import add_dc_opf_variables


def build_dc_opf_pyomo_model(
    case: PowerCase,
    *,
    cost_power_units: str = "mw",
    include_branch_limits: bool = True,
    include_angle_limits: bool = True,
    fix_reference_angle: bool = True,
) -> pyo.ConcreteModel:
    """Build the matrix-form DC OPF Pyomo model.

    Variables:
        ``Pg[g]`` active generation in p.u.
        ``theta[i]`` bus voltage angle in radians

    Network equations:
        ``Pg_at_bus - fixed_demand = Bbus * theta``
        ``Pf = Bf * theta``

    Objective:
        Generator cost is evaluated in MW by default for compatibility with
        imported Python/PyPowSyBl cost data.
    """
    data = prepare_dc_opf_data(case)

    branches_with_rates = [
        branch.id
        for branch in data.branches
        if include_branch_limits and has_branch_rate(branch)
    ]

    branches_with_angle_limits = [
        branch.id
        for branch in data.branches
        if include_angle_limits and has_angle_limits(branch)
    ]

    model = pyo.ConcreteModel(name=f"DC_OPF_{case.name}")

    # -----------------------------
    # Sets
    # -----------------------------
    model.BUS = pyo.Set(initialize=data.bus_ids, ordered=True)
    model.GEN = pyo.Set(initialize=data.gen_ids, ordered=True)
    model.BRANCH = pyo.Set(initialize=data.branch_ids, ordered=True)
    model.BRANCH_WITH_RATE = pyo.Set(initialize=branches_with_rates, ordered=True)
    model.BRANCH_WITH_ANGLE_LIMIT = pyo.Set(
        initialize=branches_with_angle_limits,
        ordered=True,
    )

    # -----------------------------
    # Variables
    # -----------------------------
    add_dc_opf_variables(model, data.gen_by_id)

    # -----------------------------
    # Objective
    # -----------------------------
    add_generation_cost_objective(
        model,
        case,
        data.generators,
        cost_power_units=cost_power_units,
    )

    # -----------------------------
    # Expressions and constraints
    # -----------------------------
    # Store row positions before creating Pf because the expression rule uses
    # branch ids while Bf is indexed by integer row.
    model._branch_position = {
        branch_id: row for row, branch_id in enumerate(data.branch_ids)
    }

    add_branch_flow_expressions(
        model,
        bf=data.bf,
        bus_ids=data.bus_ids,
    )

    add_power_balance_constraints(
        model,
        pd_by_bus=data.pd_by_bus,
        gens_at_bus=data.gens_at_bus,
        bbus=data.bbus,
        bus_ids=data.bus_ids,
    )

    if fix_reference_angle:
        add_reference_angle_constraint(model, case.slack_bus_id)
    add_branch_flow_limit_constraints(model, data.branch_by_id)
    add_angle_difference_limit_constraints(model, data.branch_by_id)

    # Store useful data for extraction/debugging.
    model._case = case
    model._gen_by_id = data.gen_by_id
    model._branch_by_id = data.branch_by_id
    model._pd_by_bus = data.pd_by_bus
    model._gens_at_bus = data.gens_at_bus
    model._bus_ids = data.bus_ids
    model._branch_ids = data.branch_ids
    model._bbus = data.bbus
    model._bf = data.bf
    model._cost_power_units = cost_power_units
    model._fix_reference_angle = fix_reference_angle

    return model
