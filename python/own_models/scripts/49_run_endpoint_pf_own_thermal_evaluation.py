"""Q7/Q8 follow-up: evaluate the OWN model's own thermal inequality formula
(h^e_thermal, the same Ohm's-law-based Sf/St computation used in the Q3-Q6
work, `calc_ac_branch_flows`) AT the post-load-flow operating point, for
both PF-seed endpoints (own's OPF solution and PowerModels' OPF solution).

The existing `python_endpoint_pypowsybl_lf_inequality_levels.csv` already
checks value-vs-bound post-PF, but for thermal it always uses OpenLoadFlow's
own recomputed branch flow -- it never evaluates the own model's own
Ohm's-law formula there. This script reuses the ALREADY-COMPUTED post-PF
Vm/Va (`python_endpoint_pypowsybl_lf_variable_comparison.csv`) -- no load
flow is re-run here, this is a pure plug-in-point evaluation at an
already-converged post-PF state. Mirrors
julia/own_models/scripts/44_run_endpoint_pf_own_thermal_evaluation.jl.

Scope: python_pm_like_zero x {load_x0.8, load_x1.2_reactive_stress} only --
qg_limits_x0.6 has no Python solve in this investigation.
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

REFERENCE_DIR = ROOT / "results" / "reference" / "powermodels" / "ac_opf"
RESULTS_ROOT = ROOT / "results" / "own_vs_powermodels"
OUT_ROOT = (
    RESULTS_ROOT
    / "_organized_for_meeting"
    / "10_opf_solution_inspection"
    / "1888_rte_objective_gap"
)
MATERIAL_TOL = 1e-4
MODEL_VARIANT = "python_pm_like_zero"
SCENARIOS = ["load_x0.8", "load_x1.2_reactive_stress"]
ENDPOINTS = ["own_opf_solution", "powermodels_opf_solution"]


def disk_path(path: Path) -> Path:
    if not path.is_absolute():
        path = path.resolve()
    if path.drive and not str(path).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(path))
    return path


def read_post_pf_voltage_state(scenario: str, endpoint_source: str) -> tuple[dict[str, float], dict[str, float]]:
    path = disk_path(RESULTS_ROOT / "python_endpoint_pypowsybl_lf_variable_comparison.csv")
    df = pd.read_csv(path)
    sub = df[
        (df["model_variant"] == MODEL_VARIANT)
        & (df["scenario"] == scenario)
        & (df["endpoint_source"] == endpoint_source)
    ]
    vm = dict(zip(sub[sub["variable"] == "vm"]["element_id"].astype(str), sub[sub["variable"] == "vm"]["pypowsybl_lf_value"]))
    va = dict(zip(sub[sub["variable"] == "va"]["element_id"].astype(str), sub[sub["variable"] == "va"]["pypowsybl_lf_value"]))
    if not vm or not va:
        raise ValueError(f"No post-PF vm/va rows found for {MODEL_VARIANT}/{scenario}/{endpoint_source}")
    return vm, va


def read_pm_engine_thermal(scenario: str, endpoint_source: str) -> dict[str, dict[str, float]]:
    """Squared apparent power (pu_squared) from OpenLoadFlow's own post-PF
    branch flow, per branch/side -- sqrt'd by the caller for a like-for-like
    comparison against this script's own (non-squared) calc_sf/calc_st."""
    path = disk_path(RESULTS_ROOT / "python_endpoint_pypowsybl_lf_inequality_levels.csv")
    df = pd.read_csv(path, low_memory=False)
    sub = df[
        (df["model_variant"] == MODEL_VARIANT)
        & (df["scenario"] == scenario)
        & (df["endpoint_source"] == endpoint_source)
        & (df["constraint_type"].isin(["thermal_from", "thermal_to"]))
    ]
    result: dict[str, dict[str, float]] = {}
    for _, r in sub.iterrows():
        bid = str(r["element_id"])
        result.setdefault(bid, {})
        squared = r["lf_value"]
        side = "sf" if r["constraint_type"] == "thermal_from" else "st"
        result[bid][side] = float(np.sqrt(squared)) if pd.notna(squared) and squared >= 0 else float("nan")
    return result


