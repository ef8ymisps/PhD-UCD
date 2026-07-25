"""Q3-Q6 plug-in-point evaluation for python_pm_like_zero.

Evaluates the OWN model's equality (power balance, Ohm's law) and
inequality constraint formulas AT PowerModels' solution point x^{p*}, with
zero re-optimization. This answers a different question than
`45_build_opf_solution_inspection_tables.py`, which only compares two
INDEPENDENTLY-SOLVED optimal points (own's own re-solve vs PowerModels' own
solve) and cannot tell you whether the own model's own equations are
satisfied at PowerModels' point without re-solving anything.

Mirrors julia/own_models/scripts/41_run_opf_constraint_residuals_at_pm_point.jl.
Output goes into a new at_pm_point/ subfolder nested inside script 45's
existing per-scenario folders -- never mixed into script 45's own files
(which it fully overwrites on every run).
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


def read_out_root_csv(path: Path) -> pd.DataFrame | None:
    dp = disk_path(path)
    if not dp.exists():
        return None
    try:
        return pd.read_csv(dp)
    except pd.errors.EmptyDataError:
        return None


def read_pm_point(scenario: str) -> dict:
    """Read PowerModels' solution point x^{p*} straight off the
    already-written raw CSVs -- no re-solve of anything, PowerModels' point
    is already on disk."""
    base = SOURCE_DIR / scenario
    gens = pd.read_csv(base / "generators.csv")
    buses = pd.read_csv(base / "buses.csv")
    branches = pd.read_csv(base / "branches.csv")

    return {
        "pm_pg": dict(zip(gens["gen_id"].astype(str), gens["pm_pg"])),
        "pm_qg": dict(zip(gens["gen_id"].astype(str), gens["pm_qg"])),
        "pm_vm": dict(zip(buses["bus_id"].astype(str), buses["pm_vm"])),
        "pm_va": dict(zip(buses["bus_id"].astype(str), buses["pm_va"])),
        "pm_pf": dict(zip(branches["branch_id"].astype(str), branches["pm_pf"])),
        "pm_qf": dict(zip(branches["branch_id"].astype(str), branches["pm_qf"])),
        "pm_pt": dict(zip(branches["branch_id"].astype(str), branches["pm_pt"])),
        "pm_qt": dict(zip(branches["branch_id"].astype(str), branches["pm_qt"])),
        "branches_raw": branches,
    }


def power_balance_residual_rows(case, data, pm) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Own model's own branch-flow arc-sum formula
    (`_add_pm_like_power_balance_constraints`, opf/ac/powermodels_like.py)
    evaluated at PM's own reported Pf*/Qf*/Pt*/Qt* directly (no Ohm's-law
    recompute needed for this residual -- that's the separate
    Ohm's-law-consistency check below)."""
    arcs_from: dict[str, list[str]] = {bus.id: [] for bus in case.buses}
    arcs_to: dict[str, list[str]] = {bus.id: [] for bus in case.buses}
    for branch in data.branches:
        arcs_from[branch.from_bus].append(branch.id)
        arcs_to[branch.to_bus].append(branch.id)

    rows_p, rows_q = [], []
    for bus in case.buses:
        bus_id = bus.id
        pgen = sum(pm["pm_pg"].get(g, 0.0) for g in data.gens_at_bus[bus_id])
        qgen = sum(pm["pm_qg"].get(g, 0.0) for g in data.gens_at_bus[bus_id])

        own_lhs_p = sum(pm["pm_pf"].get(b, 0.0) for b in arcs_from[bus_id]) + sum(
            pm["pm_pt"].get(b, 0.0) for b in arcs_to[bus_id]
        )
        own_rhs_p = pgen - data.pd_by_bus[bus_id] - data.gs_by_bus[bus_id] * pm["pm_vm"][bus_id] ** 2
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

        own_lhs_q = sum(pm["pm_qf"].get(b, 0.0) for b in arcs_from[bus_id]) + sum(
            pm["pm_qt"].get(b, 0.0) for b in arcs_to[bus_id]
        )
        own_rhs_q = qgen - data.qd_by_bus[bus_id] + data.bs_by_bus[bus_id] * pm["pm_vm"][bus_id] ** 2
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


