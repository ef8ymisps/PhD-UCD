"""Prepare Python AC-OPF debugging inventory and formulation audit tables.

This is the Python-side analogue of the Julia validation bookkeeping.  It does
not solve new OPFs.  It takes the comparison CSVs we already produced, selects
the scenarios that deserve deeper debugging, and records where bus shunt terms
enter each Python AC-OPF formulation.
"""

from __future__ import annotations

import csv
import math
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parent
RESULTS_ROOT = REPO_ROOT / "results" / "own_vs_powermodels"
ORGANIZED_ROOT = RESULTS_ROOT / "_organized_for_meeting" / "08_python_ac_opf_debugging"

OBJECTIVE_ABS_TOL = 1e-3
OBJECTIVE_REL_TOL = 1e-7
VARIABLE_TOL = 1e-4
VOLTAGE_TOL = 1e-5


@dataclass(frozen=True)
class SourceSpec:
    label: str
    model_variant: str
    path: Path


SOURCES = [
    SourceSpec(
        "python_current_full_comparison",
        "current",
        REPO_ROOT / "results" / "own_python" / "ac_opf_validation" / "compact_ac_opf_full_comparison.csv",
    ),
    SourceSpec(
        "python_current_export_summary",
        "current",
        REPO_ROOT / "results" / "own_vs_powermodels" / "python" / "summary.csv",
    ),
    SourceSpec(
        "python_pm_like_zero_vs_powermodels",
        "pm_like_zero",
        REPO_ROOT
        / "results"
        / "own_python"
        / "ac_opf_validation"
        / "compact_ac_opf_pm_like_zero_powermodels_comparison.csv",
    ),
    SourceSpec(
        "python_pm_like_zero_sweep_only",
        "pm_like_zero",
        REPO_ROOT / "results" / "own_python" / "ac_opf_validation" / "compact_ac_opf_pm_like_zero_sweeps.csv",
    ),
]

SOURCE_RANK = {source.label: idx for idx, source in enumerate(SOURCES)}
TARGET_VECTOR_SOURCES = {
    "python_current_full_comparison",
    "python_pm_like_zero_vs_powermodels",
}
PRIMARY_DEBUG_REASONS = {
    "status_mismatch",
    "own_cheaper_objective",
    "own_more_expensive_objective",
    "material_relative_objective_difference",
    "power_balance_residual",
    "postsolve_inequality_violation",
    "material_pg_difference",
    "material_qg_difference",
    "material_pf_difference",
    "material_qf_difference",
    "material_vm_difference",
    "material_va_difference",
}


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def first(row: dict[str, str], names: Iterable[str], default: str = "") -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value)
    return default


def number(row: dict[str, str], names: Iterable[str]) -> float:
    value = first(row, names)
    if value == "":
        return math.nan
    try:
        return float(value)
    except ValueError:
        return math.nan


def boolish(value: str) -> bool | None:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "optimal", "locally_solved", "locally solved", "solved"}:
        return True
    if text == "":
        return None
    if text in {"false", "0", "no", "nan", "none"}:
        return False
    return None


def scenario_key(row: dict[str, str]) -> tuple[str, str, str]:
    case = first(row, ["case_name", "case"], "unknown_case")
    scenario = first(row, ["scenario"], "")
    scenario_id = first(row, ["scenario_id"], "")
    if not scenario_id:
        scenario_id = f"{case}__{scenario}"
    return case, scenario, scenario_id


