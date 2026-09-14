"""Best-effort solver iteration-count capture for Pyomo solves.

Neither Ipopt's nor classic-interface HiGHS's Pyomo result objects reliably
expose an iteration count as a structured field (verified empirically:
Ipopt's ``SolverResults`` has no iterations attribute, and HiGHS's
``Statistics`` field is populated as ``UndefinedData`` under the installed
Pyomo/HiGHS versions). Both solvers do print an iteration count to their
console log, so this captures stdout during the solve and regex-parses it
instead of relying on solver-provided fields -- this is the path still used
for Ipopt (AC-OPF) and for the classic (file-based) HiGHS interface if ever
selected explicitly.

The appsi (Auto-Persistent Solver Interface) path below is different and
preferred for DC-OPF/DC-SCOPF (see own_models.src.opf.dc.solve.make_solver):
appsi talks to HiGHS/Gurobi directly via their own Python APIs (no LP-file
round trip), which is both faster (confirmed directly: 118_ieee's monolithic
DC-SCOPF master_solve time dropped from ~20-26s to ~10-11s, HiGHS's own
reported solve time unchanged at ~1.3-1.9s either way -- the gap was always
Pyomo's own file-translation overhead, not solver work) and exposes a
genuine structured iteration count (`simplex_iteration_count` for HiGHS,
`IterCount` for Gurobi), so no log-scraping is needed on that path at all.
"""

from __future__ import annotations

import contextlib
import io
import re
from typing import Any

from pyomo.opt import SolverStatus as _ClassicSolverStatus
from pyomo.opt import TerminationCondition as _ClassicTerminationCondition

try:
    from pyomo.contrib.appsi.base import PersistentBase as _AppsiPersistentBase
    from pyomo.contrib.appsi.base import TerminationCondition as _AppsiTerminationCondition

    _APPSI_AVAILABLE = True
except ImportError:  # pragma: no cover -- appsi ships with Pyomo, but guard anyway
    _APPSI_AVAILABLE = False

# Every appsi TerminationCondition member mapped to its classic-Pyomo
# equivalent -- appsi is its OWN enum class, NOT the same as
# pyomo.opt.TerminationCondition (confirmed directly: `AppsiTC.optimal ==
# ClassicTC.optimal` is False, and even `str()` differs -- classic gives
# "optimal", appsi gives "TerminationCondition.optimal"). Every existing
# caller across this project reads `raw_results.solver.termination_condition`
# and compares it against classic enum members (or their `.name`/`str()`
# forms) -- mapping appsi's result back into classic's own enum here, once,
# is what lets every one of those callers keep working completely unchanged
# regardless of which interface actually solved the model. appsi has no
# locallyOptimal/feasible/globallyOptimal at all (Ipopt/NLP-specific, never
# returned by HiGHS or Gurobi for an LP/MILP), so nothing is lost by mapping
# only what appsi can actually produce.
_APPSI_TO_CLASSIC_TERMINATION: dict[Any, _ClassicTerminationCondition] = {}
if _APPSI_AVAILABLE:
    _APPSI_TO_CLASSIC_TERMINATION = {
        _AppsiTerminationCondition.unknown: _ClassicTerminationCondition.unknown,
        _AppsiTerminationCondition.maxTimeLimit: _ClassicTerminationCondition.maxTimeLimit,
        _AppsiTerminationCondition.maxIterations: _ClassicTerminationCondition.maxIterations,
        _AppsiTerminationCondition.objectiveLimit: _ClassicTerminationCondition.other,
        _AppsiTerminationCondition.minStepLength: _ClassicTerminationCondition.minStepLength,
        _AppsiTerminationCondition.optimal: _ClassicTerminationCondition.optimal,
        _AppsiTerminationCondition.unbounded: _ClassicTerminationCondition.unbounded,
        _AppsiTerminationCondition.infeasible: _ClassicTerminationCondition.infeasible,
        _AppsiTerminationCondition.infeasibleOrUnbounded: _ClassicTerminationCondition.infeasibleOrUnbounded,
        _AppsiTerminationCondition.error: _ClassicTerminationCondition.error,
        _AppsiTerminationCondition.interrupted: _ClassicTerminationCondition.userInterrupt,
        _AppsiTerminationCondition.licensingProblems: _ClassicTerminationCondition.licensingProblems,
    }

_OPTIMAL_LIKE_CLASSIC = {
    _ClassicTerminationCondition.optimal,
    _ClassicTerminationCondition.locallyOptimal,
    _ClassicTerminationCondition.globallyOptimal,
    _ClassicTerminationCondition.feasible,
}


