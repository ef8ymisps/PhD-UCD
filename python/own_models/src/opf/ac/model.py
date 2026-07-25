"""AC OPF formulation using Pyomo.

Arc-based (PowerModels-style) formulation: directed branch arcs carry real
p/q flow variables, each pinned to its Ohm's-law formula via a separate
equality constraint; nodal balance sums arc flows incident at each bus.
This is the sole AC-OPF formulation in this codebase -- the previous
bus-injection (implicit Ybus-substitution) formulation was deleted after
this arc-based model reached validated parity with PowerModels.jl's own
ACPPowerModel on the full own-vs-PowerModels validation sweep.

Also owns solver execution (previously solve.py): builds the model,
creates the solver, applies options, runs the solve, and extracts results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyomo.environ as pyo

from own_models.src.data.case import PowerCase
from own_models.src.opf.ac.constraints import (
    add_reference_angle_constraint,
    branch_flow_exprs,
)
from own_models.src.opf.ac.data import ACOPFData, prepare_ac_opf_data
from own_models.src.opf.ac.initialization import build_ac_initial_point
from own_models.src.opf.ac.objective import add_ac_opf_objective
from own_models.src.opf.ac.results import ACOPFResult, extract_ac_opf_result, status_only_ac_opf_result
from own_models.src.opf.ac.variables import (
    add_ac_objective_variables,
    add_ac_target_deviation_variables,
    finite_or_none,
)
from own_models.src.opf.solver_iterations import solve_with_iteration_capture

# Pinned explicitly rather than left to Ipopt's own (solver-version-dependent)
# defaults. Verified via `ipopt --print_options_documentation` against the
# installed Ipopt 3.14.19 that these values already match what Ipopt
# defaults to today -- pinning changes no solve behavior now, it protects
# against a future Ipopt upgrade silently changing these out from under the
# validation pipeline, and matches the equivalent pin on the Julia side
# (julia/own_models/src/opf/ac/model.jl). See docs/notes/solver_tolerances.md
# for the full comparison and why these are NOT the cause of the
# binding-constraint classification mismatches seen in validation (that's a
# downstream 1e-6 classification threshold, ~100x tighter than these).
DEFAULT_IPOPT_TOLERANCES: dict[str, Any] = {
    "tol": 1e-8,
    "acceptable_tol": 1e-6,
    "constr_viol_tol": 1e-4,
    "compl_inf_tol": 1e-4,
    "dual_inf_tol": 1.0,
}


ACArc = tuple[str, str, str]


@dataclass(frozen=True)
class ACArcData:
    """AC OPF data plus directed branch-arc lookup tables."""

    base: ACOPFData
    arcs: list[ACArc]
    arcs_from: dict[str, ACArc]
    arcs_to: dict[str, ACArc]
    bus_arcs: dict[str, list[ACArc]]
    representative_angle_branch: dict[tuple[str, str], str]


def build_ac_arc_data(
    case: PowerCase,
    *,
    voltage_bounds: tuple[float, float] | dict[str, tuple[float, float]] | None = (0.95, 1.05),
) -> ACArcData:
    """Build directed arc maps mirroring PowerModels' ACP reference layer."""
    base = prepare_ac_opf_data(case, voltage_bounds=voltage_bounds)
    arcs: list[ACArc] = []
    arcs_from: dict[str, ACArc] = {}
    arcs_to: dict[str, ACArc] = {}
    bus_arcs: dict[str, list[ACArc]] = {bus.id: [] for bus in base.buses}
    representative_angle_branch: dict[tuple[str, str], str] = {}

    for branch in base.branches:
        from_arc = (branch.id, branch.from_bus, branch.to_bus)
        to_arc = (branch.id, branch.to_bus, branch.from_bus)
        arcs.extend([from_arc, to_arc])
        arcs_from[branch.id] = from_arc
        arcs_to[branch.id] = to_arc
        bus_arcs[branch.from_bus].append(from_arc)
        bus_arcs[branch.to_bus].append(to_arc)

        pair = (branch.from_bus, branch.to_bus)
        current = representative_angle_branch.get(pair)
        if current is None or branch.index < base.branch_by_id[current].index:
            representative_angle_branch[pair] = branch.id

    return ACArcData(
        base=base,
        arcs=arcs,
        arcs_from=arcs_from,
        arcs_to=arcs_to,
        bus_arcs=bus_arcs,
        representative_angle_branch=representative_angle_branch,
    )


