from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from own_models.src.data.case import PQ, PV, SLACK, PowerCase
from own_models.src.network.admittance import (
    build_admittance_matrix,
    calc_ac_branch_flows,
    calc_bus_power_injections,
)


@dataclass(frozen=True)
class ACPowerFlowResult:
    """Container for AC PF convergence, voltages, injections, and branch flows."""
    converged: bool
    iterations: int
    vm: np.ndarray
    va: np.ndarray
    pcalc: np.ndarray
    qcalc: np.ndarray
    qg_by_bus: dict[str, float]
    branch_flows: dict[str, dict[str, float]]
    mismatch_norm: float
    final_bus_types: dict[str, int] = field(default_factory=dict)
    switched_buses: tuple[str, ...] = ()
    switching_events: tuple["PVtoPQSwitchingEvent", ...] = ()
    outer_iterations: int = 0
    qg_by_generator: dict[str, float] = field(default_factory=dict)
    slack_q_violation: bool = False
    q_limit_converged: bool = True
    termination_reason: str = "fixed_type_converged"
    power_flow_converged: bool = True


@dataclass(frozen=True)
class PVtoPQSwitchingEvent:
    outer_iteration: int
    bus_id: str
    from_type: int
    to_type: int
    required_q: float
    qmin_bus: float
    qmax_bus: float
    fixed_q: float
    violation_direction: str
    violation_magnitude: float


@dataclass(frozen=True)
class QAllocationResult:
    aggregate_feasible: bool
    individual_allocation: dict[str, float]


def _bus_type_indices(case: PowerCase) -> tuple[int, list[int], list[int]]:
    """Return slack, PV, and PQ bus indices for the Newton state construction."""
    # Bus types come from case data. This path trusts case.slack_bus_id and
    # does not reclassify buses during the Newton solve.
    slack = case.bus_id_to_index[case.slack_bus_id]
    pv = [bus.index - 1 for bus in case.buses if bus.bus_type == PV]
    pq = [bus.index - 1 for bus in case.buses if bus.bus_type == PQ]
    return slack, pv, pq


def _bus_type_indices_from_working_types(
    case: PowerCase,
    bus_types: dict[str, int],
) -> tuple[int, list[int], list[int]]:
    slack = case.bus_id_to_index[case.slack_bus_id]
    pv = [case.bus_id_to_index[bus.id] for bus in case.buses if bus_types[bus.id] == PV]
    pq = [case.bus_id_to_index[bus.id] for bus in case.buses if bus_types[bus.id] == PQ]
    return slack, pv, pq


def _specified_power(case: PowerCase) -> tuple[np.ndarray, np.ndarray]:
    """Build fixed specified P/Q injection vectors for AC power flow."""
    # Fixed PF injections: online generators add Pg/Qg; online loads subtract
    # Pd/Qd. Generator controls are not redispatched in power flow.
    p = np.zeros(len(case.buses), dtype=float)
    q = np.zeros(len(case.buses), dtype=float)

    for gen in case.generators:
        if gen.status == 1:
            idx = case.bus_id_to_index[gen.bus]
            p[idx] += gen.pg0
            q[idx] += gen.qg0

    for load in case.loads:
        if load.status == 1:
            idx = case.bus_id_to_index[load.bus]
            p[idx] -= load.pd
            q[idx] -= load.qd

    return p, q


def _load_q_by_bus(case: PowerCase) -> dict[str, float]:
    qd_by_bus = {bus.id: 0.0 for bus in case.buses}
    for load in case.loads:
        if load.status == 1:
            qd_by_bus[load.bus] += load.qd
    return qd_by_bus


