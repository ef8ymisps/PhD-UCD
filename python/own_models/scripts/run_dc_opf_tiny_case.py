from __future__ import annotations

import math

from own_models.src.data.case import (
    PQ,
    PV,
    SLACK,
    Branch,
    Bus,
    Generator,
    Load,
    PowerCase,
)
from own_models.src.opf.dc.solve import solve_dc_opf_pyomo


def build_3_bus_case() -> PowerCase:
    """
    3-bus DC OPF test.

    Bus 1: cheap generator, slack
    Bus 2: expensive generator
    Bus 3: load

    Expected:
    - cheap generator should be used first
    - expensive generator should only be used if needed
    - total generation = total load
    """

    base_mva = 100.0

    buses = [
        Bus("1", 1, SLACK, 1.0, 0.0, 0.94, 1.06, 100.0),
        Bus("2", 2, PV,    1.0, 0.0, 0.94, 1.06, 100.0),
        Bus("3", 3, PQ,    1.0, 0.0, 0.94, 1.06, 100.0),
    ]

    branches = [
        Branch(
            id="1-3",
            index=1,
            from_bus="1",
            to_bus="3",
            r=0.0,
            x=0.1,
            b=0.0,
            b_fr=0.0,
            b_to=0.0,
            g_fr=0.0,
            g_to=0.0,
            rate_a=2.0,
            tap=1.0,
            shift=0.0,
            angmin=-math.pi / 2,
            angmax=math.pi / 2,
            status=1,
            branch_type="line",
        ),
        Branch(
            id="2-3",
            index=2,
            from_bus="2",
            to_bus="3",
            r=0.0,
            x=0.1,
            b=0.0,
            b_fr=0.0,
            b_to=0.0,
            g_fr=0.0,
            g_to=0.0,
            rate_a=2.0,
            tap=1.0,
            shift=0.0,
            angmin=-math.pi / 2,
            angmax=math.pi / 2,
            status=1,
            branch_type="line",
        ),
    ]

    generators = [
        Generator(
            id="G1",
            index=1,
            bus="1",
            pg0=0.5,
            qg0=0.0,
            pmin=0.0,
            pmax=1.0,
            qmin=-1.0,
            qmax=1.0,
            status=1,
            voltage_regulator_on=True,
            cost_c2=0.0,
            cost_c1=10.0,
            cost_c0=0.0,
        ),
        Generator(
            id="G2",
            index=2,
            bus="2",
            pg0=0.5,
            qg0=0.0,
            pmin=0.0,
            pmax=2.0,
            qmin=-1.0,
            qmax=1.0,
            status=1,
            voltage_regulator_on=True,
            cost_c2=0.0,
            cost_c1=30.0,
            cost_c0=0.0,
        ),
    ]

    loads = [
        Load(
            id="L3",
            index=1,
            bus="3",
            pd=1.5,
            qd=0.0,
            status=1,
        )
    ]

    return PowerCase(
        name="three_bus_economic_test",
        base_mva=base_mva,
        buses=buses,
        branches=branches,
        generators=generators,
        loads=loads,
        shunts=[],
        slack_bus_id="1",
        bus_id_to_index={"1": 0, "2": 1, "3": 2},
    )


def main() -> None:
    case = build_3_bus_case()

    model, raw_results, result = solve_dc_opf_pyomo(
        case,
        solver_name=None,
        tee=True,
        cost_power_units="mw",
        include_branch_limits=True,
        include_angle_limits=True,
    )

    print("\n=== DC OPF 3-bus result ===")
    print("status:", result.status)
    print("termination:", result.termination_condition)
    print("objective:", result.objective)
    print("total_generation_pu:", result.total_generation)
    print("total_load_pu:", result.total_load)
    print("max_balance_residual:", result.max_power_balance_residual)

    print("\nGenerators Pg [p.u.]:")
    for gen_id, value in result.pg.items():
        print(gen_id, value)

    print("\nBus angles theta [rad]:")
    for bus_id, value in result.theta.items():
        print(bus_id, value)

    print("\nBranch flows Pf [p.u.]:")
    for branch_id, value in result.branch_flow.items():
        print(branch_id, value)


if __name__ == "__main__":
    main()