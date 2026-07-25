"""Notebook-friendly AC OPF validation sweeps across PGLib cases."""

from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import re
import time
from typing import Iterable

import pandas as pd

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat
from own_models.src.analysis.contingencies import apply_branch_outage, apply_generator_outage
from own_models.src.data.case import Branch, Generator, Load, PowerCase
from own_models.src.opf.ac.model import solve_ac_opf_pyomo


DEFAULT_SETTINGS = {
    "relaxed_rate_multiplier": 10.0,
    "relaxed_voltage_bounds": (0.90, 1.10),
    "relaxed_angle_limit_deg": 180.0,
    "branch_rate_multipliers": [1.0, 0.8, 0.6, 0.4],
    "tight_voltage_bounds": [(0.95, 1.05), (0.98, 1.02), (0.99, 1.01)],
    "generator_pmax_reduction_multipliers": [1.0, 0.8, 0.6],
    "generator_pmax_expansion_multipliers": [1.0, 1.2, 1.5],
    "load_multipliers": [0.8, 1.0, 1.2, 1.4],
    "angle_limits_deg": [30.0, 20.0, 10.0, 5.0],
    "reactive_load_multipliers": [1.0, 1.2, 1.4],
    "qg_limit_multipliers": [1.0, 0.8, 0.6],
    "include_no_reference_slack": False,
    "branch_outage_top_k_loading": 3,
    "branch_outage_top_k_flow": 3,
    "generator_outage_top_k_cheapest": 2,
    "generator_outage_top_k_pg": 2,
    "voltage_bounds": None,
    "objective_type": "generation_cost",
    # "model_variant" is now a cosmetic label only (bus-injection was
    # deleted; solve_ac_opf_pyomo is the sole AC-OPF model) -- kept for CSV
    # schema compatibility with historical output.
    "model_variant": "pm_like_zero",
    # The compact validation sweep uses a flat voltage/reactive start by
    # default. On case1888_rte's generator-114 outage, the old case-file start
    # drove Ipopt to a locally infeasible point, while this start reproduced
    # the PowerModels objective quickly; see script 29 and the debug CSV.
    "warm_start": "flat",
    # Explicitly pinned rather than left to Ipopt's own default (verified
    # empirically via `ipopt --print_options_documentation`: Ipopt 3.14.19's
    # actual default max_iter is already 3000, not 500 as an earlier version
    # of this comment claimed). case6470_rte's base AC-OPF needed 564
    # iterations, case1888_rte's reactive_generation_limits scenario hit a
    # similar wall -- see docs/notes/solver_tolerances.md.
    "solver_options": {"print_level": 0, "max_iter": 3000},
    "scenario_filter_ids": None,
}


def run_compact_ac_validation_for_paths(
    case_paths: Iterable[str | Path],
    *,
    settings: dict | None = None,
    cost_power_units: str = "mw",
) -> pd.DataFrame:
    """Run compact AC OPF validation sweeps for many MATPOWER ``.mat`` files."""
    rows: list[dict[str, object]] = []
    for path in case_paths:
        case = load_powercase_from_matpower_mat(path)
        rows.extend(
            run_compact_ac_validation(
                case,
                settings=settings,
                cost_power_units=cost_power_units,
            )
        )
    return pd.DataFrame(rows)


def run_compact_ac_validation(
    case: PowerCase,
    *,
    settings: dict | None = None,
    cost_power_units: str = "mw",
) -> list[dict[str, object]]:
    """Run base, limits, stress, slack, and outage checks for one AC case."""
    return list(
        iter_compact_ac_validation(
            case,
            settings=settings,
            cost_power_units=cost_power_units,
        )
    )


