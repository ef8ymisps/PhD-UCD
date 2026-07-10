from __future__ import annotations

from dataclasses import dataclass
import math

from own_models.src.data.case import PowerCase
from own_models.src.pf.dc_power_flow import solve_dc_power_flow


EPS = 1e-9


@dataclass(frozen=True)
class SecurityCheckResult:
    has_active_generation_violation: bool
    has_voltage_violation: bool
    has_reactive_violation: bool
    has_thermal_violation: bool
    has_angle_violation: bool
    max_active_generation_violation: float
    max_voltage_violation: float
    max_reactive_violation: float
    max_thermal_violation: float
    max_angle_violation: float
    normalized_active_generation_severity: float
    normalized_voltage_severity: float
    normalized_reactive_severity: float
    normalized_thermal_severity: float
    normalized_angle_severity: float
    severity_score: float
    slack_generator_id: str
    slack_pg: float
    slack_pg_min: float
    slack_pg_max: float
    actual_pg_by_generator: dict[str, float]
    pg_adjustment_after_pf: dict[str, float]
    max_non_slack_pg_deviation: float


@dataclass(frozen=True)
class DCSecurityCheckResult:
    dc_pf_converged: bool
    dc_secure: bool
    dc_has_active_generation_violation: bool
    dc_has_thermal_violation: bool
    dc_has_angle_violation: bool
    dc_max_active_generation_violation: float
    dc_max_thermal_violation: float
    dc_max_angle_violation: float
    dc_mismatch_norm: float
    dc_slack_pg: float


def designated_slack_generator(case: PowerCase):
    for gen in case.generators:
        if gen.status == 1 and gen.bus == case.slack_bus_id:
            return gen
    raise ValueError(f"No active generator found at slack bus {case.slack_bus_id}.")


def _load_p_by_bus(case: PowerCase) -> dict[str, float]:
    values = {bus.id: 0.0 for bus in case.buses}
    for load in case.loads:
        if load.status == 1:
            values[load.bus] += load.pd
    return values


def _active_shunt_g_by_bus(case: PowerCase) -> dict[str, float]:
    values = {bus.id: 0.0 for bus in case.buses}
    for shunt in case.shunts:
        if shunt.status == 1:
            values[shunt.bus] += shunt.gs
    return values


def _loads_q_by_bus(case: PowerCase) -> dict[str, float]:
    values = {bus.id: 0.0 for bus in case.buses}
    for load in case.loads:
        if load.status == 1:
            values[load.bus] += load.qd
    return values


def _generators_by_bus(case: PowerCase) -> dict[str, list]:
    values: dict[str, list] = {bus.id: [] for bus in case.buses}
    for gen in case.generators:
        if gen.status == 1:
            values[gen.bus].append(gen)
    return values


def compute_slack_pg(case: PowerCase, pf_result, base_pg: dict[str, float], slack_generator_id: str) -> float:
    load_p = _load_p_by_bus(case)
    slack_idx = case.bus_id_to_index[case.slack_bus_id]
    required_bus_pg = float(pf_result.pcalc[slack_idx] + load_p[case.slack_bus_id])
    fixed_pg_at_slack = sum(
        value
        for gen_id, value in base_pg.items()
        if gen_id != slack_generator_id
        for gen in case.generators
        if gen.id == gen_id and gen.status == 1 and gen.bus == case.slack_bus_id
    )
    return required_bus_pg - fixed_pg_at_slack


def actual_pg_by_generator(case: PowerCase, pf_result, scheduled_pg: dict[str, float], slack_generator_id: str) -> dict[str, float]:
    actual = dict(scheduled_pg)
    actual[slack_generator_id] = compute_slack_pg(case, pf_result, scheduled_pg, slack_generator_id)
    return actual


def compute_dc_slack_pg(case: PowerCase, dc_result, slack_generator_id: str) -> float:
    load_p = _load_p_by_bus(case)
    shunt_g = _active_shunt_g_by_bus(case)
    fixed_pg_at_slack = sum(
        gen.pg0
        for gen in case.generators
        if gen.status == 1 and gen.bus == case.slack_bus_id and gen.id != slack_generator_id
    )
    return float(dc_result.slack_injection + load_p[case.slack_bus_id] + shunt_g[case.slack_bus_id] - fixed_pg_at_slack)


