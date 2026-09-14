"""Solve entry point for the DC-SCOPF Pyomo model.

Deliberately does NOT raise on infeasibility (unlike
own_models.src.opf.dc.solve.solve_dc_opf_pyomo) -- DC-SCOPF's whole point
is to distinguish securable from genuinely infeasible contingencies, so an
infeasible result is expected, useful output, not an error condition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pyomo.environ as pyo
from pyomo.opt import TerminationCondition

from own_models.src.analysis.contingencies import Contingency
from own_models.src.data.case import PowerCase
from own_models.src.opf.dc.solve import DEFAULT_HIGHS_TOLERANCES, apply_solver_tolerances, make_solver
from own_models.src.opf.solver_iterations import load_solution_into_model, solve_with_diagnostics_capture
from own_models.src.opf.dc_scopf.model import DCSCOPFSkippedContingency, build_dc_scopf_pyomo_model

OPTIMAL_LIKE = {
    TerminationCondition.optimal,
    TerminationCondition.locallyOptimal,
    TerminationCondition.feasible,
}


@dataclass(frozen=True)
class DCSCOPFResult:
    status: str
    termination_condition: str
    is_optimal: bool
    objective: float | None
    pg: dict[str, float] | None
    theta: dict[str, float] | None
    solve_iterations: float = float("nan")
    pf: dict[str, float] | None = None
    solver_reported_time_s: float = float("nan")


def _extract_result(
    model: pyo.ConcreteModel, raw_results, status: str, termination: str, iterations: float, solver_time: float,
) -> DCSCOPFResult:
    is_optimal = termination.lower() in {t.name.lower() for t in OPTIMAL_LIKE}
    if not is_optimal:
        return DCSCOPFResult(status, termination, False, None, None, None, iterations, None, solver_time)

    # load_solutions=False (see solve_dc_scopf_pyomo) means the solution is
    # NOT automatically loaded into the model's variables -- do that
    # explicitly now that we know a real solution exists to load.
    # (Dispatches between classic Pyomo's and appsi's differently-shaped
    # load APIs -- see load_solution_into_model's own docstring.)
    load_solution_into_model(raw_results, model)

    pg = {g: float(pyo.value(model.Pg[g])) for g in model.GEN}
    theta = {i: float(pyo.value(model.theta[i])) for i in model.BUS}
    pf = {b: float(pyo.value(model.Pf[b])) for b in model.Pf}
    objective = float(pyo.value(model.objective))
    return DCSCOPFResult(status, termination, True, objective, pg, theta, iterations, pf, solver_time)


def solve_dc_scopf_pyomo(
    case: PowerCase,
    contingencies: list[Contingency],
    *,
    solver_name: str | None = None,
    tee: bool = False,
    cost_power_units: str = "mw",
    fix_reference_angle: bool = True,
    solver_options: dict[str, Any] | None = None,
    anchor_pg: dict[str, float] | None = None,
) -> tuple[pyo.ConcreteModel, DCSCOPFResult, list[DCSCOPFSkippedContingency]]:
    """Build and solve the DC-SCOPF model. Returns `(model, result, skipped)`
    -- `result.is_optimal` is False (with `pg`/`theta`/`objective` all
    `None`) for a genuinely infeasible contingency set, not an exception.

    `anchor_pg`, if given, is forwarded as-is to
    `build_dc_scopf_pyomo_model` (see its own docstring) instead of letting
    the model build solve its own anchor DC-OPF -- pass the SAME persisted
    anchor (topologies/<system>/anchor_pg.csv, written by
    75_run_anchor_pg.py) that Julia's build_dc_scopf_model call also reads,
    so both languages derive their generator-outage automatic-response
    weights from one shared vertex rather than two independently-solved
    (and, for an LP-degenerate anchor like 60nordic's, potentially
    DIFFERENT) ones."""
    model, skipped = build_dc_scopf_pyomo_model(
        case, contingencies, cost_power_units=cost_power_units, fix_reference_angle=fix_reference_angle,
        anchor_pg=anchor_pg,
    )

    solver, selected_solver_name = make_solver(solver_name)
    # See the matching comment in own_models.src.opf.dc.solve.solve_dc_opf_pyomo:
    # DEFAULT_HIGHS_TOLERANCES only applies to HiGHS-backed interfaces.
    effective_options = dict(DEFAULT_HIGHS_TOLERANCES) if selected_solver_name in ("highs", "highs_appsi") else {}
    effective_options.update(solver_options or {})
    apply_solver_tolerances(solver, selected_solver_name, effective_options)

    # load_solutions=False: unlike plain DC-OPF (never intentionally
    # infeasible), DC-SCOPF's whole point is distinguishing securable from
    # infeasible contingencies -- Pyomo's native HiGHS interface otherwise
    # raises NoFeasibleSolutionError while trying to auto-load a solution
    # that doesn't exist, before this function even gets a termination
    # condition to check.
    raw_results, iterations, solver_time = solve_with_diagnostics_capture(solver, model, tee=tee, load_solutions=False)
    status = str(raw_results.solver.status)
    termination = str(raw_results.solver.termination_condition)

    result = _extract_result(model, raw_results, status, termination, iterations, solver_time)
    return model, result, skipped