def iter_compact_ac_validation(
    case: PowerCase,
    *,
    settings: dict | None = None,
    cost_power_units: str = "mw",
    completed_scenario_keys: set[tuple[str, str, str, str, str, str]] | None = None,
):
    """Yield compact AC OPF validation rows as soon as each scenario finishes."""
    cfg = {**DEFAULT_SETTINGS, **(settings or {})}
    completed_scenario_keys = completed_scenario_keys or set()

    base = _run_case(
        case,
        scenario="base",
        cost_power_units=cost_power_units,
        settings=cfg,
    )
    base_result = base["result"]
    if _is_selected_scenario(
        cfg,
        case,
        scenario="base",
    ) and not _is_completed_scenario(
        completed_scenario_keys,
        case,
        scenario="base",
    ):
        yield base if cfg.get("keep_raw_result") else _strip_result(base)
        completed_scenario_keys.add(compact_ac_scenario_key(base))

    if base_result is None:
        return

    relaxed_case = relax_limits(
        case,
        rate_multiplier=float(cfg["relaxed_rate_multiplier"]),
        angle_limit_deg=float(cfg["relaxed_angle_limit_deg"]),
    )
    yield from _run_if_needed(
        completed_scenario_keys,
        relaxed_case,
        scenario="relaxed_limits",
        parameter_name="relaxed_limits",
        parameter_value=float(cfg["relaxed_rate_multiplier"]),
        cost_power_units=cost_power_units,
        settings={**cfg, "voltage_bounds": relaxed_voltage_bounds(case, cfg["relaxed_voltage_bounds"])},
        base_result=base_result,
    )

    for multiplier in cfg["branch_rate_multipliers"]:
        scenario_case = scale_all_branch_rates(case, float(multiplier))
        yield from _run_if_needed(
            completed_scenario_keys,
            scenario_case,
            scenario="branch_congestion",
            target_type="system",
            target_id="all_branches",
            parameter_name="rate_multiplier",
            parameter_value=float(multiplier),
            cost_power_units=cost_power_units,
            settings=cfg,
            base_result=base_result,
        )

    for bounds in cfg["tight_voltage_bounds"]:
        yield from _run_if_needed(
            completed_scenario_keys,
            case,
            scenario="tight_voltage_limits",
            parameter_name="voltage_bounds",
            parameter_value=f"{bounds[0]}-{bounds[1]}",
            cost_power_units=cost_power_units,
            settings={**cfg, "voltage_bounds": restricted_voltage_bounds(case, tuple(bounds))},
            base_result=base_result,
        )

    generator = select_cheapest_dispatched_generator(case, base_result)
    if generator is not None:
        for multiplier in cfg["generator_pmax_reduction_multipliers"]:
            scenario_case = scale_generator_pmax(case, generator.id, float(multiplier))
            yield from _run_if_needed(
                completed_scenario_keys,
                scenario_case,
                scenario="generator_capacity_reduction",
                target_type="generator",
                target_id=generator.id,
                parameter_name="pmax_multiplier",
                parameter_value=float(multiplier),
                cost_power_units=cost_power_units,
                settings=cfg,
                base_result=base_result,
            )
        for multiplier in cfg["generator_pmax_expansion_multipliers"]:
            scenario_case = scale_generator_pmax(case, generator.id, float(multiplier))
            yield from _run_if_needed(
                completed_scenario_keys,
                scenario_case,
                scenario="generator_capacity_expansion",
                target_type="generator",
                target_id=generator.id,
                parameter_name="pmax_multiplier",
                parameter_value=float(multiplier),
                cost_power_units=cost_power_units,
                settings=cfg,
                base_result=base_result,
            )

    for multiplier in cfg["load_multipliers"]:
        scenario_case = scale_loads(case, float(multiplier), scale_p=True, scale_q=True)
        yield from _run_if_needed(
            completed_scenario_keys,
            scenario_case,
            scenario="load_scaling",
            parameter_name="load_multiplier",
            parameter_value=float(multiplier),
            cost_power_units=cost_power_units,
            settings=cfg,
            base_result=base_result,
        )

    for angle_limit_deg in cfg["angle_limits_deg"]:
        scenario_case = set_system_angle_limit(case, float(angle_limit_deg))
        yield from _run_if_needed(
            completed_scenario_keys,
            scenario_case,
            scenario="system_angle_limits",
            parameter_name="angle_limit_deg",
            parameter_value=float(angle_limit_deg),
            cost_power_units=cost_power_units,
            settings=cfg,
            base_result=base_result,
        )

    for multiplier in cfg["reactive_load_multipliers"]:
        scenario_case = scale_loads(case, float(multiplier), scale_p=False, scale_q=True)
        yield from _run_if_needed(
            completed_scenario_keys,
            scenario_case,
            scenario="reactive_power_stress",
            parameter_name="q_load_multiplier",
            parameter_value=float(multiplier),
            cost_power_units=cost_power_units,
            settings=cfg,
            base_result=base_result,
        )

    for multiplier in cfg["qg_limit_multipliers"]:
        scenario_case = tighten_reactive_generation_limits(case, float(multiplier))
        yield from _run_if_needed(
            completed_scenario_keys,
            scenario_case,
            scenario="reactive_generation_limits",
            target_type="system",
            target_id="all_generators",
            parameter_name="qg_limit_multiplier",
            parameter_value=float(multiplier),
            cost_power_units=cost_power_units,
            settings=cfg,
            base_result=base_result,
        )

    alternate_slack = select_alternate_slack_bus(case)
    if alternate_slack is not None:
        scenario_case = change_slack_bus(case, alternate_slack)
        yield from _run_if_needed(
            completed_scenario_keys,
            scenario_case,
            scenario="slack_consistency",
            target_type="bus",
            target_id=alternate_slack,
            parameter_name="slack_bus",
            parameter_value=alternate_slack,
            cost_power_units=cost_power_units,
            settings=cfg,
            base_result=base_result,
        )

    if bool(cfg["include_no_reference_slack"]):
        yield from _run_if_needed(
            completed_scenario_keys,
            case,
            scenario="slack_without_reference",
            parameter_name="fix_reference_angle",
            parameter_value=False,
            cost_power_units=cost_power_units,
            settings=cfg,
            base_result=base_result,
            fix_reference_angle=False,
        )

    for outage in select_branch_outages(
        case,
        base_result,
        top_k_loading=int(cfg["branch_outage_top_k_loading"]),
        top_k_flow=int(cfg["branch_outage_top_k_flow"]),
    ):
        scenario_case = apply_branch_outage(case, outage["branch_id"])
        yield from _run_if_needed(
            completed_scenario_keys,
            scenario_case,
            scenario="branch_outage",
            target_type="branch",
            target_id=outage["branch_id"],
            parameter_name="status",
            parameter_value=0.0,
            cost_power_units=cost_power_units,
            settings=cfg,
            base_result=base_result,
            metadata=outage,
        )

    for outage in select_generator_outages(
        case,
        base_result,
        top_k_cheapest=int(cfg["generator_outage_top_k_cheapest"]),
        top_k_pg=int(cfg["generator_outage_top_k_pg"]),
    ):
        scenario_case = apply_generator_outage(case, outage["gen_id"])
        yield from _run_if_needed(
            completed_scenario_keys,
            scenario_case,
            scenario="generator_outage",
            target_type="generator",
            target_id=outage["gen_id"],
            parameter_name="status",
            parameter_value=0.0,
            cost_power_units=cost_power_units,
            settings=cfg,
            base_result=base_result,
            metadata=outage,
        )


