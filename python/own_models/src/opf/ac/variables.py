"""Decision-variable declarations and initial values for AC OPF."""

from __future__ import annotations

from typing import cast

import numpy as np
import pyomo.environ as pyo


def finite_or_none(value: float):
    """Return a Pyomo-compatible finite bound or ``None``."""
    return float(value) if np.isfinite(value) else None


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
    # objective_type == "target_deviation" is handled entirely by
    # add_ac_target_deviation_variables (called instead of this function --
    # see model.py's fork on objective_type), which builds its own
    # dpg_plus/dpg_minus-style slack pairs as part of constructing
    # pg/qg/vm/va itself, not as a side-effect here.


def add_ac_target_deviation_variables(model, case, data, options: dict[str, object]) -> None:
    """Build Pg/Qg/Vm/Va for the target-deviation objective: for each of the
    4 groups, either a true `target + plus - minus` substitution Expression
    (included groups) or an ordinary bounded free Var (excluded groups) --
    mirrors Julia's `add_ac_target_deviation_variables!` (variables.jl)
    exactly, including its true-substitution fix for what used to be a
    separate linking-equality-constraint implementation (`x == target +
    plus - minus` as its own Constraint, keeping `x` a free Var) -- that
    formulation and this one have the same feasible set but are not the same
    problem to a nonlinear solver.

    Called INSTEAD of the ordinary variable-construction path (`_add_ac_variables`
    plus `add_ac_objective_variables`) when `objective_type == "target_deviation"`
    (see model.py's fork) -- builds `model.pg`/`qg`/`vm`/`va` itself, so that
    path must not also run.

    Reads from `options`: `target_pg`/`target_qg`/`target_vm`/`target_va`
    (the PowerModels reference point x^{p*}, always required); `own_pg`/
    `own_qg`/`own_vm`/`own_va` (the own model's own previous solution
    x^{e*}, required only when `warm_start_source="own"`); `include_pg`/
    `include_qg`/`include_vm`/`include_va` (default True -- excluded groups
    stay ordinary free variables, NOT pinned at target with zero degrees of
    freedom); `enforce_deviation_bounds` (default True -- False for the
    equality-only diagnostic); `enforce_excluded_bounds` (default False --
    drop the excluded group's own physical bound too, not just its
    objective tracking; see Julia's `add_ac_target_deviation_variables!`
    docstring for the direct investigation behind this default); and
    `warm_start_source` (`"target"` seeds every variable/expression at
    x^{p*}; `"own"` seeds at x^{e*}, the reachability test)."""
    warm_start_source = options.get("warm_start_source", "target")
    if warm_start_source not in ("target", "own"):
        raise ValueError(f"Unsupported warm_start_source={warm_start_source!r}. Expected 'target' or 'own'.")
    enforce_bounds = bool(options.get("enforce_deviation_bounds", True))
    enforce_excluded_bounds = bool(options.get("enforce_excluded_bounds", False))

    def build_group(name: str, index_set, target: dict[str, float], own, include: bool, bound_fn):
        target = cast(dict[str, float], target)
        own_dict = cast(dict[str, float], own) if own is not None else target

        def start_value(key, _target=target, _own=own_dict):
            return float(_own[key]) if warm_start_source == "own" else float(_target[key])

        if include:
            plus_name, minus_name = f"d{name}_plus", f"d{name}_minus"
            setattr(model, plus_name, pyo.Var(index_set, bounds=(0.0, None), initialize=0.0))
            setattr(model, minus_name, pyo.Var(index_set, bounds=(0.0, None), initialize=0.0))
            plus_var = getattr(model, plus_name)
            minus_var = getattr(model, minus_name)
            for key in index_set:
                gap = start_value(key) - float(target[key])
                plus_var[key].set_value(max(gap, 0.0))
                minus_var[key].set_value(max(-gap, 0.0))

            def expr_rule(_model, key, _target=target, _plus=plus_var, _minus=minus_var):
                return float(_target[key]) + _plus[key] - _minus[key]

            setattr(model, name, pyo.Expression(index_set, rule=expr_rule))

            if enforce_bounds:
                group_expr = getattr(model, name)
                lb_ids = [key for key in index_set if bound_fn(key)[0] is not None]
                ub_ids = [key for key in index_set if bound_fn(key)[1] is not None]
                if lb_ids:
                    setattr(
                        model,
                        f"{name}_lb",
                        pyo.Constraint(
                            lb_ids,
                            rule=lambda m, key, _bound_fn=bound_fn, _expr=group_expr: _expr[key] >= _bound_fn(key)[0],
                        ),
                    )
                if ub_ids:
                    setattr(
                        model,
                        f"{name}_ub",
                        pyo.Constraint(
                            ub_ids,
                            rule=lambda m, key, _bound_fn=bound_fn, _expr=group_expr: _expr[key] <= _bound_fn(key)[1],
                        ),
                    )
        else:
            if enforce_excluded_bounds:
                def var_bounds_rule(_model, key, _bound_fn=bound_fn):
                    return _bound_fn(key)

                setattr(model, name, pyo.Var(index_set, bounds=var_bounds_rule))
            else:
                setattr(model, name, pyo.Var(index_set))
            group_var = getattr(model, name)
            for key in index_set:
                group_var[key].set_value(start_value(key))

    build_group(
        "pg", model.GEN, options["target_pg"], options.get("own_pg"), bool(options.get("include_pg", True)),
        lambda gen_id: (finite_or_none(data.gen_by_id[gen_id].pmin), finite_or_none(data.gen_by_id[gen_id].pmax)),
    )
    build_group(
        "qg", model.GEN, options["target_qg"], options.get("own_qg"), bool(options.get("include_qg", True)),
        lambda gen_id: (finite_or_none(data.gen_by_id[gen_id].qmin), finite_or_none(data.gen_by_id[gen_id].qmax)),
    )
    build_group(
        "vm", model.BUS, options["target_vm"], options.get("own_vm"), bool(options.get("include_vm", True)),
        lambda bus_id: (finite_or_none(data.vmin_by_bus[bus_id]), finite_or_none(data.vmax_by_bus[bus_id])),
    )
    build_group(
        "va", model.BUS, options["target_va"], options.get("own_va"), bool(options.get("include_va", True)),
        lambda _bus_id: (None, None),
    )
