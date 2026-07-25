from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import LinearConstraint, linprog, minimize

from own_models.src.data.case import PowerCase
from own_models.src.network.dc_matrices import dc_branch_conductance, dc_branch_flow_coefficient
from own_models.src.opf.objectives import generation_cost_from_vector


@dataclass(frozen=True)
class DCOPFResult:
    """Container for DC OPF status, objective, dispatch, angles, and flows."""
    success: bool
    status: str
    message: str
    objective: float
    pg: dict[str, float]
    theta: dict[str, float]
    branch_flows: dict[str, float]
    max_power_balance_violation: float
    branch_flows_from: dict[str, float] | None = None
    branch_flows_to: dict[str, float] | None = None
    branch_losses: dict[str, float] | None = None
    raw_result: object | None = None


def _active_generators(case: PowerCase):
    """Return online generators that participate as Pg variables."""
    return [gen for gen in case.generators if gen.status == 1]


def _loads_by_bus(case: PowerCase) -> dict[str, float]:
    """Aggregate active load plus shunt conductance demand by bus."""
    result = {bus.id: 0.0 for bus in case.buses}
    for load in case.loads:
        if load.status == 1:
            result[load.bus] += load.pd
    for shunt in case.shunts:
        if shunt.status == 1:
            result[shunt.bus] += shunt.gs
    return result


def _unpack(x: np.ndarray, case: PowerCase, active_generators) -> tuple[dict[str, float], dict[str, float]]:
    """Split a DC OPF vector into Pg and theta dictionaries."""
    n_gen = len(active_generators)
    pg_values = x[:n_gen]
    theta_values = x[n_gen:]
    pg = {gen.id: float(value) for gen, value in zip(active_generators, pg_values, strict=True)}
    theta = {bus.id: float(theta_values[idx]) for idx, bus in enumerate(case.buses)}
    return pg, theta


def _validate_loss_model(loss_model: str) -> None:
    if loss_model not in {"lossless", "castillo_quadratic"}:
        raise ValueError(f"Unsupported DC OPF loss_model: {loss_model!r}")


def _branch_flow_components(branch, theta: dict[str, float], *, loss_model: str):
    """Evaluate directional from/to injections and active loss for a branch."""
    delta = theta[branch.from_bus] - theta[branch.to_bus]
    beta = dc_branch_flow_coefficient(branch)
    if loss_model == "lossless":
        p_from = beta * delta
        p_to = -p_from
        return p_from, p_to, 0.0

    if loss_model == "castillo_quadratic":
        conductance = dc_branch_conductance(branch)
        loss = conductance * delta**2
        half_loss = 0.5 * loss
        return beta * delta + half_loss, -beta * delta + half_loss, loss

    _validate_loss_model(loss_model)
    raise AssertionError("unreachable")


def _branch_flow(branch, theta: dict[str, float]) -> float:
    """Evaluate compatible from-side DC branch flow from the angle dictionary."""
    return _branch_flow_components(branch, theta, loss_model="lossless")[0]


def _power_balance_vector(
    case: PowerCase,
    pg: dict[str, float],
    theta: dict[str, float],
    *,
    loss_model: str = "lossless",
) -> np.ndarray:
    """Evaluate active-power balance residual at every bus."""
    _validate_loss_model(loss_model)
    demand = _loads_by_bus(case)
    balance = []
    for bus in case.buses:
        gen_sum = sum(
            pg[gen.id]
            for gen in case.generators
            if gen.status == 1 and gen.bus == bus.id and gen.id in pg
        )
        branch_injection = 0.0
        for branch in case.branches:
            if branch.status == 0:
                continue
            p_from, p_to, _ = _branch_flow_components(branch, theta, loss_model=loss_model)
            if branch.from_bus == bus.id:
                branch_injection += p_from
            elif branch.to_bus == bus.id:
                branch_injection += p_to
        balance.append(gen_sum - demand[bus.id] - branch_injection)
    return np.array(balance, dtype=float)


def _initial_point(case: PowerCase, active_generators) -> np.ndarray:
    """Build a bounded Pg and case-angle initial point for SLSQP."""
    pg0 = []
    for gen in active_generators:
        guess = gen.pg0
        if np.isfinite(gen.pmin):
            guess = max(guess, gen.pmin)
        if np.isfinite(gen.pmax):
            guess = min(guess, gen.pmax)
        pg0.append(guess)
    theta0 = [bus.va0 for bus in case.buses]
    theta0[case.bus_id_to_index[case.slack_bus_id]] = 0.0
    return np.array(pg0 + theta0, dtype=float)