def _run_if_needed(
    completed_scenario_keys: set[tuple[str, str, str, str, str, str]],
    case: PowerCase,
    *,
    scenario: str,
    cost_power_units: str,
    settings: dict,
    target_type: str = "",
    target_id: str = "",
    parameter_name: str = "",
    parameter_value=None,
    base_result=None,
    fix_reference_angle: bool = True,
    metadata: dict[str, object] | None = None,
):
    if not _is_selected_scenario(
        settings,
        case,
        scenario=scenario,
        target_type=target_type,
        target_id=target_id,
        parameter_name=parameter_name,
        parameter_value=parameter_value,
    ):
        return

    if _is_completed_scenario(
        completed_scenario_keys,
        case,
        scenario=scenario,
        target_type=target_type,
        target_id=target_id,
        parameter_name=parameter_name,
        parameter_value=parameter_value,
    ):
        return

    row = _run_case(
        case,
        scenario=scenario,
        target_type=target_type,
        target_id=target_id,
        parameter_name=parameter_name,
        parameter_value=parameter_value,
        cost_power_units=cost_power_units,
        settings=settings,
        base_result=base_result,
        fix_reference_angle=fix_reference_angle,
        metadata=metadata,
    )
    yield row if settings.get("keep_raw_result") else _strip_result(row)
    completed_scenario_keys.add(compact_ac_scenario_key(row))


def _is_selected_scenario(
    settings: dict,
    case: PowerCase,
    *,
    scenario: str,
    target_type: str = "",
    target_id: str = "",
    parameter_name: str = "",
    parameter_value=None,
) -> bool:
    filter_ids = settings.get("scenario_filter_ids")
    if not filter_ids:
        return True
    return (
        compact_ac_scenario_id_from_values(
            case.name,
            scenario,
            target_type,
            target_id,
            parameter_name,
            parameter_value,
        )
        in set(map(str, filter_ids))
    )


def _is_completed_scenario(
    completed_scenario_keys: set[tuple[str, str, str, str, str, str]],
    case: PowerCase,
    *,
    scenario: str,
    target_type: str = "",
    target_id: str = "",
    parameter_name: str = "",
    parameter_value=None,
) -> bool:
    return (
        _scenario_key_values(
            case.name,
            scenario,
            target_type,
            target_id,
            parameter_name,
            parameter_value,
        )
        in completed_scenario_keys
    )


