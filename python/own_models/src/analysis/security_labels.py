from __future__ import annotations

from dataclasses import dataclass
import math

from own_models.src.analysis.security_checks import SecurityCheckResult
from own_models.src.analysis.topology import IslandDiagnostics


ISLANDING = "ISLANDING"
PF_DID_NOT_CONVERGE = "PF_DID_NOT_CONVERGE"
ACTIVE_GENERATION_LIMIT_VIOLATION = "ACTIVE_GENERATION_LIMIT_VIOLATION"
GENERATION_IMBALANCE = "GENERATION_IMBALANCE"
VOLTAGE_VIOLATION = "VOLTAGE_VIOLATION"
REACTIVE_LIMIT_VIOLATION = "REACTIVE_LIMIT_VIOLATION"
BRANCH_THERMAL_VIOLATION = "BRANCH_THERMAL_VIOLATION"
ANGLE_LIMIT_VIOLATION = "ANGLE_LIMIT_VIOLATION"
SECURE = "SECURE"


@dataclass(frozen=True)
class ContingencyScreeningResult:
    case_name: str
    operating_point_id: str
    contingency_id: str
    k: int
    outaged_branch_ids: tuple[str, ...]
    outaged_generator_ids: tuple[str, ...]
    islanded: bool
    n_islands: int
    n_energized_islands: int
    n_load_only_islands: int
    n_generation_only_islands: int
    n_balanced_islands: int
    islanded_load_p: float
    islanded_generation_p: float
    pf_converged: bool
    secure: bool
    primary_label: str
    violation_labels: tuple[str, ...]
    has_islanding: bool
    has_nonconvergence: bool
    has_generation_imbalance: bool
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
    balancing_policy: str
    lost_generation_p: float
    available_reserve_p: float
    uncovered_power_deficit_p: float
    scheduled_pg_by_generator: dict[str, float]
    actual_pg_by_generator: dict[str, float]
    pg_adjustment_after_pf: dict[str, float]
    max_non_slack_pg_deviation: float
    responding_generator_ids: tuple[str, ...]
    saturated_generator_ids: tuple[str, ...]
    new_slack_generator_id: str
    pf_attempted: bool
    scheduled_replacement_error: float
    post_pf_loss_adjustment_p: float
    dc_evaluated: bool
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
    slack_generator_id: str
    slack_pg: float
    slack_pg_min: float
    slack_pg_max: float
    pf_mismatch_norm: float
    iterations: int


def _primary_label(labels: tuple[str, ...]) -> str:
    for label in (
        ISLANDING,
        GENERATION_IMBALANCE,
        PF_DID_NOT_CONVERGE,
        ACTIVE_GENERATION_LIMIT_VIOLATION,
        VOLTAGE_VIOLATION,
        REACTIVE_LIMIT_VIOLATION,
        BRANCH_THERMAL_VIOLATION,
        ANGLE_LIMIT_VIOLATION,
    ):
        if label in labels:
            return label
    return SECURE


def result_from_islanding(
    *,
    case_name: str,
    operating_point_id: str,
    contingency,
    diagnostics: IslandDiagnostics,
    slack_generator_id: str,
    slack_pg_min: float,
    slack_pg_max: float,
) -> ContingencyScreeningResult:
    labels = (ISLANDING,)
    return ContingencyScreeningResult(
        case_name=case_name,
        operating_point_id=operating_point_id,
        contingency_id=contingency.id,
        k=contingency.k,
        outaged_branch_ids=contingency.outaged_branch_ids,
        outaged_generator_ids=contingency.outaged_generator_ids,
        islanded=True,
        n_islands=diagnostics.n_islands,
        n_energized_islands=diagnostics.n_energized_islands,
        n_load_only_islands=diagnostics.n_load_only_islands,
        n_generation_only_islands=diagnostics.n_generation_only_islands,
        n_balanced_islands=diagnostics.n_balanced_islands,
        islanded_load_p=diagnostics.islanded_load_p,
        islanded_generation_p=diagnostics.islanded_generation_p,
        pf_converged=False,
        secure=False,
        primary_label=ISLANDING,
        violation_labels=labels,
        has_islanding=True,
        has_nonconvergence=False,
        has_generation_imbalance=False,
        has_active_generation_violation=False,
        has_voltage_violation=False,
        has_reactive_violation=False,
        has_thermal_violation=False,
        has_angle_violation=False,
        max_active_generation_violation=0.0,
        max_voltage_violation=0.0,
        max_reactive_violation=0.0,
        max_thermal_violation=0.0,
        max_angle_violation=0.0,
        normalized_active_generation_severity=0.0,
        normalized_voltage_severity=0.0,
        normalized_reactive_severity=0.0,
        normalized_thermal_severity=0.0,
        normalized_angle_severity=0.0,
        severity_score=math.nan,
        balancing_policy="",
        lost_generation_p=0.0,
        available_reserve_p=0.0,
        uncovered_power_deficit_p=0.0,
        scheduled_pg_by_generator={},
        actual_pg_by_generator={},
        pg_adjustment_after_pf={},
        max_non_slack_pg_deviation=0.0,
        responding_generator_ids=(),
        saturated_generator_ids=(),
        new_slack_generator_id=slack_generator_id,
        pf_attempted=False,
        scheduled_replacement_error=0.0,
        post_pf_loss_adjustment_p=math.nan,
        dc_evaluated=False,
        dc_pf_converged=False,
        dc_secure=False,
        dc_has_active_generation_violation=False,
        dc_has_thermal_violation=False,
        dc_has_angle_violation=False,
        dc_max_active_generation_violation=0.0,
        dc_max_thermal_violation=0.0,
        dc_max_angle_violation=0.0,
        dc_mismatch_norm=math.nan,
        dc_slack_pg=math.nan,
        slack_generator_id=slack_generator_id,
        slack_pg=math.nan,
        slack_pg_min=slack_pg_min,
        slack_pg_max=slack_pg_max,
        pf_mismatch_norm=math.nan,
        iterations=0,
    )


