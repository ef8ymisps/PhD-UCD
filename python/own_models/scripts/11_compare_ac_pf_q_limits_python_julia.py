"""Compare Python and Julia one-way PV-PQ AC PF implementations.

The Julia exporter writes canonical scenario case JSON files and Julia result
JSON files. This script loads the same case JSON in Python, runs the Python
one-way Q-limit AC PF, and writes joined comparison CSVs.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
REPO_ROOT = PYTHON_DIR.parent
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.powermodels_csv_adapter import (  # noqa: E402
    load_powercase_from_powermodels_json,
)
from own_models.src.pf.ac_power_flow import solve_ac_power_flow_with_q_limits  # noqa: E402


JULIA_EXPORT_DIR = REPO_ROOT / "results" / "reference" / "own_julia" / "ac_pf_q_limits"
OUTPUT_DIR = REPO_ROOT / "results" / "comparisons" / "ac_pf_q_limits"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _python_vector_by_bus(case, values: np.ndarray) -> dict[str, float]:
    return {bus.id: float(values[case.bus_id_to_index[bus.id]]) for bus in case.buses}


def _event_to_dict(event) -> dict[str, Any]:
    return {
        "outer_iteration": event.outer_iteration,
        "bus_id": event.bus_id,
        "from_type": event.from_type,
        "to_type": event.to_type,
        "required_q": event.required_q,
        "qmin_bus": event.qmin_bus,
        "qmax_bus": event.qmax_bus,
        "fixed_q": event.fixed_q,
        "violation_direction": event.violation_direction,
        "violation_magnitude": event.violation_magnitude,
    }


def _max_abs_diff(left: dict[str, Any], right: dict[str, Any]) -> float:
    keys = sorted(set(left) | set(right), key=str)
    if not keys:
        return 0.0
    return float(max(abs(float(left.get(key, 0.0)) - float(right.get(key, 0.0))) for key in keys))


def _sorted_list(values) -> list[str]:
    return sorted((str(value) for value in values), key=lambda value: int(value) if value.isdigit() else value)


def _same_dict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return {str(k): v for k, v in left.items()} == {str(k): v for k, v in right.items()}


def _compare_events(py_events: list[dict[str, Any]], jl_events: list[dict[str, Any]]) -> tuple[bool, list[dict[str, Any]]]:
    rows = []
    match = len(py_events) == len(jl_events)
    for idx in range(max(len(py_events), len(jl_events))):
        py = py_events[idx] if idx < len(py_events) else {}
        jl = jl_events[idx] if idx < len(jl_events) else {}
        row = {
            "event_index": idx + 1,
            "python_bus_id": py.get("bus_id"),
            "julia_bus_id": jl.get("bus_id"),
            "python_outer_iteration": py.get("outer_iteration"),
            "julia_outer_iteration": jl.get("outer_iteration"),
            "python_direction": py.get("violation_direction"),
            "julia_direction": jl.get("violation_direction"),
            "python_fixed_q": py.get("fixed_q"),
            "julia_fixed_q": jl.get("fixed_q"),
            "fixed_q_diff": (
                float(py.get("fixed_q", 0.0)) - float(jl.get("fixed_q", 0.0))
                if py and jl
                else np.nan
            ),
            "python_required_q": py.get("required_q"),
            "julia_required_q": jl.get("required_q"),
            "required_q_diff": (
                float(py.get("required_q", 0.0)) - float(jl.get("required_q", 0.0))
                if py and jl
                else np.nan
            ),
        }
        rows.append(row)
        if (
            py.get("bus_id") != jl.get("bus_id")
            or py.get("outer_iteration") != jl.get("outer_iteration")
            or py.get("violation_direction") != jl.get("violation_direction")
        ):
            match = False
    return match, rows


def _compare_one(case_json: Path, julia_result_json: Path) -> dict[str, pd.DataFrame]:
    case = load_powercase_from_powermodels_json(case_json)
    py_result = solve_ac_power_flow_with_q_limits(case)
    jl_result = _load_json(julia_result_json)

    case_name = str(jl_result["case_name"])
    scenario = str(jl_result["scenario"])
    py_vm = _python_vector_by_bus(case, py_result.vm)
    py_va = _python_vector_by_bus(case, py_result.va)
    py_events = [_event_to_dict(event) for event in py_result.switching_events]
    jl_events = list(jl_result.get("switching_events", []))
    event_sequence_match, event_rows = _compare_events(py_events, jl_events)

    py_switched = _sorted_list(py_result.switched_buses)
    jl_switched = _sorted_list(jl_result.get("switched_buses", []))
    final_bus_types_match = _same_dict(py_result.final_bus_types, jl_result.get("final_bus_types", {}))

    summary = pd.DataFrame(
        [
            {
                "case_name": case_name,
                "scenario": scenario,
                "python_converged": py_result.converged,
                "julia_converged": bool(jl_result["converged"]),
                "python_q_limit_converged": py_result.q_limit_converged,
                "julia_q_limit_converged": bool(jl_result["q_limit_converged"]),
                "python_slack_q_violation": py_result.slack_q_violation,
                "julia_slack_q_violation": bool(jl_result["slack_q_violation"]),
                "python_termination_reason": py_result.termination_reason,
                "julia_termination_reason": jl_result["termination_reason"],
                "termination_match": py_result.termination_reason == jl_result["termination_reason"],
                "python_outer_iterations": py_result.outer_iterations,
                "julia_outer_iterations": int(jl_result["outer_iterations"]),
                "python_mismatch_norm": py_result.mismatch_norm,
                "julia_mismatch_norm": float(jl_result["mismatch_norm"]),
                "mismatch_norm_diff": py_result.mismatch_norm - float(jl_result["mismatch_norm"]),
                "max_vm_diff": _max_abs_diff(py_vm, jl_result["vm"]),
                "max_va_diff": _max_abs_diff(py_va, jl_result["va"]),
                "max_qg_bus_diff": _max_abs_diff(py_result.qg_by_bus, jl_result.get("qg_by_bus", {})),
                "max_qg_generator_diff": _max_abs_diff(
                    py_result.qg_by_generator,
                    jl_result.get("qg_by_generator", {}),
                ),
                "python_switched_buses": ";".join(py_switched),
                "julia_switched_buses": ";".join(jl_switched),
                "switched_buses_match": py_switched == jl_switched,
                "final_bus_types_match": final_bus_types_match,
                "switching_sequence_match": event_sequence_match,
            }
        ]
    )

    bus_rows = []
    for bus in case.buses:
        bus_id = bus.id
        bus_rows.append(
            {
                "case_name": case_name,
                "scenario": scenario,
                "bus_id": bus_id,
                "python_vm": py_vm[bus_id],
                "julia_vm": float(jl_result["vm"][bus_id]),
                "vm_diff": py_vm[bus_id] - float(jl_result["vm"][bus_id]),
                "python_va": py_va[bus_id],
                "julia_va": float(jl_result["va"][bus_id]),
                "va_diff": py_va[bus_id] - float(jl_result["va"][bus_id]),
                "python_final_bus_type": py_result.final_bus_types.get(bus_id),
                "julia_final_bus_type": jl_result.get("final_bus_types", {}).get(bus_id),
            }
        )

    gen_rows = []
    for gen in case.generators:
        gen_rows.append(
            {
                "case_name": case_name,
                "scenario": scenario,
                "generator_id": gen.id,
                "bus_id": gen.bus,
                "python_qg": py_result.qg_by_generator.get(gen.id, 0.0),
                "julia_qg": float(jl_result.get("qg_by_generator", {}).get(gen.id, 0.0)),
                "qg_diff": py_result.qg_by_generator.get(gen.id, 0.0)
                - float(jl_result.get("qg_by_generator", {}).get(gen.id, 0.0)),
                "qmin": gen.qmin,
                "qmax": gen.qmax,
            }
        )

    branch_rows = []
    jl_branch_flows = jl_result.get("branch_flows", {})
    for branch_id, py_flow in py_result.branch_flows.items():
        jl_flow = jl_branch_flows.get(branch_id, {})
        branch_rows.append(
            {
                "case_name": case_name,
                "scenario": scenario,
                "branch_id": branch_id,
                "python_pf": py_flow.get("pf", 0.0),
                "julia_pf": float(jl_flow.get("pf", 0.0)),
                "pf_diff": py_flow.get("pf", 0.0) - float(jl_flow.get("pf", 0.0)),
                "python_qf": py_flow.get("qf", 0.0),
                "julia_qf": float(jl_flow.get("qf", 0.0)),
                "qf_diff": py_flow.get("qf", 0.0) - float(jl_flow.get("qf", 0.0)),
                "python_pt": py_flow.get("pt", 0.0),
                "julia_pt": float(jl_flow.get("pt", 0.0)),
                "pt_diff": py_flow.get("pt", 0.0) - float(jl_flow.get("pt", 0.0)),
                "python_qt": py_flow.get("qt", 0.0),
                "julia_qt": float(jl_flow.get("qt", 0.0)),
                "qt_diff": py_flow.get("qt", 0.0) - float(jl_flow.get("qt", 0.0)),
            }
        )

    for row in event_rows:
        row["case_name"] = case_name
        row["scenario"] = scenario

    return {
        "summary": summary,
        "buses": pd.DataFrame(bus_rows),
        "generators": pd.DataFrame(gen_rows),
        "branches": pd.DataFrame(branch_rows),
        "events": pd.DataFrame(event_rows),
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result_paths = sorted(JULIA_EXPORT_DIR.glob("*_julia_result.json"))
    if not result_paths:
        raise SystemExit(
            "No Julia Q-limit exports found. Run from the repo root first:\n"
            "  julia --project=julia julia/own_models/scripts/06_export_ac_pf_q_limit_results.jl"
        )

    frames: dict[str, list[pd.DataFrame]] = {
        "summary": [],
        "buses": [],
        "generators": [],
        "branches": [],
        "events": [],
    }
    for result_path in result_paths:
        case_path = result_path.with_name(result_path.name.replace("_julia_result.json", "_case.json"))
        if not case_path.exists():
            print(f"Skipping {result_path.name}: missing {case_path.name}")
            continue
        result_frames = _compare_one(case_path, result_path)
        for key, frame in result_frames.items():
            if not frame.empty:
                frames[key].append(frame)
        print(result_frames["summary"].to_string(index=False))

    for key, parts in frames.items():
        if parts:
            pd.concat(parts, ignore_index=True).to_csv(OUTPUT_DIR / f"ac_pf_q_limits_{key}.csv", index=False)

    print(f"Saved Python-vs-Julia AC PF Q-limit comparisons to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
