from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from pprint import pprint
import sys


PYTHON_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_DIR.parent
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.powermodels_csv_adapter import load_powercase_from_powermodels_json  # noqa: E402
from own_models.src.opf.dc_opf import solve_dc_opf_scipy  # noqa: E402
from own_models.src.opf.dc_opf_audit import (  # noqa: E402
    compare_scipy_pyomo_dc_opf,
    describe_dc_opf_model,
    make_three_bus_dc_case,
    make_two_bus_dc_case,
    make_two_bus_lossy_dc_case,
)
from own_models.src.opf.dc_opf_pyomo import solve_dc_opf_pyomo  # noqa: E402
from own_models.src.opf.solver_utils import (  # noqa: E402
    find_available_dc_opf_pyomo_solver,
    is_pyomo_solver_available,
)


SUMMARY_PATH = PYTHON_DIR / "results" / "dc_opf_small_cases_summary.csv"
IMPORTED_CASE_CANDIDATES = (
    REPO_ROOT / "results" / "reference" / "powermodels" / "dc_opf" / "pglib_opf_case5_pjm_base_case.json",
    REPO_ROOT / "results" / "reference" / "powermodels" / "dc_opf" / "pglib_opf_case14_ieee_base_case.json",
)


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


def _small_cases(loss_model: str):
    cases = [
        make_two_bus_dc_case() if loss_model == "lossless" else make_two_bus_lossy_dc_case(),
        make_three_bus_dc_case(),
    ]
    for path in IMPORTED_CASE_CANDIDATES:
        if path.exists():
            try:
                cases.append(load_powercase_from_powermodels_json(path))
                print(f"Loaded imported small case: {path}")
                break
            except Exception as exc:
                print(f"Skipped imported case {path}: {exc}")
    else:
        print("Skipped imported small case: no case5/IEEE14 reference JSON was found.")
    return cases


def _print_generators(case, result, *, tol: float) -> list[str]:
    binding = []
    print("Generator dispatch:")
    for gen in case.generators:
        if gen.status != 1 or gen.id not in result.pg:
            continue
        pg = result.pg[gen.id]
        flags = []
        if abs(pg - gen.pmin) <= tol:
            flags.append("Pmin")
        if abs(pg - gen.pmax) <= tol:
            flags.append("Pmax")
        if flags:
            binding.append(f"{gen.id}:{'/'.join(flags)}")
        suffix = f" ({', '.join(flags)})" if flags else ""
        print(f"  {gen.id} @ bus {gen.bus}: {pg:.8g} pu, {pg * case.base_mva:.8g} MW{suffix}")
    return binding


def _print_angles(case, result) -> None:
    print("Bus angles:")
    for bus in case.buses:
        angle = result.theta[bus.id]
        print(f"  {bus.id}: {angle:.8g} rad, {math.degrees(angle):.8g} deg")


def _print_branches(case, result, *, tol: float) -> tuple[list[str], list[str]]:
    flow_binding = []
    angle_binding = []
    print("Directional branch flows:")
    for branch in case.branches:
        if branch.status != 1 or branch.id not in result.branch_flows:
            continue
        flow_from = result.branch_flows_from[branch.id] if result.branch_flows_from else result.branch_flows[branch.id]
        flow_to = result.branch_flows_to[branch.id] if result.branch_flows_to else -flow_from
        loss = result.branch_losses[branch.id] if result.branch_losses else 0.0
        angle = result.theta[branch.from_bus] - result.theta[branch.to_bus]
        flags = []
        if branch.rate_a > 0.0 and (
            abs(abs(flow_from) - branch.rate_a) <= tol or abs(abs(flow_to) - branch.rate_a) <= tol
        ):
            flags.append("flow_limit")
            flow_binding.append(branch.id)
        if abs(angle - branch.angmin) <= tol or abs(angle - branch.angmax) <= tol:
            flags.append("angle_limit")
            angle_binding.append(branch.id)
        suffix = f" ({', '.join(flags)})" if flags else ""
        print(
            f"  {branch.id} {branch.from_bus}->{branch.to_bus}: "
            f"P_from={flow_from:.8g} pu/{flow_from * case.base_mva:.8g} MW, "
            f"P_to={flow_to:.8g} pu/{flow_to * case.base_mva:.8g} MW, "
            f"P_loss={loss:.8g} pu/{loss * case.base_mva:.8g} MW, "
            f"angle_diff={angle:.8g} rad{suffix}"
        )
    return flow_binding, angle_binding


def _summary_base(case, description) -> dict[str, object]:
    return {
        "case_name": case.name,
        "loss_model": description["loss_model"],
        "n_bus": description["num_buses"],
        "n_active_branches": description["num_active_branches"],
        "n_active_generators": description["num_active_generators"],
        "total_demand_mw": description["total_active_demand_mw"],
        "total_pmin_mw": description["total_pg_min_mw"],
        "total_pmax_mw": description["total_pg_max_mw"],
    }


def _empty_summary(case, description, *, error: str) -> dict[str, object]:
    return {
        **_summary_base(case, description),
        "scipy_success": False,
        "scipy_objective": "",
        "scipy_max_balance_violation": "",
        "pyomo_attempted": False,
        "pyomo_success": False,
        "pyomo_solver_used": "",
        "total_branch_losses_mw": "",
        "max_branch_loss_mw": "",
        "max_pg_diff": "",
        "max_theta_diff": "",
        "max_flow_diff": "",
        "objective_diff": "",
        "error": error,
    }