def allocate_required_qg_by_generator(case: PowerCase, pf_result) -> dict[str, float]:
    load_q = _loads_q_by_bus(case)
    gens_by_bus = _generators_by_bus(case)
    allocated: dict[str, float] = {}
    for bus_id, generators in gens_by_bus.items():
        if not generators:
            continue
        bus_idx = case.bus_id_to_index[bus_id]
        required_qg = float(pf_result.qcalc[bus_idx] + load_q[bus_id])
        if len(generators) == 1:
            allocated[generators[0].id] = required_qg
            continue

        sum_qmin = sum(gen.qmin for gen in generators)
        ranges = [max(0.0, gen.qmax - gen.qmin) for gen in generators]
        total_range = sum(ranges)
        if total_range <= EPS:
            share = (required_qg - sum_qmin) / len(generators)
            for gen in generators:
                allocated[gen.id] = gen.qmin + share
        else:
            for gen, q_range in zip(generators, ranges, strict=True):
                allocated[gen.id] = gen.qmin + (q_range / total_range) * (required_qg - sum_qmin)
    return allocated


def evaluate_security_checks(
    case: PowerCase,
    pf_result,
    base_pg: dict[str, float],
    *,
    voltage_tol: float = 1e-6,
    limit_tol: float = 1e-6,
) -> SecurityCheckResult:
    slack_gen = designated_slack_generator(case)
    scheduled_pg = {
        gen.id: base_pg.get(gen.id, gen.pg0)
        for gen in case.generators
        if gen.status == 1
    }
    actual_pg = actual_pg_by_generator(case, pf_result, scheduled_pg, slack_gen.id)
    slack_pg = actual_pg[slack_gen.id]
    qg_allocated = allocate_required_qg_by_generator(case, pf_result)

    active_violation = 0.0
    active_severity = 0.0
    for gen in case.generators:
        if gen.status != 1 or gen.id not in actual_pg:
            continue
        violation = max(0.0, gen.pmin - actual_pg[gen.id], actual_pg[gen.id] - gen.pmax)
        active_violation = max(active_violation, violation)
        active_severity = max(active_severity, violation / max(gen.pmax - gen.pmin, EPS))
    pg_adjustment = {
        gen_id: actual_pg[gen_id] - scheduled_pg.get(gen_id, actual_pg[gen_id])
        for gen_id in actual_pg
    }
    max_non_slack_deviation = max(
        (
            abs(adjustment)
            for gen_id, adjustment in pg_adjustment.items()
            if gen_id != slack_gen.id
        ),
        default=0.0,
    )

    voltage_violation = 0.0
    voltage_severity = 0.0
    for idx, bus in enumerate(case.buses):
        vm = float(pf_result.vm[idx])
        violation = max(0.0, bus.vmin - vm, vm - bus.vmax)
        voltage_violation = max(voltage_violation, violation)
        voltage_severity = max(voltage_severity, violation / max(bus.vmax - bus.vmin, EPS))

    reactive_violation = 0.0
    reactive_severity = 0.0
    for gen in case.generators:
        if gen.status != 1 or gen.id not in qg_allocated:
            continue
        qg = qg_allocated[gen.id]
        violation = max(0.0, gen.qmin - qg, qg - gen.qmax)
        reactive_violation = max(reactive_violation, violation)
        reactive_severity = max(reactive_severity, violation / max(gen.qmax - gen.qmin, EPS))

    thermal_violation = 0.0
    thermal_severity = 0.0
    angle_violation = 0.0
    angle_severity = 0.0
    for branch in case.branches:
        if branch.status != 1:
            continue
        if branch.id in pf_result.branch_flows and branch.rate_a > 0.0:
            flow = pf_result.branch_flows[branch.id]
            sf = math.hypot(flow["pf"], flow["qf"])
            st = math.hypot(flow["pt"], flow["qt"])
            violation = max(0.0, sf - branch.rate_a, st - branch.rate_a)
            thermal_violation = max(thermal_violation, violation)
            thermal_severity = max(thermal_severity, violation / max(branch.rate_a, EPS))

        angle_diff = (
            float(pf_result.va[case.bus_id_to_index[branch.from_bus]])
            - float(pf_result.va[case.bus_id_to_index[branch.to_bus]])
        )
        violation = max(0.0, branch.angmin - angle_diff, angle_diff - branch.angmax)
        angle_violation = max(angle_violation, violation)
        angle_range = branch.angmax - branch.angmin
        if math.isfinite(angle_range) and 0.0 < angle_range < 1e6:
            angle_severity = max(angle_severity, violation / max(angle_range, EPS))

    severity_score = max(
        active_severity,
        voltage_severity,
        reactive_severity,
        thermal_severity,
        angle_severity,
    )

    return SecurityCheckResult(
        has_active_generation_violation=active_violation > limit_tol,
        has_voltage_violation=voltage_violation > voltage_tol,
        has_reactive_violation=reactive_violation > limit_tol,
        has_thermal_violation=thermal_violation > limit_tol,
        has_angle_violation=angle_violation > limit_tol,
        max_active_generation_violation=float(active_violation),
        max_voltage_violation=float(voltage_violation),
        max_reactive_violation=float(reactive_violation),
        max_thermal_violation=float(thermal_violation),
        max_angle_violation=float(angle_violation),
        normalized_active_generation_severity=float(active_severity),
        normalized_voltage_severity=float(voltage_severity),
        normalized_reactive_severity=float(reactive_severity),
        normalized_thermal_severity=float(thermal_severity),
        normalized_angle_severity=float(angle_severity),
        severity_score=float(severity_score),
        slack_generator_id=slack_gen.id,
        slack_pg=float(slack_pg),
        slack_pg_min=float(slack_gen.pmin),
        slack_pg_max=float(slack_gen.pmax),
        actual_pg_by_generator={gen_id: float(value) for gen_id, value in actual_pg.items()},
        pg_adjustment_after_pf={gen_id: float(value) for gen_id, value in pg_adjustment.items()},
        max_non_slack_pg_deviation=float(max_non_slack_deviation),
    )


