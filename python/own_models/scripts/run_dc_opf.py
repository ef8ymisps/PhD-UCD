from __future__ import annotations

import argparse
from collections.abc import Callable

import pypowsybl as pp

from own_models.src.adapters.pypowsybl_adapter import pypowsybl_to_powercase
from own_models.src.opf.dc.solve import solve_dc_opf_pyomo
from own_models.src.opf.dc.validation import validate_bbus_consistency
from own_models.src.adapters.pglib_matpower_patch import (
    patch_branch_rate_a_from_pglib_matpower,
)

BUILTIN_NETWORKS: dict[str, Callable] = {
    "ieee9": pp.network.create_ieee9,
    "ieee14": pp.network.create_ieee14,
    "ieee30": pp.network.create_ieee30,
    "ieee57": pp.network.create_ieee57,
    "ieee118": pp.network.create_ieee118,
    "ieee300": pp.network.create_ieee300,
}


def print_case_summary(case) -> None:
    active_gens = [g for g in case.generators if g.status == 1]
    active_loads = [l for l in case.loads if l.status == 1]
    active_branches = [b for b in case.branches if b.status == 1]

    total_load = sum(l.pd for l in active_loads)
    total_pmin = sum(g.pmin for g in active_gens)
    total_pmax = sum(g.pmax for g in active_gens)
    rated_branches = [b for b in active_branches if b.rate_a > 0.0]

    print("\n=== PowerCase summary ===")
    print("name:", case.name)
    print("base_mva:", case.base_mva)
    print("n_buses:", len(case.buses))
    print("n_active_generators:", len(active_gens))
    print("n_active_loads:", len(active_loads))
    print("n_active_branches:", len(active_branches))
    print("slack_bus_id:", case.slack_bus_id)
    print("total_load_pu:", total_load)
    print("total_pmin_pu:", total_pmin)
    print("total_pmax_pu:", total_pmax)
    print("rated_branches:", len(rated_branches), "/", len(active_branches))


def print_result_summary(result, *, max_items: int = 20) -> None:
    print("\n=== DC OPF result ===")
    print("status:", result.status)
    print("termination:", result.termination_condition)
    print("objective:", result.objective)
    print("total_generation_pu:", result.total_generation)
    print("total_load_pu:", result.total_load)
    print("max_balance_residual:", result.max_power_balance_residual)

    print("\nGenerator Pg [p.u.] first entries:")
    for gen_id, pg in list(result.pg.items())[:max_items]:
        print(gen_id, pg)

    print("\nBranch Pf [p.u.] first entries:")
    for branch_id, pf in list(result.branch_flow.items())[:max_items]:
        print(branch_id, pf)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run our own Pyomo DC OPF formulation on a PyPowSyBl built-in IEEE network."
    )
    parser.add_argument(
        "case",
        choices=sorted(BUILTIN_NETWORKS),
        help="Built-in PyPowSyBl IEEE case.",
    )
    parser.add_argument(
        "--with-branch-limits",
        action="store_true",
        help="Use branch ratings if the adapter imported them.",
    )
    parser.add_argument(
        "--no-angle-limits",
        action="store_true",
        help="Disable angle-difference limits.",
    )
    parser.add_argument(
        "--tee",
        action="store_true",
        help="Show solver log.",
    )

    args = parser.parse_args()

    network = BUILTIN_NETWORKS[args.case]()

    case = pypowsybl_to_powercase(
        network,
        name=args.case,
        base_mva=100.0,
        assign_synthetic_costs=True,
    )
    case = patch_branch_rate_a_from_pglib_matpower(
        case,
        matpower_path=args.path,
    )
    print_case_summary(case)

    model, raw_results, result = solve_dc_opf_pyomo(
        case,
        solver_name=None,
        tee=args.tee,
        cost_power_units="mw",
        include_branch_limits=args.with_branch_limits,
        include_angle_limits=not args.no_angle_limits,
    )

    print_result_summary(result)


    validation = validate_bbus_consistency(
        case,
        pg=result.pg,
        theta=result.theta,
        tolerance=1e-6,
    )

    print("\n=== Matrix validation ===")
    print("max_bbus_residual:", validation.max_bbus_residual)
    print("bbus_ok:", validation.bbus_ok)

if __name__ == "__main__":
    main()