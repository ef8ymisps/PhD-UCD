"""Diagnostic (python_pm_like_zero side): with thermal (flow) constraints
removed on both sides and the REAL PGLib generation-cost objective kept
unchanged, compare own model's AC-OPF objective vs PowerModels' AC-OPF
objective, and check g^e(x) = 0 at both own's own solution and at
PowerModels' (thermal-free) solution.

Mirrors julia/own_models/scripts/45_run_no_thermal_equality_comparison.jl.
PowerModels' thermal-free AC-OPF was already solved there (Python has no
direct PowerModels.jl interop) -- this script reads its exported solution
state (`no_thermal_pm_solution_state_1888_rte.csv`) and objective
(`no_thermal_equality_comparison_julia.csv`) rather than re-solving PM.

Scope: python_pm_like_zero x {load_x0.8, load_x1.2_reactive_stress} only --
qg_limits_x0.6 has no Python solve in this investigation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(PYTHON_DIR))


def _ensure_conda_solver_path() -> None:
    # Ipopt (installed via conda-forge) lives in <env>/Library/bin on
    # Windows; Pyomo's SolverFactory won't find it unless that's on PATH.
    # Same fix already used by script 34's ensure_conda_solver_path().
    env_root = Path(sys.executable).resolve().parent
    library_bin = env_root / "Library" / "bin"
    if library_bin.exists():
        os.environ["PATH"] = str(library_bin) + os.pathsep + os.environ.get("PATH", "")


_ensure_conda_solver_path()

from own_models.src.adapters.powermodels_csv_adapter import load_powercase_from_powermodels_json  # noqa: E402
from own_models.src.network.admittance import calc_ac_branch_flows  # noqa: E402
from own_models.src.opf.ac.data import prepare_ac_opf_data  # noqa: E402
from own_models.src.opf.ac.model import solve_ac_opf_pyomo  # noqa: E402

REFERENCE_DIR = ROOT / "results" / "reference" / "powermodels" / "ac_opf"
JULIA_OUT_ROOT = (
    ROOT
    / "results"
    / "own_vs_powermodels"
    / "_organized_for_meeting"
    / "10_opf_solution_inspection"
    / "1888_rte_objective_gap"
)
MODEL_VARIANT = "python_pm_like_zero"
SCENARIOS = ["load_x0.8", "load_x1.2_reactive_stress"]
MATERIAL_TOL = 1e-4


def disk_path(path: Path) -> Path:
    if not path.is_absolute():
        path = path.resolve()
    if path.drive and not str(path).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(path))
    return path


def read_pm_state(scenario: str) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, float]]:
    path = disk_path(JULIA_OUT_ROOT / "no_thermal_pm_solution_state_1888_rte.csv")
    df = pd.read_csv(path)
    sub = df[df["scenario"] == scenario]
    buses = sub[sub["element_type"] == "bus"]
    gens = sub[sub["element_type"] == "gen"]
    vm = dict(zip(buses["element_id"].astype(str), buses["vm"]))
    va = dict(zip(buses["element_id"].astype(str), buses["va"]))
    pg = dict(zip(gens["element_id"].astype(str), gens["pg"]))
    qg = dict(zip(gens["element_id"].astype(str), gens["qg"]))
    return pg, qg, vm, va


def read_pm_objective(scenario: str) -> float:
    path = disk_path(JULIA_OUT_ROOT / "no_thermal_equality_comparison_julia.csv")
    df = pd.read_csv(path)
    sub = df[df["scenario"] == scenario]
    return float(sub.iloc[0]["pm_objective"])


def power_balance_residual_branch_flow(case, data, pm_like_data, pg: dict, qg: dict, vm: dict, va: dict) -> tuple[float, float]:
    vm_arr = np.array([vm[bus.id] for bus in case.buses])
    va_arr = np.array([va[bus.id] for bus in case.buses])
    flows = calc_ac_branch_flows(case, vm_arr, va_arr)

    max_abs_p = 0.0
    max_abs_q = 0.0
    for bus in case.buses:
        bus_id = bus.id
        pgen = sum(pg.get(g, 0.0) for g in data.gens_at_bus[bus_id])
        qgen = sum(qg.get(g, 0.0) for g in data.gens_at_bus[bus_id])

        own_lhs_p = 0.0
        own_lhs_q = 0.0
        for arc in pm_like_data.bus_arcs[bus_id]:
            branch_id, arc_from, arc_to = arc
            if branch_id not in flows:
                continue
            f = flows[branch_id]
            is_from_arc = pm_like_data.arcs_from[branch_id] == arc
            own_lhs_p += f["pf"] if is_from_arc else f["pt"]
            own_lhs_q += f["qf"] if is_from_arc else f["qt"]

        own_rhs_p = pgen - data.pd_by_bus[bus_id] - data.gs_by_bus[bus_id] * vm[bus_id] ** 2
        own_rhs_q = qgen - data.qd_by_bus[bus_id] + data.bs_by_bus[bus_id] * vm[bus_id] ** 2
        max_abs_p = max(max_abs_p, abs(own_lhs_p - own_rhs_p))
        max_abs_q = max(max_abs_q, abs(own_lhs_q - own_rhs_q))
    return max_abs_p, max_abs_q


def own_state_rows(scenario: str, own_result, case) -> list[dict]:
    """Per-element Vm/Va/Pg/Qg from own model's own thermal-free solution --
    for the Q10 own-vs-PM comparison tables (mirrors Q1/Q2's pg.csv/vm.csv
    style), not previously exported (only aggregate g(x) stats were)."""
    rows: list[dict] = []
    for bus in case.buses:
        rows.append(
            {
                "model_variant": MODEL_VARIANT,
                "scenario": scenario,
                "element_type": "bus",
                "element_id": bus.id,
                "vm": own_result.vm.get(bus.id, float("nan")),
                "va": own_result.va.get(bus.id, float("nan")),
                "pg": float("nan"),
                "qg": float("nan"),
            }
        )
    for gen in case.generators:
        if gen.id not in own_result.pg:
            continue
        rows.append(
            {
                "model_variant": MODEL_VARIANT,
                "scenario": scenario,
                "element_type": "gen",
                "element_id": gen.id,
                "vm": float("nan"),
                "va": float("nan"),
                "pg": own_result.pg[gen.id],
                "qg": own_result.qg[gen.id],
            }
        )
    return rows


def process_scenario(scenario: str) -> dict:
    from own_models.src.opf.ac.model import build_ac_arc_data

    case_json = REFERENCE_DIR / f"pglib_opf_case1888_rte_{scenario}_case.json"
    case = load_powercase_from_powermodels_json(case_json)
    data = prepare_ac_opf_data(case, voltage_bounds=None)
    pm_like_data = build_ac_arc_data(case, voltage_bounds=None)

    # voltage_bounds must be None (use the case's own vmin/vmax, matching
    # Julia's solve_ac_opf_own_powermodels_like default) -- the Pyomo
    # function's own default is a hardcoded (0.95, 1.05), tighter than
    # PGLib's actual case bounds (e.g. vmax ~1.105 seen elsewhere in this
    # investigation), which was making this thermal-free problem genuinely
    # infeasible under an artificial restriction Julia's solve never had.
    # cost_power_units="pu" -- the function's own default is "mw" (Pg*baseMVA
    # before applying cost coefficients), which does NOT match the
    # convention used everywhere else in this investigation (PowerModels'
    # own convention, no baseMVA scaling -- confirmed by cross-checking a
    # known-good thermal-ON solve: raw result.objective was exactly 100x
    # (=base_mva) the value already stored in summary.csv).
    own_result = solve_ac_opf_pyomo(
        case, objective_type="generation_cost", cost_power_units="pu", warm_start="case", include_branch_thermal=False, voltage_bounds=None,
    )
    own_ok = own_result.status.lower() in ("optimal", "locally_solved", "solved_feasible", "almost_optimal", "almost_locally_solved")
    own_objective = own_result.objective if own_ok else float("nan")

    pm_pg, pm_qg, pm_vm, pm_va = read_pm_state(scenario)
    pm_objective = read_pm_objective(scenario)

    max_p_at_own, max_q_at_own = power_balance_residual_branch_flow(case, data, pm_like_data, own_result.pg, own_result.qg, own_result.vm, own_result.va)
    max_p_at_pm, max_q_at_pm = power_balance_residual_branch_flow(case, data, pm_like_data, pm_pg, pm_qg, pm_vm, pm_va)

    gap_abs = own_objective - pm_objective
    gap_pct = 100.0 * gap_abs / pm_objective

    row = {
        "model_variant": MODEL_VARIANT,
        "scenario": scenario,
        "own_objective": own_objective,
        "own_status": own_result.status,
        "pm_objective": pm_objective,
        "own_vs_pm_gap_abs": gap_abs,
        "own_vs_pm_gap_pct": gap_pct,
        "own_cheaper_than_pm": gap_abs < 0.0,
        "max_abs_g_own_at_own_p": max_p_at_own,
        "max_abs_g_own_at_own_q": max_q_at_own,
        "g_own_at_own_satisfied_1e4": max(max_p_at_own, max_q_at_own) <= MATERIAL_TOL,
        "max_abs_g_own_at_pm_p": max_p_at_pm,
        "max_abs_g_own_at_pm_q": max_q_at_pm,
        "g_own_at_pm_satisfied_1e4": max(max_p_at_pm, max_q_at_pm) <= MATERIAL_TOL,
    }
    return row, own_state_rows(scenario, own_result, case)


def main() -> None:
    print("Diagnostic (python_pm_like_zero): thermal removed both sides, real cost kept")
    rows = []
    own_rows: list[dict] = []
    for scenario in SCENARIOS:
        row, own_rows_for_scenario = process_scenario(scenario)
        rows.append(row)
        own_rows.extend(own_rows_for_scenario)
        print(
            f"  {MODEL_VARIANT}/{scenario}: own={row['own_objective']} pm={row['pm_objective']} "
            f"gap_pct={row['own_vs_pm_gap_pct']} | g_own_at_own(P,Q)=({row['max_abs_g_own_at_own_p']},{row['max_abs_g_own_at_own_q']}) "
            f"| g_own_at_pm(P,Q)=({row['max_abs_g_own_at_pm_p']},{row['max_abs_g_own_at_pm_q']})"
        )

    df = pd.DataFrame(rows)
    out_path = JULIA_OUT_ROOT / "no_thermal_equality_comparison_python.csv"
    disk_path(out_path.parent).mkdir(parents=True, exist_ok=True)
    df.to_csv(disk_path(out_path), index=False)
    print(f"Wrote: {out_path}")

    own_state_path = JULIA_OUT_ROOT / "no_thermal_own_solution_state_1888_rte_python.csv"
    pd.DataFrame(own_rows).to_csv(disk_path(own_state_path), index=False)
    print(f"Wrote: {own_state_path}")


if __name__ == "__main__":
    main()