def material_reasons(row: dict[str, str], *, has_pm_reference: bool) -> list[str]:
    reasons: list[str] = []

    own_status = first(row, ["own_status", "status", "termination"])
    pm_status = first(row, ["pm_status"])
    own_success = boolish(first(row, ["own_success", "success", "own_solved", "own_solved_bool"]))
    pm_success = boolish(first(row, ["pm_solved", "pm_solved_bool"]))

    if has_pm_reference and own_status and pm_status:
        own_ok = own_success if own_success is not None else own_status.lower() in {"optimal", "locallyoptimal"}
        pm_ok = pm_success if pm_success is not None else pm_status.upper() in {"LOCALLY_SOLVED", "OPTIMAL"}
        if own_ok != pm_ok:
            reasons.append("status_mismatch")

    objective_diff = number(row, ["objective_diff", "objective_diff_vs_pm"])
    objective_rel_diff = number(row, ["objective_rel_diff", "objective_rel_diff_vs_pm"])
    if has_pm_reference and math.isfinite(objective_diff):
        if objective_diff < -OBJECTIVE_ABS_TOL:
            reasons.append("own_cheaper_objective")
        elif objective_diff > OBJECTIVE_ABS_TOL:
            reasons.append("own_more_expensive_objective")
    if has_pm_reference and math.isfinite(objective_rel_diff) and abs(objective_rel_diff) > OBJECTIVE_REL_TOL:
        reasons.append("material_relative_objective_difference")

    metric_specs = [
        ("max_pg_diff", VARIABLE_TOL, "material_pg_difference"),
        ("max_qg_diff", VARIABLE_TOL, "material_qg_difference"),
        ("max_pf_diff", VARIABLE_TOL, "material_pf_difference"),
        ("max_qf_diff", VARIABLE_TOL, "material_qf_difference"),
        ("max_vm_diff", VOLTAGE_TOL, "material_vm_difference"),
        ("max_va_diff", VOLTAGE_TOL, "material_va_difference"),
    ]
    for column, threshold, reason in metric_specs:
        value = number(row, [column])
        if math.isfinite(value) and value > threshold:
            reasons.append(reason)

    binding_total = number(row, ["binding_mismatches_total"])
    if math.isfinite(binding_total) and binding_total > 0:
        reasons.append("binding_mismatch")
    else:
        mismatch_columns = [
            name
            for name in row
            if (
                name.startswith("n_")
                and ("only_own" in name or "only_pm" in name)
            )
            or name.startswith("binding_mismatch_")
        ]
        total = 0.0
        for name in mismatch_columns:
            value = number(row, [name])
            if math.isfinite(value):
                total += value
        if total > 0:
            reasons.append("binding_mismatch")

    max_p_balance = number(row, ["max_p_balance_violation"])
    max_q_balance = number(row, ["max_q_balance_violation"])
    if (math.isfinite(max_p_balance) and max_p_balance > 1e-5) or (
        math.isfinite(max_q_balance) and max_q_balance > 1e-5
    ):
        reasons.append("power_balance_residual")

    max_thermal = number(row, ["max_thermal_violation"])
    max_voltage = number(row, ["max_voltage_violation"])
    max_angle = number(row, ["max_angle_violation"])
    if any(math.isfinite(value) and value > 1e-5 for value in [max_thermal, max_voltage, max_angle]):
        reasons.append("postsolve_inequality_violation")

    if not reasons and not has_pm_reference:
        success = boolish(first(row, ["success", "own_success"]))
        status = first(row, ["status", "termination"])
        if success is False or status.lower() not in {"", "optimal", "locallyoptimal"}:
            reasons.append("sweep_non_success")

    return sorted(set(reasons))


def priority_for(reasons: list[str], case: str) -> str:
    if any(reason in reasons for reason in ["status_mismatch", "own_cheaper_objective", "power_balance_residual"]):
        return "high"
    if case in {"1354_pegase", "1888_rte", "6470_rte"} and reasons:
        return "high"
    if reasons:
        return "medium"
    return "low"


