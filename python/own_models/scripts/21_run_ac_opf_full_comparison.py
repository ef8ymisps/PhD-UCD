"""Full-parity Python AC-OPF validation vs PowerModels.jl.

Extends the existing compact sweep (14_run_compact_ac_opf_sweeps.py, which
already covers all 12 scenario types x 8 cases and cross-validates
status/objective/solve-time against a real PowerModels.jl-solved reference)
with what it was missing: per-variable (Pg/Qg/Vm/Va/Pf/Qf/loading_pct)
diffs, binding-constraint-set agreement (n_pmin_both/only_own/only_pm etc.),
and PowerModels iteration counts -- bringing Python's dataset up to the same
schema Julia's compact_opf_validation_summary.csv already has (see
julia/own_models/src/analysis/opf_solution_inspection.jl's ac_opf_summary_row).

Reuses iter_compact_ac_validation directly (settings["keep_raw_result"]=True,
a minimal addition to ac_opf_sweeps.py that yields the raw case/ACOPFResult
instead of stripping them) rather than re-solving with separate logic --
same scenario definitions as the existing sweep, zero drift risk.

PowerModels reference: the existing per-element CSVs from
julia/own_models/scripts/08_run_ac_opf_powermodels_references.jl
(powermodels_ac_opf_{summary,generators,buses,branches}.csv), which target
Python's own exact 313-scenario manifest already -- no new PowerModels
solves needed here.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat  # noqa: E402
from own_models.src.analysis.ac_opf_binding_comparison import ac_opf_summary_row  # noqa: E402
from own_models.src.analysis.ac_opf_sweeps import (  # noqa: E402
    DEFAULT_SETTINGS,
    compact_ac_scenario_id,
    iter_compact_ac_validation,
)

DEFAULT_CASE_NAMES = [
    "pglib_opf_case5_pjm",
    "pglib_opf_case14_ieee",
    "pglib_opf_case30_ieee",
    "pglib_opf_case118_ieee",
    "pglib_opf_case300_ieee",
    "pglib_opf_case1354_pegase",
    "pglib_opf_case1888_rte",
    "pglib_opf_case6470_rte",
]

PM_REF_DIR = REPO_ROOT / "results" / "reference" / "powermodels" / "ac_opf_compact_sweeps"
OUTPUT_PATH = REPO_ROOT / "results" / "own_python" / "ac_opf_validation" / "compact_ac_opf_full_comparison.csv"


def case_path(case_name: str) -> Path:
    return REPO_ROOT / "data" / "raw" / "pglib" / "matpower_mat" / "base_case" / f"{case_name}.mat"


def load_pm_reference():
    """Index PowerModels reference rows by scenario_id for O(1) lookup."""
    summary = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_summary.csv")
    generators = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_generators.csv")
    buses = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_buses.csv")
    branches = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_branches.csv")

    summary_by_id = {row.scenario_id: row for row in summary.itertuples()}
    gens_by_id: dict[str, dict[str, dict]] = {}
    for row in generators.itertuples():
        gens_by_id.setdefault(row.scenario_id, {})[str(row.gen_id)] = {"pm_pg": row.pm_pg, "pm_qg": row.pm_qg}
    buses_by_id: dict[str, dict[str, dict]] = {}
    for row in buses.itertuples():
        buses_by_id.setdefault(row.scenario_id, {})[str(row.bus_id)] = {"pm_vm": row.pm_vm, "pm_va": row.pm_va}
    branches_by_id: dict[str, dict[str, dict]] = {}
    for row in branches.itertuples():
        branches_by_id.setdefault(row.scenario_id, {})[str(row.branch_id)] = {
            "pm_pf": row.pm_pf, "pm_qf": row.pm_qf, "pm_pt": row.pm_pt, "pm_qt": row.pm_qt,
        }
    return summary_by_id, gens_by_id, buses_by_id, branches_by_id


def completed_scenario_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(pd.read_csv(path, usecols=["scenario_id"])["scenario_id"])


def append_row(path: Path, row: dict) -> None:
    frame = pd.DataFrame([row])
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, mode="a" if path.exists() else "w", header=not path.exists(), index=False)


def main() -> None:
    summary_by_id, gens_by_id, buses_by_id, branches_by_id = load_pm_reference()
    print(f"Loaded PowerModels reference: {len(summary_by_id)} scenarios")

    done = completed_scenario_ids(OUTPUT_PATH)
    print("Starting fresh." if not done else f"Resuming, {len(done)} scenarios already done.")

    # DEFAULT_SETTINGS sets solver_options={"print_level": 0, ...} -- silences
    # Ipopt's console output entirely, including the "Number of Iterations...:"
    # line solve_with_iteration_capture regex-parses (confirmed empirically:
    # own_solve_iterations comes back NaN with print_level=0). Drop it so
    # iteration counts are actually captured here, matching
    # 20_run_ac_opf_warm_start_experiment.py's working precedent (which never
    # sets print_level at all).
    solver_options = {k: v for k, v in DEFAULT_SETTINGS.get("solver_options", {}).items() if k != "print_level"}
    settings = {
        **DEFAULT_SETTINGS,
        "keep_raw_result": True,
        "solver_options": solver_options,
        "include_no_reference_slack": True,
    }
    # Optional: restrict to specific scenario_ids (checked before solving, so
    # this actually skips the work, unlike the `done` check below which only
    # skips the write) -- set via SCENARIO_FILTER_IDS env var, comma-separated.
    filter_env = os.environ.get("SCENARIO_FILTER_IDS")
    if filter_env:
        settings["scenario_filter_ids"] = set(filter_env.split(","))

    for case_name in DEFAULT_CASE_NAMES:
        path = case_path(case_name)
        print(f"\n=== {case_name} ===", flush=True)
        case = load_powercase_from_matpower_mat(path)
        # PM reference scenario_ids use the stripped case name (e.g. "5_pjm",
        # not "pglib_opf_case5_pjm") -- matches 14_run_compact_ac_opf_sweeps.py's
        # --strip-case-prefix convention, which is what generated the manifest
        # 08_run_ac_opf_powermodels_references.jl solved against.
        case = replace(case, name=case_name.replace("pglib_opf_case", ""))

        for row in iter_compact_ac_validation(case, settings=settings):
            scenario_id = compact_ac_scenario_id(row)
            if scenario_id in done:
                continue
            if scenario_id not in summary_by_id:
                print(f"  [skip] {scenario_id} -- no PowerModels reference found")
                continue

            result = row.get("result")
            scenario_case = row.get("case")
            pm_row = summary_by_id[scenario_id]

            if result is None or scenario_case is None:
                # Own model failed to solve -- still record what PM did, own side is NaN throughout.
                out = {
                    "scenario_id": scenario_id, "scenario": row.get("scenario"), "case_name": row.get("case_name"),
                    "own_status": row.get("status", "ERROR"), "own_success": False,
                    "pm_status": pm_row.pm_status, "own_objective": float("nan"), "pm_objective": pm_row.pm_objective,
                    "own_solve_iterations": float("nan"), "pm_solve_iterations": pm_row.pm_solve_iterations,
                    "own_solve_time_sec": row.get("solve_time_sec", float("nan")), "pm_solve_time_sec": pm_row.pm_solve_time_sec,
                }
            else:
                out = ac_opf_summary_row(
                    scenario_case, result,
                    scenario_id=scenario_id, scenario=row.get("scenario"), case_name=row.get("case_name"),
                    pm_generators=gens_by_id.get(scenario_id, {}),
                    pm_buses=buses_by_id.get(scenario_id, {}),
                    pm_branches=branches_by_id.get(scenario_id, {}),
                    pm_status=pm_row.pm_status, pm_objective=pm_row.pm_objective,
                    pm_solve_time_sec=pm_row.pm_solve_time_sec, pm_solve_iterations=pm_row.pm_solve_iterations,
                    own_solve_time_sec=row.get("solve_time_sec", float("nan")),
                )
            append_row(OUTPUT_PATH, out)
            done.add(scenario_id)
            print(f"  {scenario_id}: own={out['own_status']} pm={out['pm_status']} obj_diff={out.get('objective_diff')}", flush=True)

    print(f"\nSaved: {OUTPUT_PATH} ({len(done)} scenarios)")


if __name__ == "__main__":
    main()
