"""
Diagnose Julia own-model vs PowerModels AC OPF discrepancies.

This script reads the existing detail tables under
results/own_vs_powermodels/julia and produces a scenario-level diagnostic table.
It does not recompute OPF. It only inspects already-exported generator, branch,
and binding-constraint comparison CSVs.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
JULIA_RESULTS = ROOT / "results" / "own_vs_powermodels" / "julia"
SUMMARY_PATH = JULIA_RESULTS / "summary.csv"
OUTPUT_PATH = ROOT / "results" / "own_vs_powermodels" / "julia_discrepancy_diagnosis.csv"
TOP_OUTPUT_PATH = ROOT / "results" / "own_vs_powermodels" / "julia_discrepancy_diagnosis_top_cases.csv"

BINDING_REAL_TOL = 1e-4
BINDING_LARGE_TOL = 1e-2
BINDING_HUGE_TOL = 1.0
MATERIAL_DIFF_TOL = 1e-5


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def as_float(value: object, default: float = math.nan) -> float:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def as_bool(value: object) -> bool:
    return str(value).strip().lower() == "true"


def max_row(rows: list[dict[str, str]], column: str) -> dict[str, str] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: as_float(row.get(column), -math.inf))


def bus_degree(branch_rows: list[dict[str, str]], bus_id: str) -> int:
    if not bus_id:
        return 0
    return sum(
        1
        for row in branch_rows
        if str(row.get("from_bus", "")).strip() == bus_id
        or str(row.get("to_bus", "")).strip() == bus_id
    )


def incident_branches(branch_rows: list[dict[str, str]], bus_id: str) -> list[dict[str, str]]:
    if not bus_id:
        return []
    return [
        row
        for row in branch_rows
        if str(row.get("from_bus", "")).strip() == bus_id
        or str(row.get("to_bus", "")).strip() == bus_id
    ]


def binding_counts(binding_rows: list[dict[str, str]]) -> dict[str, int]:
    mismatches = [
        row
        for row in binding_rows
        if str(row.get("own_binding", "")).lower() != str(row.get("pm_binding", "")).lower()
    ]
    max_margins: list[float] = []
    for row in mismatches:
        own_margin = abs(as_float(row.get("own_margin"), 0.0))
        pm_margin = abs(as_float(row.get("pm_margin"), 0.0))
        max_margins.append(max(own_margin, pm_margin))
    return {
        "binding_mismatches_total": len(mismatches),
        "binding_mismatches_le_1e_5": sum(m <= 1e-5 for m in max_margins),
        "binding_mismatches_le_1e_4": sum(m <= BINDING_REAL_TOL for m in max_margins),
        "binding_mismatches_real_gt_1e_4": sum(m > BINDING_REAL_TOL for m in max_margins),
        "binding_mismatches_large_gt_1e_2": sum(m > BINDING_LARGE_TOL for m in max_margins),
        "binding_mismatches_huge_gt_1": sum(m > BINDING_HUGE_TOL for m in max_margins),
    }


def classify(row: dict[str, object]) -> str:
    if row["details_available"] is False:
        return "data_missing_or_unclassified"
    if str(row["outcome_match"]).lower() != "true":
        return "outcome_mismatch"

    max_pg = as_float(row["max_pg_diff"], 0.0)
    max_pf = as_float(row["max_pf_diff"], 0.0)
    real_bindings = int(row["binding_mismatches_real_gt_1e_4"])

    if max(max_pg, max_pf) <= MATERIAL_DIFF_TOL and real_bindings == 0:
        return "no_material_difference"
    if real_bindings == 0:
        return "tolerance_or_nonbinding_difference"
    if (
        int(row["top_pg_bus_degree"]) == 1
        and row["top_adjacent_pf_branch_id"]
        and row["top_adjacent_pf_matches_top_pg_diff"]
    ):
        return "radial_dispatch_active_set_difference"
    if row["top_pf_adjacent_to_top_pg_bus"]:
        return "adjacent_dispatch_active_set_difference"
    return "non_radial_active_set_difference"


def diagnose_one(summary_row: dict[str, str]) -> dict[str, object]:
    case = summary_row["case"]
    scenario = summary_row["scenario"]
    scenario_dir = JULIA_RESULTS / case / scenario

    gen_rows = read_csv(scenario_dir / "generators.csv")
    branch_rows = read_csv(scenario_dir / "branches.csv")
    binding_rows = read_csv(scenario_dir / "constraints_binding.csv")
    details_available = bool(gen_rows and branch_rows and binding_rows)

    top_gen = max_row(gen_rows, "abs_pg_diff") or {}
    top_branch = max_row(branch_rows, "abs_pf_diff") or {}

    top_pg_bus = str(top_gen.get("bus_id", "")).strip()
    top_branch_from = str(top_branch.get("from_bus", "")).strip()
    top_branch_to = str(top_branch.get("to_bus", "")).strip()
    adjacent = bool(top_pg_bus and top_pg_bus in {top_branch_from, top_branch_to})
    adjacent_branches = incident_branches(branch_rows, top_pg_bus)
    top_adjacent_branch = max_row(adjacent_branches, "abs_pf_diff") or {}
    degree = bus_degree(branch_rows, top_pg_bus)
    max_pg_diff = as_float(top_gen.get("abs_pg_diff"), 0.0)
    adjacent_pf_diff = as_float(top_adjacent_branch.get("abs_pf_diff"), 0.0)
    adjacent_pf_matches_pg = abs(adjacent_pf_diff - max_pg_diff) <= max(
        1e-4,
        1e-6 * max(1.0, max_pg_diff),
    )

    counts = binding_counts(binding_rows)
    row: dict[str, object] = {
        "language": "julia",
        "case": case,
        "scenario": scenario,
        "own_status": summary_row.get("own_status", ""),
        "pm_status": summary_row.get("pm_status", ""),
        "outcome_match": summary_row.get("outcome_match", ""),
        "objective_rel_diff": summary_row.get("objective_rel_diff", ""),
        "own_solve_iterations": summary_row.get("own_solve_iterations", ""),
        "pm_solve_iterations": summary_row.get("pm_solve_iterations", ""),
        "own_solve_time_sec": summary_row.get("own_solve_time_sec", ""),
        "pm_solve_time_sec": summary_row.get("pm_solve_time_sec", ""),
        "details_available": details_available,
        "max_pg_diff": top_gen.get("abs_pg_diff", ""),
        "max_pf_diff": top_branch.get("abs_pf_diff", ""),
        "top_pg_gen_id": top_gen.get("gen_id", ""),
        "top_pg_bus": top_pg_bus,
        "top_pg_own_pg": top_gen.get("own_pg", ""),
        "top_pg_pm_pg": top_gen.get("pm_pg", ""),
        "top_pg_pmin": top_gen.get("pmin", ""),
        "top_pg_pmax": top_gen.get("pmax", ""),
        "top_pg_own_at_pmin": top_gen.get("own_at_pmin", ""),
        "top_pg_pm_at_pmin": top_gen.get("pm_at_pmin", ""),
        "top_pg_own_at_pmax": top_gen.get("own_at_pmax", ""),
        "top_pg_pm_at_pmax": top_gen.get("pm_at_pmax", ""),
        "top_pf_branch_id": top_branch.get("branch_id", ""),
        "top_pf_from_bus": top_branch_from,
        "top_pf_to_bus": top_branch_to,
        "top_pf_own_pf": top_branch.get("own_pf", ""),
        "top_pf_pm_pf": top_branch.get("pm_pf", ""),
        "top_pg_bus_degree": degree,
        "top_pf_adjacent_to_top_pg_bus": adjacent,
        "top_adjacent_pf_branch_id": top_adjacent_branch.get("branch_id", ""),
        "top_adjacent_pf_from_bus": top_adjacent_branch.get("from_bus", ""),
        "top_adjacent_pf_to_bus": top_adjacent_branch.get("to_bus", ""),
        "top_adjacent_pf_diff": top_adjacent_branch.get("abs_pf_diff", ""),
        "top_adjacent_pf_matches_top_pg_diff": adjacent_pf_matches_pg,
        **counts,
    }
    row["diagnosis"] = classify(row)
    return row


def sort_key(row: dict[str, object]) -> tuple[float, float, int]:
    objective = abs(as_float(row.get("objective_rel_diff"), 0.0))
    max_diff = max(as_float(row.get("max_pg_diff"), 0.0), as_float(row.get("max_pf_diff"), 0.0))
    bindings = int(row.get("binding_mismatches_real_gt_1e_4", 0))
    return (objective, max_diff, bindings)


def main() -> None:
    summary_rows = read_csv(SUMMARY_PATH)
    rows = [diagnose_one(row) for row in summary_rows]
    write_csv(OUTPUT_PATH, rows)

    top_rows = sorted(
        [
            row
            for row in rows
            if row["diagnosis"] != "no_material_difference"
            and row["details_available"] is True
        ],
        key=sort_key,
        reverse=True,
    )
    write_csv(TOP_OUTPUT_PATH, top_rows[:50])
    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")
    print(f"Wrote {min(len(top_rows), 50)} rows to {TOP_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
