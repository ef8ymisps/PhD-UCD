"""Decision-variable declarations and initial values for AC OPF."""

from __future__ import annotations

import numpy as np
import pyomo.environ as pyo

from own_models.src.opf.ac.initialization import build_ac_initial_point


def finite_or_none(value: float):
    """Return a Pyomo-compatible finite bound or ``None``."""
    return float(value) if np.isfinite(value) else None


def add_ac_opf_variables(model, case, data) -> None:
    """Add Pg/Qg, voltage magnitude, and voltage angle variables."""

    def pg_bounds(_model, gen_id):
        gen = data.gen_by_id[gen_id]
        return finite_or_none(gen.pmin), finite_or_none(gen.pmax)

    def qg_bounds(_model, gen_id):
        gen = data.gen_by_id[gen_id]
        return finite_or_none(gen.qmin), finite_or_none(gen.qmax)

    def vm_bounds(_model, bus_id):
        return finite_or_none(data.vmin_by_bus[bus_id]), finite_or_none(data.vmax_by_bus[bus_id])

    model.pg = pyo.Var(model.GEN, bounds=pg_bounds)
    model.qg = pyo.Var(model.GEN, bounds=qg_bounds)
    model.vm = pyo.Var(model.BUS, bounds=vm_bounds)
    model.va = pyo.Var(model.BUS)


def add_ac_objective_variables(
    model,
    data,
    *,
    objective_type: str = "generation_cost",
    objective_options: dict[str, object] | None = None,
) -> None:
    """Add optional variables required by nonstandard objective functions."""
    options = objective_options or {}
    if objective_type == "load_curtailment":
        model.load_curtailment_fraction = pyo.Var(model.LOAD, bounds=(0.0, 1.0), initialize=0.0)
    elif objective_type == "max_loadability":
        upper = options.get("loadability_upper_bound")
        bounds = (0.0, float(upper)) if upper is not None else (0.0, None)
        model.loadability_factor = pyo.Var(bounds=bounds, initialize=float(options.get("initial_loadability", 1.0)))


def set_ac_opf_initial_values(
    model,
    case,
    data,
    *,
    warm_start: str = "case",
    stored_solution=None,
    load_flow_provider=None,
) -> None:
    """Initialize AC OPF variables from the selected warm-start source."""
    initial = build_ac_initial_point(
        case,
        data,
        warm_start=warm_start,
        stored_solution=stored_solution,
        load_flow_provider=load_flow_provider,
    )

    for gen in data.generators:
        model.pg[gen.id].set_value(initial.pg[gen.id])
        model.qg[gen.id].set_value(initial.qg[gen.id])

    for bus in data.buses:
        model.vm[bus.id].set_value(initial.vm[bus.id])
        model.va[bus.id].set_value(initial.va[bus.id])
    model.va[case.slack_bus_id].set_value(0.0)
    model._warm_start_source = initial.source