def compact_ac_scenario_id(row: dict[str, object]) -> str:
    """Return the filesystem-safe scenario id used by PowerModels exports."""
    return compact_ac_scenario_id_from_values(
        row.get("case_name", ""),
        row.get("scenario", ""),
        row.get("target_type", ""),
        row.get("target_id", ""),
        row.get("parameter_name", ""),
        row.get("parameter_value", ""),
    )


def compact_ac_scenario_id_from_values(
    case_name,
    scenario,
    target_type,
    target_id,
    parameter_name,
    parameter_value,
) -> str:
    parts = [
        _key_text(case_name),
        _key_text(scenario),
        _key_text(target_type),
        _key_text(target_id),
        _key_text(parameter_name),
        _key_text(parameter_value),
    ]
    text = "__".join(part if part else "none" for part in parts)
    text = text.replace(".", "p").replace("-", "m")
    return re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")


def compact_ac_scenario_key(row: dict[str, object]) -> tuple[str, str, str, str, str, str]:
    """Return a stable resume key for one compact AC OPF scenario row."""
    return _scenario_key_values(
        row.get("case_name", ""),
        row.get("scenario", ""),
        row.get("target_type", ""),
        row.get("target_id", ""),
        row.get("parameter_name", ""),
        row.get("parameter_value", ""),
    )


def _scenario_key_values(
    case_name,
    scenario,
    target_type,
    target_id,
    parameter_name,
    parameter_value,
) -> tuple[str, str, str, str, str, str]:
    return (
        _key_text(case_name),
        _key_text(scenario),
        _key_text(target_type),
        _key_text(target_id),
        _key_text(parameter_name),
        _key_text(parameter_value),
    )


def _key_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value)


def relax_limits(case: PowerCase, *, rate_multiplier: float, angle_limit_deg: float) -> PowerCase:
    """Relax all branch thermal and angle-difference limits."""
    limit = math.radians(angle_limit_deg)
    branches = [
        replace(
            branch,
            rate_a=branch.rate_a * rate_multiplier if branch.rate_a > 0.0 else 0.0,
            angmin=-limit,
            angmax=limit,
        )
        for branch in case.branches
    ]
    return replace(case, name=f"{case.name}_relaxed", branches=branches)


def scale_all_branch_rates(case: PowerCase, multiplier: float) -> PowerCase:
    """Scale every positive branch thermal rating by ``multiplier``."""
    branches: list[Branch] = [
        replace(branch, rate_a=branch.rate_a * multiplier if branch.rate_a > 0.0 else branch.rate_a)
        for branch in case.branches
    ]
    return replace(case, name=f"{case.name}_all_branch_rates_x{multiplier}", branches=branches)


def relaxed_voltage_bounds(
    case: PowerCase,
    target_bounds: tuple[float, float],
) -> dict[str, tuple[float, float]]:
    """Widen each bus voltage range using the original case limits as baseline."""
    lower, upper = target_bounds
    return {
        bus.id: (min(float(bus.vmin), float(lower)), max(float(bus.vmax), float(upper)))
        for bus in case.buses
    }


def restricted_voltage_bounds(
    case: PowerCase,
    target_bounds: tuple[float, float],
) -> dict[str, tuple[float, float]]:
    """Tighten voltage ranges without violating original PGLib bus limits.

    PGLib cases can contain bus-specific voltage ranges that do not overlap a
    global synthetic band such as 0.99-1.01.  For these stress scenarios, treat
    the requested band as a width and place it around each bus's case voltage
    start, clipped to the original PGLib interval.  This keeps the perturbation
    tight while guaranteeing ``vmin <= vmax`` for every bus.
    """
    lower, upper = target_bounds
    half_width = max(0.0, (float(upper) - float(lower)) / 2.0)
    if half_width == 0.0:
        raise ValueError(f"Voltage tightening bounds must have positive width, got {target_bounds!r}.")

    return {
        bus.id: tight_voltage_bounds_for_bus(bus, half_width)
        for bus in case.buses
    }


def tight_voltage_bounds_for_bus(bus, half_width: float) -> tuple[float, float]:
    """Build one valid tightened voltage interval from PGLib limits and Vm start."""
    case_lower = float(bus.vmin)
    case_upper = float(bus.vmax)
    if case_lower > case_upper:
        raise ValueError(f"Bus {bus.id} has invalid PGLib voltage limits: {case_lower} > {case_upper}.")

    center = min(max(float(bus.vm0), case_lower), case_upper)
    return (
        max(case_lower, center - half_width),
        min(case_upper, center + half_width),
    )


