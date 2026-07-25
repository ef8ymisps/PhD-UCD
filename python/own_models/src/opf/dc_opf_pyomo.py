from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pyomo.environ as pyo
from pyomo.opt import SolverStatus, TerminationCondition

from own_models.src.data.case import PowerCase


class DCOPFError(RuntimeError):
    """Raised when the DC OPF model cannot be built or solved."""


@dataclass(frozen=True)
class DCOPFResult:
    status: str
    termination_condition: str
    objective: float | None
    pg: dict[str, float]
    theta: dict[str, float]
    branch_flow: dict[str, float]
    total_generation: float
    total_load: float
    max_power_balance_residual: float


def _active_buses(case: PowerCase):
    return list(case.buses)


def _active_generators(case: PowerCase):
    return [gen for gen in case.generators if gen.status == 1]


def _active_branches(case: PowerCase):
    return [branch for branch in case.branches if branch.status == 1]


def _active_loads(case: PowerCase):
    return [load for load in case.loads if load.status == 1]


def _bus_ids(case: PowerCase) -> list[str]:
    return [bus.id for bus in _active_buses(case)]


def _generator_ids(case: PowerCase) -> list[str]:
    return [gen.id for gen in _active_generators(case)]


def _branch_ids(case: PowerCase) -> list[str]:
    return [branch.id for branch in _active_branches(case)]


def _build_lookup_by_id(items) -> dict[str, Any]:
    return {item.id: item for item in items}


def _active_power_demand_by_bus(case: PowerCase) -> dict[str, float]:
    demand = {bus.id: 0.0 for bus in _active_buses(case)}

    for load in _active_loads(case):
        if load.bus not in demand:
            raise DCOPFError(f"Load {load.id} references unknown bus {load.bus}.")
        demand[load.bus] += float(load.pd)

    return demand


def _generators_at_bus(case: PowerCase) -> dict[str, list[str]]:
    gens_at_bus = {bus.id: [] for bus in _active_buses(case)}

    for gen in _active_generators(case):
        if gen.bus not in gens_at_bus:
            raise DCOPFError(f"Generator {gen.id} references unknown bus {gen.bus}.")
        gens_at_bus[gen.bus].append(gen.id)

    return gens_at_bus


def _branches_by_bus(case: PowerCase) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    outgoing = {bus.id: [] for bus in _active_buses(case)}
    incoming = {bus.id: [] for bus in _active_buses(case)}

    for branch in _active_branches(case):
        if branch.from_bus not in outgoing:
            raise DCOPFError(f"Branch {branch.id} references unknown from_bus {branch.from_bus}.")
        if branch.to_bus not in incoming:
            raise DCOPFError(f"Branch {branch.id} references unknown to_bus {branch.to_bus}.")

        outgoing[branch.from_bus].append(branch.id)
        incoming[branch.to_bus].append(branch.id)

    return outgoing, incoming


def _dc_beta(branch) -> float:
    """
    Resistance-adjusted lossless DC branch coefficient.

    Pf_ij = beta_ij * (theta_i - theta_j)

    beta = x / (r^2 + x^2)

    This is the convention we are currently using from the earlier model.
    It is lossless because Pf_ji = -Pf_ij.
    """
    r = float(branch.r)
    x = float(branch.x)
    denom = r * r + x * x

    if denom <= 1e-14:
        raise DCOPFError(
            f"Branch {branch.id} has invalid impedance r={r}, x={x}; "
            "cannot compute DC coefficient."
        )

    return x / denom


def _has_branch_rate(branch) -> bool:
    return math.isfinite(float(branch.rate_a)) and float(branch.rate_a) > 0.0


def _has_angle_limits(branch) -> bool:
    return (
        math.isfinite(float(branch.angmin))
        and math.isfinite(float(branch.angmax))
        and float(branch.angmin) <= float(branch.angmax)
    )


def _validate_case_for_dc_opf(case: PowerCase) -> None:
    bus_ids = set(_bus_ids(case))
    gen_ids = _generator_ids(case)
    branch_ids = _branch_ids(case)

    if not bus_ids:
        raise DCOPFError("DC OPF requires at least one bus.")
    if not gen_ids:
        raise DCOPFError("DC OPF requires at least one active generator.")
    if not branch_ids:
        raise DCOPFError("DC OPF requires at least one active branch.")

    if case.slack_bus_id not in bus_ids:
        raise DCOPFError(
            f"Slack bus {case.slack_bus_id!r} is not an active bus in this case."
        )

    total_load = sum(_active_power_demand_by_bus(case).values())
    total_pmin = sum(float(gen.pmin) for gen in _active_generators(case))
    total_pmax = sum(float(gen.pmax) for gen in _active_generators(case))

    if total_load < total_pmin - 1e-8:
        raise DCOPFError(
            f"Total active load {total_load:.6g} p.u. is below total Pmin "
            f"{total_pmin:.6g} p.u.; DC OPF is likely infeasible."
        )

    if total_load > total_pmax + 1e-8:
        raise DCOPFError(
            f"Total active load {total_load:.6g} p.u. exceeds total Pmax "
            f"{total_pmax:.6g} p.u.; DC OPF is infeasible before network limits."
        )


