"""Set B: Python own-model vs PowerModels, per-case inspection tables.

Mirrors julia/own_models/scripts/25_run_own_vs_powermodels_tables.jl (Set A)
exactly -- same 6 output files, same column names where the two languages'
comparison functions already agree, so the two folders are directly
comparable side by side.

Reuses own_models.src.analysis.compare_ac_with_powermodels's existing
generator/bus/branch comparison functions (already used by
07_validate_ac_opf_against_powermodels.py) rather than recomputing anything
-- this script only adds the shunts/constraints_inventory/constraints_binding
tables and writes everything to the results/own_vs_powermodels/python/<case>/
layout.

PowerModels reference JSONs come from
julia/tools/powermodels/scripts/08_export_ac_opf_reference_all_cases.jl,
which must be run first (or already have been) for all 8 cases' base
scenario -- this script does not solve PowerModels itself, since
PowerModels.jl is Julia-only.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.powermodels_csv_adapter import load_powercase_from_powermodels_json  # noqa: E402
from own_models.src.analysis.compare_ac_with_powermodels import (
    compare_ac_opf_branches_with_powermodels,
    compare_ac_opf_buses_with_powermodels,
    compare_ac_opf_generators_with_powermodels,
    load_powermodels_ac_reference,
)
from own_models.src.opf.ac import solve_ac_opf_pyomo

ROOT = Path(__file__).resolve().parents[3]
REFERENCE_DIR = ROOT / "results" / "reference" / "powermodels" / "ac_opf"
# Sole AC-OPF model output goes to "python_pm_like" -- the non-suffixed
# "python" folder is a frozen pre-2026-07-24 bus-injection snapshot that can
# no longer be regenerated (that formulation was deleted); do not write here.
OUT_ROOT = ROOT / "results" / "own_vs_powermodels" / "python_pm_like"

CASES = [
    ("pglib_opf_case5_pjm", "5_pjm"),
    ("pglib_opf_case14_ieee", "14_ieee"),
    ("pglib_opf_case30_ieee", "30_ieee"),
    ("pglib_opf_case118_ieee", "118_ieee"),
    ("pglib_opf_case300_ieee", "300_ieee"),
    ("pglib_opf_case1354_pegase", "1354_pegase"),
    ("pglib_opf_case1888_rte", "1888_rte"),
    ("pglib_opf_case6470_rte", "6470_rte"),
]

# Same 11 constraint types as Set A (Julia), file:line pointers to the
# Python implementation instead.
def constraints_inventory_rows(case) -> pd.DataFrame:
    n_bus = len(case.buses)
    n_gen = len(case.generators)
    n_branch = len(case.branches)
    rows = [
        dict(constraint_type="power_balance_p", applies_to="bus", formula="sum(p_arc) = sum(Pg) - Pd - Gs*Vm^2", file_line="opf/ac/model.py:298-311 (_add_ac_power_balance_constraints)", n_instances=n_bus, tolerance_used=float("nan")),
        dict(constraint_type="power_balance_q", applies_to="bus", formula="sum(q_arc) = sum(Qg) - Qd + Bs*Vm^2", file_line="opf/ac/model.py:298-311 (_add_ac_power_balance_constraints)", n_instances=n_bus, tolerance_used=float("nan")),
        dict(constraint_type="pg_min", applies_to="generator", formula="Pg >= Pmin", file_line="opf/ac/model.py:225-260 (_add_ac_variables, pyo.Var bounds)", n_instances=n_gen, tolerance_used=1e-6),
        dict(constraint_type="pg_max", applies_to="generator", formula="Pg <= Pmax", file_line="opf/ac/model.py:225-260 (_add_ac_variables, pyo.Var bounds)", n_instances=n_gen, tolerance_used=1e-6),
        dict(constraint_type="qg_min", applies_to="generator", formula="Qg >= Qmin", file_line="opf/ac/model.py:225-260 (_add_ac_variables, pyo.Var bounds)", n_instances=n_gen, tolerance_used=1e-6),
        dict(constraint_type="qg_max", applies_to="generator", formula="Qg <= Qmax", file_line="opf/ac/model.py:225-260 (_add_ac_variables, pyo.Var bounds)", n_instances=n_gen, tolerance_used=1e-6),
        dict(constraint_type="vm_min", applies_to="bus", formula="Vm >= Vmin", file_line="opf/ac/model.py:225-260 (_add_ac_variables, pyo.Var bounds)", n_instances=n_bus, tolerance_used=1e-6),
        dict(constraint_type="vm_max", applies_to="bus", formula="Vm <= Vmax", file_line="opf/ac/model.py:225-260 (_add_ac_variables, pyo.Var bounds)", n_instances=n_bus, tolerance_used=1e-6),
        dict(constraint_type="thermal", applies_to="branch", formula="max(Sf, St) <= rate_a", file_line="opf/ac/model.py:315-360 (_add_ac_branch_constraints, thermal)", n_instances=n_branch, tolerance_used=1e-6),
        dict(constraint_type="angle_lower", applies_to="branch", formula="theta_i - theta_j >= angmin", file_line="opf/ac/model.py:315-360 (_add_ac_branch_constraints, angle_limits)", n_instances=n_branch, tolerance_used=1e-6),
        dict(constraint_type="angle_upper", applies_to="branch", formula="theta_i - theta_j <= angmax", file_line="opf/ac/model.py:315-360 (_add_ac_branch_constraints, angle_limits)", n_instances=n_branch, tolerance_used=1e-6),
    ]
    return pd.DataFrame(rows)


def constraints_binding_rows(gen_rows: pd.DataFrame, bus_rows: pd.DataFrame, branch_rows: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for _, r in gen_rows.iterrows():
        rows.append(dict(constraint_type="pg_min", element_id=r["gen_id"], bound=r["pmin"], own_value=r["own_pg"], pm_value=r["pm_pg"], own_margin=r["own_pg"] - r["pmin"], pm_margin=r["pm_pg"] - r["pmin"], own_binding=r["own_at_pmin"], pm_binding=r["pm_at_pmin"]))
        rows.append(dict(constraint_type="pg_max", element_id=r["gen_id"], bound=r["pmax"], own_value=r["own_pg"], pm_value=r["pm_pg"], own_margin=r["pmax"] - r["own_pg"], pm_margin=r["pmax"] - r["pm_pg"], own_binding=r["own_at_pmax"], pm_binding=r["pm_at_pmax"]))
        rows.append(dict(constraint_type="qg_min", element_id=r["gen_id"], bound=r["qmin"], own_value=r["own_qg"], pm_value=r["pm_qg"], own_margin=r["own_qg"] - r["qmin"], pm_margin=r["pm_qg"] - r["qmin"], own_binding=r["own_at_qmin"], pm_binding=r["pm_at_qmin"]))
        rows.append(dict(constraint_type="qg_max", element_id=r["gen_id"], bound=r["qmax"], own_value=r["own_qg"], pm_value=r["pm_qg"], own_margin=r["qmax"] - r["own_qg"], pm_margin=r["qmax"] - r["pm_qg"], own_binding=r["own_at_qmax"], pm_binding=r["pm_at_qmax"]))
    for _, r in bus_rows.iterrows():
        rows.append(dict(constraint_type="vm_min", element_id=r["bus_id"], bound=r["vmin"], own_value=r["own_vm"], pm_value=r["pm_vm"], own_margin=r["own_vm"] - r["vmin"], pm_margin=r["pm_vm"] - r["vmin"], own_binding=r["own_at_vmin"], pm_binding=r["pm_at_vmin"]))
        rows.append(dict(constraint_type="vm_max", element_id=r["bus_id"], bound=r["vmax"], own_value=r["own_vm"], pm_value=r["pm_vm"], own_margin=r["vmax"] - r["own_vm"], pm_margin=r["vmax"] - r["pm_vm"], own_binding=r["own_at_vmax"], pm_binding=r["pm_at_vmax"]))
    for _, r in branch_rows.iterrows():
        own_loading = max(r["own_sf"], r["own_st"])
        pm_loading = max(r["pm_sf"], r["pm_st"])
        rows.append(dict(constraint_type="thermal", element_id=r["branch_id"], bound=r["rate_a"], own_value=own_loading, pm_value=pm_loading, own_margin=r["rate_a"] - own_loading, pm_margin=r["rate_a"] - pm_loading, own_binding=r["own_thermal_binding"], pm_binding=r["pm_thermal_binding"]))
        rows.append(dict(constraint_type="angle_lower", element_id=r["branch_id"], bound=r["angmin"], own_value=r["angle_diff"], pm_value=r["pm_angle_diff"], own_margin=r["angle_diff"] - r["angmin"], pm_margin=r["pm_angle_diff"] - r["angmin"], own_binding=r["own_angle_lower_binding"], pm_binding=r["pm_angle_lower_binding"]))
        rows.append(dict(constraint_type="angle_upper", element_id=r["branch_id"], bound=r["angmax"], own_value=r["angle_diff"], pm_value=r["pm_angle_diff"], own_margin=r["angmax"] - r["angle_diff"], pm_margin=r["angmax"] - r["pm_angle_diff"], own_binding=r["own_angle_upper_binding"], pm_binding=r["pm_angle_upper_binding"]))
    df = pd.DataFrame(rows)
    return df.sort_values("own_margin").reset_index(drop=True)


def shunts_rows(case) -> pd.DataFrame:
    return pd.DataFrame(
        [{"shunt_id": s.id, "bus_id": s.bus, "gs": s.gs, "bs": s.bs, "status": s.status} for s in case.shunts]
    )


SUMMARY_PATH = OUT_ROOT / "summary.csv"


def append_summary_row(row: dict) -> None:
    df = pd.DataFrame([row])
    df.to_csv(SUMMARY_PATH, mode="a", header=not SUMMARY_PATH.exists(), index=False)


def process_scenario(stem: str, label: str, scenario_label: str, reference_stem_suffix: str) -> None:
    """`reference_stem_suffix` is the exporter's file-name suffix (e.g. "base",
    "load_x0.8"); `scenario_label` is the output subfolder name -- usually the
    same string, kept separate in case the two ever need to diverge."""
    print(f"=== {label} / {scenario_label} ===")
    case_json_path = REFERENCE_DIR / f"{stem}_{reference_stem_suffix}_case.json"
    reference_json_path = REFERENCE_DIR / f"{stem}_{reference_stem_suffix}_reference.json"
    if not case_json_path.exists() or not reference_json_path.exists():
        print(f"  SKIPPED -- missing reference files for {stem}_{reference_stem_suffix}")
        return

    case = load_powercase_from_powermodels_json(case_json_path)
    reference = load_powermodels_ac_reference(reference_json_path)
    solve_start = time.time()
    own_result = solve_ac_opf_pyomo(case, cost_power_units="pu", voltage_bounds=None)
    own_solve_time_sec = time.time() - solve_start

    own_ok = bool(own_result.success)
    pm_ok = str(reference.get("termination_status", "")).upper() in (
        "OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED",
    ) or bool(reference.get("solved", False))
    pm_objective = reference.get("objective")
    obj_diff = (own_result.objective - float(pm_objective)) if (own_ok and pm_ok and pm_objective is not None) else float("nan")
    obj_rel_diff = (abs(obj_diff) / (1.0 + abs(float(pm_objective)))) if (own_ok and pm_ok and pm_objective is not None) else float("nan")

    append_summary_row(
        {
            "case": label,
            "scenario": scenario_label,
            "own_status": own_result.status,
            "pm_status": reference.get("termination_status", "UNKNOWN"),
            "outcome_match": own_ok == pm_ok,
            "own_objective": own_result.objective if own_ok else float("nan"),
            "pm_objective": pm_objective if pm_ok else float("nan"),
            "objective_diff": obj_diff,
            "objective_rel_diff": obj_rel_diff,
            "own_solve_time_sec": own_solve_time_sec,
            "pm_solve_time_sec": reference.get("solve_time_sec"),
            "own_solve_iterations": getattr(own_result, "solve_iterations", float("nan")),
            "pm_solve_iterations": reference.get("solve_iterations"),
        }
    )

    if not own_ok or not pm_ok:
        print(f"  SKIPPED (tables) -- own_ok={own_ok} pm_ok={pm_ok}")
        return

    gen_rows = compare_ac_opf_generators_with_powermodels(case, own_result, reference)
    bus_rows = compare_ac_opf_buses_with_powermodels(case, own_result, reference)
    branch_rows = compare_ac_opf_branches_with_powermodels(case, own_result, reference)

    out_dir = OUT_ROOT / label / scenario_label
    out_dir.mkdir(parents=True, exist_ok=True)
    gen_rows.to_csv(out_dir / "generators.csv", index=False)
    bus_rows.to_csv(out_dir / "buses.csv", index=False)
    branch_rows.to_csv(out_dir / "branches.csv", index=False)
    shunts_rows(case).to_csv(out_dir / "shunts.csv", index=False)
    constraints_inventory_rows(case).to_csv(out_dir / "constraints_inventory.csv", index=False)
    constraints_binding_rows(gen_rows, bus_rows, branch_rows).to_csv(out_dir / "constraints_binding.csv", index=False)
    print(f"  wrote 6 CSVs to {out_dir}")


# 1888_rte's two non-uniqueness spotlight scenarios (see
# julia/own_models/scripts/25_run_own_vs_powermodels_tables.jl's
# SPOTLIGHT_SCENARIOS and julia/tools/powermodels/scripts/
# 09_export_ac_opf_reference_spotlight_scenarios.jl for how the matching
# reference JSONs were produced).
SPOTLIGHT_SCENARIOS = [
    ("pglib_opf_case1888_rte", "1888_rte", "load_x0.8"),
    ("pglib_opf_case1888_rte", "1888_rte", "load_x1.2_reactive_stress"),
    ("pglib_opf_case1888_rte", "1888_rte", "qg_limits_x0.8"),
]


def main() -> None:
    # This script always reprocesses its full fixed scenario list (unlike
    # the checkpointed/resumable full-sweep scripts) -- start each run from
    # a clean summary file rather than appending. Appending across runs
    # whose row-dict schema had drifted over time previously caused a real
    # column-misalignment bug (pandas has no header validation on append).
    if SUMMARY_PATH.exists():
        SUMMARY_PATH.unlink()

    for stem, label in CASES:
        try:
            process_scenario(stem, label, "base", "base")
        except Exception as exc:  # noqa: BLE001 -- report and continue across cases
            print(f"  ERROR on {stem}: {exc}")

    for stem, label, scenario_label in SPOTLIGHT_SCENARIOS:
        try:
            process_scenario(stem, label, scenario_label, scenario_label)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR on {stem}/{scenario_label}: {exc}")


if __name__ == "__main__":
    main()
