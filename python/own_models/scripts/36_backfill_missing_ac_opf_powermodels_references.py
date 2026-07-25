"""Backfill missing AC OPF PowerModels reference JSONs from Julia comparison tables.

This is intentionally narrow: it only handles scenarios already present in the
Python PM-like sweep as ``MISSING_REFERENCE``.  The Julia comparison folders
already contain the PowerModels-side solution columns (``pm_*``), so solved
references can be reconstructed without rerunning PowerModels.  Non-solved
references carry the termination status and static limits, with null solution
values, matching the existing reference schema.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
REFERENCE_DIR = ROOT / "results" / "reference" / "powermodels" / "ac_opf"
PY_PM_LIKE_ROOT = ROOT / "results" / "own_vs_powermodels" / "python_pm_like"
JULIA_ROOTS = [
    ROOT / "results" / "own_vs_powermodels" / "julia_pm_like",
    ROOT / "results" / "own_vs_powermodels" / "julia",
]
ORGANIZED_ROOT = (
    ROOT
    / "results"
    / "own_vs_powermodels"
    / "_organized_for_meeting"
    / "08_python_ac_opf_debugging"
)

SUMMARY_PATH = PY_PM_LIKE_ROOT / "summary.csv"
PROVENANCE_PATH = PY_PM_LIKE_ROOT / "backfilled_powermodels_references.csv"
PM_SOLVED = {"LOCALLY_SOLVED", "OPTIMAL", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED"}


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    open_path = Path("\\\\?\\" + str(path.resolve())) if os.name == "nt" else path
    with open_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4)


def sorted_ids(mapping: dict) -> list[str]:
    return sorted((str(key) for key in mapping.keys()), key=lambda value: int(value))


def find_julia_summary_row(case: str, scenario: str) -> tuple[pd.Series, Path]:
    for root in JULIA_ROOTS:
        summary_path = root / "summary.csv"
        if not summary_path.exists():
            continue
        summary = pd.read_csv(summary_path, dtype=str, keep_default_na=False)
        matches = summary[(summary["case"] == case) & (summary["scenario"] == scenario)]
        if not matches.empty:
            return matches.iloc[-1], root
    raise FileNotFoundError(f"No Julia summary row for {case} / {scenario}")


def find_julia_scenario_dir(root: Path, case: str, scenario: str) -> Path | None:
    case_dir = root / case
    exact = case_dir / scenario
    if exact.exists():
        return exact
    if not case_dir.exists():
        return None
    for child in case_dir.iterdir():
        if not child.is_dir():
            continue
        label_file = child / "scenario_label.txt"
        if label_file.exists() and label_file.read_text(encoding="utf-8").strip() == scenario:
            return child
    return None


def maybe_float(value):
    if value is None:
        return None
    text = str(value)
    if text == "" or text.lower() == "nan":
        return None
    return float(text)


def table_by_id(path: Path, id_column: str) -> dict[str, pd.Series]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    return {str(row[id_column]): row for _, row in frame.iterrows()}


def build_reference(case: str, scenario: str, case_data: dict, summary_row: pd.Series, table_dir: Path | None) -> dict:
    status = str(summary_row.get("pm_status", "UNKNOWN"))
    solved = status in PM_SOLVED
    gen_rows = table_by_id(table_dir / "generators.csv", "gen_id") if table_dir else {}
    bus_rows = table_by_id(table_dir / "buses.csv", "bus_id") if table_dir else {}
    branch_rows = table_by_id(table_dir / "branches.csv", "branch_id") if table_dir else {}

    generators = {}
    for gen_id in sorted_ids(case_data.get("gen", {})):
        gen = case_data["gen"][gen_id]
        row = gen_rows.get(gen_id)
        generators[gen_id] = {
            "pg": maybe_float(row["pm_pg"]) if row is not None and solved else None,
            "qg": maybe_float(row["pm_qg"]) if row is not None and solved else None,
            "pmin": maybe_float(gen.get("pmin")),
            "pmax": maybe_float(gen.get("pmax")),
            "qmin": maybe_float(gen.get("qmin")),
            "qmax": maybe_float(gen.get("qmax")),
            "cost": gen.get("cost", []),
        }

    buses = {}
    for bus_id in sorted_ids(case_data.get("bus", {})):
        bus = case_data["bus"][bus_id]
        row = bus_rows.get(bus_id)
        buses[bus_id] = {
            "va": maybe_float(row["pm_va"]) if row is not None and solved else None,
            "vm": maybe_float(row["pm_vm"]) if row is not None and solved else None,
            "vmin": maybe_float(bus.get("vmin")),
            "vmax": maybe_float(bus.get("vmax")),
        }

    branches = {}
    for branch_id in sorted_ids(case_data.get("branch", {})):
        branch = case_data["branch"][branch_id]
        row = branch_rows.get(branch_id)
        branches[branch_id] = {
            "pf": maybe_float(row["pm_pf"]) if row is not None and solved else None,
            "qf": maybe_float(row["pm_qf"]) if row is not None and solved else None,
            "pt": maybe_float(row["pm_pt"]) if row is not None and solved else None,
            "qt": maybe_float(row["pm_qt"]) if row is not None and solved else None,
            "rate_a": maybe_float(branch.get("rate_a")),
            "angmin": maybe_float(branch.get("angmin")),
            "angmax": maybe_float(branch.get("angmax")),
        }

    return {
        "case_name": case,
        "scenario": scenario,
        "termination_status": status,
        "solved": solved,
        "objective": maybe_float(summary_row.get("pm_objective")) if solved else None,
        "solve_iterations": maybe_float(summary_row.get("pm_solve_iterations")),
        "solve_time_sec": maybe_float(summary_row.get("pm_solve_time_sec")),
        "generators": generators,
        "buses": buses,
        "branches": branches,
        "reference_source": "backfilled_from_existing_julia_pm_columns",
    }


def main() -> None:
    summary = pd.read_csv(SUMMARY_PATH, dtype=str, keep_default_na=False)
    missing = summary[summary["own_status"] == "MISSING_REFERENCE"]
    rows = []
    for _, missing_row in missing.iterrows():
        case = str(missing_row["case"])
        scenario = str(missing_row["scenario"])
        stem = f"pglib_opf_case{case}_{scenario}"
        case_path = REFERENCE_DIR / f"{stem}_case.json"
        reference_path = REFERENCE_DIR / f"{stem}_reference.json"
        if not case_path.exists():
            raise FileNotFoundError(case_path)

        summary_row, julia_root = find_julia_summary_row(case, scenario)
        table_dir = find_julia_scenario_dir(julia_root, case, scenario)
        payload = build_reference(case, scenario, load_json(case_path), summary_row, table_dir)
        write_json(reference_path, payload)
        rows.append(
            {
                "case": case,
                "scenario": scenario,
                "reference_path": str(reference_path.relative_to(ROOT)),
                "julia_source_root": str(julia_root.relative_to(ROOT)),
                "julia_table_dir": "" if table_dir is None else str(table_dir.relative_to(ROOT)),
                "pm_status": payload["termination_status"],
                "pm_solved": payload["solved"],
                "source": payload["reference_source"],
            }
        )

    provenance = pd.DataFrame(rows)
    provenance.to_csv(PROVENANCE_PATH, index=False)
    ORGANIZED_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROVENANCE_PATH, ORGANIZED_ROOT / f"python_pm_like_full_sweep_{PROVENANCE_PATH.name}")
    print(f"Backfilled {len(provenance)} reference JSON file(s).")
    print(f"Provenance: {PROVENANCE_PATH}")


if __name__ == "__main__":
    main()