def evaluate_dc_security_checks(
    case: PowerCase,
    *,
    limit_tol: float = 1e-6,
) -> DCSecurityCheckResult:
    slack_gen = designated_slack_generator(case)
    try:
        dc_result = solve_dc_power_flow(case)
    except Exception:
        return DCSecurityCheckResult(
            dc_pf_converged=False,
            dc_secure=False,
            dc_has_active_generation_violation=False,
            dc_has_thermal_violation=False,
            dc_has_angle_violation=False,
            dc_max_active_generation_violation=0.0,
            dc_max_thermal_violation=0.0,
            dc_max_angle_violation=0.0,
            dc_mismatch_norm=math.inf,
            dc_slack_pg=math.nan,
        )

    slack_pg = compute_dc_slack_pg(case, dc_result, slack_gen.id)
    active_violation = max(0.0, slack_gen.pmin - slack_pg, slack_pg - slack_gen.pmax)

    thermal_violation = 0.0
    angle_violation = 0.0
    for branch in case.branches:
        if branch.status != 1:
            continue
        flow = dc_result.branch_flows.get(branch.id, 0.0)
        if branch.rate_a > 0.0:
            thermal_violation = max(thermal_violation, abs(flow) - branch.rate_a)
        angle_diff = (
            float(dc_result.theta[case.bus_id_to_index[branch.from_bus]])
            - float(dc_result.theta[case.bus_id_to_index[branch.to_bus]])
        )
        angle_violation = max(angle_violation, branch.angmin - angle_diff, angle_diff - branch.angmax)

    has_active = active_violation > limit_tol
    has_thermal = thermal_violation > limit_tol
    has_angle = angle_violation > limit_tol
    dc_secure = bool(dc_result.converged and not has_active and not has_thermal and not has_angle)

    return DCSecurityCheckResult(
        dc_pf_converged=bool(dc_result.converged),
        dc_secure=dc_secure,
        dc_has_active_generation_violation=has_active,
        dc_has_thermal_violation=has_thermal,
        dc_has_angle_violation=has_angle,
        dc_max_active_generation_violation=float(active_violation),
        dc_max_thermal_violation=float(max(0.0, thermal_violation)),
        dc_max_angle_violation=float(max(0.0, angle_violation)),
        dc_mismatch_norm=float(dc_result.mismatch_norm),
        dc_slack_pg=float(slack_pg),
    )