def scale_generator_pmax(case: PowerCase, gen_id: str, multiplier: float) -> PowerCase:
    """Scale one generator's active-power upper limit by ``multiplier``."""
    generators: list[Generator] = [
        replace(gen, pmax=max(gen.pmin, gen.pmax * multiplier)) if gen.id == gen_id else gen
        for gen in case.generators
    ]
    return replace(case, name=f"{case.name}_gen_{gen_id}_pmax_x{multiplier}", generators=generators)


def scale_loads(case: PowerCase, multiplier: float, *, scale_p: bool, scale_q: bool) -> PowerCase:
    """Scale active and/or reactive loads by ``multiplier``."""
    loads: list[Load] = [
        replace(
            load,
            pd=load.pd * multiplier if scale_p else load.pd,
            qd=load.qd * multiplier if scale_q else load.qd,
        )
        for load in case.loads
    ]
    return replace(case, name=f"{case.name}_load_x{multiplier}", loads=loads)


def set_system_angle_limit(case: PowerCase, angle_limit_deg: float) -> PowerCase:
    """Set the same symmetric angle-difference limit on every branch."""
    limit = math.radians(angle_limit_deg)
    branches: list[Branch] = [
        replace(branch, angmin=-limit, angmax=limit)
        for branch in case.branches
    ]
    return replace(case, name=f"{case.name}_angle_{angle_limit_deg}deg", branches=branches)


def tighten_reactive_generation_limits(case: PowerCase, multiplier: float) -> PowerCase:
    """Shrink each online generator's reactive capability interval around qg0."""
    generators: list[Generator] = []
    for gen in case.generators:
        if gen.status != 1:
            generators.append(gen)
            continue
        center = min(max(gen.qg0, gen.qmin), gen.qmax)
        lower_width = max(0.0, center - gen.qmin) * multiplier
        upper_width = max(0.0, gen.qmax - center) * multiplier
        generators.append(
            replace(
                gen,
                qmin=center - lower_width,
                qmax=center + upper_width,
                qg0=center,
            )
        )
    return replace(case, name=f"{case.name}_qg_limits_x{multiplier}", generators=generators)


def change_slack_bus(case: PowerCase, slack_bus_id: str) -> PowerCase:
    """Change only the reference bus id used by the OPF model."""
    return replace(case, name=f"{case.name}_slack_{slack_bus_id}", slack_bus_id=slack_bus_id)


def select_cheapest_dispatched_generator(case: PowerCase, result) -> Generator | None:
    """Select the cheapest online generator that produces above Pmin."""
    candidates = [
        gen
        for gen in case.generators
        if gen.status == 1 and gen.id in result.pg and result.pg[gen.id] > gen.pmin + 1e-7
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda gen: (gen.cost_c1, gen.cost_c2, gen.index))


def select_alternate_slack_bus(case: PowerCase) -> str | None:
    """Pick a deterministic non-current slack bus for reference-bus checks."""
    for bus in case.buses:
        if bus.id != case.slack_bus_id:
            return bus.id
    return None


def select_branch_outages(case: PowerCase, result, *, top_k_loading: int, top_k_flow: int) -> list[dict[str, object]]:
    """Select branch outages by highest loading and highest apparent flow."""
    candidates = []
    for branch in case.branches:
        if branch.status == 0 or branch.id not in result.branch_flows:
            continue
        flow = result.branch_flows[branch.id]
        apparent = max(
            math.hypot(float(flow["pf"]), float(flow["qf"])),
            math.hypot(float(flow["pt"]), float(flow["qt"])),
        )
        loading_pct = apparent / branch.rate_a * 100.0 if branch.rate_a > 0.0 else math.inf
        candidates.append(
            {
                "branch": branch,
                "branch_id": branch.id,
                "base_flow_pu": apparent,
                "rate_a_pu": branch.rate_a,
                "base_loading_pct": loading_pct,
            }
        )

    by_loading = sorted(
        candidates,
        key=lambda item: (float(item["base_loading_pct"]), float(item["base_flow_pu"])),
        reverse=True,
    )
    by_flow = sorted(candidates, key=lambda item: float(item["base_flow_pu"]), reverse=True)
    selected: dict[str, dict[str, object]] = {}
    _add_selection(selected, by_loading[:top_k_loading], "highest_loading", "loading_rank", "branch")
    _add_selection(selected, by_flow[:top_k_flow], "highest_flow", "flow_rank", "branch")
    return _sorted_selection(selected, "branch_id", ("loading_rank", "flow_rank"))