def _linear_constraint_matrices(case: PowerCase, active_generators):
    """Build equality and inequality matrices for the linear DC OPF constraints."""
    n_gen = len(active_generators)
    n_bus = len(case.buses)
    n_var = n_gen + n_bus
    demand = _loads_by_bus(case)

    # Variable order is [Pg for active generators, theta for all buses].
    # Rows below encode nodal active balance, slack angle, and branch limits.
    eq_rows = []
    eq_values = []
    ineq_rows = []
    ineq_lower = []
    ineq_upper = []

    for bus_idx, bus in enumerate(case.buses):
        row = np.zeros(n_var)
        for gen_idx, gen in enumerate(active_generators):
            if gen.bus == bus.id:
                row[gen_idx] = 1.0

        for branch in case.branches:
            if branch.status == 0:
                continue
            coeff = dc_branch_flow_coefficient(branch)
            from_idx = case.bus_id_to_index[branch.from_bus]
            to_idx = case.bus_id_to_index[branch.to_bus]

            if branch.from_bus == bus.id:
                row[n_gen + from_idx] -= coeff
                row[n_gen + to_idx] += coeff
            elif branch.to_bus == bus.id:
                row[n_gen + from_idx] += coeff
                row[n_gen + to_idx] -= coeff

        eq_rows.append(row)
        eq_values.append(demand[bus.id])

    slack_row = np.zeros(n_var)
    slack_row[n_gen + case.bus_id_to_index[case.slack_bus_id]] = 1.0
    eq_rows.append(slack_row)
    eq_values.append(0.0)

    for branch in case.branches:
        if branch.status == 0:
            continue

        from_idx = case.bus_id_to_index[branch.from_bus]
        to_idx = case.bus_id_to_index[branch.to_bus]

        angle_row = np.zeros(n_var)
        angle_row[n_gen + from_idx] = 1.0
        angle_row[n_gen + to_idx] = -1.0
        ineq_rows.append(angle_row)
        ineq_lower.append(branch.angmin)
        ineq_upper.append(branch.angmax)

        if branch.rate_a > 0.0:
            flow_row = np.zeros(n_var)
            coeff = dc_branch_flow_coefficient(branch)
            flow_row[n_gen + from_idx] = coeff
            flow_row[n_gen + to_idx] = -coeff
            ineq_rows.append(flow_row)
            ineq_lower.append(-branch.rate_a)
            ineq_upper.append(branch.rate_a)

    a_eq = np.vstack(eq_rows)
    b_eq = np.array(eq_values)
    if ineq_rows:
        a_ineq = np.vstack(ineq_rows)
        ineq_lower_array = np.array(ineq_lower)
        ineq_upper_array = np.array(ineq_upper)
    else:
        a_ineq = np.zeros((0, n_var))
        ineq_lower_array = np.zeros(0)
        ineq_upper_array = np.zeros(0)
    return a_eq, b_eq, a_ineq, ineq_lower_array, ineq_upper_array


def _linear_constraints(case: PowerCase, active_generators) -> list[LinearConstraint]:
    """Wrap DC OPF constraint matrices as SciPy LinearConstraint objects."""
    a_eq, b_eq, a_ineq, ineq_lower, ineq_upper = _linear_constraint_matrices(
        case, active_generators
    )
    constraints = [LinearConstraint(a_eq, b_eq, b_eq)]
    if a_ineq.shape[0] > 0:
        constraints.append(LinearConstraint(a_ineq, ineq_lower, ineq_upper))
    return constraints


def _variable_bounds(case: PowerCase, active_generators):
    """Build Pg bounds and unconstrained theta bounds for DC OPF."""
    bounds = []
    for gen in active_generators:
        lower = gen.pmin if np.isfinite(gen.pmin) else None
        upper = gen.pmax if np.isfinite(gen.pmax) else None
        bounds.append((lower, upper))
    bounds.extend([(None, None) for _ in case.buses])
    return bounds


def _all_costs_linear(active_generators, *, tol: float = 1e-12) -> bool:
    """Return true when every active generator has negligible quadratic cost."""
    return all(abs(gen.cost_c2) <= tol for gen in active_generators)


