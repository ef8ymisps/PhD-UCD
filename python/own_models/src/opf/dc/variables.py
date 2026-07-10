"""Decision-variable declarations for the DC OPF Pyomo model."""

from __future__ import annotations

import pyomo.environ as pyo


def add_dc_opf_variables(model, gen_by_id) -> None:
    """Add DC OPF variables.

    Mathematical role:
        ``Pg[g]`` is active generation in p.u.
        ``theta[i]`` is bus voltage angle in radians.
    """

    def pg_bounds(_model, gen_id):
        gen = gen_by_id[gen_id]
        return float(gen.pmin), float(gen.pmax)

    def pg_initial(_model, gen_id):
        gen = gen_by_id[gen_id]
        return min(max(float(gen.pg0), float(gen.pmin)), float(gen.pmax))

    model.Pg = pyo.Var(model.GEN, bounds=pg_bounds, initialize=pg_initial)
    model.theta = pyo.Var(model.BUS, initialize=0.0)