def select_generator_outages(case: PowerCase, result, *, top_k_cheapest: int, top_k_pg: int) -> list[dict[str, object]]:
    """Select generator outages by cheapest dispatched and largest dispatched."""
    candidates = []
    for gen in case.generators:
        if gen.status == 0 or gen.id not in result.pg:
            continue
        pg = result.pg[gen.id]
        if pg <= gen.pmin + 1e-7:
            continue
        candidates.append(
            {
                "gen": gen,
                "gen_id": gen.id,
                "bus_id": gen.bus,
                "base_pg_pu": pg,
                "pmax_pu": gen.pmax,
                "cost_c1": gen.cost_c1,
                "cost_c2": gen.cost_c2,
            }
        )

    by_cost = sorted(candidates, key=lambda item: (float(item["cost_c1"]), float(item["cost_c2"]), int(item["gen"].index)))
    by_pg = sorted(candidates, key=lambda item: float(item["base_pg_pu"]), reverse=True)
    selected: dict[str, dict[str, object]] = {}
    _add_selection(selected, by_cost[:top_k_cheapest], "cheapest_dispatched", "cost_rank", "gen")
    _add_selection(selected, by_pg[:top_k_pg], "largest_dispatched", "pg_rank", "gen")
    return _sorted_selection(selected, "gen_id", ("cost_rank", "pg_rank"))