def result_from_nonconvergence(
    *,
    case_name: str,
    operating_point_id: str,
    contingency,
    diagnostics: IslandDiagnostics,
    pf_result,
    dc_checks,
    balancing_result=None,
    slack_generator_id: str,
    slack_pg_min: float,
    slack_pg_max: float,
) -> ContingencyScreeningResult:
    labels = (PF_DID_NOT_CONVERGE,)
    return ContingencyScreeningResult(
        case_name=case_name,
        operating_point_id=operating_point_id,
        contingency_id=contingency.id,
        k=contingency.k,
        outaged_branch_ids=contingency.outaged_branch_ids,
        outaged_generator_ids=contingency.outaged_generator_ids,
        islanded=False,
        n_islands=diagnostics.n_islands,
        n_energized_islands=diagnostics.n_energized_islands,
        n_load_only_islands=diagnostics.n_load_only_islands,
        n_generation_only_islands=diagnostics.n_generation_only_islands,
        n_balanced_islands=diagnostics.n_balanced_islands,
        islanded_load_p=diagnostics.islanded_load_p,
        islanded_generation_p=diagnostics.islanded_generation_p,
        pf_converged=False,
        secure=False,
        primary_label=PF_DID_NOT_CONVERGE,
        violation_labels=labels,
        has_islanding=False,
        has_nonconvergence=True,
        has_generation_imbalance=False,
        has_active_generation_violation=False,
        has_voltage_violation=False,
        has_reactive_violation=False,
        has_thermal_violation=False,
        has_angle_violation=False,
        max_active_generation_violation=0.0,
        max_voltage_violation=0.0,
        max_reactive_violation=0.0,
        max_thermal_violation=0.0,
        max_angle_violation=0.0,
        normalized_active_generation_severity=0.0,
        normalized_voltage_severity=0.0,
        normalized_reactive_severity=0.0,
        normalized_thermal_severity=0.0,
        normalized_angle_severity=0.0,
        severity_score=math.nan,
        balancing_policy=balancing_result.policy if balancing_result is not None else "",
        lost_generation_p=balancing_result.lost_generation_p if balancing_result is not None else 0.0,
        available_reserve_p=balancing_result.available_reserve_p if balancing_result is not None else 0.0,
        uncovered_power_deficit_p=balancing_result.uncovered_power_deficit_p if balancing_result is not None else 0.0,
        scheduled_pg_by_generator=balancing_result.scheduled_pg_by_generator if balancing_result is not None else {},
        actual_pg_by_generator={},
        pg_adjustment_after_pf={},
        max_non_slack_pg_deviation=0.0,
        responding_generator_ids=balancing_result.responding_generator_ids if balancing_result is not None else (),
        saturated_generator_ids=balancing_result.saturated_generator_ids if balancing_result is not None else (),
        new_slack_generator_id=balancing_result.new_slack_generator_id if balancing_result is not None else slack_generator_id,
        pf_attempted=balancing_result.pf_attempted if balancing_result is not None else True,
        scheduled_replacement_error=balancing_result.scheduled_replacement_error if balancing_result is not None else 0.0,
        post_pf_loss_adjustment_p=math.nan,
        dc_evaluated=True,
        dc_pf_converged=dc_checks.dc_pf_converged,
        dc_secure=dc_checks.dc_secure,
        dc_has_active_generation_violation=dc_checks.dc_has_active_generation_violation,
        dc_has_thermal_violation=dc_checks.dc_has_thermal_violation,
        dc_has_angle_violation=dc_checks.dc_has_angle_violation,
        dc_max_active_generation_violation=dc_checks.dc_max_active_generation_violation,
        dc_max_thermal_violation=dc_checks.dc_max_thermal_violation,
        dc_max_angle_violation=dc_checks.dc_max_angle_violation,
        dc_mismatch_norm=dc_checks.dc_mismatch_norm,
        dc_slack_pg=dc_checks.dc_slack_pg,
        slack_generator_id=slack_generator_id,
        slack_pg=math.nan,
        slack_pg_min=slack_pg_min,
        slack_pg_max=slack_pg_max,
        pf_mismatch_norm=float(pf_result.mismatch_norm),
        iterations=int(pf_result.iterations),
    )


