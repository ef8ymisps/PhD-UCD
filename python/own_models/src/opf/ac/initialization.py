"""Initial-value construction for AC OPF.

Warm starts are research infrastructure, not part of the OPF formulation. This
module builds a complete initial point for Pg, Qg, Vm, and Va from increasingly
rich sources while keeping the Pyomo model assembly independent of those
sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from own_models.src.opf.solutions import StoredOPFSolution


LoadFlowProvider = Callable[[object], object | None]


@dataclass(frozen=True)
class ACInitialPoint:
    """Complete AC OPF initial point indexed by repository ids."""

    source: str
    pg: dict[str, float]
    qg: dict[str, float]
    vm: dict[str, float]
    va: dict[str, float]


def clip_to_bounds(value: float, lower: float, upper: float) -> float:
    """Clip a scalar value to finite lower/upper bounds when they exist."""
    if np.isfinite(lower):
        value = max(value, lower)
    if np.isfinite(upper):
        value = min(value, upper)
    return float(value)


def build_ac_initial_point(
    case,
    data,
    *,
    warm_start: str = "case",
    stored_solution: StoredOPFSolution | None = None,
    load_flow_provider: LoadFlowProvider | None = None,
) -> ACInitialPoint:
    """Build a complete AC OPF initial point.

    Supported modes:
    - ``case``: use Pg/Qg/Vm/Va already stored in the PowerCase.
    - ``flat``: use clipped generation starts, Vm=1.0, Va=0.0.
    - ``stored_opf``: use a provided stored AC/DC OPF solution where available.
    - ``pypowsybl_lf``: use a caller-provided PyPowSyBl LF result/provider.
    - ``replacement``: currently aliases case starts; the case records already
      hold PGLib/MATPOWER Pg, Qg, and bus voltage starts.
    - ``auto``: stored OPF, then PyPowSyBl LF, then replacement/case, then flat.
    """
    mode = warm_start.lower()
    if mode == "auto":
        for candidate in ("stored_opf", "pypowsybl_lf", "replacement", "flat"):
            point = _try_build_initial_point(
                case,
                data,
                candidate,
                stored_solution=stored_solution,
                load_flow_provider=load_flow_provider,
            )
            if point is not None:
                return point
        return build_flat_initial_point(case, data)

    point = _try_build_initial_point(
        case,
        data,
        mode,
        stored_solution=stored_solution,
        load_flow_provider=load_flow_provider,
    )
    if point is None:
        raise ValueError(f"Warm-start mode {warm_start!r} could not build an initial point.")
    return point


def _try_build_initial_point(
    case,
    data,
    mode: str,
    *,
    stored_solution: StoredOPFSolution | None,
    load_flow_provider: LoadFlowProvider | None,
) -> ACInitialPoint | None:
    """Return an initial point for one source, or None when unavailable."""
    if mode == "case" or mode == "replacement":
        return build_case_initial_point(case, data, source=mode)
    if mode == "flat":
        return build_flat_initial_point(case, data)
    if mode == "stored_opf":
        if stored_solution is None:
            return None
        return build_stored_solution_initial_point(case, data, stored_solution)
    if mode == "pypowsybl_lf":
        if load_flow_provider is None:
            return None
        lf_result = load_flow_provider(case)
        if lf_result is None:
            return None
        return build_load_flow_initial_point(case, data, lf_result)
    raise ValueError(f"Unsupported AC warm-start mode {mode!r}.")


def build_case_initial_point(case, data, *, source: str = "case") -> ACInitialPoint:
    """Use the case's Pg/Qg/Vm/Va values as the initial point."""
    return ACInitialPoint(
        source=source,
        pg={
            gen.id: clip_to_bounds(gen.pg0, gen.pmin, gen.pmax)
            for gen in data.generators
        },
        qg={
            gen.id: clip_to_bounds(gen.qg0, gen.qmin, gen.qmax)
            for gen in data.generators
        },
        vm={
            bus.id: clip_to_bounds(bus.vm0, data.vmin_by_bus[bus.id], data.vmax_by_bus[bus.id])
            for bus in data.buses
        },
        va={bus.id: float(bus.va0) for bus in data.buses},
    )


