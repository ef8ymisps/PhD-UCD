"""Solver execution for the AC OPF Pyomo model.

This module owns solver execution. It does not define variables,
constraints, or the objective — see model.py/variables.py/constraints.py/
objective.py for those. It:
1. Builds the Pyomo model
2. Creates the solver
3. Applies solver options
4. Runs the solve
5. Extracts results
"""

from __future__ import annotations

from typing import Any

try:
    import pyomo.environ as pyo
except ImportError as exc:  # pragma: no cover - exercised only without Pyomo installed
    raise ImportError(
        "Pyomo is required to solve the AC OPF model. Install it with `pip install pyomo`."
    ) from exc

from own_models.src.data.case import PowerCase
from own_models.src.opf.ac.model import build_ac_opf_pyomo_model
from own_models.src.opf.ac.results import ACOPFResult, extract_ac_opf_result
from own_models.src.opf.solver_iterations import solve_with_iteration_capture


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
    warm_start: str = "case",
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

    if solver_options:
        for key, value in solver_options.items():
            opt.options[key] = value

    solve_result, iterations = solve_with_iteration_capture(opt, model, tee=tee)

    return extract_ac_opf_result(
        model, solve_result, feasibility_tol=feasibility_tol, solve_iterations=iterations
    )