def _solve_linear_dc_opf(case: PowerCase, active_generators, n_gen: int, n_bus: int):
    """Solve the linear-cost DC OPF path with scipy.linprog."""
    # Linear costs can use HiGHS through scipy.linprog. Quadratic costs fall
    # back to the SLSQP path in solve_dc_opf.
    n_var = n_gen + n_bus
    c = np.zeros(n_var)
    constant = 0.0
    for gen_idx, gen in enumerate(active_generators):
        c[gen_idx] = gen.cost_c1 * case.base_mva
        constant += gen.cost_c0

    a_eq, b_eq, a_ineq, ineq_lower, ineq_upper = _linear_constraint_matrices(
        case, active_generators
    )

    a_ub_rows = []
    b_ub_values = []
    for row, lower, upper in zip(a_ineq, ineq_lower, ineq_upper, strict=True):
        if np.isfinite(upper):
            a_ub_rows.append(row)
            b_ub_values.append(upper)
        if np.isfinite(lower):
            a_ub_rows.append(-row)
            b_ub_values.append(-lower)

    a_ub = np.vstack(a_ub_rows) if a_ub_rows else None
    b_ub = np.array(b_ub_values) if b_ub_values else None

    result = linprog(
        c,
        A_ub=a_ub,
        b_ub=b_ub,
        A_eq=a_eq,
        b_eq=b_eq,
        bounds=_variable_bounds(case, active_generators),
        method="highs",
    )

    if result.x is None:
        x = _initial_point(case, active_generators)
        objective = float("nan")
    else:
        x = result.x
        objective = float(result.fun + constant)

    return result, x, objective


def _branch_limit_margins(case: PowerCase, theta: dict[str, float], *, loss_model: str) -> np.ndarray:
    margins = []
    for branch in case.branches:
        if branch.status == 0 or branch.rate_a <= 0.0:
            continue
        p_from, p_to, _ = _branch_flow_components(branch, theta, loss_model=loss_model)
        margins.extend(
            [
                branch.rate_a - p_from,
                p_from + branch.rate_a,
                branch.rate_a - p_to,
                p_to + branch.rate_a,
            ]
        )
    return np.array(margins, dtype=float)


def _angle_limit_margins(case: PowerCase, theta: dict[str, float]) -> np.ndarray:
    margins = []
    for branch in case.branches:
        if branch.status == 0:
            continue
        angle = theta[branch.from_bus] - theta[branch.to_bus]
        margins.extend([angle - branch.angmin, branch.angmax - angle])
    return np.array(margins, dtype=float)


def _solve_nonlinear_dc_opf(
    case: PowerCase,
    active_generators,
    n_gen: int,
    *,
    loss_model: str,
    method: str,
    max_iter: int,
    ftol: float,
):
    def objective(x: np.ndarray) -> float:
        return generation_cost_from_vector(case, active_generators, x[:n_gen])

    def unpack_x(x: np.ndarray):
        return _unpack(x, case, active_generators)

    def equality_constraints(x: np.ndarray) -> np.ndarray:
        pg, theta = unpack_x(x)
        return np.concatenate(
            [
                _power_balance_vector(case, pg, theta, loss_model=loss_model),
                np.array([theta[case.slack_bus_id]], dtype=float),
            ]
        )

    def inequality_constraints(x: np.ndarray) -> np.ndarray:
        _, theta = unpack_x(x)
        return np.concatenate(
            [
                _branch_limit_margins(case, theta, loss_model=loss_model),
                _angle_limit_margins(case, theta),
            ]
        )

    result = minimize(
        objective,
        _initial_point(case, active_generators),
        method="SLSQP" if method.lower() == "auto" else method,
        bounds=_variable_bounds(case, active_generators),
        constraints=[
            {"type": "eq", "fun": equality_constraints},
            {"type": "ineq", "fun": inequality_constraints},
        ],
        options={"maxiter": max_iter, "ftol": ftol},
    )
    objective_value = float(result.fun) if np.isfinite(result.fun) else float("nan")
    return result, result.x, objective_value


