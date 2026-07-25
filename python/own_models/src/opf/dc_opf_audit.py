from __future__ import annotations

from dataclasses import replace
import math
from typing import Any

from own_models.src.data.case import Branch, Bus, Generator, Load, PowerCase, Shunt
from own_models.src.network.dc_matrices import dc_branch_conductance, dc_branch_flow_coefficient


LOSSLESS_MODEL_LABEL = "lossless DC OPF with resistance-adjusted branch-flow coefficients"
CASTILLO_MODEL_LABEL = (
    "Castillo-style lossy DC OPF with quadratic angle-difference losses "
    "and equal loss allocation"
)
MODEL_LABEL = LOSSLESS_MODEL_LABEL
NON_FEATURES = ("AC OPF", "transformer tap-aware DC OPF", "phase-shifter-aware DC OPF")


def _validate_coefficient_mode(coefficient_mode: str) -> None:
    if coefficient_mode != "resistance_adjusted":
        raise ValueError("Only coefficient_mode='resistance_adjusted' is currently supported.")


def _validate_loss_model(loss_model: str) -> None:
    if loss_model not in {"lossless", "castillo_quadratic"}:
        raise ValueError(f"Unsupported DC OPF loss_model: {loss_model!r}")


def _model_label(loss_model: str) -> str:
    _validate_loss_model(loss_model)
    return LOSSLESS_MODEL_LABEL if loss_model == "lossless" else CASTILLO_MODEL_LABEL


def _non_features(loss_model: str) -> list[str]:
    features = list(NON_FEATURES)
    if loss_model == "lossless":
        features.insert(0, "lossy DC OPF")
    return features


def _active_generators(case: PowerCase):
    return [gen for gen in case.generators if gen.status == 1]


def _active_branches(case: PowerCase):
    return [branch for branch in case.branches if branch.status == 1]


def _demand_by_bus(case: PowerCase, *, include_shunt_conductance: bool) -> dict[str, float]:
    demand = {bus.id: 0.0 for bus in case.buses}
    for load in case.loads:
        if load.status == 1:
            demand[load.bus] += load.pd
    if include_shunt_conductance:
        for shunt in case.shunts:
            if shunt.status == 1:
                demand[shunt.bus] += shunt.gs
    return demand


def _shunt_conductance_by_bus(case: PowerCase) -> dict[str, float]:
    shunts = {bus.id: 0.0 for bus in case.buses}
    for shunt in case.shunts:
        if shunt.status == 1:
            shunts[shunt.bus] += shunt.gs
    return shunts


def _gens_at_bus(case: PowerCase) -> dict[str, list[str]]:
    active_generators = _active_generators(case)
    return {
        bus.id: [gen.id for gen in active_generators if gen.bus == bus.id]
        for bus in case.buses
    }


def _format_number(value: float) -> str:
    return f"{value:.12g}"


def _branch_beta(branch: Branch) -> float | None:
    try:
        return dc_branch_flow_coefficient(branch)
    except ValueError:
        return None