def build_ac_opf_pyomo_model(
    case: PowerCase,
    *,
    cost_power_units: str = "mw",
    objective_type: str = "generation_cost",
    objective_options: dict[str, object] | None = None,
    voltage_bounds: tuple[float, float] | dict[str, tuple[float, float]] | None = (0.95, 1.05),
    include_branch_thermal: bool = True,
    include_branch_angles: bool = True,
    fix_reference_angle: bool = True,
    deduplicate_angle_constraints: bool = False,
    warm_start: str = "zero",
    stored_solution=None,
    load_flow_provider=None,
    model_name: str | None = None,
) -> pyo.ConcreteModel:
    """Build the AC OPF Pyomo model."""
    data = build_ac_arc_data(case, voltage_bounds=voltage_bounds)
    base = data.base

    model = pyo.ConcreteModel(name=model_name or f"{case.name}_ac_opf")
    model.BUS = pyo.Set(initialize=base.bus_ids, ordered=True)
    model.GEN = pyo.Set(initialize=base.gen_ids, ordered=True)
    model.BRANCH = pyo.Set(initialize=base.branch_ids, ordered=True)
    model.LOAD = pyo.Set(initialize=base.load_ids, ordered=True)
    model.ARC = pyo.Set(dimen=3, initialize=data.arcs, ordered=True)

    if objective_type == "target_deviation":
        # add_ac_target_deviation_variables builds pg/qg/vm/va itself (true
        # substitution) -- arc p/q flow variables are never substitution
        # targets, but their bounds must be droppable too
        # (enforce_deviation_bounds=False for the equality-only diagnostic),
        # hence the separate arc-only builder.
        options = objective_options or {}
        add_ac_target_deviation_variables(model, case, base, options)
        _add_ac_arc_variables(model, data, enforce_arc_bounds=bool(options.get("enforce_deviation_bounds", True)))
    else:
        _add_ac_variables(
            model,
            case,
            data,
            warm_start=warm_start,
            stored_solution=stored_solution,
            load_flow_provider=load_flow_provider,
        )
        add_ac_objective_variables(
            model,
            base,
            objective_type=objective_type,
            objective_options=objective_options,
        )

    model._case = case
    model._ac_data = base
    model._ac_arc_data = data
    add_ac_opf_objective(
        model,
        case,
        objective_type=objective_type,
        cost_power_units=cost_power_units,
        objective_options=objective_options,
    )

    _add_ac_power_balance_constraints(model, data)
    _add_ac_branch_constraints(
        model,
        data,
        include_branch_thermal=include_branch_thermal,
        include_branch_angles=include_branch_angles,
        deduplicate_angle_constraints=deduplicate_angle_constraints,
    )

    if fix_reference_angle:
        add_reference_angle_constraint(model, case.slack_bus_id)

    model._cost_power_units = cost_power_units
    model._objective_options = objective_options or {}
    model._voltage_bounds = voltage_bounds
    model._fix_reference_angle = fix_reference_angle
    model._include_branch_thermal = include_branch_thermal
    model._include_branch_angles = include_branch_angles
    model._warm_start = warm_start
    model._formulation = "arc_based"

    return model


def _add_ac_arc_variables(
    model,
    data: ACArcData,
    *,
    enforce_arc_bounds: bool = True,
) -> None:
    """Directed branch-arc p/q flow variables only. Split out of
    `_add_ac_variables` so the target-deviation branch (which builds
    pg/qg/vm/va via `add_ac_target_deviation_variables` instead) can still
    get arc variables, with bounds droppable via `enforce_arc_bounds` for
    the equality-only diagnostic -- mirrors Julia's `add_ac_arc_variables!`."""
    base = data.base

    def p_arc_bounds(_model, branch_id, _from_bus, _to_bus):
        if not enforce_arc_bounds:
            return None, None
        branch = base.branch_by_id[branch_id]
        if branch.rate_a > 0.0:
            return -branch.rate_a, branch.rate_a
        return None, None

    model.p = pyo.Var(model.ARC, bounds=p_arc_bounds)
    model.q = pyo.Var(model.ARC, bounds=p_arc_bounds)

    for arc in data.arcs:
        model.p[arc].set_value(0.0)
        model.q[arc].set_value(0.0)


