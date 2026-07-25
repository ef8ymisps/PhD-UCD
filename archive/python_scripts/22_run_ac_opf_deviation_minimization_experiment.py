"""C2: deviation-minimization formulation-equivalence test (Python own model).

Mirrors julia/own_models/scripts/13_run_deviation_minimization_experiment.jl
exactly -- same question, same four variants, same 8 base cases + 2 stressed
1888_rte scenarios, same output schema (column names match 1:1 so the two
CSVs can be compared/plotted side by side).

Question: is own model's AC-OPF formulation *equivalent* to PowerModels' --
not just "do their optimal objectives happen to match" but "is PowerModels'
optimal point actually feasible, and reachable, inside own model's feasible
region?" Method: pin PowerModels' solution as a target and minimize L1
deviation `sum(dx_plus + dx_minus)` from it (x = target + dx_plus - dx_minus,
dx_plus, dx_minus >= 0) subject to own model's real constraints. If the
achieved deviation is ~0, PowerModels' point is demonstrated feasible under
own model's formulation.

Four variants per case/scenario, Pg and Va always pinned to target:
- full:        Qg and Vm both pinned
- no_qg:       Qg free, Vm pinned
- no_vm:       Vm free, Qg pinned
- no_qg_no_vm: both free

Reuses the PowerModels reference already solved by
julia/own_models/scripts/08_run_ac_opf_powermodels_references.jl (same
scenario_ids as the compact sweep) as the target point -- no fresh
PowerModels solve needed. The two stressed-scenario cases are pulled via
iter_compact_ac_validation's own load_scaling/reactive_power_stress scenario
definitions (keep_raw_result + scenario_filter_ids), so the perturbed
PowerCase matches exactly what PowerModels solved against.

Critical fix (see this session's debugging): solve_ac_opf_pyomo's function
default `voltage_bounds=(0.95, 1.05)` clips PowerModels' actual solution out
of reach on cases like 5_pjm (bus voltages up to 1.10 pu) -- this experiment
must pass `voltage_bounds=None` explicitly (use each case's own bounds,
matching Julia's own `voltage_bounds=nothing` default) or the deviation
comes out spuriously large regardless of warm start or tolerances.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import pyomo.environ as pyo

PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat  # noqa: E402
from own_models.src.analysis.ac_opf_sweeps import iter_compact_ac_validation  # noqa: E402
from own_models.src.data.case import PowerCase  # noqa: E402
from own_models.src.opf.ac.model import DEFAULT_IPOPT_TOLERANCES, build_ac_opf_pyomo_model  # noqa: E402
from own_models.src.opf.ac.results import extract_ac_opf_result  # noqa: E402
from own_models.src.opf.solver_iterations import solve_with_iteration_capture  # noqa: E402

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

# Matches julia/own_models/scripts/13_run_deviation_minimization_experiment.jl's
# STRESSED_SCENARIOS exactly: the only two rows (out of 607) in the full
# compact validation sweep with a genuinely large own-vs-PM Pg diff --
# everywhere else the "full" variant would just reconfirm ~0 deviation on
# cases where nothing interesting was ever going to happen.
STRESSED_SCENARIO_IDS = {
    "1888_rte_load_x0p8": "1888_rte_load_x0p8__load_scaling__none__none__load_multiplier__0p8",
    "1888_rte_load_x1p2": "1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2",
}

VARIANTS = [
    ("full", True, True),
    ("no_qg", False, True),
    ("no_vm", True, False),
    ("no_qg_no_vm", False, False),
]

# Tighter than the repo's usual pinned tolerances (docs/notes/solver_tolerances.md)
# -- this experiment specifically measures how close to zero the deviation
# gets, so a loose constr_viol_tol/compl_inf_tol would blur the very thing
# being tested. Matches Julia's build_ac_optimizer() in the mirrored script.
TIGHT_IPOPT_TOLERANCES: dict[str, Any] = {
    "tol": 1e-10,
    "constr_viol_tol": 1e-9,
    "compl_inf_tol": 1e-9,
    "dual_inf_tol": 1.0,
}

PM_REF_DIR = REPO_ROOT / "results" / "reference" / "powermodels" / "ac_opf_compact_sweeps"
OUTPUT_PATH = REPO_ROOT / "results" / "own_python" / "deviation_minimization" / "deviation_minimization_results.csv"


def case_path(case_name: str) -> Path:
    return REPO_ROOT / "data" / "raw" / "pglib" / "matpower_mat" / "base_case" / f"{case_name}.mat"


def load_target(scenario_id: str) -> dict[str, dict[str, float]]:
    """Pull PowerModels' solved (Pg, Qg, Vm, Va) for a scenario_id as the target point."""
    gens = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_generators.csv", usecols=["scenario_id", "gen_id", "pm_pg", "pm_qg"])
    buses = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_buses.csv", usecols=["scenario_id", "bus_id", "pm_vm", "pm_va"])
    g = gens[gens.scenario_id == scenario_id]
    b = buses[buses.scenario_id == scenario_id]
    if g.empty or b.empty:
        raise ValueError(f"No PowerModels reference found for scenario_id={scenario_id!r}.")
    return {
        "pg": {str(r.gen_id): float(r.pm_pg) for r in g.itertuples()},
        "qg": {str(r.gen_id): float(r.pm_qg) for r in g.itertuples()},
        "vm": {str(r.bus_id): float(r.pm_vm) for r in b.itertuples()},
        "va": {str(r.bus_id): float(r.pm_va) for r in b.itertuples()},
    }