def _jacobian(
    g: np.ndarray,
    b: np.ndarray,
    vm: np.ndarray,
    va: np.ndarray,
    pcalc: np.ndarray,
    qcalc: np.ndarray,
    angle_buses: list[int],
    pq_buses: list[int],
) -> np.ndarray:
    """Build the Newton AC power-flow Jacobian for the current voltage state."""
    n_angle = len(angle_buses)
    n_pq = len(pq_buses)
    j11 = np.zeros((n_angle, n_angle), dtype=float)
    j12 = np.zeros((n_angle, n_pq), dtype=float)
    j21 = np.zeros((n_pq, n_angle), dtype=float)
    j22 = np.zeros((n_pq, n_pq), dtype=float)

    for row, i in enumerate(angle_buses):
        for col, j in enumerate(angle_buses):
            if i == j:
                j11[row, col] = -qcalc[i] - b[i, i] * vm[i] ** 2
            else:
                angle = va[i] - va[j]
                j11[row, col] = vm[i] * vm[j] * (
                    g[i, j] * np.sin(angle) - b[i, j] * np.cos(angle)
                )

        for col, j in enumerate(pq_buses):
            if i == j:
                j12[row, col] = pcalc[i] / vm[i] + g[i, i] * vm[i]
            else:
                angle = va[i] - va[j]
                j12[row, col] = vm[i] * (
                    g[i, j] * np.cos(angle) + b[i, j] * np.sin(angle)
                )

    for row, i in enumerate(pq_buses):
        for col, j in enumerate(angle_buses):
            if i == j:
                j21[row, col] = pcalc[i] - g[i, i] * vm[i] ** 2
            else:
                angle = va[i] - va[j]
                j21[row, col] = -vm[i] * vm[j] * (
                    g[i, j] * np.cos(angle) + b[i, j] * np.sin(angle)
                )

        for col, j in enumerate(pq_buses):
            if i == j:
                j22[row, col] = qcalc[i] / vm[i] - b[i, i] * vm[i]
            else:
                angle = va[i] - va[j]
                j22[row, col] = vm[i] * (
                    g[i, j] * np.sin(angle) - b[i, j] * np.cos(angle)
                )

    return np.block([[j11, j12], [j21, j22]])


def _estimate_qg_by_bus(case: PowerCase, qcalc: np.ndarray) -> dict[str, float]:
    """Reconstruct bus-level reactive generation needed by the solved PF state."""
    qd_by_bus = {bus.id: 0.0 for bus in case.buses}
    for load in case.loads:
        if load.status == 1:
            qd_by_bus[load.bus] += load.qd

    qg_by_bus: dict[str, float] = {}
    gen_buses = {gen.bus for gen in case.generators if gen.status == 1}
    for bus_id in gen_buses:
        idx = case.bus_id_to_index[bus_id]
        qg_by_bus[bus_id] = float(qcalc[idx] + qd_by_bus.get(bus_id, 0.0))
    return qg_by_bus


def _active_generators_by_bus(case: PowerCase) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for gen in case.generators:
        if gen.status == 1:
            grouped.setdefault(gen.bus, []).append(gen)
    for generators in grouped.values():
        generators.sort(key=lambda gen: (gen.index, gen.id))
    return grouped


def _aggregate_q_limits_by_bus(case: PowerCase) -> dict[str, tuple[float, float]]:
    result: dict[str, tuple[float, float]] = {}
    for bus_id, generators in _active_generators_by_bus(case).items():
        result[bus_id] = (
            float(sum(gen.qmin for gen in generators)),
            float(sum(gen.qmax for gen in generators)),
        )
    return result