def cost_power_expression(pg_pu, base_mva: float, cost_power_units: str):
    """
    Return the power expression used inside the generator cost.

    In the Python/PyPowSyBl path:
        Pg variable is p.u.
        costs are MW-based
        so use Pg * base_mva.

    In a future Python path with p.u.-scaled costs:
        use Pg directly.
    """
    if cost_power_units == "mw":
        return pg_pu * base_mva
    if cost_power_units == "pu":
        return pg_pu
    raise DCOPFError(f"Unsupported cost_power_units={cost_power_units!r}.")


def build_dc_opf_pyomo_model(
    case: PowerCase,
    *,
    cost_power_units: str = "mw",
    include_branch_limits: bool = True,
    include_angle_limits: bool = True,
) -> pyo.ConcreteModel:
    """
    Build a lossless resistance-adjusted DC OPF model.

    Variables:
        Pg[g]      active generation in p.u.
        theta[i]   bus voltage angle in radians

    Branch flow:
        Pf[l] = beta[l] * (theta[from_bus[l]] - theta[to_bus[l]])

    Power balance:
        sum Pg at bus - Pd = outgoing Pf - incoming Pf

    Objective:
        sum c2 * Pg_cost^2 + c1 * Pg_cost + c0

    For the current Python/PyPowSyBl adapter, use cost_power_units="mw".
    """
    _validate_case_for_dc_opf(case)

    buses = _active_buses(case)
    generators = _active_generators(case)
    branches = _active_branches(case)

    bus_ids = [bus.id for bus in buses]
    gen_ids = [gen.id for gen in generators]
    branch_ids = [branch.id for branch in branches]

    gen_by_id = _build_lookup_by_id(generators)
    branch_by_id = _build_lookup_by_id(branches)

    pd_by_bus = _active_power_demand_by_bus(case)
    gens_at_bus = _generators_at_bus(case)
    outgoing, incoming = _branches_by_bus(case)

    beta = {branch.id: _dc_beta(branch) for branch in branches}
    from_bus = {branch.id: branch.from_bus for branch in branches}
    to_bus = {branch.id: branch.to_bus for branch in branches}

    branches_with_rates = [
        branch.id for branch in branches if include_branch_limits and _has_branch_rate(branch)
    ]

    branches_with_angle_limits = [
        branch.id for branch in branches if include_angle_limits and _has_angle_limits(branch)
    ]

    m = pyo.ConcreteModel(name=f"DC_OPF_{case.name}")

    # -----------------------------
    # Sets
    # -----------------------------
    m.BUS = pyo.Set(initialize=bus_ids, ordered=True)
    m.GEN = pyo.Set(initialize=gen_ids, ordered=True)
    m.BRANCH = pyo.Set(initialize=branch_ids, ordered=True)
    m.BRANCH_WITH_RATE = pyo.Set(initialize=branches_with_rates, ordered=True)
    m.BRANCH_WITH_ANGLE_LIMIT = pyo.Set(initialize=branches_with_angle_limits, ordered=True)

    # -----------------------------
    # Variables
    # -----------------------------
    def pg_bounds(_m, g):
        gen = gen_by_id[g]
        return float(gen.pmin), float(gen.pmax)

    def pg_initial(_m, g):
        gen = gen_by_id[g]
        return min(max(float(gen.pg0), float(gen.pmin)), float(gen.pmax))

    m.Pg = pyo.Var(m.GEN, bounds=pg_bounds, initialize=pg_initial)
    m.theta = pyo.Var(m.BUS, initialize=0.0)

    # -----------------------------
    # Branch active-power flow expression
    # -----------------------------
    def branch_flow_rule(_m, branch_id):
        return beta[branch_id] * (
            _m.theta[from_bus[branch_id]] - _m.theta[to_bus[branch_id]]
        )

    m.Pf = pyo.Expression(m.BRANCH, rule=branch_flow_rule)

    # -----------------------------
    # Objective
    # -----------------------------
    def objective_rule(_m):
        total_cost = 0.0

        for gen in generators:
            pg_cost = cost_power_expression(
                _m.Pg[gen.id],
                case.base_mva,
                cost_power_units,
            )

            total_cost += (
                float(gen.cost_c2) * pg_cost**2
                + float(gen.cost_c1) * pg_cost
                + float(gen.cost_c0)
            )

        return total_cost

    m.objective = pyo.Objective(rule=objective_rule, sense=pyo.minimize)

    # -----------------------------
    # Active-power balance
    # -----------------------------
    def power_balance_rule(_m, bus_id):
        generation = sum(_m.Pg[g] for g in gens_at_bus[bus_id])
        demand = pd_by_bus[bus_id]
        outgoing_flow = sum(_m.Pf[l] for l in outgoing[bus_id])
        incoming_flow = sum(_m.Pf[l] for l in incoming[bus_id])

        return generation - demand == outgoing_flow - incoming_flow

    m.power_balance = pyo.Constraint(m.BUS, rule=power_balance_rule)

    # -----------------------------
    # Reference angle
    # -----------------------------
    m.reference_angle = pyo.Constraint(expr=m.theta[case.slack_bus_id] == 0.0)

    # -----------------------------
    # Branch flow limits
    # -----------------------------
    def branch_flow_limit_rule(_m, branch_id):
        rate = float(branch_by_id[branch_id].rate_a)
        return -rate, _m.Pf[branch_id], rate

    m.branch_flow_limit = pyo.Constraint(
        m.BRANCH_WITH_RATE,
        rule=branch_flow_limit_rule,
    )

    # -----------------------------
    # Angle-difference limits
    # -----------------------------
    def angle_limit_rule(_m, branch_id):
        branch = branch_by_id[branch_id]
        angle_difference = _m.theta[branch.from_bus] - _m.theta[branch.to_bus]
        return float(branch.angmin), angle_difference, float(branch.angmax)

    m.angle_difference_limit = pyo.Constraint(
        m.BRANCH_WITH_ANGLE_LIMIT,
        rule=angle_limit_rule,
    )

    # Store useful dictionaries on the model for result extraction/debugging.
    m._case = case
    m._gen_by_id = gen_by_id
    m._branch_by_id = branch_by_id
    m._pd_by_bus = pd_by_bus
    m._gens_at_bus = gens_at_bus
    m._outgoing = outgoing
    m._incoming = incoming
    m._beta = beta
    m._cost_power_units = cost_power_units

    return m