def process_scenario(scenario: str) -> list[dict]:
    case_json = REFERENCE_DIR / f"pglib_opf_case1888_rte_{scenario}_case.json"
    case = load_powercase_from_powermodels_json(case_json)

    rate_by_branch = {b.id: float(b.rate_a) for b in case.branches if b.status != 0}
    from_bus_by_branch = {b.id: b.from_bus for b in case.branches if b.status != 0}
    to_bus_by_branch = {b.id: b.to_bus for b in case.branches if b.status != 0}

    rows: list[dict] = []
    for endpoint_source in ENDPOINTS:
        vm_dict, va_dict = read_post_pf_voltage_state(scenario, endpoint_source)
        pm_engine_thermal = read_pm_engine_thermal(scenario, endpoint_source)

        vm = np.array([vm_dict[bus.id] for bus in case.buses])
        va = np.array([va_dict[bus.id] for bus in case.buses])
        flows = calc_ac_branch_flows(case, vm, va)

        for branch_id, rate_a in rate_by_branch.items():
            if rate_a <= 0.0 or branch_id not in flows:
                continue
            f = flows[branch_id]
            calc_sf = float(np.hypot(f["pf"], f["qf"]))
            calc_st = float(np.hypot(f["pt"], f["qt"]))
            calc_value = max(calc_sf, calc_st)
            own_thermal_margin = rate_a - calc_value

            engine = pm_engine_thermal.get(branch_id, {})
            pm_engine_sf = engine.get("sf", float("nan"))
            pm_engine_st = engine.get("st", float("nan"))
            pm_engine_value = max(v for v in (pm_engine_sf, pm_engine_st) if pd.notna(v)) if (pd.notna(pm_engine_sf) or pd.notna(pm_engine_st)) else float("nan")

            # IMPORTANT: unlike the Julia script, this is NOT PowerModels.jl's
            # own thermal formula -- the Python endpoint-PF pipeline never
            # calls PowerModels.jl at all, only OpenLoadFlow (both for the PF
            # solve and the post-PF branch-flow recompute). pm_engine_value
            # here is OpenLoadFlow's own recomputed apparent power; labeling
            # it "PowerModels'" would be inaccurate, so it's flagged as such.
            pm_engine_margin = rate_a - pm_engine_value if pd.notna(pm_engine_value) else float("nan")
            pm_engine_violates_limit = pd.notna(pm_engine_margin) and pm_engine_margin < -MATERIAL_TOL

            rows.append(
                {
                    "model_variant": MODEL_VARIANT,
                    "scenario": scenario,
                    "endpoint_source": endpoint_source,
                    "branch_id": branch_id,
                    "from_bus": from_bus_by_branch[branch_id],
                    "to_bus": to_bus_by_branch[branch_id],
                    "rate_a": rate_a,
                    "calc_sf": calc_sf,
                    "calc_st": calc_st,
                    "calc_value": calc_value,
                    "own_thermal_margin": own_thermal_margin,
                    "own_thermal_violates_limit": own_thermal_margin < -MATERIAL_TOL,
                    "pm_engine_name": "OpenLoadFlow branch flow (NOT PowerModels.jl -- not run in this pipeline)",
                    "pm_engine_value": pm_engine_value,
                    "pm_engine_margin": pm_engine_margin,
                    "pm_engine_violates_limit": pm_engine_violates_limit,
                    "ohms_consistency_residual": calc_value - pm_engine_value,
                    "ohms_consistency_satisfied_1e4": abs(calc_value - pm_engine_value) <= MATERIAL_TOL if pd.notna(pm_engine_value) else False,
                }
            )
    return rows


def main() -> None:
    print("Q7/Q8 follow-up: own model's h^e_thermal at post-PF operating points (python_pm_like_zero)")
    all_rows: list[dict] = []
    for scenario in SCENARIOS:
        rows = process_scenario(scenario)
        all_rows.extend(rows)
        n_violations = sum(1 for r in rows if r["own_thermal_violates_limit"])
        n_pm_engine_violations = sum(1 for r in rows if r["pm_engine_violates_limit"])
        residuals = [abs(r["ohms_consistency_residual"]) for r in rows if pd.notna(r["ohms_consistency_residual"])]
        max_resid = max(residuals) if residuals else float("nan")
        print(f"  {MODEL_VARIANT}/{scenario}: {len(rows)} branch-endpoint rows, {n_violations} own_thermal violations, {n_pm_engine_violations} pm_engine (OpenLoadFlow) violations, max ohms-consistency residual = {max_resid}")

    df = pd.DataFrame(all_rows)
    out_path = OUT_ROOT / "endpoint_pf_own_thermal_evaluation_python.csv"
    disk_path(out_path.parent).mkdir(parents=True, exist_ok=True)
    df.to_csv(disk_path(out_path), index=False)
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
