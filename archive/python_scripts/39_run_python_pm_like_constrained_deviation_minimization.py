"""Constrained deviation minimization for the full Python PM-like sweep.

Mirrors `37_run_python_pm_like_equality_only_deviation.py` exactly, except the
physical inequality constraints (generator P/Q bounds, voltage bounds, branch
thermal limits, branch angle limits) stay active during the solve -- the L1
deviation-to-PowerModels objective is minimized subject to the *full* normal
AC-OPF feasible region, not just the bare equality physics. This is the
constrained counterpart script 37 didn't have a full-sweep version of yet
(only a compact, single-scenario debug run existed before this script).

Reuses every shared helper from script 37 (target selection, inequality-level
evaluation, binding-mismatch summarization, reference-file lookup, Ipopt log
parsing, etc.) via `importlib`, rather than duplicating ~800 lines -- script
37 is loaded as a plain module (safe: its own solve loop is guarded by
`if __name__ == "__main__":`, so importing it triggers no side effects).
Only the model-building step and the run-one/main loop are written fresh
here, since those are the only parts that actually differ (bounds kept
active, different output filenames/field prefixes).

Outputs (mirroring script 37's naming, "constrained" instead of "equality_only"):
    results/own_vs_powermodels/python_pm_like/constrained_deviation_targets.csv
    results/own_vs_powermodels/python_pm_like/constrained_deviation_minimization.csv
    results/own_vs_powermodels/python_pm_like/constrained_deviation_variable_differences.csv
    results/own_vs_powermodels/python_pm_like/constrained_deviation_inequality_levels.csv
    results/own_vs_powermodels/python_pm_like/constrained_deviation_binding_summary.csv
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from own_models.src.opf.ac.model import build_ac_opf_pyomo_model  # noqa: E402

_EQ_ONLY_SPEC = importlib.util.spec_from_file_location(
    "python_pm_like_equality_only_deviation",
    Path(__file__).resolve().parent / "37_run_python_pm_like_equality_only_deviation.py",
)
eq = importlib.util.module_from_spec(_EQ_ONLY_SPEC)
_EQ_ONLY_SPEC.loader.exec_module(eq)

SWEEP_ROOT = eq.SWEEP_ROOT
ORGANIZED_ROOT = eq.ORGANIZED_ROOT
TARGETS_PATH = SWEEP_ROOT / "constrained_deviation_targets.csv"
SUMMARY_PATH = SWEEP_ROOT / "constrained_deviation_minimization.csv"
VARIABLES_PATH = SWEEP_ROOT / "constrained_deviation_variable_differences.csv"
LEVELS_PATH = SWEEP_ROOT / "constrained_deviation_inequality_levels.csv"
BINDING_SUMMARY_PATH = SWEEP_ROOT / "constrained_deviation_binding_summary.csv"


def build_constrained_model(case, target, own, reference, *, variant: str, warm_start_source: str):
    """Same true-substitution target-deviation objective as the
    equality-only model, but with generator/voltage/thermal/angle bounds
    left active (`enforce_deviation_bounds` defaults True, so no
    bounds-suppression flag is passed here -- correct "constrained"
    semantics)."""
    model = build_ac_opf_pyomo_model(
        case,
        cost_power_units="pu",
        objective_type="target_deviation",
        objective_options=eq.target_options(target, own, variant, warm_start_source=warm_start_source),
        voltage_bounds=None,
        include_branch_thermal=True,
        include_branch_angles=True,
        fix_reference_angle=True,
        warm_start="zero",
        model_name=f"{case.name}_python_pm_like_constrained_{variant}_{warm_start_source}",
    )
    eq.seed_branch_arcs_from_reference(model, reference)
    return model


def run_one(
    row: dict,
    *,
    variant: str,
    warm_start_source: str,
    max_iter: int,
    max_cpu_time: float | None,
    binding_tol: float,
    limit_violation_tol: float,
):
    case_label = str(row["case"])
    scenario = str(row["scenario"])
    case_path, reference_path, reference_suffix = eq.find_reference_files(case_label, scenario)
    case = eq.load_powercase_from_powermodels_json(eq.disk_path(case_path))
    reference = eq.load_powermodels_ac_reference(eq.disk_path(reference_path))
    pm_outcome_class = eq.classify_powermodels_ac_outcome(reference)
    target, pm_va_reference_offset = eq.align_target_reference_angle(
        eq.target_from_reference(reference), case.slack_bus_id
    )
    own = eq.read_own_point(case_label, scenario)

    model = build_constrained_model(case, target, own, reference, variant=variant, warm_start_source=warm_start_source)
    result, solve_time_sec, ipopt_diagnostics = eq.solve_model(model, max_iter=max_iter, max_cpu_time=max_cpu_time)

    termination = str(result.status)
    solver_locally_solved = termination in eq.SOLVED_TERMINATIONS
    max_pg, l1_pg = eq.max_group_diff(result.pg, target["pg"])
    max_qg, l1_qg = eq.max_group_diff(result.qg, target["qg"])
    max_vm, l1_vm = eq.max_group_diff(result.vm, target["vm"])
    max_va, l1_va = eq.max_group_diff(result.va, target["va"])
    l1_nonnegative = max(0.0, eq.safe_float(result.objective))
    diagnostic_cost = eq.generation_cost(case, result.pg, cost_power_units="pu") if result.pg else math.nan

    vm_c, va_c = eq.result_arrays(case, result) if result.vm and result.va else (eq.np.array([]), eq.np.array([]))
    vm_pm, va_pm = eq.target_arrays(case, target)
    data = model._ac_data
    constrained_violations = (
        eq.evaluate_ac_opf_constraint_violations(case, data, result.pg, result.qg, vm_c, va_c)
        if result.pg and result.qg and vm_c.size
        else {}
    )
    pm_violations = eq.evaluate_ac_opf_constraint_violations(case, data, target["pg"], target["qg"], vm_pm, va_pm)

    base_keys = {
        "case": case_label,
        "scenario": scenario,
        "reference_suffix": reference_suffix,
        "model_variant": "python_pm_like_zero",
        "diagnostic_mode": "constrained",
        "variant": variant,
        "warm_start_source": warm_start_source,
        "target_source": "powermodels_reference",
    }
    summary_row = {
        **base_keys,
        "target_reasons": str(row.get("target_reasons", "")),
        "pm_status": reference.get("termination_status", ""),
        "pm_outcome_class": pm_outcome_class,
        "pm_objective": reference.get("objective", math.nan),
        "own_start_status": row.get("own_status", ""),
        "own_start_objective": row.get("own_objective", math.nan),
        "own_start_objective_diff": row.get("objective_diff", math.nan),
        "own_start_objective_rel_diff": row.get("objective_rel_diff", math.nan),
        "pm_va_reference_offset_subtracted": pm_va_reference_offset,
        "constrained_status": result.status,
        "constrained_solver_locally_solved": solver_locally_solved,
        "constrained_l1_deviation": result.objective,
        "constrained_l1_deviation_nonnegative": l1_nonnegative,
        "constrained_generation_cost": diagnostic_cost,
        "cost_diff_constrained_minus_pm": diagnostic_cost - eq.safe_float(reference.get("objective")) if math.isfinite(diagnostic_cost) else math.nan,
        "cost_rel_diff_constrained_vs_pm": abs(diagnostic_cost - eq.safe_float(reference.get("objective"))) / (1.0 + abs(eq.safe_float(reference.get("objective")))) if math.isfinite(diagnostic_cost) else math.nan,
        "solve_time_sec": solve_time_sec,
        "solve_iterations": result.solve_iterations,
        "max_abs_pg_deviation": max_pg,
        "total_l1_pg_deviation": l1_pg,
        "max_abs_qg_deviation": max_qg,
        "total_l1_qg_deviation": l1_qg,
        "max_abs_vm_deviation": max_vm,
        "total_l1_vm_deviation": l1_vm,
        "max_abs_va_deviation": max_va,
        "total_l1_va_deviation": l1_va,
        **{f"constrained_{key}": value for key, value in constrained_violations.items()},
        **{f"pm_{key}": value for key, value in pm_violations.items()},
        **ipopt_diagnostics,
        "solver_message": result.message,
    }

    variable_rows: list[dict] = []
    for variable, values, target_values in [
        ("pg", result.pg, target["pg"]),
        ("qg", result.qg, target["qg"]),
        ("vm", result.vm, target["vm"]),
        ("va", result.va, target["va"]),
    ]:
        for element_id, pm_value in target_values.items():
            if element_id not in values:
                continue
            c_value = float(values[element_id])
            variable_rows.append(
                {
                    **base_keys,
                    "variable": variable,
                    "element_id": element_id,
                    "powermodels_value": float(pm_value),
                    "constrained_value": c_value,
                    "difference_constrained_minus_pm": c_value - float(pm_value),
                    "abs_difference": abs(c_value - float(pm_value)),
                }
            )

    pm_level_rows = eq.inequality_level_rows(case, target["pg"], target["qg"], vm_pm, va_pm, source="powermodels_reference", tol=binding_tol)
    c_level_rows = (
        eq.inequality_level_rows(case, result.pg, result.qg, vm_c, va_c, source="constrained_solution", tol=binding_tol)
        if result.pg and result.qg and vm_c.size
        else []
    )
    compared_levels = eq.compare_inequality_levels(pm_level_rows, c_level_rows, violation_tol=limit_violation_tol)
    # eq.compare_inequality_levels names its own-side columns "equality_only_*";
    # relabel to "constrained_*" for this script's own output naming consistency.
    renamed_levels = []
    for level_row in compared_levels:
        renamed = {
            (key.replace("equality_only_", "constrained_") if key.startswith("equality_only_") else key): value
            for key, value in level_row.items()
        }
        renamed.update(base_keys)
        renamed_levels.append(renamed)
    binding_rows = eq.binding_summary_rows(base_keys, compared_levels)
    for binding_row in binding_rows:
        binding_row["n_only_constrained_binding"] = binding_row.pop("n_only_equality_binding")
        binding_row["max_constrained_violation"] = binding_row.pop("max_equality_only_violation")
        binding_row["n_constrained_violations"] = binding_row.pop("n_equality_only_violations")

    return summary_row, variable_rows, renamed_levels, binding_rows


def main() -> None:
    eq.ensure_conda_solver_path()

    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", nargs="*", default=["full"], choices=sorted(eq.VARIANT_TARGETS))
    parser.add_argument("--cases", nargs="*", default=None)
    parser.add_argument("--scenarios", nargs="*", default=None)
    parser.add_argument("--max-targets", type=int, default=None)
    parser.add_argument("--objective-abs-tol", type=float, default=1e-3)
    parser.add_argument("--objective-rel-tol", type=float, default=1e-6)
    parser.add_argument("--pg-tol", type=float, default=1e-4)
    parser.add_argument("--qg-tol", type=float, default=1e-4)
    parser.add_argument("--vm-tol", type=float, default=1e-5)
    parser.add_argument("--va-tol", type=float, default=1e-5)
    parser.add_argument("--flow-tol", type=float, default=1e-4)
    parser.add_argument("--binding-tol", type=float, default=1e-5)
    parser.add_argument("--limit-violation-tol", type=float, default=1e-5)
    parser.add_argument("--max-iter", type=int, default=500)
    parser.add_argument("--max-cpu-time", type=float, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    outputs = [TARGETS_PATH, SUMMARY_PATH, VARIABLES_PATH, LEVELS_PATH, BINDING_SUMMARY_PATH]
    if args.overwrite:
        for path in outputs:
            path.unlink(missing_ok=True)

    selected = eq.select_targets(args)
    target_rows = [
        {
            "case": row["case"],
            "scenario": row["scenario"],
            "model_variant": row.get("model_variant", "python_pm_like_zero"),
            "target_reasons": row.get("target_reasons", ""),
            "own_objective": row.get("own_objective", math.nan),
            "pm_objective": row.get("pm_objective", math.nan),
            "objective_diff": row.get("objective_diff", math.nan),
            "objective_rel_diff": row.get("objective_rel_diff", math.nan),
            "max_pg_diff": row.get("max_pg_diff", math.nan),
            "max_qg_diff": row.get("max_qg_diff", math.nan),
            "max_vm_diff": row.get("max_vm_diff", math.nan),
            "max_va_diff": row.get("max_va_diff", math.nan),
            "max_pf_diff": row.get("max_pf_diff", math.nan),
            "max_qf_diff": row.get("max_qf_diff", math.nan),
        }
        for row in selected
    ]
    eq.write_csv(TARGETS_PATH, target_rows)
    eq.copy_outputs([TARGETS_PATH])

    print(f"Selected {len(selected)} material Python PM-like target scenario(s).", flush=True)
    if args.dry_run:
        for row in target_rows:
            print(f"  {row['case']} / {row['scenario']} :: {row['target_reasons']}", flush=True)
        return

    for row in selected:
        for variant in args.variants:
            for warm_start_source in ("target", "own"):
                print(f"=== {row['case']} / {row['scenario']} / {variant} / warm_start_source={warm_start_source} ===", flush=True)
                try:
                    summary_row, variable_rows, level_rows, binding_rows = run_one(
                        row,
                        variant=variant,
                        warm_start_source=warm_start_source,
                        max_iter=args.max_iter,
                        max_cpu_time=args.max_cpu_time,
                        binding_tol=args.binding_tol,
                        limit_violation_tol=args.limit_violation_tol,
                    )
                except Exception as exc:  # noqa: BLE001 -- checkpoint diagnostics and continue
                    base_keys = {
                        "case": row.get("case", ""),
                        "scenario": row.get("scenario", ""),
                        "reference_suffix": "",
                        "model_variant": "python_pm_like_zero",
                        "diagnostic_mode": "constrained",
                        "variant": variant,
                        "warm_start_source": warm_start_source,
                        "target_source": "powermodels_reference",
                    }
                    summary_row = {
                        **base_keys,
                        "target_reasons": row.get("target_reasons", ""),
                        "pm_status": "",
                        "pm_outcome_class": "",
                        "pm_objective": row.get("pm_objective", math.nan),
                        "own_start_status": row.get("own_status", ""),
                        "own_start_objective": row.get("own_objective", math.nan),
                        "own_start_objective_diff": row.get("objective_diff", math.nan),
                        "own_start_objective_rel_diff": row.get("objective_rel_diff", math.nan),
                        "pm_va_reference_offset_subtracted": math.nan,
                        "constrained_status": "ERROR",
                        "constrained_solver_locally_solved": False,
                        "constrained_l1_deviation": math.nan,
                        "constrained_l1_deviation_nonnegative": math.nan,
                        "constrained_generation_cost": math.nan,
                        "cost_diff_constrained_minus_pm": math.nan,
                        "cost_rel_diff_constrained_vs_pm": math.nan,
                        "solve_time_sec": math.nan,
                        "solve_iterations": math.nan,
                        "max_abs_pg_deviation": math.nan,
                        "total_l1_pg_deviation": math.nan,
                        "max_abs_qg_deviation": math.nan,
                        "total_l1_qg_deviation": math.nan,
                        "max_abs_vm_deviation": math.nan,
                        "total_l1_va_deviation": math.nan,
                        "solver_message": str(exc),
                    }
                    variable_rows = []
                    level_rows = []
                    binding_rows = []
                    print(f"  ERROR: {exc}", flush=True)

                eq.append_csv(SUMMARY_PATH, [summary_row])
                eq.append_csv(VARIABLES_PATH, variable_rows)
                eq.append_csv(LEVELS_PATH, level_rows)
                eq.append_csv(BINDING_SUMMARY_PATH, binding_rows)
                print(
                    "  status="
                    f"{summary_row['constrained_status']} "
                    f"L1={summary_row['constrained_l1_deviation']} "
                    f"maxPg={summary_row['max_abs_pg_deviation']} "
                    f"maxVm={summary_row['max_abs_vm_deviation']}",
                    flush=True,
                )

    eq.copy_outputs(outputs)
    print("Saved Python PM-like constrained deviation outputs:", flush=True)
    for path in outputs:
        print(f"  {path}", flush=True)
    print(f"Copied meeting copies to {ORGANIZED_ROOT}", flush=True)


if __name__ == "__main__":
    main()