def describe_dc_opf_model(
    case: PowerCase,
    *,
    coefficient_mode: str = "resistance_adjusted",
    loss_model: str = "lossless",
    include_shunt_conductance: bool = True,
    materiality_tol: float = 1e-6,
) -> dict[str, Any]:
    """Return structured ownership diagnostics for the current DC OPF model.

    The implemented model is lossless DC OPF with resistance-adjusted
    branch-flow coefficients. It is not lossy DC OPF, AC OPF, tap-aware DC OPF,
    or phase-shifter-aware DC OPF.
    """
    _validate_coefficient_mode(coefficient_mode)
    _validate_loss_model(loss_model)
    active_generators = _active_generators(case)
    active_branches = _active_branches(case)
    demand = _demand_by_bus(case, include_shunt_conductance=include_shunt_conductance)
    shunts = _shunt_conductance_by_bus(case)
    gens_at_bus = _gens_at_bus(case)

    total_demand_pu = sum(demand.values())
    total_shunt_pu = sum(shunts.values()) if include_shunt_conductance else 0.0
    total_pmin_pu = sum(gen.pmin for gen in active_generators if math.isfinite(gen.pmin))
    total_pmax_pu = sum(gen.pmax for gen in active_generators if math.isfinite(gen.pmax))

    branches_without_limits = [
        branch.id
        for branch in case.branches
        if branch.status == 1 and branch.rate_a <= 0.0
    ]
    suspicious_rx = []
    material_beta_changes = []
    beta_values = {}
    conductance_values = {}

    for branch in active_branches:
        values = [branch.r, branch.x]
        if any(not math.isfinite(value) for value in values):
            suspicious_rx.append(
                {
                    "branch_id": branch.id,
                    "r": branch.r,
                    "x": branch.x,
                    "reason": "non-finite r or x",
                }
            )
            continue
        if branch.r**2 + branch.x**2 < 1e-18 or abs(branch.x) < 1e-12:
            suspicious_rx.append(
                {
                    "branch_id": branch.id,
                    "r": branch.r,
                    "x": branch.x,
                    "reason": "near-zero impedance or reactance",
                }
            )
            continue

        beta = dc_branch_flow_coefficient(branch)
        conductance_values[branch.id] = dc_branch_conductance(branch)
        classical_beta = 1.0 / branch.x
        relative_change = abs(beta - classical_beta) / max(abs(classical_beta), 1e-12)
        beta_values[branch.id] = beta
        if relative_change > materiality_tol:
            material_beta_changes.append(
                {
                    "branch_id": branch.id,
                    "r": branch.r,
                    "x": branch.x,
                    "resistance_adjusted_beta": beta,
                    "classical_beta": classical_beta,
                    "relative_change": relative_change,
                }
            )

    buses_with_load_no_generator = [
        bus.id
        for bus in case.buses
        if abs(demand[bus.id]) > 1e-12 and not gens_at_bus[bus.id]
    ]

    return {
        "model": _model_label(loss_model),
        "explicitly_not": _non_features(loss_model),
        "num_buses": len(case.buses),
        "num_generators": len(case.generators),
        "num_active_generators": len(active_generators),
        "num_branches": len(case.branches),
        "num_active_branches": len(active_branches),
        "slack_bus_id": case.slack_bus_id,
        "base_mva": case.base_mva,
        "coefficient_mode": coefficient_mode,
        "loss_model": loss_model,
        "include_shunt_conductance": include_shunt_conductance,
        "total_active_demand_pu": total_demand_pu,
        "total_active_demand_mw": total_demand_pu * case.base_mva,
        "total_active_shunt_conductance_included_pu": total_shunt_pu,
        "total_active_shunt_conductance_included_mw": total_shunt_pu * case.base_mva,
        "total_pg_min_pu": total_pmin_pu,
        "total_pg_min_mw": total_pmin_pu * case.base_mva,
        "total_pg_max_pu": total_pmax_pu,
        "total_pg_max_mw": total_pmax_pu * case.base_mva,
        "buses_with_no_generator_but_nonzero_demand": buses_with_load_no_generator,
        "branches_with_rate_a_le_zero": branches_without_limits,
        "branches_with_non_finite_or_suspicious_rx": suspicious_rx,
        "branches_where_resistance_materially_changes_beta": material_beta_changes,
        "active_branch_beta": beta_values,
        "active_branch_conductance": conductance_values,
    }