class _SolverStatusView:
    """Mimics classic Pyomo `SolverResults.solver`'s own shape --
    `.status`/`.termination_condition`, both already-classic enum members
    -- so `str(raw_results.solver.termination_condition)`,
    `raw_results.solver.termination_condition not in {...}`, and
    `raw_results.solver.termination_condition.name` all behave identically
    to a real classic-interface solve, no caller-side changes needed."""

    def __init__(self, termination_condition: _ClassicTerminationCondition, status: _ClassicSolverStatus) -> None:
        self.termination_condition = termination_condition
        self.status = status


class AppsiResultsAdapter:
    """Wraps an appsi `Results` object to present the same `.solver.status`/
    `.solver.termination_condition` shape as classic Pyomo's `SolverResults`
    -- see this module's own docstring and `_APPSI_TO_CLASSIC_TERMINATION`
    for why this mapping (not a raw pass-through) is required for
    correctness, not just convenience."""

    def __init__(self, appsi_results: Any) -> None:
        self.appsi_results = appsi_results
        classic_tc = _APPSI_TO_CLASSIC_TERMINATION.get(
            appsi_results.termination_condition, _ClassicTerminationCondition.unknown,
        )
        classic_status = _ClassicSolverStatus.ok if classic_tc in _OPTIMAL_LIKE_CLASSIC else _ClassicSolverStatus.warning
        self.solver = _SolverStatusView(classic_tc, classic_status)

    def load_solution(self) -> None:
        """Load the solved values into the model's variables. Only valid to
        call when a solution actually exists (optimal-like termination) --
        used for the deferred-load pattern (`load_solution=False` at solve
        time, e.g. DC-SCOPF's own `solve_dc_scopf_pyomo`, which solves with
        loading deferred so a genuinely infeasible contingency set doesn't
        raise before its termination condition can be inspected). appsi's
        own equivalent of classic Pyomo's `model.solutions.load_from(...)`
        is `solution_loader.load_vars()` -- see `load_solution_into_model`
        below, which picks the right one for whichever interface solved."""
        self.appsi_results.solution_loader.load_vars()


def load_solution_into_model(raw_results: Any, model: Any) -> None:
    """Load a solved model's values into its own variables, dispatching to
    the right API for whichever interface actually solved it -- classic
    Pyomo's `model.solutions.load_from(raw_results)` expects a real
    `SolverResults` object (a `.solution` list) that `AppsiResultsAdapter`
    does not have, so calling it directly on an appsi result raises.
    Centralized here (not repeated at call sites) since this is the exact
    counterpart of `apply_solver_tolerances` in own_models.src.opf.dc.solve
    -- one dispatch point per interface-shape difference between classic
    and appsi. Used by DC-SCOPF's own deferred-load pattern
    (`solve_dc_scopf_pyomo`/`_extract_result`)."""
    if isinstance(raw_results, AppsiResultsAdapter):
        raw_results.load_solution()
    else:
        model.solutions.load_from(raw_results)


def _appsi_iteration_count(opt: Any) -> float:
    """Structured iteration count, no log-scraping -- HiGHS's own
    `simplex_iteration_count` (via appsi's `_solver_model`, the raw
    highspy.Highs instance, confirmed directly to match the previously
    regex-parsed value exactly: 23583 both ways on 118_ieee's monolithic
    DC-SCOPF) or Gurobi's own `IterCount` (via appsi's public
    `get_model_attr`, the documented way to read a live Gurobi model
    attribute)."""
    try:
        if hasattr(opt, "get_model_attr"):  # Gurobi
            return float(opt.get_model_attr("IterCount"))
        return float(opt._solver_model.getInfo().simplex_iteration_count)  # HiGHS
    except Exception:
        return float("nan")


def _solve_appsi(opt: Any, model: Any, *, tee: bool, **solve_kwargs: Any) -> tuple[AppsiResultsAdapter, float, float]:
    """appsi solve path -- no log capture/regex parsing needed at all, the
    iteration count and solve time both come from structured fields.
    `solve_kwargs` only ever carries `load_solutions` at this project's
    existing call sites (see solve_with_diagnostics_capture's own callers)
    -- translated to appsi's own `config.load_solution` (singular)."""
    opt.config.stream_solver = tee
    if "load_solutions" in solve_kwargs:
        opt.config.load_solution = solve_kwargs["load_solutions"]
    appsi_results = opt.solve(model)
    iterations = _appsi_iteration_count(opt)
    solver_time = float(getattr(appsi_results, "wallclock_time", float("nan")))
    return AppsiResultsAdapter(appsi_results), iterations, solver_time