def _add_ac_variables(
    model,
    case: PowerCase,
    data: ACArcData,
    *,
    warm_start: str,
    stored_solution,
    load_flow_provider,
) -> None:
    base = data.base

    def pg_bounds(_model, gen_id):
        gen = base.gen_by_id[gen_id]
        return finite_or_none(gen.pmin), finite_or_none(gen.pmax)

    def qg_bounds(_model, gen_id):
        gen = base.gen_by_id[gen_id]
        return finite_or_none(gen.qmin), finite_or_none(gen.qmax)

    def vm_bounds(_model, bus_id):
        return finite_or_none(base.vmin_by_bus[bus_id]), finite_or_none(base.vmax_by_bus[bus_id])

    model.va = pyo.Var(model.BUS)
    model.vm = pyo.Var(model.BUS, bounds=vm_bounds)
    model.pg = pyo.Var(model.GEN, bounds=pg_bounds)
    model.qg = pyo.Var(model.GEN, bounds=qg_bounds)
    _add_ac_arc_variables(model, data, enforce_arc_bounds=True)

    initial = build_ac_initial_point(
        case,
        base,
        warm_start=warm_start,
        stored_solution=stored_solution,
        load_flow_provider=load_flow_provider,
    )
    for bus in base.buses:
        model.vm[bus.id].set_value(initial.vm[bus.id])
        model.va[bus.id].set_value(initial.va[bus.id])
    model.va[case.slack_bus_id].set_value(0.0)

    for gen in base.generators:
        model.pg[gen.id].set_value(initial.pg[gen.id])
        model.qg[gen.id].set_value(initial.qg[gen.id])

    model._warm_start_source = initial.source


def _load_scale(model):
    return model.loadability_factor if hasattr(model, "loadability_factor") else 1.0


def _served_load_fraction(model, load_id: str):
    if hasattr(model, "load_curtailment_fraction"):
        return 1.0 - model.load_curtailment_fraction[load_id]
    return 1.0


def _active_demand(model, data: ACArcData, bus_id: str):
    base = data.base
    return sum(
        _load_scale(model) * base.load_by_id[load_id].pd * _served_load_fraction(model, load_id)
        for load_id in base.loads_at_bus[bus_id]
    )


def _reactive_demand(model, data: ACArcData, bus_id: str):
    base = data.base
    return sum(
        _load_scale(model) * base.load_by_id[load_id].qd * _served_load_fraction(model, load_id)
        for load_id in base.loads_at_bus[bus_id]
    )


def _add_ac_power_balance_constraints(model, data: ACArcData) -> None:
    base = data.base

    def p_balance_rule(_model, bus_id):
        generation = sum(_model.pg[gen_id] for gen_id in base.gens_at_bus[bus_id])
        flows_out = sum(_model.p[arc] for arc in data.bus_arcs[bus_id])
        return flows_out == generation - _active_demand(_model, data, bus_id) - base.gs_by_bus[bus_id] * _model.vm[bus_id] ** 2

    def q_balance_rule(_model, bus_id):
        generation = sum(_model.qg[gen_id] for gen_id in base.gens_at_bus[bus_id])
        flows_out = sum(_model.q[arc] for arc in data.bus_arcs[bus_id])
        return flows_out == generation - _reactive_demand(_model, data, bus_id) + base.bs_by_bus[bus_id] * _model.vm[bus_id] ** 2

    model.p_balance = pyo.Constraint(model.BUS, rule=p_balance_rule)
    model.q_balance = pyo.Constraint(model.BUS, rule=q_balance_rule)