def ohms_consistency_tables(case, pm) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Recompute Pf/Qf/Pt/Qt from PM's own Vm*/Va* via the already-existing
    `calc_ac_branch_flows` (network/admittance.py) and diff against PM's own
    reported flow. Zero new source code needed -- this function already does
    exactly this recompute."""
    vm = np.array([pm["pm_vm"][bus.id] for bus in case.buses])
    va = np.array([pm["pm_va"][bus.id] for bus in case.buses])
    flows = calc_ac_branch_flows(case, vm, va)

    pf_rows, qf_rows, pt_rows, qt_rows = [], [], [], []
    for branch in case.branches:
        if branch.status == 0 or branch.id not in flows:
            continue
        calc = flows[branch.id]
        pm_pf = pm["pm_pf"].get(branch.id, float("nan"))
        pm_qf = pm["pm_qf"].get(branch.id, float("nan"))
        pm_pt = pm["pm_pt"].get(branch.id, float("nan"))
        pm_qt = pm["pm_qt"].get(branch.id, float("nan"))

        pf_rows.append(
            {
                "branch_id": branch.id,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "pm_value": pm_pf,
                "calc_value": calc["pf"],
                "residual": calc["pf"] - pm_pf,
                "satisfied_1e4": abs(calc["pf"] - pm_pf) <= MATERIAL_TOL,
            }
        )
        qf_rows.append(
            {
                "branch_id": branch.id,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "pm_value": pm_qf,
                "calc_value": calc["qf"],
                "residual": calc["qf"] - pm_qf,
                "satisfied_1e4": abs(calc["qf"] - pm_qf) <= MATERIAL_TOL,
            }
        )
        pt_rows.append(
            {
                "branch_id": branch.id,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "pm_value": pm_pt,
                "calc_value": calc["pt"],
                "residual": calc["pt"] - pm_pt,
                "satisfied_1e4": abs(calc["pt"] - pm_pt) <= MATERIAL_TOL,
            }
        )
        qt_rows.append(
            {
                "branch_id": branch.id,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "pm_value": pm_qt,
                "calc_value": calc["qt"],
                "residual": calc["qt"] - pm_qt,
                "satisfied_1e4": abs(calc["qt"] - pm_qt) <= MATERIAL_TOL,
            }
        )
    return pd.DataFrame(pf_rows), pd.DataFrame(qf_rows), pd.DataFrame(pt_rows), pd.DataFrame(qt_rows)


def thermal_at_pm_point_table(
    scenario: str,
    branches_raw: pd.DataFrame,
    pf_df: pd.DataFrame,
    qf_df: pd.DataFrame,
    pt_df: pd.DataFrame,
    qt_df: pd.DataFrame,
) -> pd.DataFrame:
    """Genuine new computation: script 45's existing `thermal.csv` `pm_value`
    is PM's own reported apparent power, not the own model's Ohm's-law
    formula evaluated at PM's Vm*/Va*. Derives Sf/St from the Ohm's-law
    recompute above, joins rate_a from the raw branches.csv, and diffs
    against script 45's existing own_margin (own model at x^{e*})."""
    existing = read_out_root_csv(OUT_ROOT / MODEL_VARIANT / scenario / "thermal.csv")

    # Python's raw branches.csv (unlike Julia's) has no from_bus/to_bus
    # columns -- pull them from pf_df instead, which already carries them
    # (sourced from case.branches in ohms_consistency_tables).
    rate_by_branch = dict(zip(branches_raw["branch_id"].astype(str), branches_raw["rate_a"]))
    from_bus_by_branch = dict(zip(pf_df["branch_id"].astype(str), pf_df["from_bus"].astype(str)))
    to_bus_by_branch = dict(zip(pf_df["branch_id"].astype(str), pf_df["to_bus"].astype(str)))

    calc_pf = dict(zip(pf_df["branch_id"].astype(str), pf_df["calc_value"]))
    calc_qf = dict(zip(qf_df["branch_id"].astype(str), qf_df["calc_value"]))
    calc_pt = dict(zip(pt_df["branch_id"].astype(str), pt_df["calc_value"]))
    calc_qt = dict(zip(qt_df["branch_id"].astype(str), qt_df["calc_value"]))

    own_margin_at_own_from: dict[str, float] = {}
    own_margin_at_own_to: dict[str, float] = {}
    if existing is not None:
        for _, r in existing.iterrows():
            bid = str(r["branch_id"])
            if r["constraint_type"] == "thermal_from":
                own_margin_at_own_from[bid] = r["own_margin"]
            elif r["constraint_type"] == "thermal_to":
                own_margin_at_own_to[bid] = r["own_margin"]

    rows = []
    for bid, rate_a in rate_by_branch.items():
        if rate_a <= 0.0 or bid not in calc_pf:
            continue
        cf = float(np.hypot(calc_pf[bid], calc_qf[bid]))
        own_margin_at_pm_f = rate_a - cf
        own_margin_at_own_f = own_margin_at_own_from.get(bid, float("nan"))
        rows.append(
            {
                "constraint_type": "thermal_from",
                "branch_id": bid,
                "from_bus": from_bus_by_branch[bid],
                "to_bus": to_bus_by_branch[bid],
                "calc_value": cf,
                "own_margin_at_own": own_margin_at_own_f,
                "own_margin_at_pm": own_margin_at_pm_f,
                "margin_gap": own_margin_at_pm_f - own_margin_at_own_f,
                "calc_violates_limit": own_margin_at_pm_f < -MATERIAL_TOL,
            }
        )

        ct = float(np.hypot(calc_pt[bid], calc_qt[bid]))
        own_margin_at_pm_t = rate_a - ct
        own_margin_at_own_t = own_margin_at_own_to.get(bid, float("nan"))
        rows.append(
            {
                "constraint_type": "thermal_to",
                "branch_id": bid,
                "from_bus": from_bus_by_branch[bid],
                "to_bus": to_bus_by_branch[bid],
                "calc_value": ct,
                "own_margin_at_own": own_margin_at_own_t,
                "own_margin_at_pm": own_margin_at_pm_t,
                "margin_gap": own_margin_at_pm_t - own_margin_at_own_t,
                "calc_violates_limit": own_margin_at_pm_t < -MATERIAL_TOL,
            }
        )
    return pd.DataFrame(rows)