def load_pm_objective(scenario_id: str) -> tuple[float, str]:
    summary = pd.read_csv(PM_REF_DIR / "powermodels_ac_opf_summary.csv", usecols=["scenario_id", "pm_status", "pm_objective"])
    row = summary[summary.scenario_id == scenario_id].iloc[0]
    return float(row.pm_objective), str(row.pm_status)


def slack_group_stats(model, plus_name: str, minus_name: str, ids) -> dict[str, float]:
    plus = getattr(model, plus_name)
    minus = getattr(model, minus_name)
    signed = [pyo.value(plus[i]) - pyo.value(minus[i]) for i in ids]
    l1 = [pyo.value(plus[i]) + pyo.value(minus[i]) for i in ids]
    return {"max_abs": max(abs(v) for v in signed), "total_l1": sum(l1)}


def free_group_deviation(values: dict[str, float], target: dict[str, float]) -> dict[str, float]:
    diffs = [values[i] - target[i] for i in target]
    return {"max_abs": max(abs(d) for d in diffs), "total_l1": sum(abs(d) for d in diffs)}


def solve_deviation_model(case: PowerCase, target: dict[str, dict[str, float]], *, include_qg: bool, include_vm: bool):
    """Build + solve the target_deviation model, returning (model, ACOPFResult).

    Local helper rather than a change to solve_ac_opf_pyomo: this experiment
    is the only caller that needs the live model back (to read the
    dpg_plus/dpg_minus-style slack variables directly), so keeping it here
    avoids growing the shared solve API for a single-use case.
    """
    options: dict[str, object] = {"target_pg": target["pg"], "target_va": target["va"]}
    if include_qg:
        options["target_qg"] = target["qg"]
    if include_vm:
        options["target_vm"] = target["vm"]

    model = build_ac_opf_pyomo_model(
        case,
        objective_type="target_deviation",
        objective_options=options,
        voltage_bounds=None,
        fix_reference_angle=True,
        warm_start="case",
    )
    opt = pyo.SolverFactory("ipopt")
    effective_options = {**DEFAULT_IPOPT_TOLERANCES, **TIGHT_IPOPT_TOLERANCES}
    for key, value in effective_options.items():
        opt.options[key] = value
    solve_result, iterations = solve_with_iteration_capture(opt, model, tee=False)
    result = extract_ac_opf_result(model, solve_result, solve_iterations=iterations)
    return model, result


