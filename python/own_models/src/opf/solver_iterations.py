"""Best-effort solver iteration-count capture for Pyomo solves.

Neither Ipopt's nor HiGHS's Pyomo result objects reliably expose an iteration
count as a structured field (verified empirically: Ipopt's ``SolverResults``
has no iterations attribute, and HiGHS's ``Statistics`` field is populated as
``UndefinedData`` under the installed Pyomo/HiGHS versions). Both solvers do
print an iteration count to their console log, so this captures stdout during
the solve and regex-parses it instead of relying on solver-provided fields.
"""

from __future__ import annotations

import contextlib
import io
import re
from typing import Any

_IPOPT_PATTERN = re.compile(r"Number of Iterations\.+:\s*(\d+)")
_HIGHS_SIMPLEX_PATTERN = re.compile(r"Simplex\s+iterations:\s*(\d+)")
_HIGHS_IPM_PATTERN = re.compile(r"IPM\s+iterations:\s*(\d+)")
# HiGHS's presolve can fully reduce a small/well-conditioned DC-OPF LP to
# nothing ("Presolve reductions: ... - Reduced to empty") before simplex ever
# runs -- confirmed on case14_ieee/case30_ieee's DC OPF. No "Simplex
# iterations" line is printed in that case, but the true count is 0, not
# unknown: presolve alone found the optimum.
_HIGHS_PRESOLVE_EMPTY_PATTERN = re.compile(r"Reduced to empty")


def _parse_iteration_count(log: str) -> float:
    for pattern in (_IPOPT_PATTERN, _HIGHS_SIMPLEX_PATTERN, _HIGHS_IPM_PATTERN):
        match = pattern.search(log)
        if match:
            return float(match.group(1))
    if _HIGHS_PRESOLVE_EMPTY_PATTERN.search(log):
        return 0.0
    return float("nan")


def solve_with_iteration_capture(opt: Any, model: Any, *, tee: bool, **solve_kwargs: Any) -> tuple[Any, float]:
    """Solve ``model`` with ``opt``, returning ``(raw_result, iteration_count)``.

    Always captures the solver's console output internally (regardless of
    ``tee``) so the iteration count can be parsed; re-prints it to stdout
    when ``tee=True`` so caller-visible behavior is unchanged.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        raw_result = opt.solve(model, tee=True, **solve_kwargs)
    log = buffer.getvalue()
    if tee:
        print(log, end="")
    return raw_result, _parse_iteration_count(log)