def build_flat_initial_point(case, data) -> ACInitialPoint:
    """Use a simple complete initial point: clipped Pg/Qg, Vm=1, Va=0."""
    return ACInitialPoint(
        source="flat",
        pg={
            gen.id: clip_to_bounds(gen.pg0, gen.pmin, gen.pmax)
            for gen in data.generators
        },
        qg={
            gen.id: clip_to_bounds(0.0, gen.qmin, gen.qmax)
            for gen in data.generators
        },
        vm={
            bus.id: clip_to_bounds(1.0, data.vmin_by_bus[bus.id], data.vmax_by_bus[bus.id])
            for bus in data.buses
        },
        va={bus.id: 0.0 for bus in data.buses},
    )


def build_stored_solution_initial_point(case, data, solution: StoredOPFSolution) -> ACInitialPoint:
    """Use stored OPF values where available and fill missing fields from case data."""
    fallback = build_case_initial_point(case, data)
    pg = dict(fallback.pg)
    qg = dict(fallback.qg)
    vm = dict(fallback.vm)
    va = dict(fallback.va)

    for gen in data.generators:
        if gen.id in solution.pg:
            pg[gen.id] = clip_to_bounds(float(solution.pg[gen.id]), gen.pmin, gen.pmax)
        if solution.qg is not None and gen.id in solution.qg:
            qg[gen.id] = clip_to_bounds(float(solution.qg[gen.id]), gen.qmin, gen.qmax)

    for bus in data.buses:
        if solution.vm is not None and bus.id in solution.vm:
            vm[bus.id] = clip_to_bounds(
                float(solution.vm[bus.id]),
                data.vmin_by_bus[bus.id],
                data.vmax_by_bus[bus.id],
            )
        if solution.va is not None and bus.id in solution.va:
            va[bus.id] = float(solution.va[bus.id])
        elif solution.theta is not None and bus.id in solution.theta:
            va[bus.id] = float(solution.theta[bus.id])

    return ACInitialPoint(source=f"stored_opf:{solution.source}", pg=pg, qg=qg, vm=vm, va=va)


def build_load_flow_initial_point(case, data, lf_result) -> ACInitialPoint:
    """Use a PyPowSyBl-style LF result for voltages, with case generation fallback.

    The provider may return either an ``ACInitialPoint`` or an object/dict with
    ``vm``/``va`` dictionaries. Pg/Qg are optional; missing values fall back to
    the case starts.
    """
    if isinstance(lf_result, ACInitialPoint):
        return lf_result

    fallback = build_case_initial_point(case, data)
    pg = dict(fallback.pg)
    qg = dict(fallback.qg)
    vm = dict(fallback.vm)
    va = dict(fallback.va)

    source_pg = _mapping_value(lf_result, "pg") or {}
    source_qg = _mapping_value(lf_result, "qg") or {}
    source_vm = _mapping_value(lf_result, "vm") or {}
    source_va = _mapping_value(lf_result, "va") or {}

    for gen in data.generators:
        if gen.id in source_pg:
            pg[gen.id] = clip_to_bounds(float(source_pg[gen.id]), gen.pmin, gen.pmax)
        if gen.id in source_qg:
            qg[gen.id] = clip_to_bounds(float(source_qg[gen.id]), gen.qmin, gen.qmax)

    for bus in data.buses:
        if bus.id in source_vm:
            vm[bus.id] = clip_to_bounds(
                float(source_vm[bus.id]),
                data.vmin_by_bus[bus.id],
                data.vmax_by_bus[bus.id],
            )
        if bus.id in source_va:
            va[bus.id] = float(source_va[bus.id])

    return ACInitialPoint(source="pypowsybl_lf", pg=pg, qg=qg, vm=vm, va=va)


def _mapping_value(obj, name: str):
    """Read mapping field from either a dict or a light result object."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
