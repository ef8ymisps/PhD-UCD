"""Completing the "both reference points feasible" argument for the
deviation-minimization rebuild (python_pm_like_zero side): evaluates the OWN
model's equality (power balance, Ohm's law) constraint formulas AT the own
model's OWN solution point x^{e*}, with zero re-optimization. Mirrors
script 47 (which does the same at PowerModels' x^{p*}) almost verbatim --
only the column prefix read from the raw per-scenario CSVs changes (own_*
instead of pm_*).

The inequality half (h^e(x^{e*})) needs NO new computation: the existing
pg_bounds.csv/qg_bounds.csv/vm_bounds.csv/thermal.csv (script 45's output)
already carry own_value/own_margin/own_violates_limit -- that IS
h^e(x^{e*}), already computed. This script only produces the equality side.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.powermodels_csv_adapter import load_powercase_from_powermodels_json  # noqa: E402
from own_models.src.network.admittance import calc_ac_branch_flows  # noqa: E402
from own_models.src.opf.ac.data import prepare_ac_opf_data  # noqa: E402

SOURCE_DIR = ROOT / "results" / "own_vs_powermodels" / "python_pm_like" / "1888_rte"
REFERENCE_DIR = ROOT / "results" / "reference" / "powermodels" / "ac_opf"
OUT_ROOT = (
    ROOT
    / "results"
    / "own_vs_powermodels"
    / "_organized_for_meeting"
    / "10_opf_solution_inspection"
    / "1888_rte_objective_gap"
    / "by_model_scenario"
)
MATERIAL_TOL = 1e-4
MODEL_VARIANT = "python_pm_like_zero"
SCENARIOS = ["load_x0.8", "load_x1.2_reactive_stress"]


def disk_path(path: Path) -> Path:
    if not path.is_absolute():
        path = path.resolve()
    if path.drive and not str(path).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(path))
    return path


def write_csv(df: pd.DataFrame, path: Path) -> None:
    disk_path(path.parent).mkdir(parents=True, exist_ok=True)
    df.to_csv(disk_path(path), index=False)


def read_own_point(scenario: str) -> dict:
    """Read the own model's OWN solution point x^{e*} straight off the
    already-written raw CSVs -- no re-solve, x^{e*} is already on disk."""
    base = SOURCE_DIR / scenario
    gens = pd.read_csv(base / "generators.csv")
    buses = pd.read_csv(base / "buses.csv")
    branches = pd.read_csv(base / "branches.csv")

    return {
        "own_pg": dict(zip(gens["gen_id"].astype(str), gens["own_pg"])),
        "own_qg": dict(zip(gens["gen_id"].astype(str), gens["own_qg"])),
        "own_vm": dict(zip(buses["bus_id"].astype(str), buses["own_vm"])),
        "own_va": dict(zip(buses["bus_id"].astype(str), buses["own_va"])),
        "own_pf": dict(zip(branches["branch_id"].astype(str), branches["own_pf"])),
        "own_qf": dict(zip(branches["branch_id"].astype(str), branches["own_qf"])),
        "own_pt": dict(zip(branches["branch_id"].astype(str), branches["own_pt"])),
        "own_qt": dict(zip(branches["branch_id"].astype(str), branches["own_qt"])),
    }


def power_balance_residual_rows(case, data, own) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Own model's own branch-flow arc-sum formula evaluated at x^{e*}'s own
    reported Pf*/Qf*/Pt*/Qt* directly."""
    arcs_from: dict[str, list[str]] = {bus.id: [] for bus in case.buses}
    arcs_to: dict[str, list[str]] = {bus.id: [] for bus in case.buses}
    for branch in data.branches:
        arcs_from[branch.from_bus].append(branch.id)
        arcs_to[branch.to_bus].append(branch.id)

    rows_p, rows_q = [], []
    for bus in case.buses:
        bus_id = bus.id
        pgen = sum(own["own_pg"].get(g, 0.0) for g in data.gens_at_bus[bus_id])
        qgen = sum(own["own_qg"].get(g, 0.0) for g in data.gens_at_bus[bus_id])

        own_lhs_p = sum(own["own_pf"].get(b, 0.0) for b in arcs_from[bus_id]) + sum(
            own["own_pt"].get(b, 0.0) for b in arcs_to[bus_id]
        )
        own_rhs_p = pgen - data.pd_by_bus[bus_id] - data.gs_by_bus[bus_id] * own["own_vm"][bus_id] ** 2
        residual_p = own_lhs_p - own_rhs_p
        rows_p.append(
            {
                "bus_id": bus_id,
                "own_lhs": own_lhs_p,
                "own_rhs": own_rhs_p,
                "residual": residual_p,
                "satisfied_1e4": abs(residual_p) <= MATERIAL_TOL,
            }
        )

        own_lhs_q = sum(own["own_qf"].get(b, 0.0) for b in arcs_from[bus_id]) + sum(
            own["own_qt"].get(b, 0.0) for b in arcs_to[bus_id]
        )
        own_rhs_q = qgen - data.qd_by_bus[bus_id] + data.bs_by_bus[bus_id] * own["own_vm"][bus_id] ** 2
        residual_q = own_lhs_q - own_rhs_q
        rows_q.append(
            {
                "bus_id": bus_id,
                "own_lhs": own_lhs_q,
                "own_rhs": own_rhs_q,
                "residual": residual_q,
                "satisfied_1e4": abs(residual_q) <= MATERIAL_TOL,
            }
        )
    return pd.DataFrame(rows_p), pd.DataFrame(rows_q)