def solve_dc_opf_scipy(
    case: PowerCase,
    *,
    method: str = "auto",
    loss_model: str = "lossless",
    max_iter: int = 1000,
    ftol: float = 1e-9,
    feasibility_tol: float = 1e-6,
) -> DCOPFResult:
    """Solve DC OPF with the SciPy reference implementation."""
    _validate_loss_model(loss_model)
    if method.lower() == "pyomo":
        raise ValueError(
            "Use own_models.src.opf.dc_opf_pyomo.solve_dc_opf_pyomo(...) "
            "for the Pyomo DC OPF backend."
        )

    active_generators = _active_generators(case)
    n_gen = len(active_generators)
    n_bus = len(case.buses)

    if not active_generators:
        raise ValueError("DC OPF requires at least one active generator.")

    def objective(x: np.ndarray) -> float:
        """Evaluate generation cost from the Pg slice of the DC OPF vector."""
        return generation_cost_from_vector(case, active_generators, x[:n_gen])

    def unpack_x(x: np.ndarray):
        """Unpack a candidate vector using this case's active generator layout."""
        return _unpack(x, case, active_generators)

    # Auto mode chooses LP only when every active generator has zero quadratic
    # cost; otherwise SLSQP handles the nonlinear objective.
    use_linear_solver = (
        loss_model == "lossless"
        and method.lower() in {"auto", "linprog", "highs"}
        and _all_costs_linear(active_generators)
    )

    if use_linear_solver:
        result, x, objective_value = _solve_linear_dc_opf(case, active_generators, n_gen, n_bus)
    elif loss_model == "castillo_quadratic":
        result, x, objective_value = _solve_nonlinear_dc_opf(
            case,
            active_generators,
            n_gen,
            loss_model=loss_model,
            method=method,
            max_iter=max_iter,
            ftol=ftol,
        )
    else:
        constraints = _linear_constraints(case, active_generators)
        result = minimize(
            objective,
            _initial_point(case, active_generators),
            method="SLSQP" if method.lower() == "auto" else method,
            bounds=_variable_bounds(case, active_generators),
            constraints=constraints,
            options={"maxiter": max_iter, "ftol": ftol},
        )
        x = result.x
        objective_value = float(result.fun) if np.isfinite(result.fun) else float("nan")

    pg, theta = unpack_x(x)
    branch_flow_components = {
        branch.id: _branch_flow_components(branch, theta, loss_model=loss_model)
        for branch in case.branches
        if branch.status == 1
    }
    branch_flows_from = {
        branch_id: float(values[0])
        for branch_id, values in branch_flow_components.items()
    }
    branch_flows_to = {
        branch_id: float(values[1])
        for branch_id, values in branch_flow_components.items()
    }
    branch_losses = {
        branch_id: float(values[2])
        for branch_id, values in branch_flow_components.items()
    }
    branch_flows = dict(branch_flows_from)
    balance = _power_balance_vector(case, pg, theta, loss_model=loss_model)
    max_balance = float(np.linalg.norm(balance, ord=np.inf))
    feasible = max_balance <= feasibility_tol
    if loss_model == "castillo_quadratic":
        branch_margins = _branch_limit_margins(case, theta, loss_model=loss_model)
        angle_margins = _angle_limit_margins(case, theta)
        feasible = (
            feasible
            and (branch_margins.size == 0 or float(branch_margins.min()) >= -feasibility_tol)
            and (angle_margins.size == 0 or float(angle_margins.min()) >= -feasibility_tol)
        )

    return DCOPFResult(
        success=bool(result.success or (loss_model == "castillo_quadratic" and feasible)),
        status=str(result.status),
        message=str(result.message),
        objective=objective_value,
        pg=pg,
        theta=theta,
        branch_flows=branch_flows,
        max_power_balance_violation=max_balance,
        branch_flows_from=branch_flows_from,
        branch_flows_to=branch_flows_to,
        branch_losses=branch_losses,
        raw_result=result,
    )


def solve_dc_opf(
    case: PowerCase,
    *,
    method: str = "auto",
    loss_model: str = "lossless",
    max_iter: int = 1000,
    ftol: float = 1e-9,
    feasibility_tol: float = 1e-6,
) -> DCOPFResult:
    """Backward-compatible DC OPF entry point using SciPy by default."""
    return solve_dc_opf_scipy(
        case,
        method=method,
        loss_model=loss_model,
        max_iter=max_iter,
        ftol=ftol,
        feasibility_tol=feasibility_tol,
    )
