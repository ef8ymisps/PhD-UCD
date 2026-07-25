"""Result extraction helpers for solved AC OPF models."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyomo.environ as pyo

from own_models.src.network.admittance import calc_ac_branch_flows
from own_models.src.opf.ac.constraints import evaluate_ac_opf_constraint_violations


@dataclass(frozen=True)
class ACOPFResult:
    """Container for AC OPF status, dispatch, voltages, flows, and violations."""

    success: bool
    status: str
    message: str
    objective: float
    pg: dict[str, float]
    qg: dict[str, float]
    vm: dict[str, float]
    va: dict[str, float]
    branch_flows: dict[str, dict[str, float]]
    load_curtailment: dict[str, float]
    loadability_factor: float
    max_p_balance_violation: float
    max_q_balance_violation: float
    max_pg_limit_violation: float
    max_qg_limit_violation: float
    max_voltage_violation: float
    max_thermal_violation: float
    max_angle_violation: float
    warm_start_source: str = ""
    raw_result: object | None = None
    solve_iterations: float = float("nan")


def termination_success(termination_condition) -> bool:
    """Map Pyomo termination conditions to this repository's success flag."""
    return str(termination_condition) in {"optimal", "locallyOptimal", "globallyOptimal"}


def extract_ac_opf_result(
    model,
    solve_result,
    *,
    feasibility_tol: float = 1e-5,
    solve_iterations: float = float("nan"),
) -> ACOPFResult:
    """Extract AC OPF values and residual/violation metrics from a Pyomo model."""
    case = model._case
    data = model._ac_data
    termination = solve_result.solver.termination_condition

    pg = {gen.id: float(pyo.value(model.pg[gen.id])) for gen in data.generators}
    qg = {gen.id: float(pyo.value(model.qg[gen.id])) for gen in data.generators}
    vm_array = np.array([float(pyo.value(model.vm[bus.id])) for bus in data.buses], dtype=float)
    va_array = np.array([float(pyo.value(model.va[bus.id])) for bus in data.buses], dtype=float)
    load_curtailment = (
        {load.id: float(pyo.value(model.load_curtailment_fraction[load.id])) for load in data.loads}
        if hasattr(model, "load_curtailment_fraction")
        else {}
    )
    loadability_factor = (
        float(pyo.value(model.loadability_factor))
        if hasattr(model, "loadability_factor")
        else 1.0
    )
    flows = calc_ac_branch_flows(case, vm_array, va_array)
    violations = evaluate_ac_opf_constraint_violations(
        case,
        data,
        pg,
        qg,
        vm_array,
        va_array,
        load_curtailment=load_curtailment,
        loadability_factor=loadability_factor,
    )
    feasible = (
        violations["max_p_balance_violation"] <= feasibility_tol
        and violations["max_q_balance_violation"] <= feasibility_tol
        and violations["max_pg_limit_violation"] <= feasibility_tol
        and violations["max_qg_limit_violation"] <= feasibility_tol
        and violations["max_voltage_violation"] <= feasibility_tol
        and violations["max_thermal_violation"] <= feasibility_tol
        and violations["max_angle_violation"] <= feasibility_tol
    )

    return ACOPFResult(
        success=bool(termination_success(termination) and feasible),
        status=str(termination),
        message=str(solve_result.solver.message),
        objective=float(pyo.value(model.objective)),
        pg=pg,
        qg=qg,
        vm={bus.id: float(vm_array[idx]) for idx, bus in enumerate(data.buses)},
        va={bus.id: float(va_array[idx]) for idx, bus in enumerate(data.buses)},
        branch_flows=flows,
        load_curtailment=load_curtailment,
        loadability_factor=loadability_factor,
        warm_start_source=str(getattr(model, "_warm_start_source", "")),
        raw_result=solve_result,
        solve_iterations=solve_iterations,
        **violations,
    )


def status_only_ac_opf_result(
    model,
    solve_result,
    *,
    message_suffix: str = "",
    solve_iterations: float = float("nan"),
) -> ACOPFResult:
    """Return a diagnostic result when the solver produced no loadable point."""
    termination = solve_result.solver.termination_condition
    message = str(solve_result.solver.message)
    if message_suffix:
        message = f"{message}; {message_suffix}" if message else message_suffix

    return ACOPFResult(
        success=False,
        status=str(termination),
        message=message,
        objective=float("nan"),
        pg={},
        qg={},
        vm={},
        va={},
        branch_flows={},
        load_curtailment={},
        loadability_factor=float("nan"),
        max_p_balance_violation=float("nan"),
        max_q_balance_violation=float("nan"),
        max_pg_limit_violation=float("nan"),
        max_qg_limit_violation=float("nan"),
        max_voltage_violation=float("nan"),
        max_thermal_violation=float("nan"),
        max_angle_violation=float("nan"),
        warm_start_source=str(getattr(model, "_warm_start_source", "")),
        raw_result=solve_result,
        solve_iterations=solve_iterations,
    )