def audit_dc_opf_equations(
    case: PowerCase,
    *,
    coefficient_mode: str = "resistance_adjusted",
    loss_model: str = "lossless",
    include_shunt_conductance: bool = True,
) -> dict[str, Any]:
    """Return human-readable equations for the current DC OPF model option."""
    _validate_coefficient_mode(coefficient_mode)
    _validate_loss_model(loss_model)
    active_generators = _active_generators(case)
    active_branches = _active_branches(case)
    demand = _demand_by_bus(case, include_shunt_conductance=include_shunt_conductance)
    gens_at_bus = _gens_at_bus(case)
    beta = {
        branch.id: dc_branch_flow_coefficient(branch)
        for branch in active_branches
    }
    conductance = {
        branch.id: dc_branch_conductance(branch)
        for branch in active_branches
    }

    generator_variables = [
        f"Pg[{gen.id}] in [{_format_number(gen.pmin)}, {_format_number(gen.pmax)}] pu"
        for gen in active_generators
    ]
    theta_variables = [f"theta[{bus.id}] in radians" for bus in case.buses]
    objective_terms = [
        (
            f"{gen.cost_c2}*(Pg[{gen.id}]*{case.base_mva})^2 + "
            f"{gen.cost_c1}*(Pg[{gen.id}]*{case.base_mva}) + {gen.cost_c0}"
        )
        for gen in active_generators
    ]

    branch_flow_expressions = []
    branch_flow_limits = []
    angle_difference_limits = []
    for branch in active_branches:
        if loss_model == "lossless":
            branch_flow_expressions.extend(
                [
                    (
                        f"DeltaTheta[{branch.id}] = theta[{branch.from_bus}] - "
                        f"theta[{branch.to_bus}]"
                    ),
                    f"P_loss[{branch.id}] = 0",
                    f"P_from[{branch.id}] = {_format_number(beta[branch.id])} * DeltaTheta[{branch.id}]",
                    f"P_to[{branch.id}] = -P_from[{branch.id}]",
                ]
            )
        else:
            branch_flow_expressions.extend(
                [
                    (
                        f"DeltaTheta[{branch.id}] = theta[{branch.from_bus}] - "
                        f"theta[{branch.to_bus}]"
                    ),
                    (
                        f"P_loss[{branch.id}] = {_format_number(conductance[branch.id])} * "
                        f"DeltaTheta[{branch.id}]^2"
                    ),
                    (
                        f"P_from[{branch.id}] = {_format_number(beta[branch.id])} * "
                        f"DeltaTheta[{branch.id}] + 0.5 * P_loss[{branch.id}]"
                    ),
                    (
                        f"P_to[{branch.id}] = -{_format_number(beta[branch.id])} * "
                        f"DeltaTheta[{branch.id}] + 0.5 * P_loss[{branch.id}]"
                    ),
                ]
            )
        if branch.rate_a > 0.0:
            branch_flow_limits.append(
                (
                    f"-{_format_number(branch.rate_a)} <= P_from[{branch.id}], "
                    f"P_to[{branch.id}] <= {_format_number(branch.rate_a)}"
                )
            )
        else:
            branch_flow_limits.append(f"P_from/P_to[{branch.id}] have no rate_a limit")
        angle_difference_limits.append(
            (
                f"{_format_number(branch.angmin)} <= theta[{branch.from_bus}] - "
                f"theta[{branch.to_bus}] <= {_format_number(branch.angmax)}"
            )
        )

    nodal_balance = {}
    for bus in case.buses:
        generation = " + ".join(f"Pg[{gen_id}]" for gen_id in gens_at_bus[bus.id]) or "0"
        from_terms = [
            f"P_from[{branch.id}]"
            for branch in active_branches
            if branch.from_bus == bus.id
        ]
        to_terms = [
            f"P_to[{branch.id}]"
            for branch in active_branches
            if branch.to_bus == bus.id
        ]
        network_terms = from_terms + to_terms
        network = " + ".join(network_terms) if network_terms else "0"
        nodal_balance[bus.id] = (
            f"{generation} - {_format_number(demand[bus.id])} == {network}"
        )

    sections = {
        "generator_variables": generator_variables,
        "theta_variables": theta_variables,
        "objective": "min " + " + ".join(objective_terms),
        "nodal_balance": nodal_balance,
        "branch_flow_expressions": branch_flow_expressions,
        "branch_flow_limits": branch_flow_limits,
        "angle_difference_limits": angle_difference_limits,
        "reference_angle": f"theta[{case.slack_bus_id}] == 0",
    }

    lines = [
        f"Model: {_model_label(loss_model)}",
        f"Not implemented here: {', '.join(_non_features(loss_model))}",
        "Generator variables:",
        *[f"  {line}" for line in generator_variables],
        "Theta variables:",
        *[f"  {line}" for line in theta_variables],
        "Objective:",
        f"  {sections['objective']}",
        "Nodal balance:",
        *[f"  Bus {bus_id}: {equation}" for bus_id, equation in nodal_balance.items()],
        "Branch flow expressions:",
        *[f"  {line}" for line in branch_flow_expressions],
        "Branch flow limits:",
        *[f"  {line}" for line in branch_flow_limits],
        "Angle difference limits:",
        *[f"  {line}" for line in angle_difference_limits],
        "Reference angle:",
        f"  {sections['reference_angle']}",
    ]

    return {
        "model": _model_label(loss_model),
        "explicitly_not": _non_features(loss_model),
        "loss_model": loss_model,
        "sections": sections,
        "lines": lines,
        "text": "\n".join(lines),
    }


