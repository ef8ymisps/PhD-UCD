"""Prepare all-model presentation tables for the AC-OPF discussion deck.

This script is intentionally narrow: it combines already-generated validation
artifacts into two compact CSVs used by the Beamer slides.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, median


ROOT = Path(__file__).resolve().parents[3]
RESULTS = ROOT / "results" / "own_vs_powermodels"
ORG = RESULTS / "_organized_for_meeting"


CASE_ORDER = [
    "5_pjm",
    "14_ieee",
    "30_ieee",
    "118_ieee",
    "300_ieee",
    "1354_pegase",
    "1888_rte",
    "6470_rte",
]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def write_rows(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def f(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    if value in ("", None):
        return math.nan
    try:
        return float(value)
    except ValueError:
        return math.nan


def b(value: str | None) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def fmt(value: float | int | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if math.isnan(value):
        return ""
    return f"{value:.12g}"


def family_from_constraint_type(name: str) -> str:
    name = (name or "").lower()
    if name.startswith("angle"):
        return "angle"
    if name.startswith("pg"):
        return "pg"
    if name.startswith("qg"):
        return "qg"
    if name.startswith("vm") or name.startswith("voltage"):
        return "vm"
    if name.startswith("sf") or name.startswith("st") or name.startswith("thermal"):
        return "thermal"
    return name or "unknown"


def model_from_julia_variant(variant: str) -> str:
    variant = (variant or "").lower()
    if variant == "current":
        return "julia_current"
    if variant in {"pm_like", "pmlike"}:
        return "julia_pm_like"
    return f"julia_{variant}" if variant else "julia_unknown"


def add_to_bucket(bucket: dict[str, float], row: dict[str, str], *, source: str) -> None:
    bucket["n_constraints"] += int(f(row, "n_constraints") if not math.isnan(f(row, "n_constraints")) else 0)
    bucket["n_binding_mismatches"] += int(
        f(row, "n_binding_mismatches") if not math.isnan(f(row, "n_binding_mismatches")) else 0
    )
    bucket["n_equality_only_violations"] += int(
        f(row, "n_equality_only_violations") if not math.isnan(f(row, "n_equality_only_violations")) else 0
    )
    if "n_pm_violations" in row:
        bucket["n_pm_violations"] += int(f(row, "n_pm_violations") if not math.isnan(f(row, "n_pm_violations")) else 0)

    max_keys = [
        ("max_violation", "max_equality_only_violation"),
        ("max_abs_value_diff", "max_abs_value_diff"),
        ("max_abs_margin_diff", "max_abs_margin_diff"),
        ("max_abs_equality_only_margin", "max_abs_equality_only_margin"),
        ("max_abs_pm_margin", "max_abs_pm_margin"),
    ]
    for input_key, output_key in max_keys:
        value = f(row, input_key)
        if math.isnan(value):
            continue
        bucket[output_key] = max(bucket[output_key], value)


def build_equality_only_h_level_table() -> list[dict[str, object]]:
    julia_path = RESULTS / "equality_only_deviation_inequality_level_by_family_julia.csv"
    python_path = RESULTS / "python_pm_like" / "equality_only_deviation_inequality_levels.csv"

    buckets: dict[tuple[str, str, str, str], dict[str, float | str]] = defaultdict(
        lambda: {
            "n_constraints": 0,
            "n_binding_mismatches": 0,
            "n_equality_only_violations": 0,
            "n_pm_violations": 0,
            "max_equality_only_violation": 0.0,
            "max_abs_value_diff": 0.0,
            "max_abs_margin_diff": 0.0,
            "max_abs_equality_only_margin": 0.0,
            "max_abs_pm_margin": 0.0,
            "source": "",
        }
    )

    for row in read_rows(julia_path):
        model = model_from_julia_variant(row.get("variant", ""))
        family = family_from_constraint_type(row.get("constraint_type", ""))
        key = (model, row["case"], row["scenario"], family)
        buckets[key]["source"] = julia_path.name
        add_to_bucket(buckets[key], row, source="julia")

    detail_buckets: dict[tuple[str, str, str, str], dict[str, float | str]] = defaultdict(
        lambda: {
            "n_constraints": 0,
            "n_binding_mismatches": 0,
            "n_equality_only_violations": 0,
            "n_pm_violations": 0,
            "max_equality_only_violation": 0.0,
            "max_abs_value_diff": 0.0,
            "max_abs_margin_diff": 0.0,
            "max_abs_equality_only_margin": 0.0,
            "max_abs_pm_margin": 0.0,
            "source": python_path.name,
        }
    )
    for row in read_rows(python_path):
        if row.get("diagnostic_mode") != "equality_only":
            continue
        if row.get("variant") != "full":
            continue
        model = row.get("model_variant") or "python_pm_like_zero"
        family = family_from_constraint_type(row.get("constraint_type", ""))
        key = (model, row["case"], row["scenario"], family)
        bucket = detail_buckets[key]
        bucket["n_constraints"] += 1
        bucket["n_binding_mismatches"] += int(b(row.get("equality_only_binding")) != b(row.get("pm_binding")))
        bucket["n_equality_only_violations"] += int(b(row.get("equality_only_violates_limit")))
        bucket["n_pm_violations"] += int(b(row.get("pm_violates_limit")))
        bucket["max_equality_only_violation"] = max(bucket["max_equality_only_violation"], f(row, "equality_only_violation"))
        bucket["max_abs_value_diff"] = max(bucket["max_abs_value_diff"], f(row, "abs_value_diff"))
        bucket["max_abs_margin_diff"] = max(bucket["max_abs_margin_diff"], f(row, "abs_margin_diff"))
        bucket["max_abs_equality_only_margin"] = max(
            bucket["max_abs_equality_only_margin"], abs(f(row, "equality_only_margin"))
        )
        bucket["max_abs_pm_margin"] = max(bucket["max_abs_pm_margin"], abs(f(row, "pm_margin")))

    buckets.update(detail_buckets)

    rows = []
    for (model, case, scenario, family), data in sorted(
        buckets.items(),
        key=lambda item: (
            CASE_ORDER.index(item[0][1]) if item[0][1] in CASE_ORDER else 999,
            item[0][2],
            item[0][0],
            item[0][3],
        ),
    ):
        rows.append(
            {
                "model": model,
                "case": case,
                "scenario": scenario,
                "constraint_family": family,
                "n_constraints": int(data["n_constraints"]),
                "n_binding_mismatches": int(data["n_binding_mismatches"]),
                "n_equality_only_violations": int(data["n_equality_only_violations"]),
                "n_pm_violations": int(data["n_pm_violations"]),
                "max_equality_only_violation": fmt(data["max_equality_only_violation"]),
                "max_abs_value_diff": fmt(data["max_abs_value_diff"]),
                "max_abs_margin_diff": fmt(data["max_abs_margin_diff"]),
                "max_abs_equality_only_margin": fmt(data["max_abs_equality_only_margin"]),
                "max_abs_pm_margin": fmt(data["max_abs_pm_margin"]),
                "source": data["source"],
            }
        )
    return rows


def numeric_values(rows: list[dict[str, str]], key: str) -> list[float]:
    values = [f(row, key) for row in rows]
    return [value for value in values if not math.isnan(value)]


def python_iteration_backfill() -> dict[tuple[str, str], float]:
    path = RESULTS / "python_pm_like" / "iteration_backfill.csv"
    if not path.exists():
        return {}
    backfill: dict[tuple[str, str], float] = {}
    for row in read_rows(path):
        value = f(row, "solve_iterations")
        if not math.isnan(value):
            backfill[(row["case"], row["scenario"])] = value
    return backfill


def build_performance_table() -> list[dict[str, object]]:
    julia_rows = read_rows(RESULTS / "solve_performance_by_case_julia_models.csv")
    py_rows = read_rows(RESULTS / "python_pm_like" / "summary.csv")
    status_rows = {row["case"]: row for row in read_rows(RESULTS / "python_pm_like" / "status_by_case.csv")}
    iteration_backfill = python_iteration_backfill()

    by_case: dict[str, dict[str, str]] = {case: {"case": case} for case in CASE_ORDER}
    for row in julia_rows:
        case = row["case"]
        if case not in by_case:
            by_case[case] = {"case": case}
        model = row["model"]
        if model == "current_julia":
            prefix = "julia_current"
        elif model == "pm_like_julia":
            prefix = "julia_pm_like"
        else:
            continue
        by_case[case][f"{prefix}_mean_time_sec"] = fmt(f(row, "mean_own_time_sec"))
        by_case[case][f"{prefix}_mean_iterations"] = fmt(f(row, "mean_own_iterations"))
        by_case[case]["powermodels_mean_time_sec"] = fmt(f(row, "mean_pm_time_sec"))
        by_case[case]["powermodels_mean_iterations"] = fmt(f(row, "mean_pm_iterations"))

    py_by_case: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in py_rows:
        if b(row.get("own_success")):
            py_by_case[row["case"]].append(row)

    for case, rows in py_by_case.items():
        if case not in by_case:
            by_case[case] = {"case": case}
        times = numeric_values(rows, "own_solve_time_sec")
        iterations: list[float] = []
        for row in rows:
            iteration = f(row, "own_solve_iterations")
            if math.isnan(iteration):
                iteration = f(row, "solve_iterations")
            if math.isnan(iteration):
                iteration = iteration_backfill.get((row["case"], row["scenario"]), math.nan)
            if not math.isnan(iteration):
                iterations.append(iteration)
        by_case[case]["python_pm_like_mean_time_sec"] = fmt(mean(times)) if times else ""
        by_case[case]["python_pm_like_median_time_sec"] = fmt(median(times)) if times else ""
        by_case[case]["python_pm_like_mean_iterations"] = fmt(mean(iterations)) if iterations else ""
        by_case[case]["python_pm_like_median_iterations"] = fmt(median(iterations)) if iterations else ""
        by_case[case]["python_pm_like_solved_rows"] = str(len(rows))

    for case, status in status_rows.items():
        if case not in by_case:
            by_case[case] = {"case": case}
        by_case[case]["python_pm_like_total_rows"] = status.get("total", "")
        by_case[case]["python_pm_like_optimal_rows"] = status.get("optimal", "")
        by_case[case]["python_pm_like_max_iteration_rows"] = status.get("maxIterations", "")

    for case, row in by_case.items():
        if not row.get("python_pm_like_mean_iterations"):
            row["python_iteration_note"] = "not exported in full Python PM-like sweep"
        elif row.get("python_pm_like_solved_rows") and row.get("python_pm_like_solved_rows") != "":
            row["python_iteration_note"] = "iteration backfill"
        else:
            row["python_iteration_note"] = ""

    return [by_case[case] for case in CASE_ORDER if case in by_case]


def main() -> None:
    h_rows = build_equality_only_h_level_table()
    h_fields = [
        "model",
        "case",
        "scenario",
        "constraint_family",
        "n_constraints",
        "n_binding_mismatches",
        "n_equality_only_violations",
        "n_pm_violations",
        "max_equality_only_violation",
        "max_abs_value_diff",
        "max_abs_margin_diff",
        "max_abs_equality_only_margin",
        "max_abs_pm_margin",
        "source",
    ]
    h_outputs = [
        RESULTS / "equality_only_deviation_inequality_level_by_family_all_models.csv",
        ORG / "09_constraint_mismatch_evidence" / "equality_only_constraint_level_summary_by_family_all_models.csv",
    ]
    for path in h_outputs:
        write_rows(path, h_rows, h_fields)

    perf_rows = build_performance_table()
    perf_fields = [
        "case",
        "julia_current_mean_time_sec",
        "julia_pm_like_mean_time_sec",
        "python_pm_like_mean_time_sec",
        "powermodels_mean_time_sec",
        "julia_current_mean_iterations",
        "julia_pm_like_mean_iterations",
        "python_pm_like_mean_iterations",
        "powermodels_mean_iterations",
        "python_pm_like_solved_rows",
        "python_pm_like_total_rows",
        "python_pm_like_optimal_rows",
        "python_pm_like_max_iteration_rows",
        "python_iteration_note",
    ]
    perf_outputs = [
        RESULTS / "solve_performance_by_case_all_models_with_python_pm_like.csv",
        ORG / "02_pm_like_vs_current" / "solve_performance_by_case_all_models_with_python_pm_like.csv",
    ]
    for path in perf_outputs:
        write_rows(path, perf_rows, perf_fields)

    print(f"Wrote {len(h_rows)} equality-only family rows")
    print(f"Wrote {len(perf_rows)} performance rows")
    for path in h_outputs + perf_outputs:
        print(path)


if __name__ == "__main__":
    main()