def build_target_inventory() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    rows_out: list[dict[str, object]] = []
    for source in SOURCES:
        has_pm_reference = "pm_status" in {key for row in read_rows(source.path)[:1] for key in row}
        for row in read_rows(source.path):
            case, scenario, scenario_id = scenario_key(row)
            reasons = material_reasons(row, has_pm_reference=has_pm_reference)
            if not reasons:
                continue
            rows_out.append(
                {
                    "source": source.label,
                    "model_variant": source.model_variant,
                    "case": case,
                    "scenario": scenario,
                    "scenario_id": scenario_id,
                    "priority": priority_for(reasons, case),
                    "target_reasons": ";".join(reasons),
                    "own_status": first(row, ["own_status", "status", "termination"]),
                    "pm_status": first(row, ["pm_status"]),
                    "objective_diff_own_minus_pm": number(row, ["objective_diff", "objective_diff_vs_pm"]),
                    "objective_rel_diff": number(row, ["objective_rel_diff", "objective_rel_diff_vs_pm"]),
                    "own_objective": number(row, ["own_objective", "objective"]),
                    "pm_objective": number(row, ["pm_objective"]),
                    "max_pg_diff": number(row, ["max_pg_diff"]),
                    "max_qg_diff": number(row, ["max_qg_diff"]),
                    "max_vm_diff": number(row, ["max_vm_diff"]),
                    "max_va_diff": number(row, ["max_va_diff"]),
                    "max_pf_diff": number(row, ["max_pf_diff"]),
                    "max_qf_diff": number(row, ["max_qf_diff"]),
                    "max_p_balance_violation": number(row, ["max_p_balance_violation"]),
                    "max_q_balance_violation": number(row, ["max_q_balance_violation"]),
                    "max_thermal_violation": number(row, ["max_thermal_violation"]),
                    "max_voltage_violation": number(row, ["max_voltage_violation"]),
                    "max_angle_violation": number(row, ["max_angle_violation"]),
                    "own_solve_iterations": number(row, ["own_solve_iterations"]),
                    "pm_solve_iterations": number(row, ["pm_solve_iterations"]),
                    "own_solve_time_sec": number(row, ["own_solve_time_sec", "solve_time_sec"]),
                    "pm_solve_time_sec": number(row, ["pm_solve_time_sec"]),
                }
            )

    reason_counts: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    priority_counts: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for row in rows_out:
        key = (str(row["source"]), str(row["model_variant"]), str(row["case"]))
        priority_counts[key][str(row["priority"])] += 1
        for reason in str(row["target_reasons"]).split(";"):
            reason_counts[key][reason] += 1

    summary_rows: list[dict[str, object]] = []
    for key in sorted(set(reason_counts) | set(priority_counts)):
        source, variant, case = key
        reason_counter = reason_counts[key]
        priority_counter = priority_counts[key]
        summary_rows.append(
            {
                "source": source,
                "model_variant": variant,
                "case": case,
                "n_debug_targets": sum(priority_counter.values()),
                "n_high_priority": priority_counter.get("high", 0),
                "n_medium_priority": priority_counter.get("medium", 0),
                "reasons": ";".join(f"{reason}:{count}" for reason, count in sorted(reason_counter.items())),
            }
        )

    return rows_out, summary_rows