def run_one_variant(case: PowerCase, target, pm_objective: float, pm_status: str, variant) -> dict[str, object]:
    label, include_qg, include_vm = variant
    print(f"  --- variant={label} ---")
    model, result = solve_deviation_model(case, target, include_qg=include_qg, include_vm=include_vm)
    print(f"    own status={result.status} objective(pinned-terms L1)={result.objective}")

    row: dict[str, object] = {
        "variant": label,
        "pm_status": pm_status,
        "pm_objective": pm_objective,
        "own_status": result.status,
        "pinned_l1_deviation": result.objective,
    }

    if not result.success:
        for key in (
            "max_abs_pg_deviation", "max_abs_qg_deviation", "max_abs_vm_deviation", "max_abs_va_deviation",
            "own_cost_at_deviation_point", "cost_rel_diff_vs_pm",
        ):
            row[key] = float("nan")
        print("    ! own model did not solve to a usable status.")
        return row

    gen_ids = list(target["pg"].keys())
    bus_ids = list(target["vm"].keys())

    pg_stats = slack_group_stats(model, "dpg_plus", "dpg_minus", gen_ids)
    va_stats = slack_group_stats(model, "dva_plus", "dva_minus", bus_ids)
    row["max_abs_pg_deviation"] = pg_stats["max_abs"]
    row["max_abs_va_deviation"] = va_stats["max_abs"]

    if include_qg:
        qg_stats = slack_group_stats(model, "dqg_plus", "dqg_minus", gen_ids)
    else:
        qg_stats = free_group_deviation(result.qg, target["qg"])
    row["max_abs_qg_deviation"] = qg_stats["max_abs"]

    if include_vm:
        vm_stats = slack_group_stats(model, "dvm_plus", "dvm_minus", bus_ids)
    else:
        vm_stats = free_group_deviation(result.vm, target["vm"])
    row["max_abs_vm_deviation"] = vm_stats["max_abs"]

    own_cost_at_target = sum(
        float(gen.cost_c2) * (result.pg[gen.id] * case.base_mva) ** 2
        + float(gen.cost_c1) * (result.pg[gen.id] * case.base_mva)
        + float(gen.cost_c0)
        for gen in case.generators
        if gen.status == 1
    )
    row["own_cost_at_deviation_point"] = own_cost_at_target
    row["cost_rel_diff_vs_pm"] = abs(own_cost_at_target - pm_objective) / (1.0 + abs(pm_objective))

    print(
        f"    max |Pg dev|={pg_stats['max_abs']} max |Qg dev|={row['max_abs_qg_deviation']} "
        f"max |Vm dev|={row['max_abs_vm_deviation']} max |Va dev|={va_stats['max_abs']}"
    )
    return row


def run_one_case(label: str, case: PowerCase, scenario_id: str) -> list[dict[str, object]]:
    print(f"=== {label} ===")
    target = load_target(scenario_id)
    pm_objective, pm_status = load_pm_objective(scenario_id)
    print(f"  pm status={pm_status} objective={pm_objective}")

    rows = [run_one_variant(case, target, pm_objective, pm_status, variant) for variant in VARIANTS]
    for row in rows:
        row["case_name"] = label
    return rows


def load_base_case(case_name: str) -> tuple[PowerCase, str]:
    case = load_powercase_from_matpower_mat(case_path(case_name))
    short_name = case_name.replace("pglib_opf_case", "")
    case = replace(case, name=short_name)
    scenario_id = f"{short_name}__base__none__none__none__none"
    return case, scenario_id


def load_stressed_case(case_name: str, scenario_id: str) -> PowerCase:
    """Pull the exact perturbed PowerCase from the compact sweep's own
    load_scaling/reactive_power_stress scenario generation, filtered to the
    one scenario_id needed -- guarantees the perturbation matches what
    PowerModels solved against without duplicating the scaling logic."""
    case = load_powercase_from_matpower_mat(case_path(case_name))
    short_name = case_name.replace("pglib_opf_case", "")
    case = replace(case, name=short_name)
    settings = {"keep_raw_result": True, "scenario_filter_ids": {scenario_id}}
    for row in iter_compact_ac_validation(case, settings=settings):
        if row.get("case") is not None:
            return row["case"]
    raise ValueError(f"Could not locate case for scenario_id={scenario_id!r}.")


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    base_case_rows: list[dict[str, object]] = []
    for case_name in DEFAULT_CASE_NAMES:
        case, scenario_id = load_base_case(case_name)
        base_case_rows.extend(run_one_case(case.name, case, scenario_id))

    stressed_rows: list[dict[str, object]] = []
    for label, scenario_id in STRESSED_SCENARIO_IDS.items():
        case = load_stressed_case("pglib_opf_case1888_rte", scenario_id)
        stressed_rows.extend(run_one_case(label, case, scenario_id))

    df = pd.DataFrame(base_case_rows + stressed_rows)
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved: {OUTPUT_PATH}")
    print(df.to_string())


if __name__ == "__main__":
    main()