def result_from_generation_imbalance(
    *,
    case_name: str,
    operating_point_id: str,
    contingency,
    diagnostics: IslandDiagnostics,
    balancing_result,
    slack_pg_min: float,
    slack_pg_max: float,
) -> ContingencyScreeningResult:
    labels = (GENERATION_IMBALANCE,)
    return ContingencyScreeningResult(
        case_name=case_name,
        operating_point_id=operating_point_id,
        contingency_id=contingency.id,
        k=contingency.k,
        outaged_branch_ids=contingency.outaged_branch_ids,
        outaged_generator_ids=contingency.outaged_generator_ids,
        islanded=False,
        n_islands=diagnostics.n_islands,
        n_energized_islands=diagnostics.n_energized_islands,
        n_load_only_islands=diagnostics.n_load_only_islands,
        n_generation_only_islands=diagnostics.n_generation_only_islands,
        n_balanced_islands=diagnostics.n_balanced_islands,
        islanded_load_p=diagnostics.islanded_load_p,
        islanded_generation_p=diagnostics.islanded_generation_p,
        pf_converged=False,
        secure=False,
        primary_label=GENERATION_IMBALANCE,
        violation_labels=labels,
        has_islanding=False,
        has_nonconvergence=False,
        has_generation_imbalance=True,
        has_active_generation_violation=False,
        has_voltage_violation=False,
        has_reactive_violation=False,
        has_thermal_violation=False,
        has_angle_violation=False,
        max_active_generation_violation=0.0,
        max_voltage_violation=0.0,
        max_reactive_violation=0.0,
        max_thermal_violation=0.0,
        max_angle_violation=0.0,
        normalized_active_generation_severity=0.0,
        normalized_voltage_severity=0.0,
        normalized_reactive_severity=0.0,
        normalized_thermal_severity=0.0,
        normalized_angle_severity=0.0,
        severity_score=math.nan,
        balancing_policy=balancing_result.policy,
        lost_generation_p=balancing_result.lost_generation_p,
        available_reserve_p=balancing_result.available_reserve_p,
        uncovered_power_deficit_p=balancing_result.uncovered_power_deficit_p,
        scheduled_pg_by_generator=balancing_result.scheduled_pg_by_generator,
        actual_pg_by_generator={},
        pg_adjustment_after_pf={},
        max_non_slack_pg_deviation=0.0,
        responding_generator_ids=balancing_result.responding_generator_ids,
        saturated_generator_ids=balancing_result.saturated_generator_ids,
        new_slack_generator_id=balancing_result.new_slack_generator_id,
        pf_attempted=False,
        scheduled_replacement_error=balancing_result.scheduled_replacement_error,
        post_pf_loss_adjustment_p=math.nan,
        dc_evaluated=False,
        dc_pf_converged=False,
        dc_secure=False,
        dc_has_active_generation_violation=False,
        dc_has_thermal_violation=False,
        dc_has_angle_violation=False,
        dc_max_active_generation_violation=0.0,
        dc_max_thermal_violation=0.0,
        dc_max_angle_violation=0.0,
        dc_mismatch_norm=math.nan,
        dc_slack_pg=math.nan,
        slack_generator_id=balancing_result.new_slack_generator_id,
        slack_pg=math.nan,
        slack_pg_min=slack_pg_min,
        slack_pg_max=slack_pg_max,
        pf_mismatch_norm=math.nan,
        iterations=0,
    )


