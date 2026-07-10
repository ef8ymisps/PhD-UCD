from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

PYTHON_ROOT = Path(__file__).resolve().parents[2]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat
from own_models.src.opf.dc.solve import solve_dc_opf_pyomo


def print_case_summary(case) -> None:
    active_gens = [g for g in case.generators if g.status == 1]
    active_loads = [l for l in case.loads if l.status == 1]
    active_branches = [b for b in case.branches if b.status == 1]
    rated_branches = [b for b in active_branches if b.rate_a > 0.0]
    angle_limited_branches = [
        b
        for b in active_branches
        if math.isfinite(b.angmin) and math.isfinite(b.angmax)
    ]

    print("\n=== PowerCase summary ===")
    print("name:", case.name)
    print("base_mva:", case.base_mva)
    print("n_buses:", len(case.buses))
    print("n_active_generators:", len(active_gens))
    print("n_active_loads:", len(active_loads))
    print("n_active_branches:", len(active_branches))
    print("rated_branches:", len(rated_branches), "/", len(active_branches))
    print("angle_limited_branches:", len(angle_limited_branches), "/", len(active_branches))
    print("total_load_pu:", sum(l.pd for l in active_loads))
    print("total_pmin_pu:", sum(g.pmin for g in active_gens))
    print("total_pmax_pu:", sum(g.pmax for g in active_gens))


def print_result_summary(result) -> None:
    print("\n=== DC OPF result ===")
    print("status:", result.status)
    print("termination:", result.termination_condition)
    print("objective:", result.objective)
    print("total_generation_pu:", result.total_generation)
    print("total_load_pu:", result.total_load)
    print("max_balance_residual:", result.max_power_balance_residual)

    print("\nGenerator Pg [p.u.]:")
    for gen_id, pg in result.pg.items():
        print(gen_id, pg)

    print("\nFirst 20 branch flows Pf [p.u.]:")
    for branch_id, pf in list(result.branch_flow.items())[:20]:
        print(branch_id, pf)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "matpower_path",
        type=Path,
        help="Path to PGLib MATPOWER .mat file.",
    )
    parser.add_argument(
        "--with-branch-limits",
        action="store_true",
        help="Activate patched MATPOWER rateA branch limits.",
    )
    parser.add_argument(
        "--no-angle-limits",
        action="store_true",
    )
    parser.add_argument(
        "--tee",
        action="store_true",
    )
    args = parser.parse_args()

    case = load_powercase_from_matpower_mat(args.matpower_path)

    print_case_summary(case)

    model, raw_results, result = solve_dc_opf_pyomo(
        case,
        solver_name=None,
        tee=args.tee,
        cost_power_units="mw",
        include_branch_limits=args.with_branch_limits,
        include_angle_limits=not args.no_angle_limits,
    )

    print("\n=== Active OPF limits ===")
    print("branch_flow_limit_constraints:", len(model.branch_flow_limit))
    print("angle_difference_limit_constraints:", len(model.angle_difference_limit))

    print_result_summary(result)


if __name__ == "__main__":
    main()