def build_run_queue(inventory_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Deduplicate inventory rows into the scenarios worth actually running.

    Binding-only rows are useful for inspection but should not drive expensive
    Python re-solves.  This queue keeps objective/status/residual/large-variable
    rows and merges duplicated current-summary/full-comparison entries.
    """
    merged: dict[tuple[str, str], dict[str, object]] = {}
    reason_sets: dict[tuple[str, str], set[str]] = defaultdict(set)
    source_sets: dict[tuple[str, str], set[str]] = defaultdict(set)

    for row in inventory_rows:
        reasons = set(str(row["target_reasons"]).split(";"))
        if not reasons & PRIMARY_DEBUG_REASONS:
            continue
        key = (str(row["model_variant"]), str(row["scenario_id"]))
        reason_sets[key].update(reasons)
        source_sets[key].add(str(row["source"]))
        current = merged.get(key)
        if current is None or SOURCE_RANK.get(str(row["source"]), 999) < SOURCE_RANK.get(str(current["source"]), 999):
            merged[key] = dict(row)

    queue_rows: list[dict[str, object]] = []
    for key, row in sorted(merged.items(), key=lambda item: (item[1]["model_variant"], item[1]["case"], item[1]["scenario_id"])):
        reasons = sorted(reason_sets[key])
        steps: list[str] = []
        pm_status = str(row.get("pm_status", ""))
        own_status = str(row.get("own_status", ""))
        pm_solved = pm_status.upper() in {"LOCALLY_SOLVED", "OPTIMAL", "SOLVED"} or pm_status == ""
        own_solved = own_status.lower() in {"optimal", "locallyoptimal", "locally_solved", "solved"}
        has_target_vectors = bool(source_sets[key] & TARGET_VECTOR_SOURCES)
        objective_comparison_valid = bool(pm_solved and own_solved and has_target_vectors)
        if any(reason in reasons for reason in ["status_mismatch", "power_balance_residual", "postsolve_inequality_violation"]):
            steps.append("solver_diagnostics")
        if pm_solved and has_target_vectors and any(reason in reasons for reason in ["own_cheaper_objective", "own_more_expensive_objective", "material_relative_objective_difference"]):
            steps.append("constrained_deviation")
            steps.append("equality_only_deviation")
        elif any(reason in reasons for reason in ["own_cheaper_objective", "own_more_expensive_objective", "material_relative_objective_difference"]):
            steps.append("objective_status_inspection")
        if any(reason.startswith("material_") and reason.endswith("_difference") for reason in reasons):
            steps.append("variable_level_inspection")
        if not steps:
            steps.append("inspect")
        if "constrained_deviation" in steps:
            diagnostic_bucket = "solved_reference_deviation_candidate"
        elif "objective_status_inspection" in steps:
            diagnostic_bucket = "status_or_infeasible_objective_inspection"
        elif "solver_diagnostics" in steps:
            diagnostic_bucket = "solver_residual_diagnostics"
        elif "variable_level_inspection" in steps:
            diagnostic_bucket = "variable_level_inspection"
        else:
            diagnostic_bucket = "inspect"

        out = {
            "model_variant": row["model_variant"],
            "case": row["case"],
            "scenario": row["scenario"],
            "scenario_id": row["scenario_id"],
            "priority": row["priority"],
            "diagnostic_bucket": diagnostic_bucket,
            "objective_comparison_valid": objective_comparison_valid,
            "has_pm_target_vectors": has_target_vectors,
            "recommended_steps": ";".join(dict.fromkeys(steps)),
            "target_reasons": ";".join(reasons),
            "sources": ";".join(sorted(source_sets[key])),
            "own_status": row["own_status"],
            "pm_status": row["pm_status"],
            "objective_diff_own_minus_pm": row["objective_diff_own_minus_pm"],
            "objective_rel_diff": row["objective_rel_diff"],
            "max_pg_diff": row["max_pg_diff"],
            "max_qg_diff": row["max_qg_diff"],
            "max_vm_diff": row["max_vm_diff"],
            "max_va_diff": row["max_va_diff"],
            "max_pf_diff": row["max_pf_diff"],
            "max_qf_diff": row["max_qf_diff"],
            "max_p_balance_violation": row["max_p_balance_violation"],
            "max_q_balance_violation": row["max_q_balance_violation"],
            "max_thermal_violation": row["max_thermal_violation"],
            "max_voltage_violation": row["max_voltage_violation"],
            "max_angle_violation": row["max_angle_violation"],
        }
        queue_rows.append(out)
    return queue_rows


def find_line(path: Path, needle: str) -> tuple[int, str]:
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if needle in line:
            return idx, line.strip()
    return 0, ""


def build_shunt_audit() -> list[dict[str, object]]:
    rel_paths = {
        "admittance": Path("python/own_models/src/network/admittance.py"),
        "data": Path("python/own_models/src/opf/ac/data.py"),
        "constraints": Path("python/own_models/src/opf/ac/constraints.py"),
        "pm_like": Path("python/own_models/src/opf/ac/powermodels_like.py"),
    }
    paths = {key: REPO_ROOT / rel for key, rel in rel_paths.items()}

    entries = [
        (
            "current",
            "bus_injection",
            "Ybus diagonal assembly",
            "add(i, i, complex(shunt.gs, shunt.bs))",
            "Explicit bus shunts are inserted into the diagonal of Ybus.",
            paths["admittance"],
        ),
        (
            "current",
            "bus_injection",
            "sparse Ybus row terms",
            "ybus_terms_by_bus=ybus_terms_from_csr",
            "The current formulation consumes sparse Ybus row terms, so diagonal shunts appear inside Gii/Bii.",
            paths["data"],
        ),
        (
            "current",
            "bus_injection",
            "active balance",
            "generation - active_demand(_model, bus_id) - pcalc(bus_id) == 0.0",
            "No separate gs*vm^2 term is written here because pcalc includes Ybus diagonal shunts.",
            paths["constraints"],
        ),
        (
            "current",
            "bus_injection",
            "reactive balance",
            "generation - reactive_demand(_model, bus_id) - qcalc(bus_id) == 0.0",
            "No separate bs*vm^2 term is written here because qcalc includes Ybus diagonal shunts.",
            paths["constraints"],
        ),
        (
            "branch_flow",
            "branch_flow",
            "active balance",
            "data.gs_by_bus[bus_id] * _model.vm[bus_id] ** 2",
            "The branch-flow formulation has no Ybus nodal sum, so it subtracts explicit active bus-shunt demand.",
            paths["constraints"],
        ),
        (
            "branch_flow",
            "branch_flow",
            "reactive balance",
            "data.bs_by_bus[bus_id] * _model.vm[bus_id] ** 2",
            "The branch-flow formulation has no Ybus nodal sum, so it adds the explicit reactive shunt sign convention.",
            paths["constraints"],
        ),
        (
            "pm_like_zero",
            "powermodels_like",
            "active balance",
            "base.gs_by_bus[bus_id] * _model.vm[bus_id] ** 2",
            "The opt-in PM-like Python model mirrors PowerModels-style explicit active bus shunt treatment.",
            paths["pm_like"],
        ),
        (
            "pm_like_zero",
            "powermodels_like",
            "reactive balance",
            "base.bs_by_bus[bus_id] * _model.vm[bus_id] ** 2",
            "The opt-in PM-like Python model mirrors PowerModels-style explicit reactive bus shunt treatment.",
            paths["pm_like"],
        ),
    ]

    rows = []
    for model_variant, formulation, component, needle, interpretation, path in entries:
        line_no, code = find_line(path, needle)
        rows.append(
            {
                "language": "python",
                "model_variant": model_variant,
                "formulation": formulation,
                "component": component,
                "file": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                "line": line_no,
                "code_fragment": code,
                "interpretation": interpretation,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        fieldnames = list(rows[0].keys())
    else:
        fieldnames = ["note"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_note(
    path: Path,
    *,
    inventory_rows: list[dict[str, object]],
    summary_rows: list[dict[str, object]],
    queue_rows: list[dict[str, object]],
    shunt_rows: list[dict[str, object]],
) -> None:
    high_priority = [row for row in inventory_rows if row["priority"] == "high"]
    queue_high_priority = [row for row in queue_rows if row["priority"] == "high"]
    note = [
        "# Python AC OPF Debugging Start Point",
        "",
        "Date: 2026-07-17",
        "",
        "This note starts the Python AC OPF debugging pass using the same discipline as the Julia validation.",
        "",
        "## What Is Already Clear",
        "",
        "- The current Python bus-injection formulation includes explicit bus shunts through the Ybus diagonal.",
        "- The Python branch-flow and PM-like formulations include bus shunts explicitly in the nodal balance as `gs * vm^2` and `bs * vm^2` terms.",
        "- The first Python debugging scope should use scenarios already flagged by objective, variable, residual, status, or binding differences.",
        "",
        "## Generated Tables",
        "",
        "- `results/own_vs_powermodels/python_ac_opf_debug_target_inventory.csv`",
        "- `results/own_vs_powermodels/python_ac_opf_debug_target_summary.csv`",
        "- `results/own_vs_powermodels/python_ac_opf_debug_recommended_run_queue.csv`",
        "- `results/own_vs_powermodels/python_power_balance_bus_shunt_term_locations.csv`",
        "- `results/own_vs_powermodels/python_deviation_minimization_diagnostics.csv`",
        "- `results/own_vs_powermodels/python_deviation_variable_differences.csv`",
        "- `results/own_vs_powermodels/python_deviation_inequality_levels.csv`",
        "- `results/own_vs_powermodels/python_deviation_binding_summary.csv`",
        "- organized copies under `results/own_vs_powermodels/_organized_for_meeting/08_python_ac_opf_debugging`",
        "",
        "## Immediate Run Scope",
        "",
        f"- high-priority target rows selected: {len(high_priority)}",
        f"- de-duplicated recommended run-queue rows: {len(queue_rows)}",
        f"- high-priority recommended run-queue rows: {len(queue_high_priority)}",
        f"- source/case summary rows: {len(summary_rows)}",
        f"- shunt-formulation audit rows: {len(shunt_rows)}",
        "",
        "## Implemented Runner",
        "",
        "- `python/own_models/scripts/32_run_python_ac_opf_deviation_diagnostics.py` runs constrained and equality-only deviation minimization.",
        "- It supports `current` and `pm_like_zero` variants.",
        "- It aligns PowerModels voltage-angle targets by subtracting the PM slack-bus angle before solving, because Python fixes the reference angle to zero.",
        "- It writes summary, variable-difference, inequality-level, and binding-summary tables.",
        "",
        "Pilot result already run:",
        "",
        "- `1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2`, current Python model.",
        "- Constrained/full after angle alignment: max Pg deviation about `4.89e-5` pu and max Vm deviation about `1.27e-7`.",
        "- Equality-only/full after angle alignment: max Pg deviation about `1.26e-13` pu and max Vm deviation about `1.01e-12`.",
        "",
        "Next coding step:",
        "",
        "1. Run the deviation diagnostic on the remaining solved-reference rows in the recommended queue.",
        "2. Add Python solver diagnostics for objective/status-inspection rows.",
        "3. Run PM-like Python deviation diagnostics on its solved-reference row.",
        "4. Add Python shunt-toggle only if a Python-specific inconsistency remains after those checks.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(note) + "\n", encoding="utf-8")


def copy_to_organized(paths: list[Path]) -> None:
    ORGANIZED_ROOT.mkdir(parents=True, exist_ok=True)
    for path in paths:
        shutil.copy2(path, ORGANIZED_ROOT / path.name)


def main() -> None:
    inventory_rows, summary_rows = build_target_inventory()
    queue_rows = build_run_queue(inventory_rows)
    shunt_rows = build_shunt_audit()

    inventory_path = RESULTS_ROOT / "python_ac_opf_debug_target_inventory.csv"
    summary_path = RESULTS_ROOT / "python_ac_opf_debug_target_summary.csv"
    queue_path = RESULTS_ROOT / "python_ac_opf_debug_recommended_run_queue.csv"
    shunt_path = RESULTS_ROOT / "python_power_balance_bus_shunt_term_locations.csv"
    note_path = REPO_ROOT / "docs" / "notes" / "python_ac_opf_debugging_start_point.md"

    write_csv(inventory_path, inventory_rows)
    write_csv(summary_path, summary_rows)
    write_csv(queue_path, queue_rows)
    write_csv(shunt_path, shunt_rows)
    write_note(
        note_path,
        inventory_rows=inventory_rows,
        summary_rows=summary_rows,
        queue_rows=queue_rows,
        shunt_rows=shunt_rows,
    )
    copy_to_organized([inventory_path, summary_path, queue_path, shunt_path])

    print(f"Wrote {inventory_path} ({len(inventory_rows)} rows)")
    print(f"Wrote {summary_path} ({len(summary_rows)} rows)")
    print(f"Wrote {queue_path} ({len(queue_rows)} rows)")
    print(f"Wrote {shunt_path} ({len(shunt_rows)} rows)")
    print(f"Wrote {note_path}")
    print(f"Copied CSVs to {ORGANIZED_ROOT}")


if __name__ == "__main__":
    main()