def result_from_checks(
    *,
    case_name: str,
    operating_point_id: str,
    contingency,
    diagnostics: IslandDiagnostics,
    pf_result,
    checks: SecurityCheckResult,
    dc_checks,
    balancing_result=None,
) -> ContingencyScreeningResult:
    labels = []
    if checks.has_active_generation_violation:
        labels.append(ACTIVE_GENERATION_LIMIT_VIOLATION)
    if checks.has_voltage_violation:
        labels.append(VOLTAGE_VIOLATION)
    if checks.has_reactive_violation:
        labels.append(REACTIVE_LIMIT_VIOLATION)
    if checks.has_thermal_violation:
        labels.append(BRANCH_THERMAL_VIOLATION)
    if checks.has_angle_violation:
        labels.append(ANGLE_LIMIT_VIOLATION)
    labels_tuple = tuple(labels)
    secure = not labels_tuple
    return ContingencyScreeningResult(
        case_name=case_name,
        operating_point_id=operating_point_id,
        contingency_id=contingency.id,
        k=contingency.k,
        outaged_branch_ids=contingency.outaged_branch_ids,
        outaged_generator_ids=contingency.outaged_generator_ids,
        islanded=False,
        n_islands=diagnostics.n_islands,
        n_energized_islands=diagnostics.n_energized_islands,
        n_load_only_islands=diagnostics.n_load_only_islands,
        n_generation_only_islands=diagnostics.n_generation_only_islands,
        n_balanced_islands=diagnostics.n_balanced_islands,
        islanded_load_p=diagnostics.islanded_load_p,
        islanded_generation_p=diagnostics.islanded_generation_p,
        pf_converged=True,
        secure=secure,
        primary_label=_primary_label(labels_tuple),
        violation_labels=labels_tuple,
        has_islanding=False,
        has_nonconvergence=False,
        has_generation_imbalance=False,
        has_active_generation_violation=checks.has_active_generation_violation,
        has_voltage_violation=checks.has_voltage_violation,
        has_reactive_violation=checks.has_reactive_violation,
        has_thermal_violation=checks.has_thermal_violation,
        has_angle_violation=checks.has_angle_violation,
        max_active_generation_violation=checks.max_active_generation_violation,
        max_voltage_violation=checks.max_voltage_violation,
        max_reactive_violation=checks.max_reactive_violation,
        max_thermal_violation=checks.max_thermal_violation,
        max_angle_violation=checks.max_angle_violation,
        normalized_active_generation_severity=checks.normalized_active_generation_severity,
        normalized_voltage_severity=checks.normalized_voltage_severity,
        normalized_reactive_severity=checks.normalized_reactive_severity,
        normalized_thermal_severity=checks.normalized_thermal_severity,
        normalized_angle_severity=checks.normalized_angle_severity,
        severity_score=checks.severity_score,
        balancing_policy=balancing_result.policy if balancing_result is not None else "",
        lost_generation_p=balancing_result.lost_generation_p if balancing_result is not None else 0.0,
        available_reserve_p=balancing_result.available_reserve_p if balancing_result is not None else 0.0,
        uncovered_power_deficit_p=balancing_result.uncovered_power_deficit_p if balancing_result is not None else 0.0,
        scheduled_pg_by_generator=balancing_result.scheduled_pg_by_generator if balancing_result is not None else {},
        actual_pg_by_generator=checks.actual_pg_by_generator,
        pg_adjustment_after_pf=checks.pg_adjustment_after_pf,
        max_non_slack_pg_deviation=checks.max_non_slack_pg_deviation,
        responding_generator_ids=balancing_result.responding_generator_ids if balancing_result is not None else (),
        saturated_generator_ids=balancing_result.saturated_generator_ids if balancing_result is not None else (),
        new_slack_generator_id=balancing_result.new_slack_generator_id if balancing_result is not None else checks.slack_generator_id,
        pf_attempted=balancing_result.pf_attempted if balancing_result is not None else True,
        scheduled_replacement_error=balancing_result.scheduled_replacement_error if balancing_result is not None else 0.0,
        post_pf_loss_adjustment_p=sum(checks.actual_pg_by_generator.values())
        - sum((balancing_result.scheduled_pg_by_generator if balancing_result is not None else {}).values())
        if balancing_result is not None
        else 0.0,
        dc_evaluated=True,
        dc_pf_converged=dc_checks.dc_pf_converged,
        dc_secure=dc_checks.dc_secure,
        dc_has_active_generation_violation=dc_checks.dc_has_active_generation_violation,
        dc_has_thermal_violation=dc_checks.dc_has_thermal_violation,
        dc_has_angle_violation=dc_checks.dc_has_angle_violation,
        dc_max_active_generation_violation=dc_checks.dc_max_active_generation_violation,
        dc_max_thermal_violation=dc_checks.dc_max_thermal_violation,
        dc_max_angle_violation=dc_checks.dc_max_angle_violation,
        dc_mismatch_norm=dc_checks.dc_mismatch_norm,
        dc_slack_pg=dc_checks.dc_slack_pg,
        slack_generator_id=checks.slack_generator_id,
        slack_pg=checks.slack_pg,
        slack_pg_min=checks.slack_pg_min,
        slack_pg_max=checks.slack_pg_max,
        pf_mismatch_norm=float(pf_result.mismatch_norm),
        iterations=int(pf_result.iterations),
    )