def allocate_qg_to_generators(generators: list, q_required: float, *, tol: float = 1e-8) -> QAllocationResult:
    """Allocate bus-level reactive generation deterministically across generators.

    This is an allocation convention for reporting and limit checking; AC PF
    determines only the aggregate reactive requirement at the bus.
    """
    if not generators:
        return QAllocationResult(
            aggregate_feasible=abs(q_required) <= tol,
            individual_allocation={},
        )

    ordered = sorted(generators, key=lambda gen: (gen.index, gen.id))
    qmin_total = float(sum(gen.qmin for gen in ordered))
    qmax_total = float(sum(gen.qmax for gen in ordered))
    aggregate_feasible = qmin_total - tol <= q_required <= qmax_total + tol
    target = min(max(q_required, qmin_total), qmax_total)

    allocation = {gen.id: float(gen.qmin) for gen in ordered}
    remaining = target - qmin_total
    flexible = [gen for gen in ordered if gen.qmax - gen.qmin > tol]

    while remaining > tol and flexible:
        total_range = sum(gen.qmax - allocation[gen.id] for gen in flexible)
        if total_range <= tol:
            break
        next_flexible = []
        used = 0.0
        for gen in flexible:
            headroom = gen.qmax - allocation[gen.id]
            add = min(headroom, remaining * headroom / total_range)
            allocation[gen.id] += add
            used += add
            if gen.qmax - allocation[gen.id] > tol:
                next_flexible.append(gen)
        remaining -= used
        flexible = next_flexible

    if abs(sum(allocation.values()) - target) > tol:
        for gen in ordered:
            headroom = gen.qmax - allocation[gen.id]
            add = min(headroom, target - sum(allocation.values()))
            if add > 0:
                allocation[gen.id] += add
            if abs(sum(allocation.values()) - target) <= tol:
                break

    return QAllocationResult(
        aggregate_feasible=aggregate_feasible,
        individual_allocation={gen_id: float(value) for gen_id, value in allocation.items()},
    )


def _allocate_all_qg_by_generator(case: PowerCase, qg_by_bus: dict[str, float]) -> tuple[dict[str, float], bool]:
    grouped = _active_generators_by_bus(case)
    allocation: dict[str, float] = {}
    all_feasible = True
    for bus_id, generators in grouped.items():
        result = allocate_qg_to_generators(generators, qg_by_bus.get(bus_id, 0.0))
        allocation.update(result.individual_allocation)
        all_feasible = all_feasible and result.aggregate_feasible
    return allocation, all_feasible


def solve_ac_power_flow_newton_fixed_types(
    case: PowerCase,
    *,
    pv_buses: list[int],
    pq_buses: list[int],
    qspec_override: dict[int, float] | None = None,
    vm_start: np.ndarray | None = None,
    va_start: np.ndarray | None = None,
    tol: float = 1e-8,
    max_iter: int = 20,
) -> ACPowerFlowResult:
    """Solve one fixed PV/PQ/slack classification with Newton-Raphson."""
    ybus = build_admittance_matrix(case)
    g = ybus.real
    b = ybus.imag
    pspec, qspec = _specified_power(case)
    if qspec_override:
        for idx, value in qspec_override.items():
            qspec[idx] = value
    angle_buses = sorted(pv_buses + pq_buses)

    vm = np.array(vm_start if vm_start is not None else [bus.vm0 for bus in case.buses], dtype=float)
    va = np.array(va_start if va_start is not None else [bus.va0 for bus in case.buses], dtype=float)
    iterations = 0
    mismatch_norm = np.inf
    converged = False

    for iteration in range(1, max_iter + 1):
        pcalc, qcalc = calc_bus_power_injections(ybus, vm, va)
        mismatch = np.concatenate(
            [pspec[angle_buses] - pcalc[angle_buses], qspec[pq_buses] - qcalc[pq_buses]]
        )
        mismatch_norm = float(np.linalg.norm(mismatch, ord=np.inf))
        iterations = iteration - 1
        if mismatch_norm <= tol:
            converged = True
            break

        jac = _jacobian(g, b, vm, va, pcalc, qcalc, angle_buses, pq_buses)
        try:
            dx = np.linalg.solve(jac, mismatch)
        except np.linalg.LinAlgError:
            break
        n_angle = len(angle_buses)
        va[angle_buses] += dx[:n_angle]
        vm[pq_buses] += dx[n_angle:]
        iterations = iteration

    pcalc, qcalc = calc_bus_power_injections(ybus, vm, va)
    final_mismatch = np.concatenate(
        [pspec[angle_buses] - pcalc[angle_buses], qspec[pq_buses] - qcalc[pq_buses]]
    )
    mismatch_norm = float(np.linalg.norm(final_mismatch, ord=np.inf))
    converged = converged or mismatch_norm <= tol
    qg_by_bus = _estimate_qg_by_bus(case, qcalc)
    qg_by_generator, allocation_feasible = _allocate_all_qg_by_generator(case, qg_by_bus)

    return ACPowerFlowResult(
        converged=converged,
        iterations=iterations,
        vm=vm,
        va=va,
        pcalc=pcalc,
        qcalc=qcalc,
        qg_by_bus=qg_by_bus,
        qg_by_generator=qg_by_generator,
        branch_flows=calc_ac_branch_flows(case, vm, va),
        mismatch_norm=mismatch_norm,
        final_bus_types={bus.id: bus.bus_type for bus in case.buses},
        q_limit_converged=allocation_feasible,
        termination_reason="fixed_type_converged" if converged else "newton_failed",
        power_flow_converged=converged,
    )


