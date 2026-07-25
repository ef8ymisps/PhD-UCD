from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io


DC_PYOMO_SOLVER_PREFERENCE = ("appsi_highs", "highs", "glpk", "cbc")
NONLINEAR_DC_PYOMO_SOLVER_PREFERENCE = ("ipopt",)


def _import_pyomo():
    """Import Pyomo only for optional Pyomo solver discovery."""
    try:
        import pyomo.environ as pyo
    except ImportError:
        return None
    return pyo


def is_pyomo_solver_available(solver_name: str) -> bool:
    """Return whether a named Pyomo solver is available in this environment."""
    pyo = _import_pyomo()
    if pyo is None:
        return False
    try:
        # Pyomo can emit noisy warnings while constructing unavailable solvers.
        # Solver discovery should be quiet; scripts print their own clear skip.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            solver = pyo.SolverFactory(solver_name)
            return bool(solver is not None and solver.available(exception_flag=False))
    except Exception:
        return False


def find_available_pyomo_solver(
    preferred: tuple[str, ...] = DC_PYOMO_SOLVER_PREFERENCE,
) -> str | None:
    """Return the first available Pyomo solver from the preferred list.

    For DC OPF, prefer appsi_highs / HiGHS. Reserve Ipopt for future nonlinear
    AC OPF workflows rather than including it in this default DC preference list.
    """
    for solver_name in preferred:
        if is_pyomo_solver_available(solver_name):
            return solver_name
    return None


def find_available_dc_opf_pyomo_solver(loss_model: str = "lossless") -> str | None:
    """Return an available Pyomo solver appropriate for a DC OPF loss model."""
    if loss_model == "lossless":
        return find_available_pyomo_solver(DC_PYOMO_SOLVER_PREFERENCE)
    if loss_model == "castillo_quadratic":
        return find_available_pyomo_solver(NONLINEAR_DC_PYOMO_SOLVER_PREFERENCE)
    raise ValueError(f"Unsupported DC OPF loss_model: {loss_model!r}")
