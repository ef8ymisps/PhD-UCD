"""Build compact Julia formulation audit tables for meeting discussion.

Outputs:

* ``julia_parallel_branch_ybus_audit.csv``: shows that branch constraints are
  still per physical branch even though Ybus nodal balances aggregate parallel
  branch admittances by bus pair.
* ``equality_only_deviation_inequality_level_by_family_julia.csv``: compact
  family-level summary of the equality-only inequality level table.
* ``equality_only_deviation_inequality_level_top_rows_julia.csv``: largest
  equality-only vs PowerModels inequality-level differences.
"""

from __future__ import annotations

import csv
import math
import shutil
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPO_ROOT / "results" / "own_vs_powermodels"
ORGANIZED_ROOT = RESULTS_ROOT / "_organized_for_meeting"


def read_csv(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else ["note"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def branch_audit_rows() -> list[dict]:
    rows: list[dict] = []
    roots = [
        ("julia_current", RESULTS_ROOT / "julia"),
        ("julia_pm_like", RESULTS_ROOT / "julia_pm_like"),
    ]
    for family, root in roots:
        if not root.exists():
            continue
        for branches_path in sorted(root.glob("*/*/branches.csv")):
            rel = branches_path.relative_to(root)
            case = rel.parts[0]
            scenario = rel.parts[1]
            pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
            n_branch_rows = 0
            for row in read_csv(branches_path):
                n_branch_rows += 1
                f_bus = str(row.get("from_bus", ""))
                t_bus = str(row.get("to_bus", ""))
                pair = tuple(sorted((f_bus, t_bus)))
                pairs[pair].append(str(row.get("branch_id", "")))

            parallel_groups = {pair: ids for pair, ids in pairs.items() if len(ids) > 1}
            max_parallel_count = max((len(ids) for ids in pairs.values()), default=0)
            sample_parallel_groups = "; ".join(
                f"{a}-{b}:{len(ids)}[{','.join(ids[:5])}{'...' if len(ids) > 5 else ''}]"
                for (a, b), ids in sorted(parallel_groups.items(), key=lambda item: (-len(item[1]), item[0]))[:5]
            )

            inventory_path = branches_path.parent / "constraints_inventory.csv"
            thermal_instances = math.nan
            if inventory_path.exists():
                for inv in read_csv(inventory_path):
                    if inv.get("constraint_type") == "thermal":
                        thermal_instances = to_float(inv.get("n_instances"))
                        break

            rows.append(
                {
                    "result_family": family,
                    "case": case,
                    "scenario": scenario,
                    "n_branch_rows": n_branch_rows,
                    "n_unique_undirected_bus_pairs": len(pairs),
                    "n_parallel_bus_pairs": len(parallel_groups),
                    "n_branches_in_parallel_groups": sum(len(ids) for ids in parallel_groups.values()),
                    "max_parallel_count": max_parallel_count,
                    "thermal_constraint_instances": thermal_instances,
                    "thermal_constraints_match_branch_rows": (
                        math.isfinite(thermal_instances) and int(thermal_instances) == n_branch_rows
                    ),
                    "sample_parallel_groups": sample_parallel_groups,
                    "interpretation": (
                        "Ybus nodal balance aggregates branch admittances by bus pair; "
                        "thermal/angle constraints and branch-flow rows remain per physical branch_id."
                    ),
                }
            )
    return rows


def equality_level_summary_rows() -> tuple[list[dict], list[dict]]:
    levels_path = (
        ORGANIZED_ROOT
        / "07_deviation_minimization"
        / "equality_only_deviation_inequality_levels_julia.csv"
    )
    if not levels_path.exists():
        levels_path = RESULTS_ROOT / "equality_only_deviation_inequality_levels_julia.csv"
    if not levels_path.exists():
        return [], []

    grouped: dict[tuple[str, str, str, str], dict] = {}
    top_candidates: list[dict] = []
    for row in read_csv(levels_path):
        key = (
            row.get("case", ""),
            row.get("scenario", ""),
            row.get("variant", ""),
            row.get("constraint_type", ""),
        )
        stats = grouped.setdefault(
            key,
            {
                "case": key[0],
                "scenario": key[1],
                "variant": key[2],
                "constraint_type": key[3],
                "n_constraints": 0,
                "n_binding_mismatches": 0,
                "n_equality_only_violations": 0,
                "max_violation": 0.0,
                "max_abs_value_diff": 0.0,
                "max_abs_margin_diff": 0.0,
                "max_abs_equality_only_margin": 0.0,
                "max_abs_pm_margin": 0.0,
            },
        )
        stats["n_constraints"] += 1
        relation = row.get("binding_relation", "")
        stats["n_binding_mismatches"] += int(relation != "same")
        eq_margin = to_float(row.get("equality_only_margin"))
        pm_margin = to_float(row.get("pm_margin"))
        abs_value_diff = to_float(row.get("abs_value_diff"))
        abs_margin_diff = to_float(row.get("abs_margin_diff"))
        violation = max(0.0, -eq_margin) if math.isfinite(eq_margin) else 0.0
        stats["n_equality_only_violations"] += int(str(row.get("equality_only_violates_limit", "")).lower() == "true")
        stats["max_violation"] = max(stats["max_violation"], violation)
        stats["max_abs_value_diff"] = max(stats["max_abs_value_diff"], abs_value_diff if math.isfinite(abs_value_diff) else 0.0)
        stats["max_abs_margin_diff"] = max(stats["max_abs_margin_diff"], abs_margin_diff if math.isfinite(abs_margin_diff) else 0.0)
        stats["max_abs_equality_only_margin"] = max(stats["max_abs_equality_only_margin"], abs(eq_margin) if math.isfinite(eq_margin) else 0.0)
        stats["max_abs_pm_margin"] = max(stats["max_abs_pm_margin"], abs(pm_margin) if math.isfinite(pm_margin) else 0.0)

        top_row = dict(row)
        top_row["_sort_abs_margin_diff"] = abs_margin_diff if math.isfinite(abs_margin_diff) else 0.0
        top_row["_sort_violation"] = violation
        top_candidates.append(top_row)

    summary = sorted(grouped.values(), key=lambda r: (r["case"], r["scenario"], r["variant"], r["constraint_type"]))
    top_candidates.sort(key=lambda r: (r["_sort_violation"], r["_sort_abs_margin_diff"]), reverse=True)
    top_rows = []
    for row in top_candidates[:100]:
        row = {key: value for key, value in row.items() if not key.startswith("_sort_")}
        top_rows.append(row)
    return summary, top_rows


def copy_to_organized(paths: list[Path]) -> None:
    formulation_dir = ORGANIZED_ROOT / "05_shunt_and_formulation"
    deviation_dir = ORGANIZED_ROOT / "07_deviation_minimization"
    formulation_dir.mkdir(parents=True, exist_ok=True)
    deviation_dir.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if "parallel_branch" in path.name:
            shutil.copy2(path, formulation_dir / path.name)
        else:
            shutil.copy2(path, deviation_dir / path.name)


def main() -> None:
    parallel_path = RESULTS_ROOT / "julia_parallel_branch_ybus_audit.csv"
    equality_family_path = RESULTS_ROOT / "equality_only_deviation_inequality_level_by_family_julia.csv"
    equality_top_path = RESULTS_ROOT / "equality_only_deviation_inequality_level_top_rows_julia.csv"

    parallel_rows = branch_audit_rows()
    equality_family_rows, equality_top_rows = equality_level_summary_rows()

    write_csv(parallel_path, parallel_rows)
    write_csv(equality_family_path, equality_family_rows)
    write_csv(equality_top_path, equality_top_rows)
    copy_to_organized([parallel_path, equality_family_path, equality_top_path])

    print(f"Wrote {parallel_path} ({len(parallel_rows)} rows)")
    print(f"Wrote {equality_family_path} ({len(equality_family_rows)} rows)")
    print(f"Wrote {equality_top_path} ({len(equality_top_rows)} rows)")


if __name__ == "__main__":
    main()
