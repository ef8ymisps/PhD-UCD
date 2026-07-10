from __future__ import annotations

from pathlib import Path
from pprint import pprint
import argparse
import sys


PYTHON_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.opf.dc_opf import solve_dc_opf_scipy  # noqa: E402
from own_models.src.opf.dc_opf_audit import (  # noqa: E402
    audit_dc_opf_equations,
    compare_scipy_pyomo_dc_opf,
    describe_dc_opf_model,
    make_two_bus_dc_case,
    make_two_bus_lossy_dc_case,
)
from own_models.src.opf.dc_opf_pyomo import solve_dc_opf_pyomo  # noqa: E402
from own_models.src.opf.solver_utils import (  # noqa: E402
    find_available_dc_opf_pyomo_solver,
    is_pyomo_solver_available,
)


def _print_result(title, result) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    print(f"objective: {result.objective:.12g}")
    print("Pg:")
    pprint(result.pg)
    print("theta:")
    pprint(result.theta)
    print("branch flows:")
    pprint(result.branch_flows)
    if result.branch_losses is not None:
        print("branch losses:")
        pprint(result.branch_losses)
    print(f"max balance violation: {result.max_power_balance_violation:.12g}")


def _manual_two_bus_solution(case, *, loss_model: str):
    branch = next(branch for branch in case.branches if branch.status == 1)
    demand = sum(load.pd for load in case.loads if load.status == 1)
    beta = branch.x / (branch.r**2 + branch.x**2)
    conductance = branch.r / (branch.r**2 + branch.x**2)
    if loss_model == "lossless":
        delta = demand / beta
        expected_flow = demand
        expected_pg = demand
        expected_to_flow = -demand
        expected_loss = 0.0
    else:
        # Receiving-bus balance for this two-bus case:
        # -demand = -beta*delta + 0.5*g*delta^2.
        a = 0.5 * conductance
        discriminant = beta**2 - 4.0 * a * demand
        delta = (beta - discriminant**0.5) / (2.0 * a)
        expected_loss = conductance * delta**2
        expected_flow = beta * delta + 0.5 * expected_loss
        expected_to_flow = -demand
        expected_pg = expected_flow
    expected_theta = {
        branch.from_bus: 0.0,
        branch.to_bus: -delta,
    }
    return {
        "beta": beta,
        "conductance": conductance,
        "expected_pg": expected_pg,
        "expected_flow": expected_flow,
        "expected_to_flow": expected_to_flow,
        "expected_loss": expected_loss,
        "expected_theta": expected_theta,
    }


def _print_manual_comparison(case, scipy_result, *, loss_model: str) -> None:
    branch = next(branch for branch in case.branches if branch.status == 1)
    generator = next(gen for gen in case.generators if gen.status == 1)
    manual = _manual_two_bus_solution(case, loss_model=loss_model)

    print("\nManual two-bus solution")
    print("-----------------------")
    print(f"beta = x / (r^2 + x^2) = {manual['beta']:.12g}")
    print(f"g = r / (r^2 + x^2) = {manual['conductance']:.12g}")
    print(f"expected Pg[{generator.id}] = {manual['expected_pg']:.12g} pu")
    print(f"expected P_from[{branch.id}] = {manual['expected_flow']:.12g} pu")
    print(f"expected P_to[{branch.id}] = {manual['expected_to_flow']:.12g} pu")
    print(f"expected P_loss[{branch.id}] = {manual['expected_loss']:.12g} pu")
    print(
        f"expected theta[{branch.to_bus}] = "
        f"{manual['expected_theta'][branch.to_bus]:.12g} rad"
    )

    print("\nSolver minus manual")
    print("-------------------")
    print(
        f"Pg[{generator.id}]: "
        f"{scipy_result.pg[generator.id] - manual['expected_pg']:.12g}"
    )
    print(
        f"Pf[{branch.id}]: "
        f"{scipy_result.branch_flows[branch.id] - manual['expected_flow']:.12g}"
    )
    if scipy_result.branch_flows_to is not None:
        print(
            f"P_to[{branch.id}]: "
            f"{scipy_result.branch_flows_to[branch.id] - manual['expected_to_flow']:.12g}"
        )
    if scipy_result.branch_losses is not None:
        print(
            f"P_loss[{branch.id}]: "
            f"{scipy_result.branch_losses[branch.id] - manual['expected_loss']:.12g}"
        )
    for bus_id, expected in manual["expected_theta"].items():
        print(f"theta[{bus_id}]: {scipy_result.theta[bus_id] - expected:.12g}")


def _resolve_pyomo_solver(requested_solver: str | None, *, loss_model: str):
    if loss_model == "castillo_quadratic" and requested_solver not in {None, "ipopt"}:
        return None, "Castillo quadratic-loss DC OPF requires a nonlinear solver such as Ipopt."
    if requested_solver:
        if is_pyomo_solver_available(requested_solver):
            return requested_solver, None
        return None, f"requested Pyomo solver {requested_solver!r} is not available."

    solver_name = find_available_dc_opf_pyomo_solver(loss_model)
    if solver_name is None:
        if loss_model == "castillo_quadratic":
            return None, "Castillo quadratic-loss DC OPF requires a nonlinear solver such as Ipopt."
        return None, "no preferred DC Pyomo solver is available. Install highspy for appsi_highs."
    return solver_name, None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Demonstrate the lossless resistance-adjusted DC OPF baseline."
    )
    parser.add_argument(
        "--pyomo-solver",
        help="Optional Pyomo solver name. Omit to auto-detect appsi_highs/HiGHS first.",
    )
    parser.add_argument(
        "--loss-model",
        choices=("lossless", "castillo_quadratic"),
        default="lossless",
        help="DC OPF loss model to demonstrate. Default: lossless.",
    )
    args = parser.parse_args()

    case = make_two_bus_dc_case() if args.loss_model == "lossless" else make_two_bus_lossy_dc_case()

    print("DC OPF Ownership Demonstration")
    print("==============================")
    print(
        "This is a learning script for the DC OPF baseline with "
        f"loss_model={args.loss_model!r}."
    )

    print("\nModel description")
    print("-----------------")
    pprint(describe_dc_opf_model(case, loss_model=args.loss_model))

    print("\nHuman-readable equations")
    print("------------------------")
    print(audit_dc_opf_equations(case, loss_model=args.loss_model)["text"])

    scipy_result = solve_dc_opf_scipy(case, loss_model=args.loss_model)
    _print_result("SciPy DC OPF result", scipy_result)
    _print_manual_comparison(case, scipy_result, loss_model=args.loss_model)

    solver_name, unavailable_reason = _resolve_pyomo_solver(args.pyomo_solver, loss_model=args.loss_model)
    if solver_name is None:
        print("\nPyomo DC OPF result")
        print("-------------------")
        print(f"Skipped: {unavailable_reason}")
        return

    try:
        pyomo_result = solve_dc_opf_pyomo(
            case,
            solver_name=solver_name,
            loss_model=args.loss_model,
            tee=False,
        )
    except (ImportError, RuntimeError) as exc:
        print("\nPyomo DC OPF result")
        print("-------------------")
        print(f"Skipped: {exc}")
        return

    _print_result(f"Pyomo DC OPF result ({solver_name})", pyomo_result)

    print("\nSciPy/Pyomo comparison")
    print("----------------------")
    pprint(compare_scipy_pyomo_dc_opf(scipy_result, pyomo_result))


if __name__ == "__main__":
    main()