def _make_solver(solver_name: str | None = None):
    candidates = [solver_name] if solver_name is not None else [
        "appsi_highs",
        "highs",
        "glpk",
        "cbc",
    ]

    for name in candidates:
        if name is None:
            continue

        solver = pyo.SolverFactory(name)

        try:
            available = solver.available(exception_flag=False)
        except TypeError:
            available = solver.available()

        if available:
            return solver, name

    raise DCOPFError(
        "No suitable Pyomo solver found. Install highspy for appsi_highs/highs, "
        "or install another LP/QP solver."
    )


def solve_dc_opf_pyomo(
    case: PowerCase,
    *,
    solver_name: str | None = None,
    tee: bool = False,
    cost_power_units: str = "mw",
    include_branch_limits: bool = True,
    include_angle_limits: bool = True,
) -> tuple[pyo.ConcreteModel, Any, DCOPFResult]:
    model = build_dc_opf_pyomo_model(
        case,
        cost_power_units=cost_power_units,
        include_branch_limits=include_branch_limits,
        include_angle_limits=include_angle_limits,
    )

    solver, selected_solver_name = _make_solver(solver_name)

    results = solver.solve(model, tee=tee)

    status = str(results.solver.status)
    termination = str(results.solver.termination_condition)

    acceptable = {
        TerminationCondition.optimal,
        TerminationCondition.locallyOptimal,
        TerminationCondition.feasible,
    }

    if results.solver.termination_condition not in acceptable:
        raise DCOPFError(
            f"DC OPF did not solve successfully with solver {selected_solver_name}. "
            f"Status={status}, termination={termination}."
        )

    result = extract_dc_opf_result(model, status=status, termination_condition=termination)

    return model, results, result


def extract_dc_opf_result(
    model: pyo.ConcreteModel,
    *,
    status: str = "",
    termination_condition: str = "",
) -> DCOPFResult:
    case: PowerCase = model._case

    pg = {g: float(pyo.value(model.Pg[g])) for g in model.GEN}
    theta = {i: float(pyo.value(model.theta[i])) for i in model.BUS}
    branch_flow = {l: float(pyo.value(model.Pf[l])) for l in model.BRANCH}

    objective = float(pyo.value(model.objective))

    total_generation = sum(pg.values())
    total_load = sum(model._pd_by_bus.values())

    max_residual = 0.0

    for bus_id in model.BUS:
        generation = sum(pg[g] for g in model._gens_at_bus[bus_id])
        demand = model._pd_by_bus[bus_id]
        outgoing_flow = sum(branch_flow[l] for l in model._outgoing[bus_id])
        incoming_flow = sum(branch_flow[l] for l in model._incoming[bus_id])

        residual = generation - demand - outgoing_flow + incoming_flow
        max_residual = max(max_residual, abs(residual))

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
    )