def reprojected_bound_table(scenario: str, family: str) -> pd.DataFrame:
    """Re-project script 45's existing pg_bounds/qg_bounds/vm_bounds/
    angle_difference CSVs into simply-named at_pm_point files. For these
    four families, PowerModels' own reported value combined with the SHARED
    bound parameters already IS the own model's bound formula evaluated at
    x^{p*}: `pm_margin` there is not new computation, just a rename/
    re-projection for a dedicated, easy-to-find file."""
    df = read_out_root_csv(OUT_ROOT / MODEL_VARIANT / scenario / f"{family}.csv")
    if df is None or df.empty:
        return pd.DataFrame()

    if family in ("pg_bounds", "qg_bounds"):
        id_cols = ["gen_id", "bus_id"]
    elif family == "vm_bounds":
        id_cols = ["bus_id"]
    else:  # angle_difference
        id_cols = ["branch_id", "from_bus", "to_bus"]

    out = df[["constraint_type"] + id_cols].copy()
    out["own_margin_at_own"] = df["own_margin"]
    out["own_margin_at_pm"] = df["pm_margin"]
    out["margin_gap"] = df["pm_margin"] - df["own_margin"]
    out["pm_violates_limit"] = df["pm_violates_limit"]
    return out


def process_scenario(scenario: str) -> None:
    case_json = REFERENCE_DIR / f"pglib_opf_case1888_rte_{scenario}_case.json"
    case = load_powercase_from_powermodels_json(case_json)
    data = prepare_ac_opf_data(case)
    pm = read_pm_point(scenario)

    pb_p, pb_q = power_balance_residual_rows(case, data, pm)
    pf_df, qf_df, pt_df, qt_df = ohms_consistency_tables(case, pm)
    thermal_df = thermal_at_pm_point_table(scenario, pm["branches_raw"], pf_df, qf_df, pt_df, qt_df)

    pg_df = reprojected_bound_table(scenario, "pg_bounds")
    qg_df = reprojected_bound_table(scenario, "qg_bounds")
    vm_df = reprojected_bound_table(scenario, "vm_bounds")
    angle_df = reprojected_bound_table(scenario, "angle_difference")

    out_dir = OUT_ROOT / MODEL_VARIANT / scenario / "at_pm_point"
    write_csv(pb_p, out_dir / "power_balance_p.csv")
    write_csv(pb_q, out_dir / "power_balance_q.csv")
    write_csv(pf_df, out_dir / "pf_ohms_at_pm_point.csv")
    write_csv(qf_df, out_dir / "qf_ohms_at_pm_point.csv")
    write_csv(pt_df, out_dir / "pt_ohms_at_pm_point.csv")
    write_csv(qt_df, out_dir / "qt_ohms_at_pm_point.csv")
    write_csv(pg_df, out_dir / "pg_bounds_at_pm_point.csv")
    write_csv(qg_df, out_dir / "qg_bounds_at_pm_point.csv")
    write_csv(vm_df, out_dir / "vm_bounds_at_pm_point.csv")
    write_csv(angle_df, out_dir / "angle_difference_at_pm_point.csv")
    write_csv(thermal_df, out_dir / "thermal_at_pm_point.csv")
    write_csv(
        pd.DataFrame([{"model_variant": MODEL_VARIANT, "case": "1888_rte", "scenario": scenario, "formulation": "branch_flow", "status": "included"}]),
        out_dir / "at_pm_point_manifest.csv",
    )

    max_pb = max(pb_p["residual"].abs().max(), pb_q["residual"].abs().max()) if not pb_p.empty else float("nan")
    print(f"  wrote at_pm_point tables for {MODEL_VARIANT}/{scenario} -- max |power balance residual| = {max_pb}")


def main() -> None:
    print("Q3-Q6 plug-in-point residuals at PowerModels' solution (python_pm_like_zero)")
    for scenario in SCENARIOS:
        try:
            process_scenario(scenario)
        except Exception as exc:  # noqa: BLE001 -- keep going across scenarios, same pattern as script 45/40
            print(f"  ERROR on {MODEL_VARIANT}/{scenario}: {exc}")
            raise


if __name__ == "__main__":
    main()
