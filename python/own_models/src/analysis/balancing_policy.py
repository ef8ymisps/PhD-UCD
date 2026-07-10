from __future__ import annotations

from dataclasses import dataclass, replace

from own_models.src.analysis.contingencies import Contingency
from own_models.src.data.case import PQ, PV, SLACK, Bus, Generator, PowerCase


EPS = 1e-8


@dataclass(frozen=True)
class BalancingPolicyResult:
    """Result of static post-contingency active-power balancing."""
    success: bool
    policy: str
    lost_generation_p: float
    available_reserve_p: float
    uncovered_power_deficit_p: float
    scheduled_pg_by_generator: dict[str, float]
    responding_generator_ids: tuple[str, ...]
    saturated_generator_ids: tuple[str, ...]
    new_slack_generator_id: str
    pf_attempted: bool
    has_generation_imbalance: bool
    scheduled_replacement_error: float


def _base_pg(base_opf_result, gen_id: str, fallback: float) -> float:
    """Read base OPF Pg for a generator, falling back to case data."""
    return float(base_opf_result.pg.get(gen_id, fallback))


def _select_new_slack_generator(case: PowerCase, base_opf_result) -> Generator:
    """Select a replacement slack generator after the original slack is outaged."""
    # If the old slack generator is gone, choose the online generator with the
    # largest active reserve headroom as the new reference generator.
    candidates = [gen for gen in case.generators if gen.status == 1]
    if not candidates:
        raise ValueError("Cannot select a new slack generator: no active generators remain.")
    return sorted(
        candidates,
        key=lambda gen: (-(gen.pmax - _base_pg(base_opf_result, gen.id, gen.pg0)), gen.index, gen.id),
    )[0]


def _bus_type_after_slack_change(bus: Bus, generators: list[Generator], new_slack_bus_id: str) -> int:
    """Return the bus type after a post-contingency slack reassignment."""
    if bus.id == new_slack_bus_id:
        return SLACK
    # Keep generator buses as PV after slack reassignment; load-only buses are PQ.
    has_active_generator = any(gen.status == 1 and gen.bus == bus.id for gen in generators)
    return PV if has_active_generator else PQ


def _update_slack_if_needed(
    case: PowerCase,
    base_opf_result,
    outaged_generator_ids: tuple[str, ...],
) -> tuple[PowerCase, str]:
    """Keep the existing slack when possible, otherwise choose a new slack bus."""
    active_slack_generators = [
        gen for gen in case.generators if gen.status == 1 and gen.bus == case.slack_bus_id
    ]
    if active_slack_generators:
        selected = sorted(active_slack_generators, key=lambda gen: (gen.index, gen.id))[0]
        return case, selected.id

    new_slack = _select_new_slack_generator(case, base_opf_result)
    buses = [
        replace(bus, bus_type=_bus_type_after_slack_change(bus, case.generators, new_slack.bus))
        for bus in case.buses
    ]
    return replace(case, buses=buses, slack_bus_id=new_slack.bus), new_slack.id


def apply_balancing_policy(
    case: PowerCase,
    base_opf_result,
    contingency: Contingency,
    *,
    policy: str = "proportional_reserve",
    tol: float = EPS,
) -> tuple[PowerCase, BalancingPolicyResult]:
    """Apply static proportional-reserve balancing for one generator outage."""
    if policy != "proportional_reserve":
        raise NotImplementedError("Only proportional_reserve balancing is implemented in v1.")
    if len(contingency.outaged_generator_ids) != 1:
        raise NotImplementedError("Only N-1 generator balancing is implemented in v1.")

    outaged_id = contingency.outaged_generator_ids[0]
    outaged = next(gen for gen in case.generators if gen.id == outaged_id)
    lost_generation = _base_pg(base_opf_result, outaged.id, outaged.pg0)
    balanced_case, slack_generator_id = _update_slack_if_needed(case, base_opf_result, contingency.outaged_generator_ids)

    scheduled = {
        gen.id: _base_pg(base_opf_result, gen.id, gen.pg0)
        for gen in balanced_case.generators
        if gen.status == 1
    }
    reserves = {
        gen.id: max(0.0, gen.pmax - _base_pg(base_opf_result, gen.id, gen.pg0))
        for gen in balanced_case.generators
        if gen.status == 1 and gen.id != outaged_id
    }
    eligible = tuple(gen_id for gen_id, reserve in reserves.items() if reserve > tol)
    total_reserve = sum(reserves[gen_id] for gen_id in eligible)
    uncovered = max(lost_generation - total_reserve, 0.0)

    if total_reserve + tol < lost_generation:
        # Do not force an infeasible redispatch; the caller records this as a
        # generation-imbalance case before attempting AC PF.
        result = BalancingPolicyResult(
            success=False,
            policy=policy,
            lost_generation_p=float(lost_generation),
            available_reserve_p=float(total_reserve),
            uncovered_power_deficit_p=float(uncovered),
            scheduled_pg_by_generator=scheduled,
            responding_generator_ids=eligible,
            saturated_generator_ids=(),
            new_slack_generator_id=slack_generator_id,
            pf_attempted=False,
            has_generation_imbalance=True,
            scheduled_replacement_error=float(-lost_generation),
        )
        return balanced_case, result

    for gen_id in eligible:
        # Static proportional reserve policy: pick up lost active power in
        # proportion to each generator's available headroom.
        scheduled[gen_id] = scheduled[gen_id] + lost_generation * reserves[gen_id] / total_reserve

    saturated = []
    replacement = 0.0
    for gen in balanced_case.generators:
        if gen.status != 1 or gen.id == outaged_id:
            continue
        base_pg = _base_pg(base_opf_result, gen.id, gen.pg0)
        replacement += scheduled.get(gen.id, base_pg) - base_pg
        if abs(scheduled.get(gen.id, base_pg) - gen.pmax) <= tol:
            saturated.append(gen.id)

    generators = [
        replace(gen, pg0=scheduled[gen.id]) if gen.status == 1 and gen.id in scheduled else gen
        for gen in balanced_case.generators
    ]
    result = BalancingPolicyResult(
        success=True,
        policy=policy,
        lost_generation_p=float(lost_generation),
        available_reserve_p=float(total_reserve),
        uncovered_power_deficit_p=0.0,
        scheduled_pg_by_generator=scheduled,
        responding_generator_ids=eligible,
        saturated_generator_ids=tuple(saturated),
        new_slack_generator_id=slack_generator_id,
        pf_attempted=True,
        has_generation_imbalance=False,
        scheduled_replacement_error=float(replacement - lost_generation),
    )
    return replace(balanced_case, generators=generators), result