_IPOPT_PATTERN = re.compile(r"Number of Iterations\.+:\s*(\d+)")
_HIGHS_SIMPLEX_PATTERN = re.compile(r"Simplex\s+iterations:\s*(\d+)")
_HIGHS_IPM_PATTERN = re.compile(r"IPM\s+iterations:\s*(\d+)")
# HiGHS's presolve can fully reduce a small/well-conditioned DC-OPF LP to
# nothing ("Presolve reductions: ... - Reduced to empty") before simplex ever
# runs -- confirmed on case14_ieee/case30_ieee's DC OPF. No "Simplex
# iterations" line is printed in that case, but the true count is 0, not
# unknown: presolve alone found the optimum.
_HIGHS_PRESOLVE_EMPTY_PATTERN = re.compile(r"Reduced to empty")
# HiGHS's own reported wall time for the whole solve call (presolve +
# simplex/IPM + postsolve), NOT including Pyomo's own model-translation
# overhead on either side of it (writing the LP out to HiGHS, reading the
# solution back) -- confirmed directly on case118_ieee's monolithic
# DC-SCOPF: HiGHS reports ~1.4s here while the wall-clock around the whole
# opt.solve() call is ~11-20s, the gap being that Pyomo translation
# overhead, not more solver work (see 65_run_dc_scopf_monolithic_branches.py's
# own investigation). Ipopt has no equivalent single-line summary parsed
# here (its own iteration count is enough for that solver's own use so far).
_HIGHS_RUNTIME_PATTERN = re.compile(r"HiGHS run time\s*:\s*([\d.]+)")


def _parse_iteration_count(log: str) -> float:
    for pattern in (_IPOPT_PATTERN, _HIGHS_SIMPLEX_PATTERN, _HIGHS_IPM_PATTERN):
        match = pattern.search(log)
        if match:
            return float(match.group(1))
    if _HIGHS_PRESOLVE_EMPTY_PATTERN.search(log):
        return 0.0
    return float("nan")


def _parse_solver_reported_time(log: str) -> float:
    match = _HIGHS_RUNTIME_PATTERN.search(log)
    return float(match.group(1)) if match else float("nan")


def _solve_capturing_log(opt: Any, model: Any, *, tee: bool, **solve_kwargs: Any) -> tuple[Any, str]:
    """Solve `model` with `opt`, returning `(raw_result, log)`. Always
    captures the solver's console output internally (regardless of `tee`)
    so it can be parsed; re-prints it to stdout when `tee=True` so
    caller-visible behavior is unchanged."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        raw_result = opt.solve(model, tee=True, **solve_kwargs)
    log = buffer.getvalue()
    if tee:
        print(log, end="")
    return raw_result, log


def solve_with_iteration_capture(opt: Any, model: Any, *, tee: bool, **solve_kwargs: Any) -> tuple[Any, float]:
    """Solve ``model`` with ``opt``, returning ``(raw_result, iteration_count)``.
    See `solve_with_diagnostics_capture` for a 3-tuple variant that ALSO
    returns the solver's own reported time -- kept separate rather than
    changing this function's return arity, since 8 existing call sites
    already unpack a 2-tuple.

    Branches to the structured, log-free appsi path (`_solve_appsi`) when
    `opt` is an appsi solver (HiGHS or Gurobi via
    own_models.src.opf.dc.solve.make_solver's preference order) -- see this
    module's own docstring for why that path exists and is preferred."""
    if _APPSI_AVAILABLE and isinstance(opt, _AppsiPersistentBase):
        raw_result, iterations, _solver_time = _solve_appsi(opt, model, tee=tee, **solve_kwargs)
        return raw_result, iterations
    raw_result, log = _solve_capturing_log(opt, model, tee=tee, **solve_kwargs)
    return raw_result, _parse_iteration_count(log)


def solve_with_diagnostics_capture(opt: Any, model: Any, *, tee: bool, **solve_kwargs: Any) -> tuple[Any, float, float]:
    """Solve ``model`` with ``opt``, returning ``(raw_result,
    iteration_count, solver_reported_time_s)`` -- the same iteration-count
    capture as `solve_with_iteration_capture`, plus the solver's own
    reported solve time (NaN for Ipopt or if the pattern isn't found on the
    classic-interface path; appsi's own `wallclock_time` on the appsi path),
    so callers that want to isolate solver time from Pyomo's own
    model-translation overhead (see scopf_evidence's own
    `master_solve_time_s` vs this) can. Same appsi branch as
    `solve_with_iteration_capture` above."""
    if _APPSI_AVAILABLE and isinstance(opt, _AppsiPersistentBase):
        return _solve_appsi(opt, model, tee=tee, **solve_kwargs)
    raw_result, log = _solve_capturing_log(opt, model, tee=tee, **solve_kwargs)
    return raw_result, _parse_iteration_count(log), _parse_solver_reported_time(log)