def _add_ac_branch_constraints(
    model,
    data: ACArcData,
    *,
    include_branch_thermal: bool,
    include_branch_angles: bool,
    deduplicate_angle_constraints: bool,
) -> None:
    angle_branch_ids: list[str] = []
    limited_branch_ids: list[str] = []

    for branch in data.base.branches:
        if include_branch_angles:
            representative = data.representative_angle_branch.get(
                (branch.from_bus, branch.to_bus),
                branch.id,
            )
            if not deduplicate_angle_constraints or representative == branch.id:
                angle_branch_ids.append(branch.id)
        if include_branch_thermal and branch.rate_a > 0.0:
            limited_branch_ids.append(branch.id)

    model.ANGLE_BRANCH = pyo.Set(initialize=angle_branch_ids, ordered=True)
    model.LIMITED_BRANCH = pyo.Set(initialize=limited_branch_ids, ordered=True)

    def angle_limit_rule(_model, branch_id):
        branch = data.base.branch_by_id[branch_id]
        angle = _model.va[branch.from_bus] - _model.va[branch.to_bus]
        return finite_or_none(branch.angmin), angle, finite_or_none(branch.angmax)

    def ohms_from_p_rule(_model, branch_id):
        branch = data.base.branch_by_id[branch_id]
        pf, _, _, _ = branch_flow_exprs(_model, branch)
        return _model.p[data.arcs_from[branch_id]] == pf

    def ohms_from_q_rule(_model, branch_id):
        branch = data.base.branch_by_id[branch_id]
        _, qf, _, _ = branch_flow_exprs(_model, branch)
        return _model.q[data.arcs_from[branch_id]] == qf

    def ohms_to_p_rule(_model, branch_id):
        branch = data.base.branch_by_id[branch_id]
        _, _, pt, _ = branch_flow_exprs(_model, branch)
        return _model.p[data.arcs_to[branch_id]] == pt

    def ohms_to_q_rule(_model, branch_id):
        branch = data.base.branch_by_id[branch_id]
        _, _, _, qt = branch_flow_exprs(_model, branch)
        return _model.q[data.arcs_to[branch_id]] == qt

    def from_thermal_rule(_model, branch_id):
        arc = data.arcs_from[branch_id]
        branch = data.base.branch_by_id[branch_id]
        return _model.p[arc] ** 2 + _model.q[arc] ** 2 <= branch.rate_a**2

    def to_thermal_rule(_model, branch_id):
        arc = data.arcs_to[branch_id]
        branch = data.base.branch_by_id[branch_id]
        return _model.p[arc] ** 2 + _model.q[arc] ** 2 <= branch.rate_a**2

    model.angle_limits = pyo.Constraint(model.ANGLE_BRANCH, rule=angle_limit_rule)
    model.ohms_from_p = pyo.Constraint(model.BRANCH, rule=ohms_from_p_rule)
    model.ohms_from_q = pyo.Constraint(model.BRANCH, rule=ohms_from_q_rule)
    model.ohms_to_p = pyo.Constraint(model.BRANCH, rule=ohms_to_p_rule)
    model.ohms_to_q = pyo.Constraint(model.BRANCH, rule=ohms_to_q_rule)
    model.from_thermal_limits = pyo.Constraint(model.LIMITED_BRANCH, rule=from_thermal_rule)
    model.to_thermal_limits = pyo.Constraint(model.LIMITED_BRANCH, rule=to_thermal_rule)


def solve_ac_opf_pyomo(
    case: PowerCase,
    *,
    pyomo_solver: str = "ipopt",
    tee: bool = False,
    cost_power_units: str = "mw",
    objective_type: str = "generation_cost",
    objective_options: dict[str, object] | None = None,
    voltage_bounds: tuple[float, float] | dict[str, tuple[float, float]] | None = (0.95, 1.05),
    include_branch_thermal: bool = True,
    include_branch_angles: bool = True,
    fix_reference_angle: bool = True,
    deduplicate_angle_constraints: bool = False,
    warm_start: str = "zero",
    stored_solution=None,
    load_flow_provider=None,
    solver_options: dict[str, Any] | None = None,
    feasibility_tol: float = 1e-5,
    model_name: str | None = None,
) -> ACOPFResult:
    """Build and solve the AC OPF model with Pyomo, defaulting to Ipopt.

    Does not raise on a non-optimal termination condition; check
    ``result.success`` / ``result.status`` instead.
    """
    model = build_ac_opf_pyomo_model(
        case,
        cost_power_units=cost_power_units,
        objective_type=objective_type,
        objective_options=objective_options,
        voltage_bounds=voltage_bounds,
        include_branch_thermal=include_branch_thermal,
        include_branch_angles=include_branch_angles,
        fix_reference_angle=fix_reference_angle,
        deduplicate_angle_constraints=deduplicate_angle_constraints,
        warm_start=warm_start,
        stored_solution=stored_solution,
        load_flow_provider=load_flow_provider,
        model_name=model_name,
    )

    opt = pyo.SolverFactory(pyomo_solver)
    if opt is None or not opt.available(exception_flag=False):
        raise RuntimeError(
            f"Pyomo solver {pyomo_solver!r} is not available. Install Ipopt or "
            "provide a compatible Pyomo solver name explicitly."
        )

    effective_options = dict(DEFAULT_IPOPT_TOLERANCES) if pyomo_solver == "ipopt" else {}
    effective_options.update(solver_options or {})
    for key, value in effective_options.items():
        opt.options[key] = value

    solve_result, iterations = solve_with_iteration_capture(opt, model, tee=tee, load_solutions=False)
    try:
        if len(solve_result.solution) > 0:
            model.solutions.load_from(solve_result)
        else:
            return status_only_ac_opf_result(
                model,
                solve_result,
                message_suffix="solver returned no loadable solution",
                solve_iterations=iterations,
            )
    except Exception as exc:  # noqa: BLE001 -- keep solver diagnostics for failed stress cases
        return status_only_ac_opf_result(
            model,
            solve_result,
            message_suffix=f"solution load failed: {exc}",
            solve_iterations=iterations,
        )

    return extract_ac_opf_result(
        model, solve_result, feasibility_tol=feasibility_tol, solve_iterations=iterations
    )