def compare_scipy_pyomo_dc_opf(
    scipy_result,
    pyomo_result,
    *,
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Compare SciPy and Pyomo DC OPF result objects field by field."""
    mismatches = []

    def compare_mapping(name: str, left: dict[str, float], right: dict[str, float]) -> float:
        max_diff = 0.0
        all_keys = sorted(set(left) | set(right), key=str)
        for key in all_keys:
            if key not in left or key not in right:
                mismatches.append(
                    {
                        "field": name,
                        "id": key,
                        "scipy_value": left.get(key),
                        "pyomo_value": right.get(key),
                        "difference": math.inf,
                    }
                )
                max_diff = math.inf
                continue
            difference = abs(left[key] - right[key])
            max_diff = max(max_diff, difference)
            if difference > tolerance:
                mismatches.append(
                    {
                        "field": name,
                        "id": key,
                        "scipy_value": left[key],
                        "pyomo_value": right[key],
                        "difference": difference,
                    }
                )
        return max_diff

    objective_difference = abs(scipy_result.objective - pyomo_result.objective)
    balance_difference = abs(
        scipy_result.max_power_balance_violation - pyomo_result.max_power_balance_violation
    )
    if objective_difference > tolerance:
        mismatches.append(
            {
                "field": "objective",
                "scipy_value": scipy_result.objective,
                "pyomo_value": pyomo_result.objective,
                "difference": objective_difference,
            }
        )
    if balance_difference > tolerance:
        mismatches.append(
            {
                "field": "max_power_balance_violation",
                "scipy_value": scipy_result.max_power_balance_violation,
                "pyomo_value": pyomo_result.max_power_balance_violation,
                "difference": balance_difference,
            }
        )

    max_pg_difference = compare_mapping("pg", scipy_result.pg, pyomo_result.pg)
    max_theta_difference = compare_mapping("theta", scipy_result.theta, pyomo_result.theta)
    max_branch_flow_difference = compare_mapping(
        "branch_flows",
        scipy_result.branch_flows,
        pyomo_result.branch_flows,
    )

    return {
        "passed": not mismatches,
        "tolerance": tolerance,
        "objective_difference": objective_difference,
        "max_pg_difference": max_pg_difference,
        "max_theta_difference": max_theta_difference,
        "max_branch_flow_difference": max_branch_flow_difference,
        "max_balance_residual_difference": balance_difference,
        "scipy_max_power_balance_violation": scipy_result.max_power_balance_violation,
        "pyomo_max_power_balance_violation": pyomo_result.max_power_balance_violation,
        "mismatches": mismatches,
    }


def make_two_bus_dc_case() -> PowerCase:
    """Build a hand-solvable two-bus DC OPF case.

    Expected baseline solution: Pg[G1]=1.0 pu, theta[1]=0.0,
    theta[2]=-0.1 rad, Pf[L1]=1.0 pu.
    """
    buses = [
        Bus("1", 1, 3, 1.0, 0.0, 0.9, 1.1, 230.0),
        Bus("2", 2, 1, 1.0, 0.0, 0.9, 1.1, 230.0),
    ]
    branches = [
        Branch(
            "L1",
            1,
            "1",
            "2",
            0.0,
            0.1,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            10.0,
            1.0,
            0.0,
            -math.pi,
            math.pi,
            1,
        )
    ]
    generators = [
        Generator(
            "G1",
            1,
            "1",
            1.0,
            0.0,
            0.0,
            2.0,
            -1.0,
            1.0,
            1,
            cost_c2=0.0,
            cost_c1=20.0,
            cost_c0=3.0,
        )
    ]
    loads = [Load("D1", 1, "2", 1.0, 0.0, 1)]
    return PowerCase(
        "two_bus_dc_audit",
        100.0,
        buses,
        branches,
        generators,
        loads,
        [],
        "1",
        {"1": 0, "2": 1},
    )


def make_two_bus_lossy_dc_case() -> PowerCase:
    """Build a hand-solvable two-bus Castillo quadratic-loss DC OPF case.

    With r=0.1, x=0.2, load=1.0 pu, beta=4, and conductance=2, the small-angle
    solution is delta=2-sqrt(3), theta[2]=-delta, Pg=P_from, and loss=2*delta^2.
    """
    base = make_two_bus_dc_case()
    return replace(
        base,
        name="two_bus_castillo_dc_audit",
        branches=[
            replace(
                base.branches[0],
                r=0.1,
                x=0.2,
                rate_a=10.0,
            )
        ],
    )


def make_three_bus_dc_case() -> PowerCase:
    """Build a deterministic three-bus DC OPF case with two generators."""
    base = make_two_bus_dc_case()
    buses = [
        base.buses[0],
        replace(base.buses[1], index=2),
        Bus("3", 3, 1, 1.0, 0.0, 0.9, 1.1, 230.0),
    ]
    branches = [
        replace(base.branches[0], rate_a=10.0),
        Branch(
            "L2",
            2,
            "2",
            "3",
            0.02,
            0.2,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            10.0,
            1.0,
            0.0,
            -math.pi,
            math.pi,
            1,
        ),
        Branch(
            "L3",
            3,
            "1",
            "3",
            0.01,
            0.25,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            10.0,
            1.0,
            0.0,
            -math.pi,
            math.pi,
            1,
        ),
    ]
    generators = [
        replace(base.generators[0], pmax=2.0, cost_c1=25.0),
        Generator(
            "G2",
            2,
            "3",
            0.5,
            0.0,
            0.0,
            2.0,
            -1.0,
            1.0,
            1,
            cost_c2=0.0,
            cost_c1=15.0,
            cost_c0=0.0,
        ),
    ]
    loads = [
        Load("D1", 1, "2", 0.8, 0.0, 1),
        Load("D2", 2, "3", 0.4, 0.0, 1),
    ]
    shunts = [Shunt("S1", 1, "2", 0.05, 0.0, 1)]
    return PowerCase(
        "three_bus_dc_audit",
        100.0,
        buses,
        branches,
        generators,
        loads,
        shunts,
        "1",
        {"1": 0, "2": 1, "3": 2},
    )