def _run_case(
    case,
    *,
    loss_model: str,
    solver_name: str | None,
    solver_skip_reason: str | None,
    tol: float,
) -> dict[str, object]:
    print("\n" + "=" * 80)
    print(f"Case: {case.name}")
    print("=" * 80)
    description = describe_dc_opf_model(case, loss_model=loss_model)
    pprint(description)

    try:
        scipy_result = solve_dc_opf_scipy(case, loss_model=loss_model)
    except Exception as exc:
        print(f"SciPy solve failed: {exc}")
        return _empty_summary(case, description, error=str(exc))

    branch_losses = scipy_result.branch_losses or {}
    total_branch_losses_mw = sum(branch_losses.values()) * case.base_mva
    max_branch_loss_mw = max(branch_losses.values(), default=0.0) * case.base_mva

    print(f"SciPy success: {scipy_result.success}")
    print(f"SciPy objective: {scipy_result.objective:.12g}")
    print(f"SciPy max power-balance violation: {scipy_result.max_power_balance_violation:.12g}")
    binding_generators = _print_generators(case, scipy_result, tol=tol)
    _print_angles(case, scipy_result)
    binding_flows, binding_angles = _print_branches(case, scipy_result, tol=tol)
    print(f"Generators at Pmin/Pmax within {tol}: {binding_generators or 'none'}")
    print(f"Branches at flow limit within {tol}: {binding_flows or 'none'}")
    print(f"Branches near angle limits within {tol}: {binding_angles or 'none'}")
    print(f"Total branch losses: {total_branch_losses_mw:.12g} MW")
    print(f"Max branch loss: {max_branch_loss_mw:.12g} MW")

    pyomo_attempted = False
    pyomo_success = False
    max_pg_diff = ""
    max_theta_diff = ""
    max_flow_diff = ""
    objective_diff = ""

    if solver_name is None:
        print(f"Pyomo skipped: {solver_skip_reason}")
    else:
        pyomo_attempted = True
        try:
            pyomo_result = solve_dc_opf_pyomo(
                case,
                solver_name=solver_name,
                loss_model=loss_model,
                tee=False,
            )
            pyomo_success = pyomo_result.success
            comparison = compare_scipy_pyomo_dc_opf(scipy_result, pyomo_result, tolerance=tol)
            print(f"Pyomo success ({solver_name}): {pyomo_result.success}")
            print("SciPy/Pyomo comparison:")
            pprint(comparison)
            max_pg_diff = comparison["max_pg_difference"]
            max_theta_diff = comparison["max_theta_difference"]
            max_flow_diff = comparison["max_branch_flow_difference"]
            objective_diff = comparison["objective_difference"]
        except Exception as exc:
            print(f"Pyomo solve skipped/failed: {exc}")

    return {
        **_summary_base(case, description),
        "scipy_success": scipy_result.success,
        "scipy_objective": scipy_result.objective,
        "scipy_max_balance_violation": scipy_result.max_power_balance_violation,
        "pyomo_attempted": pyomo_attempted,
        "pyomo_success": pyomo_success,
        "pyomo_solver_used": solver_name if pyomo_attempted else "",
        "total_branch_losses_mw": total_branch_losses_mw,
        "max_branch_loss_mw": max_branch_loss_mw,
        "max_pg_diff": max_pg_diff,
        "max_theta_diff": max_theta_diff,
        "max_flow_diff": max_flow_diff,
        "objective_diff": objective_diff,
        "error": "",
    }


def _write_summary(rows: list[dict[str, object]]) -> None:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "case_name",
        "loss_model",
        "n_bus",
        "n_active_branches",
        "n_active_generators",
        "total_demand_mw",
        "total_pmin_mw",
        "total_pmax_mw",
        "scipy_success",
        "scipy_objective",
        "scipy_max_balance_violation",
        "pyomo_attempted",
        "pyomo_success",
        "pyomo_solver_used",
        "total_branch_losses_mw",
        "max_branch_loss_mw",
        "max_pg_diff",
        "max_theta_diff",
        "max_flow_diff",
        "objective_diff",
        "error",
    ]
    with SUMMARY_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote summary CSV: {SUMMARY_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run small-case inspection for the resistance-adjusted DC OPF baseline."
    )
    parser.add_argument(
        "--pyomo-solver",
        help="Optional Pyomo solver name. Omit to auto-detect an appropriate solver.",
    )
    parser.add_argument(
        "--loss-model",
        choices=("lossless", "castillo_quadratic"),
        default="lossless",
        help="DC OPF loss model. Default: lossless.",
    )
    args = parser.parse_args()

    print("Small-Case DC OPF Validation Runner")
    print("===================================")
    print("Workflow: inspection/validation, not benchmarking.")
    print(f"Model: resistance-adjusted DC OPF baseline, loss_model={args.loss_model!r}.")
    solver_name, solver_skip_reason = _resolve_pyomo_solver(args.pyomo_solver, loss_model=args.loss_model)
    if solver_name is None:
        print(f"Pyomo status: {solver_skip_reason}")
    else:
        print(f"Pyomo status: using solver {solver_name}")

    rows = [
        _run_case(
            case,
            loss_model=args.loss_model,
            solver_name=solver_name,
            solver_skip_reason=solver_skip_reason,
            tol=1e-6,
        )
        for case in _small_cases(args.loss_model)
    ]
    _write_summary(rows)


if __name__ == "__main__":
    main()
