"""Solver selection and execution for the DC OPF Pyomo model."""

from __future__ import annotations

from typing import Any

import pyomo.environ as pyo
from pyomo.opt import TerminationCondition

from own_models.src.data.case import PowerCase
from own_models.src.opf.dc.data import DCOPFError
from own_models.src.opf.dc.model import build_dc_opf_pyomo_model
from own_models.src.opf.dc.results import DCOPFResult, extract_dc_opf_result
from own_models.src.opf.solver_iterations import solve_with_iteration_capture


def make_solver(solver_name: str | None = None):
    """Return an available Pyomo solver, defaulting to HiGHS for DC OPF."""
    candidates = [solver_name] if solver_name is not None else [
        "highs"
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
        "No suitable Pyomo HiGHS solver found. Install highspy or provide a "
        "compatible Pyomo solver name explicitly."
    )


def solve_dc_opf_pyomo(
    case: PowerCase,
    *,
    solver_name: str | None = None,
    tee: bool = False,
    cost_power_units: str = "mw",
    include_branch_limits: bool = True,
    include_angle_limits: bool = True,
    fix_reference_angle: bool = True,
) -> tuple[pyo.ConcreteModel, Any, DCOPFResult]:
    """Build and solve the DC OPF model with Pyomo and HiGHS."""
    model = build_dc_opf_pyomo_model(
        case,
        cost_power_units=cost_power_units,
        include_branch_limits=include_branch_limits,
        include_angle_limits=include_angle_limits,
        fix_reference_angle=fix_reference_angle,
    )

    solver, selected_solver_name = make_solver(solver_name)
    raw_results, iterations = solve_with_iteration_capture(solver, model, tee=tee)

    status = str(raw_results.solver.status)
    termination = str(raw_results.solver.termination_condition)

    acceptable = {
        TerminationCondition.optimal,
        TerminationCondition.locallyOptimal,
        TerminationCondition.feasible,
    }

    if raw_results.solver.termination_condition not in acceptable:
        raise DCOPFError(
            f"DC OPF failed with solver {selected_solver_name}. "
            f"Status={status}, termination={termination}."
        )

    result = extract_dc_opf_result(
        model,
        status=status,
        termination_condition=termination,
        solve_iterations=iterations,
    )

    return model, raw_results, result