def _run_case(
    case: PowerCase,
    *,
    scenario: str,
    cost_power_units: str,
    settings: dict,
    target_type: str = "",
    target_id: str = "",
    parameter_name: str = "",
    parameter_value=None,
    base_result=None,
    fix_reference_angle: bool = True,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    start = time.perf_counter()
    # "model_variant" is now a cosmetic label only -- solve_ac_opf_pyomo is
    # the sole AC-OPF model (bus-injection was deleted).
    model_variant = str(settings.get("model_variant", "pm_like_zero"))
    solver = solve_ac_opf_pyomo
    row_metadata = {"model_variant": model_variant}
    if metadata:
        row_metadata.update(metadata)
    try:
        result = solver(
            case,
            cost_power_units=cost_power_units,
            objective_type=str(settings["objective_type"]),
            voltage_bounds=settings["voltage_bounds"],
            include_branch_thermal=True,
            include_branch_angles=True,
            fix_reference_angle=fix_reference_angle,
            warm_start=str(settings["warm_start"]),
            solver_options=settings.get("solver_options"),
            model_name=_model_name(case, scenario, parameter_name, parameter_value, target_id),
        )
        solve_time_sec = time.perf_counter() - start
        return {
            **_success_row(
                case,
                result,
                scenario=scenario,
                target_type=target_type,
                target_id=target_id,
                parameter_name=parameter_name,
                parameter_value=parameter_value,
                base_result=base_result,
                solve_time_sec=solve_time_sec,
                metadata=row_metadata,
            ),
            "result": result,
            "case": case,
        }
    except Exception as exc:
        solve_time_sec = time.perf_counter() - start
        return {
            **_failure_row(
                case,
                exc,
                scenario=scenario,
                target_type=target_type,
                target_id=target_id,
                parameter_name=parameter_name,
                parameter_value=parameter_value,
                solve_time_sec=solve_time_sec,
                metadata=row_metadata,
            ),
            "result": None,
            "case": case,
        }


def _success_row(
    case: PowerCase,
    result,
    *,
    scenario: str,
    target_type: str,
    target_id: str,
    parameter_name: str,
    parameter_value,
    base_result,
    solve_time_sec: float,
    metadata: dict[str, object] | None,
) -> dict[str, object]:
    branch_metrics = _branch_metrics(case, result)
    generator_metrics = _generator_metrics(case, result)
    voltage_metrics = _voltage_metrics(result)
    objective_delta = 0.0 if base_result is None else result.objective - base_result.objective
    row = {
        "case_name": case.name,
        "n_buses": len(case.buses),
        "n_branches": sum(1 for branch in case.branches if branch.status == 1),
        "n_generators": sum(1 for gen in case.generators if gen.status == 1),
        "scenario": scenario,
        "target_type": target_type,
        "target_id": target_id,
        "parameter_name": parameter_name,
        "parameter_value": parameter_value,
        "success": bool(result.success),
        "status": result.status,
        "termination": result.status,
        "objective": result.objective,
        "objective_delta_vs_base": objective_delta,
        "total_pg": sum(result.pg.values()),
        "total_qg": sum(result.qg.values()),
        "total_pd": sum(load.pd for load in case.loads if load.status == 1),
        "total_qd": sum(load.qd for load in case.loads if load.status == 1),
        "min_vm": voltage_metrics["min_vm"],
        "max_vm": voltage_metrics["max_vm"],
        "max_p_balance_violation": result.max_p_balance_violation,
        "max_q_balance_violation": result.max_q_balance_violation,
        "max_voltage_violation": result.max_voltage_violation,
        "max_thermal_violation": result.max_thermal_violation,
        "max_angle_violation": result.max_angle_violation,
        "max_branch_loading_pct": branch_metrics["max_branch_loading_pct"],
        "n_thermal_binding": branch_metrics["n_thermal_binding"],
        "n_angle_binding": branch_metrics["n_angle_binding"],
        "n_gen_at_pmin": generator_metrics["n_gen_at_pmin"],
        "n_gen_at_pmax": generator_metrics["n_gen_at_pmax"],
        "n_qg_at_qmin": generator_metrics["n_qg_at_qmin"],
        "n_qg_at_qmax": generator_metrics["n_qg_at_qmax"],
        "loadability_factor": result.loadability_factor,
        "total_load_curtailment": sum(result.load_curtailment.values()),
        "warm_start_source": result.warm_start_source,
        "solve_time_sec": solve_time_sec,
        "message": result.message,
        "expected_observation": _expected_observation(scenario),
        "observation_note": _observation_note(scenario, result.success),
        "pm_status": "",
        "pm_objective": math.nan,
        "objective_diff_vs_pm": math.nan,
    }
    _add_metadata(row, metadata)
    return row


def _failure_row(case: PowerCase, exc: Exception, **kwargs) -> dict[str, object]:
    row = {
        "case_name": case.name,
        "n_buses": len(case.buses),
        "n_branches": sum(1 for branch in case.branches if branch.status == 1),
        "n_generators": sum(1 for gen in case.generators if gen.status == 1),
        "scenario": kwargs["scenario"],
        "target_type": kwargs["target_type"],
        "target_id": kwargs["target_id"],
        "parameter_name": kwargs["parameter_name"],
        "parameter_value": kwargs["parameter_value"],
        "success": False,
        "status": "ERROR",
        "termination": "ERROR",
        "objective": math.nan,
        "objective_delta_vs_base": math.nan,
        "total_pg": math.nan,
        "total_qg": math.nan,
        "total_pd": sum(load.pd for load in case.loads if load.status == 1),
        "total_qd": sum(load.qd for load in case.loads if load.status == 1),
        "min_vm": math.nan,
        "max_vm": math.nan,
        "max_p_balance_violation": math.nan,
        "max_q_balance_violation": math.nan,
        "max_voltage_violation": math.nan,
        "max_thermal_violation": math.nan,
        "max_angle_violation": math.nan,
        "max_branch_loading_pct": math.nan,
        "n_thermal_binding": 0,
        "n_angle_binding": 0,
        "n_gen_at_pmin": 0,
        "n_gen_at_pmax": 0,
        "n_qg_at_qmin": 0,
        "n_qg_at_qmax": 0,
        "loadability_factor": math.nan,
        "total_load_curtailment": math.nan,
        "warm_start_source": "",
        "solve_time_sec": kwargs["solve_time_sec"],
        "message": str(exc),
        "expected_observation": _expected_observation(kwargs["scenario"]),
        "observation_note": _observation_note(kwargs["scenario"], False),
        "pm_status": "",
        "pm_objective": math.nan,
        "objective_diff_vs_pm": math.nan,
    }
    _add_metadata(row, kwargs.get("metadata"))
    return row


def _branch_metrics(case: PowerCase, result, *, tol: float = 1e-5) -> dict[str, float | int]:
    max_loading = 0.0
    thermal_binding = 0
    angle_binding = 0
    for branch in case.branches:
        if branch.status == 0 or branch.id not in result.branch_flows:
            continue
        flow = result.branch_flows[branch.id]
        apparent = max(math.hypot(flow["pf"], flow["qf"]), math.hypot(flow["pt"], flow["qt"]))
        if branch.rate_a > 0.0:
            loading = apparent / branch.rate_a * 100.0
            max_loading = max(max_loading, loading)
            thermal_binding += int(abs(apparent - branch.rate_a) <= tol)
        angle_diff = result.va[branch.from_bus] - result.va[branch.to_bus]
        angle_binding += int(abs(angle_diff - branch.angmin) <= tol or abs(angle_diff - branch.angmax) <= tol)
    return {"max_branch_loading_pct": max_loading, "n_thermal_binding": thermal_binding, "n_angle_binding": angle_binding}


def _expected_observation(scenario: str) -> str:
    """Return the validation expectation for a scenario."""
    return {
        "base": "base AC OPF should solve normally",
        "relaxed_limits": "relaxed limits should not increase objective when feasible",
        "branch_congestion": "tighter thermal limits may bind, raise cost, or become infeasible",
        "tight_voltage_limits": "narrower voltage bounds may raise cost or become infeasible",
        "generator_capacity_reduction": "reduced cheap capacity may raise cost or become infeasible",
        "generator_capacity_expansion": "expanded cheap capacity may lower cost if usable",
        "load_scaling": "higher load should raise cost or eventually become infeasible",
        "system_angle_limits": "tighter angle limits may raise cost or become infeasible",
        "reactive_power_stress": "higher Q demand may bind Q limits or become infeasible",
        "reactive_generation_limits": "tighter Q capability may bind Q limits or become infeasible",
        "slack_consistency": "changing reference bus should preserve dispatch/objective",
        "slack_without_reference": "missing reference angle is deliberately ill-posed",
        "branch_outage": "outage may redispatch or become infeasible",
        "generator_outage": "outage may redispatch or become infeasible",
    }.get(scenario, "")


def _observation_note(scenario: str, success: bool) -> str:
    """Summarize whether infeasibility is surprising for this scenario."""
    if success:
        return "solved"
    if scenario == "base":
        return "unexpected: base case did not solve"
    if scenario == "slack_without_reference":
        return "expected-risk: missing reference angle gives an ill-posed model"
    return "stress boundary reached or contingency infeasible"


def _generator_metrics(case: PowerCase, result, *, tol: float = 1e-5) -> dict[str, int]:
    metrics = {"n_gen_at_pmin": 0, "n_gen_at_pmax": 0, "n_qg_at_qmin": 0, "n_qg_at_qmax": 0}
    for gen in case.generators:
        if gen.status == 0 or gen.id not in result.pg:
            continue
        metrics["n_gen_at_pmin"] += int(abs(result.pg[gen.id] - gen.pmin) <= tol)
        metrics["n_gen_at_pmax"] += int(abs(result.pg[gen.id] - gen.pmax) <= tol)
        metrics["n_qg_at_qmin"] += int(abs(result.qg[gen.id] - gen.qmin) <= tol)
        metrics["n_qg_at_qmax"] += int(abs(result.qg[gen.id] - gen.qmax) <= tol)
    return metrics


def _voltage_metrics(result) -> dict[str, float]:
    values = list(result.vm.values())
    return {"min_vm": min(values) if values else math.nan, "max_vm": max(values) if values else math.nan}


def _model_name(
    case: PowerCase,
    scenario: str,
    parameter_name: str,
    parameter_value,
    target_id: str,
) -> str:
    """Build a readable Pyomo model name for Ipopt warnings."""
    parts = [case.name, scenario]
    if target_id:
        parts.append(str(target_id))
    if parameter_name:
        parts.extend([parameter_name, str(parameter_value)])
    cleaned = "_".join(parts).replace(".", "p").replace("-", "m").replace(" ", "_")
    return f"{cleaned}_ac_opf"


def _add_selection(selected, items, reason: str, rank_key: str, object_key: str) -> None:
    for rank, item in enumerate(items, start=1):
        item_id = str(item["branch_id"] if object_key == "branch" else item["gen_id"])
        if item_id not in selected:
            selected[item_id] = {key: value for key, value in item.items() if key != object_key}
            selected[item_id]["selection_reason"] = reason
        else:
            selected[item_id]["selection_reason"] += f"+{reason}"
        selected[item_id][rank_key] = rank


def _sorted_selection(selected, id_key: str, rank_keys: tuple[str, str]) -> list[dict[str, object]]:
    return sorted(
        selected.values(),
        key=lambda item: (
            min(_rank_or_large(item.get(rank_keys[0])), _rank_or_large(item.get(rank_keys[1]))),
            str(item[id_key]),
        ),
    )


def _rank_or_large(value) -> int:
    return int(value) if value is not None and not pd.isna(value) else 10**9


def _add_metadata(row: dict[str, object], metadata: dict[str, object] | None) -> None:
    if not metadata:
        return
    for key, value in metadata.items():
        row[key] = value


def _strip_result(row: dict[str, object]) -> dict[str, object]:
    clean = dict(row)
    clean.pop("result", None)
    clean.pop("case", None)
    return clean
