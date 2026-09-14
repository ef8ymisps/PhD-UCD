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

# Pinned explicitly rather than left to HiGHS's own (solver-version-dependent)
# defaults. Verified via highspy against the installed HiGHS 1.15.1 that
# these values already match what HiGHS defaults to today -- pinning changes
# no solve behavior now, it protects against a future HiGHS upgrade silently
# changing these out from under the validation pipeline, and matches the
# equivalent pin on the Julia side (julia/own_models/src/opf/dc/solve.jl).
# See docs/notes/solver_tolerances.md.
DEFAULT_HIGHS_TOLERANCES: dict[str, Any] = {
    "primal_feasibility_tolerance": 1e-7,
    "dual_feasibility_tolerance": 1e-7,
    "ipm_optimality_tolerance": 1e-8,
}


def make_solver(solver_name: str | None = None):
    """Return an available Pyomo solver, defaulting to HiGHS for DC OPF.

    Preference order when `solver_name` is not given: appsi-Gurobi (if
    licensed) -> appsi-HiGHS -> classic (file-based) HiGHS. appsi (Pyomo's
    Auto-Persistent Solver Interface) talks to the solver directly via its
    own Python API instead of writing an LP file out and re-parsing HiGHS's
    own solution back -- confirmed directly on 118_ieee's monolithic
    DC-SCOPF: master_solve time drops from ~20-26s to ~10-11s (appsi-HiGHS)
    or ~10s (appsi-Gurobi), with HiGHS's own reported solve time unchanged
    either way (~1.3-1.9s) -- the old gap was purely Pyomo's own
    translation overhead, not more solver work. Gurobi is additionally
    faster than HiGHS on the generator-outage automatic-response MILP
    specifically (confirmed: 26.8s vs ~150-465s depending on gap
    tolerance, same objective to solver precision) -- the real binary
    variables in that model are exactly where Gurobi's branch-and-cut has
    room to matter. Gracefully falls back when Gurobi isn't
    installed/licensed (e.g. a future environment without an academic
    license) rather than failing outright -- `selected_solver_name` tells
    the caller which one it actually got ("gurobi_appsi", "highs_appsi",
    or "highs"), used both for tolerance-option dispatch (see
    `apply_solver_tolerances` below) and recorded in evidence CSVs.

    Explicitly passing `solver_name` bypasses this preference order
    entirely and returns exactly that classic Pyomo solver (unchanged
    behavior, e.g. for Ipopt/AC-OPF callers that never wanted this path).
    """
    if solver_name is not None:
        solver = pyo.SolverFactory(solver_name)
        try:
            available = solver.available(exception_flag=False)
        except TypeError:
            available = solver.available()
        if available:
            return solver, solver_name
        raise DCOPFError(f"Requested solver {solver_name!r} is not available.")

    try:
        from pyomo.contrib.appsi.solvers import Gurobi as _AppsiGurobi

        gurobi = _AppsiGurobi()
        if gurobi.available() in (gurobi.Availability.FullLicense, gurobi.Availability.LimitedLicense):
            return gurobi, "gurobi_appsi"
    except Exception:
        pass

    try:
        from pyomo.contrib.appsi.solvers import Highs as _AppsiHighs

        highs = _AppsiHighs()
        if highs.available():
            return highs, "highs_appsi"
    except Exception:
        pass

    solver = pyo.SolverFactory("highs")
    try:
        available = solver.available(exception_flag=False)
    except TypeError:
        available = solver.available()
    if available:
        return solver, "highs"

    raise DCOPFError(
        "No suitable Pyomo HiGHS solver found. Install highspy or provide a "
        "compatible Pyomo solver name explicitly."
    )


def apply_solver_tolerances(solver: Any, selected_solver_name: str, options: dict[str, Any]) -> None:
    """Apply `options` to `solver`, dispatching to the right attribute for
    whichever interface `make_solver` actually returned -- classic Pyomo
    solvers take options via `.options[key]=value`; appsi-HiGHS via
    `.highs_options[key]=value`; appsi-Gurobi via `.gurobi_options[key]=
    value`. Centralized here (not repeated at every call site) since two
    modules (this one's own `solve_dc_opf_pyomo` and
    `opf/dc_scopf/solve.py`'s `solve_dc_scopf_pyomo`) both need it."""
    if selected_solver_name == "gurobi_appsi":
        for key, value in options.items():
            solver.gurobi_options[key] = value
    elif selected_solver_name == "highs_appsi":
        for key, value in options.items():
            solver.highs_options[key] = value
    else:
        for key, value in options.items():
            solver.options[key] = value


def solve_dc_opf_pyomo(
    case: PowerCase,
    *,
    solver_name: str | None = None,
    tee: bool = False,
    cost_power_units: str = "mw",
    include_branch_limits: bool = True,
    include_angle_limits: bool = True,
    fix_reference_angle: bool = True,
    solver_options: dict[str, Any] | None = None,
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
    # DEFAULT_HIGHS_TOLERANCES only applies to HiGHS-backed interfaces
    # ("highs" classic or "highs_appsi") -- Gurobi has entirely different
    # option names, and there are no similarly-verified Gurobi tolerance
    # defaults pinned here (same "don't pin unverified values" standard as
    # the rest of this project) -- gurobi_appsi runs on Gurobi's own
    # defaults unless `solver_options` overrides them explicitly.
    effective_options = dict(DEFAULT_HIGHS_TOLERANCES) if selected_solver_name in ("highs", "highs_appsi") else {}
    effective_options.update(solver_options or {})
    apply_solver_tolerances(solver, selected_solver_name, effective_options)

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