def solve_ac_power_flow_newton(
    case: PowerCase,
    *,
    tol: float = 1e-8,
    max_iter: int = 20,
) -> ACPowerFlowResult:
    """Solve AC power flow with a classical Newton-Raphson iteration."""
    _, pv_buses, pq_buses = _bus_type_indices(case)
    return solve_ac_power_flow_newton_fixed_types(
        case,
        pv_buses=pv_buses,
        pq_buses=pq_buses,
        tol=tol,
        max_iter=max_iter,
    )


def solve_ac_power_flow_with_q_limits(
    case: PowerCase,
    *,
    mode: str = "one_way",
    switch_policy: str = "all",
    q_tolerance: float = 1e-6,
    max_switch_iterations: int | None = None,
    newton_tol: float = 1e-8,
    newton_max_iter: int = 20,
) -> ACPowerFlowResult:
    """Solve AC PF with one-way PV-PQ switching for generator Q limits."""
    if mode != "one_way":
        raise NotImplementedError("Only one-way PV-PQ switching is implemented in this phase.")
    if switch_policy not in {"all", "largest_violation"}:
        raise ValueError("switch_policy must be 'all' or 'largest_violation'.")

    bus_types = {bus.id: bus.bus_type for bus in case.buses}
    _, original_pv, _ = _bus_type_indices(case)
    original_non_slack_pv = [
        idx for idx in original_pv if case.buses[idx].id != case.slack_bus_id
    ]
    if max_switch_iterations is None:
        max_switch_iterations = len(original_non_slack_pv) + 1

    qd_by_bus = _load_q_by_bus(case)
    q_limits = _aggregate_q_limits_by_bus(case)
    qspec_override: dict[int, float] = {}
    switched: set[str] = set()
    events: list[PVtoPQSwitchingEvent] = []
    vm_start = None
    va_start = None
    last_result: ACPowerFlowResult | None = None

    for outer in range(1, max_switch_iterations + 1):
        _, pv_buses, pq_buses = _bus_type_indices_from_working_types(case, bus_types)
        result = solve_ac_power_flow_newton_fixed_types(
            case,
            pv_buses=pv_buses,
            pq_buses=pq_buses,
            qspec_override=qspec_override,
            vm_start=vm_start,
            va_start=va_start,
            tol=newton_tol,
            max_iter=newton_max_iter,
        )
        last_result = result
        if not result.converged:
            return ACPowerFlowResult(
                converged=result.converged,
                iterations=result.iterations,
                vm=result.vm,
                va=result.va,
                pcalc=result.pcalc,
                qcalc=result.qcalc,
                qg_by_bus=result.qg_by_bus,
                qg_by_generator=result.qg_by_generator,
                branch_flows=result.branch_flows,
                mismatch_norm=result.mismatch_norm,
                final_bus_types=bus_types.copy(),
                switched_buses=tuple(sorted(switched)),
                switching_events=tuple(events),
                outer_iterations=outer,
                q_limit_converged=False,
                termination_reason="newton_failed",
                power_flow_converged=False,
            )

        violations = []
        for idx in pv_buses:
            bus = case.buses[idx]
            if bus.id == case.slack_bus_id or bus.id in switched or bus.id not in q_limits:
                continue
            q_required = result.qg_by_bus.get(bus.id, 0.0)
            qmin, qmax = q_limits[bus.id]
            if q_required > qmax + q_tolerance:
                violations.append((q_required - qmax, bus.id, q_required, qmin, qmax, qmax, "upper"))
            elif q_required < qmin - q_tolerance:
                violations.append((qmin - q_required, bus.id, q_required, qmin, qmax, qmin, "lower"))

        if not violations:
            qg_by_generator, allocation_feasible = _allocate_all_qg_by_generator(case, result.qg_by_bus)
            slack_violation = False
            if case.slack_bus_id in q_limits:
                q_slack = result.qg_by_bus.get(case.slack_bus_id, 0.0)
                qmin_slack, qmax_slack = q_limits[case.slack_bus_id]
                slack_violation = q_slack < qmin_slack - q_tolerance or q_slack > qmax_slack + q_tolerance
            q_limit_ok = allocation_feasible and not slack_violation
            reason = "q_limits_satisfied" if q_limit_ok else (
                "slack_q_limit_violated" if slack_violation else "individual_q_allocation_infeasible"
            )
            return ACPowerFlowResult(
                converged=result.converged,
                iterations=result.iterations,
                vm=result.vm,
                va=result.va,
                pcalc=result.pcalc,
                qcalc=result.qcalc,
                qg_by_bus=result.qg_by_bus,
                qg_by_generator=qg_by_generator,
                branch_flows=result.branch_flows,
                mismatch_norm=result.mismatch_norm,
                final_bus_types=bus_types.copy(),
                switched_buses=tuple(sorted(switched)),
                switching_events=tuple(events),
                outer_iterations=outer,
                slack_q_violation=slack_violation,
                q_limit_converged=q_limit_ok,
                termination_reason=reason,
                power_flow_converged=result.converged,
            )

        if switch_policy == "largest_violation":
            violations = [max(violations, key=lambda item: item[0])]

        for magnitude, bus_id, q_required, qmin, qmax, fixed_q, direction in violations:
            idx = case.bus_id_to_index[bus_id]
            bus_types[bus_id] = PQ
            qspec_override[idx] = fixed_q - qd_by_bus.get(bus_id, 0.0)
            switched.add(bus_id)
            events.append(
                PVtoPQSwitchingEvent(
                    outer_iteration=outer,
                    bus_id=bus_id,
                    from_type=PV,
                    to_type=PQ,
                    required_q=float(q_required),
                    qmin_bus=float(qmin),
                    qmax_bus=float(qmax),
                    fixed_q=float(fixed_q),
                    violation_direction=direction,
                    violation_magnitude=float(magnitude),
                )
            )
        vm_start = result.vm.copy()
        va_start = result.va.copy()

    assert last_result is not None
    return ACPowerFlowResult(
        converged=last_result.converged,
        iterations=last_result.iterations,
        vm=last_result.vm,
        va=last_result.va,
        pcalc=last_result.pcalc,
        qcalc=last_result.qcalc,
        qg_by_bus=last_result.qg_by_bus,
        qg_by_generator=last_result.qg_by_generator,
        branch_flows=last_result.branch_flows,
        mismatch_norm=last_result.mismatch_norm,
        final_bus_types=bus_types.copy(),
        switched_buses=tuple(sorted(switched)),
        switching_events=tuple(events),
        outer_iterations=max_switch_iterations,
        q_limit_converged=False,
        termination_reason="maximum_switch_iterations",
        power_flow_converged=last_result.converged,
    )


def solve_ac_power_flow(
    case: PowerCase,
    *,
    enforce_q_limits: bool = False,
    q_limit_mode: str = "one_way",
    **kwargs,
) -> ACPowerFlowResult:
    """Public AC power-flow entry point."""
    if enforce_q_limits:
        return solve_ac_power_flow_with_q_limits(case, mode=q_limit_mode, **kwargs)
    return solve_ac_power_flow_newton(case, **kwargs)