def ohms_consistency_tables(case, own) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Recompute Pf/Qf/Pt/Qt from x^{e*}'s own Vm*/Va* via the already-existing
    `calc_ac_branch_flows` and diff against the own model's own reported flow."""
    vm = np.array([own["own_vm"][bus.id] for bus in case.buses])
    va = np.array([own["own_va"][bus.id] for bus in case.buses])
    flows = calc_ac_branch_flows(case, vm, va)

    pf_rows, qf_rows, pt_rows, qt_rows = [], [], [], []
    for branch in case.branches:
        if branch.status == 0 or branch.id not in flows:
            continue
        calc = flows[branch.id]
        own_pf = own["own_pf"].get(branch.id, float("nan"))
        own_qf = own["own_qf"].get(branch.id, float("nan"))
        own_pt = own["own_pt"].get(branch.id, float("nan"))
        own_qt = own["own_qt"].get(branch.id, float("nan"))

        pf_rows.append(
            {
                "branch_id": branch.id,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "own_value": own_pf,
                "calc_value": calc["pf"],
                "residual": calc["pf"] - own_pf,
                "satisfied_1e4": abs(calc["pf"] - own_pf) <= MATERIAL_TOL,
            }
        )
        qf_rows.append(
            {
                "branch_id": branch.id,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "own_value": own_qf,
                "calc_value": calc["qf"],
                "residual": calc["qf"] - own_qf,
                "satisfied_1e4": abs(calc["qf"] - own_qf) <= MATERIAL_TOL,
            }
        )
        pt_rows.append(
            {
                "branch_id": branch.id,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "own_value": own_pt,
                "calc_value": calc["pt"],
                "residual": calc["pt"] - own_pt,
                "satisfied_1e4": abs(calc["pt"] - own_pt) <= MATERIAL_TOL,
            }
        )
        qt_rows.append(
            {
                "branch_id": branch.id,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "own_value": own_qt,
                "calc_value": calc["qt"],
                "residual": calc["qt"] - own_qt,
                "satisfied_1e4": abs(calc["qt"] - own_qt) <= MATERIAL_TOL,
            }
        )
    return pd.DataFrame(pf_rows), pd.DataFrame(qf_rows), pd.DataFrame(pt_rows), pd.DataFrame(qt_rows)


def process_scenario(scenario: str) -> None:
    case_json = REFERENCE_DIR / f"pglib_opf_case1888_rte_{scenario}_case.json"
    case = load_powercase_from_powermodels_json(case_json)
    data = prepare_ac_opf_data(case)
    own = read_own_point(scenario)

    pb_p, pb_q = power_balance_residual_rows(case, data, own)
    pf_df, qf_df, pt_df, qt_df = ohms_consistency_tables(case, own)

    out_dir = OUT_ROOT / MODEL_VARIANT / scenario / "at_own_point"
    write_csv(pb_p, out_dir / "power_balance_p.csv")
    write_csv(pb_q, out_dir / "power_balance_q.csv")
    write_csv(pf_df, out_dir / "pf_ohms_at_own_point.csv")
    write_csv(qf_df, out_dir / "qf_ohms_at_own_point.csv")
    write_csv(pt_df, out_dir / "pt_ohms_at_own_point.csv")
    write_csv(qt_df, out_dir / "qt_ohms_at_own_point.csv")
    write_csv(
        pd.DataFrame([{"model_variant": MODEL_VARIANT, "case": "1888_rte", "scenario": scenario, "formulation": "branch_flow", "status": "included"}]),
        out_dir / "at_own_point_manifest.csv",
    )

    max_pb = max(pb_p["residual"].abs().max(), pb_q["residual"].abs().max()) if not pb_p.empty else float("nan")
    print(f"  wrote at_own_point tables for {MODEL_VARIANT}/{scenario} -- max |power balance residual| = {max_pb}")


def main() -> None:
    print("Completing the feasibility argument: g^e(x^e*) residuals at the own model's own point (python_pm_like_zero)")
    for scenario in SCENARIOS:
        try:
            process_scenario(scenario)
        except Exception as exc:  # noqa: BLE001 -- keep going across scenarios, same pattern as script 47
            print(f"  ERROR on {MODEL_VARIANT}/{scenario}: {exc}")
            raise


if __name__ == "__main__":
    main()
