"""Equality-only deviation minimization for the full Python PM-like sweep.

This diagnostic mirrors the Julia equality-only check, but targets the full
``results/own_vs_powermodels/python_pm_like`` validation sweep instead of the
older compact Python debug queue.

For each material objective/variable mismatch scenario, it solves:

    minimize ||Pg - Pg_PM||_1 + ||Qg - Qg_PM||_1
             + ||Vm - Vm_PM||_1 + ||Va - Va_PM||_1

subject only to the PM-like AC equality constraints:

* bus active/reactive power balance;
* branch Ohm's-law equality constraints;
* reference angle constraint;
* L1 deviation-link equalities.

The physical inequality constraints are deliberately not imposed in the solve.
Afterwards, this script evaluates generator, voltage, thermal, and angle-limit
levels externally at both the equality-only projected point and the PowerModels
reference point.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyomo.environ as pyo

PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from own_models.src.adapters.powermodels_csv_adapter import load_powercase_from_powermodels_json  # noqa: E402
from own_models.src.analysis.compare_ac_with_powermodels import (  # noqa: E402
    classify_powermodels_ac_outcome,
    load_powermodels_ac_reference,
)
from own_models.src.data.case import PowerCase  # noqa: E402
from own_models.src.network.admittance import calc_ac_branch_flows  # noqa: E402
from own_models.src.opf.ac.constraints import evaluate_ac_opf_constraint_violations  # noqa: E402
from own_models.src.opf.ac.model import DEFAULT_IPOPT_TOLERANCES, build_ac_opf_pyomo_model  # noqa: E402
from own_models.src.opf.ac.results import extract_ac_opf_result, status_only_ac_opf_result  # noqa: E402


REFERENCE_DIR = REPO_ROOT / "results" / "reference" / "powermodels" / "ac_opf"
SWEEP_ROOT = REPO_ROOT / "results" / "own_vs_powermodels" / "python_pm_like"
SWEEP_SUMMARY_PATH = SWEEP_ROOT / "summary.csv"
ORGANIZED_ROOT = (
    REPO_ROOT
    / "results"
    / "own_vs_powermodels"
    / "_organized_for_meeting"
    / "08_python_ac_opf_debugging"
)

TARGETS_PATH = SWEEP_ROOT / "equality_only_deviation_targets.csv"
SUMMARY_PATH = SWEEP_ROOT / "equality_only_deviation_minimization.csv"
VARIABLES_PATH = SWEEP_ROOT / "equality_only_deviation_variable_differences.csv"
LEVELS_PATH = SWEEP_ROOT / "equality_only_deviation_inequality_levels.csv"
BINDING_SUMMARY_PATH = SWEEP_ROOT / "equality_only_deviation_binding_summary.csv"

VARIANT_TARGETS = {
    "full": {"pg", "qg", "vm", "va"},
    "no_qg": {"pg", "vm", "va"},
    "no_v": {"pg", "qg"},
    "pg_only": {"pg"},
}

SOLVED_TERMINATIONS = {"optimal", "locallyOptimal", "globallyOptimal"}
IPOPT_ITER_PATTERN = re.compile(r"Number of Iterations\.+:\s*(\d+)")
IPOPT_EXIT_PATTERN = re.compile(r"EXIT:\s*(.+)")
IPOPT_ROW_PATTERN = re.compile(
    r"^\s*(?P<iter>\d+r?)\s+"
    r"(?P<objective>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][+-]?\d+)?)\s+"
    r"(?P<inf_pr>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][+-]?\d+)?)\s+"
    r"(?P<inf_du>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][+-]?\d+)?)\s+"
    r"(?P<lg_mu>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][+-]?\d+)?)"
)


def disk_path(path: Path) -> Path:
    """Return a long-path-safe Path on Windows."""
    if os.name != "nt":
        return path
    resolved = path.resolve()
    text = str(resolved)
    return Path(text if text.startswith("\\\\?\\") else "\\\\?\\" + text)


def ensure_conda_solver_path() -> None:
    """Make directly invoked Conda Python discover ``ipopt.exe`` on Windows."""
    env_root = Path(sys.executable).resolve().parent
    library_bin = env_root / "Library" / "bin"
    if library_bin.exists():
        os.environ["PATH"] = str(library_bin) + os.pathsep + os.environ.get("PATH", "")


def as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def parse_ipopt_log(log: str) -> dict[str, object]:
    """Extract a compact Ipopt diagnostic summary from console output."""
    iterations = math.nan
    match = IPOPT_ITER_PATTERN.search(log)
    if match:
        iterations = float(match.group(1))

    exit_message = ""
    exit_matches = IPOPT_EXIT_PATTERN.findall(log)
    if exit_matches:
        exit_message = exit_matches[-1].strip()

    final_row: dict[str, object] = {}
    for row_match in IPOPT_ROW_PATTERN.finditer(log):
        final_row = {
            "ipopt_final_iter": row_match.group("iter"),
            "ipopt_final_objective": safe_float(row_match.group("objective").replace("D", "E").replace("d", "e")),
            "ipopt_final_inf_pr": safe_float(row_match.group("inf_pr").replace("D", "E").replace("d", "e")),
            "ipopt_final_inf_du": safe_float(row_match.group("inf_du").replace("D", "E").replace("d", "e")),
            "ipopt_final_lg_mu": safe_float(row_match.group("lg_mu").replace("D", "E").replace("d", "e")),
        }

    return {
        "ipopt_iterations_from_log": iterations,
        "ipopt_exit_message": exit_message,
        **final_row,
    }


def target_from_reference(reference: dict) -> dict[str, dict[str, float]]:
    generators = reference.get("generators", {})
    buses = reference.get("buses", {})
    return {
        "pg": {
            str(gen_id): float(values["pg"])
            for gen_id, values in generators.items()
            if values.get("pg") is not None
        },
        "qg": {
            str(gen_id): float(values["qg"])
            for gen_id, values in generators.items()
            if values.get("qg") is not None
        },
        "vm": {
            str(bus_id): float(values["vm"])
            for bus_id, values in buses.items()
            if values.get("vm") is not None
        },
        "va": {
            str(bus_id): float(values["va"])
            for bus_id, values in buses.items()
            if values.get("va") is not None
        },
    }


def align_target_reference_angle(target: dict[str, dict[str, float]], slack_bus_id: str) -> tuple[dict[str, dict[str, float]], float]:
    """Shift PM voltage angles so the own PM-like slack/reference bus is zero."""
    offset = float(target["va"].get(slack_bus_id, 0.0))
    return (
        {
            "pg": dict(target["pg"]),
            "qg": dict(target["qg"]),
            "vm": dict(target["vm"]),
            "va": {bus_id: float(value) - offset for bus_id, value in target["va"].items()},
        },
        offset,
    )


def scenario_own_point_dir(case_label: str, scenario_label: str) -> Path:
    """Mirror `34_run_own_vs_powermodels_pm_like_full_sweep.py`'s
    `scenario_output_dir` path-shortening exactly, so the raw per-scenario
    CSVs (own_pg/own_qg/own_vm/own_va columns) already written by the full
    python_pm_like sweep can be located for any (case, scenario) pair
    without needing a manifest file."""
    safe_label = re.sub(r"[^A-Za-z0-9._=-]+", "_", scenario_label).strip("_")
    candidate = SWEEP_ROOT / case_label / safe_label
    if len(str(candidate / "constraints_inventory.csv")) <= 190:
        return candidate
    digest = hashlib.sha1(scenario_label.encode("utf-8")).hexdigest()[:10]
    shortened = f"{safe_label[:48].rstrip('_')}__{digest}"
    return SWEEP_ROOT / case_label / shortened


def read_own_point(case_label: str, scenario_label: str) -> dict[str, dict[str, float]]:
    """Read the own model's own solution point x^{e*} straight off the
    already-written raw per-scenario CSVs -- no re-solve. Same source script
    52 reads (generalized here to any case/scenario, not just the two
    1888_rte scenarios script 52 covers)."""
    base = disk_path(scenario_own_point_dir(case_label, scenario_label))
    gens = pd.read_csv(base / "generators.csv")
    buses = pd.read_csv(base / "buses.csv")
    return {
        "pg": dict(zip(gens["gen_id"].astype(str), gens["own_pg"])),
        "qg": dict(zip(gens["gen_id"].astype(str), gens["own_qg"])),
        "vm": dict(zip(buses["bus_id"].astype(str), buses["own_vm"])),
        "va": dict(zip(buses["bus_id"].astype(str), buses["own_va"])),
    }


def target_options(
    target: dict[str, dict[str, float]],
    own: dict[str, dict[str, float]],
    variant: str,
    *,
    warm_start_source: str = "target",
    enforce_deviation_bounds: bool = True,
) -> dict[str, object]:
    """Always pass all four target_* dicts plus include_pg/qg/vm/va
    booleans (previously: include a target_* key only if that group was in
    scope, which doubled as both "what to seed" and "what to include" --
    the true-substitution rebuild needs the full target dict for EVERY
    group, since excluded groups are now also seeded at target/own instead
    of left at a generic cold start). `own` is x^{e*}, needed whenever
    `warm_start_source="own"`."""
    groups = VARIANT_TARGETS[variant]
    return {
        "target_pg": target["pg"],
        "target_qg": target["qg"],
        "target_vm": target["vm"],
        "target_va": target["va"],
        "own_pg": own["pg"],
        "own_qg": own["qg"],
        "own_vm": own["vm"],
        "own_va": own["va"],
        "include_pg": "pg" in groups,
        "include_qg": "qg" in groups,
        "include_vm": "vm" in groups,
        "include_va": "va" in groups,
        "warm_start_source": warm_start_source,
        "enforce_deviation_bounds": enforce_deviation_bounds,
    }


def seed_branch_arcs_from_reference(model, reference: dict) -> None:
    """Seed PM-like directed arc variables at the PowerModels branch-flow point."""
    if not hasattr(model, "p") or not hasattr(model, "_ac_pm_like_data"):
        return
    data = model._ac_pm_like_data
    for branch_id, values in reference.get("branches", {}).items():
        branch_id = str(branch_id)
        if branch_id not in data.arcs_from or branch_id not in data.arcs_to:
            continue
        if values.get("pf") is not None:
            model.p[data.arcs_from[branch_id]].set_value(float(values["pf"]), skip_validation=True)
        if values.get("qf") is not None:
            model.q[data.arcs_from[branch_id]].set_value(float(values["qf"]), skip_validation=True)
        if values.get("pt") is not None:
            model.p[data.arcs_to[branch_id]].set_value(float(values["pt"]), skip_validation=True)
        if values.get("qt") is not None:
            model.q[data.arcs_to[branch_id]].set_value(float(values["qt"]), skip_validation=True)


def build_equality_only_model(
    case: PowerCase,
    target: dict[str, dict[str, float]],
    own: dict[str, dict[str, float]],
    reference: dict,
    *,
    variant: str,
    warm_start_source: str,
):
    """`enforce_deviation_bounds=False` drops Pg/Qg/Vm bounds AND (via the
    same flag, threaded through to the PM-like arc builder) the arc-flow
    p/q bounds at build time -- replaces the old post-hoc
    `remove_physical_bounds` pass, which is no longer needed."""
    model = build_ac_opf_pyomo_model(
        case,
        cost_power_units="pu",
        objective_type="target_deviation",
        objective_options=target_options(
            target, own, variant, warm_start_source=warm_start_source, enforce_deviation_bounds=False
        ),
        voltage_bounds=None,
        include_branch_thermal=False,
        include_branch_angles=False,
        fix_reference_angle=True,
        warm_start="zero",
        model_name=f"{case.name}_python_pm_like_equality_only_{variant}_{warm_start_source}",
    )
    seed_branch_arcs_from_reference(model, reference)
    return model


def solve_model(model, *, max_iter: int, max_cpu_time: float | None):
    opt = pyo.SolverFactory("ipopt")
    if opt is None or not opt.available(exception_flag=False):
        raise RuntimeError("Ipopt is not available through Pyomo in this environment.")

    options = {
        **DEFAULT_IPOPT_TOLERANCES,
        "tol": 1e-10,
        "constr_viol_tol": 1e-9,
        "compl_inf_tol": 1e-9,
        "dual_inf_tol": 1.0,
        "max_iter": int(max_iter),
        "print_level": 5,
    }
    if max_cpu_time is not None:
        options["max_cpu_time"] = float(max_cpu_time)
    for key, value in options.items():
        opt.options[key] = value

    buffer = io.StringIO()
    started = time.perf_counter()
    with contextlib.redirect_stdout(buffer):
        raw_result = opt.solve(model, tee=True, load_solutions=False)
    elapsed = time.perf_counter() - started
    ipopt_diagnostics = parse_ipopt_log(buffer.getvalue())
    iterations = safe_float(ipopt_diagnostics.get("ipopt_iterations_from_log"))

    if len(raw_result.solution) > 0:
        model.solutions.load_from(raw_result)
        result = extract_ac_opf_result(model, raw_result, solve_iterations=iterations)
    else:
        result = status_only_ac_opf_result(
            model,
            raw_result,
            message_suffix="solver returned no loadable solution",
            solve_iterations=iterations,
        )
    return result, elapsed, ipopt_diagnostics


def generation_cost(case: PowerCase, pg: dict[str, float], *, cost_power_units: str = "pu") -> float:
    value = 0.0
    for gen in case.generators:
        if gen.status != 1 or gen.id not in pg:
            continue
        p = float(pg[gen.id])
        if cost_power_units == "mw":
            p *= case.base_mva
        elif cost_power_units != "pu":
            raise ValueError(f"Unsupported cost_power_units={cost_power_units!r}.")
        value += float(gen.cost_c2) * p**2 + float(gen.cost_c1) * p + float(gen.cost_c0)
    return value


def max_group_diff(values: dict[str, float], target: dict[str, float]) -> tuple[float, float]:
    diffs = [abs(float(values[key]) - float(target[key])) for key in target if key in values]
    if not diffs:
        return math.nan, math.nan
    return max(diffs), sum(diffs)


def arrays_from_mapping(case: PowerCase, values: dict[str, float], element: str) -> np.ndarray:
    if element == "bus":
        ids = [bus.id for bus in case.buses]
    else:
        raise ValueError(f"Unsupported element={element!r}.")
    return pd.Series(values, dtype=float).reindex(ids).to_numpy(dtype=float)


def result_arrays(case: PowerCase, result) -> tuple[np.ndarray, np.ndarray]:
    return arrays_from_mapping(case, result.vm, "bus"), arrays_from_mapping(case, result.va, "bus")


def target_arrays(case: PowerCase, target: dict[str, dict[str, float]]) -> tuple[np.ndarray, np.ndarray]:
    return arrays_from_mapping(case, target["vm"], "bus"), arrays_from_mapping(case, target["va"], "bus")


def add_level(
    rows: list[dict[str, object]],
    *,
    constraint_type: str,
    element_id: str,
    bound: float,
    value: float,
    margin: float,
    units: str,
    source: str,
    tol: float,
) -> None:
    rows.append(
        {
            "source": source,
            "constraint_type": constraint_type,
            "element_id": str(element_id),
            "bound": float(bound),
            "value": float(value),
            "margin": float(margin),
            "violation": max(0.0, -float(margin)),
            "is_binding": abs(float(margin)) <= tol,
            "level_units": units,
        }
    )


def inequality_level_rows(
    case: PowerCase,
    pg: dict[str, float],
    qg: dict[str, float],
    vm_array: np.ndarray,
    va_array: np.ndarray,
    *,
    source: str,
    tol: float,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    flows = calc_ac_branch_flows(case, vm_array, va_array)

    for gen in case.generators:
        if gen.status != 1 or gen.id not in pg or gen.id not in qg:
            continue
        add_level(rows, constraint_type="pg_min", element_id=gen.id, bound=gen.pmin, value=pg[gen.id], margin=pg[gen.id] - gen.pmin, units="pu", source=source, tol=tol)
        add_level(rows, constraint_type="pg_max", element_id=gen.id, bound=gen.pmax, value=pg[gen.id], margin=gen.pmax - pg[gen.id], units="pu", source=source, tol=tol)
        add_level(rows, constraint_type="qg_min", element_id=gen.id, bound=gen.qmin, value=qg[gen.id], margin=qg[gen.id] - gen.qmin, units="pu", source=source, tol=tol)
        add_level(rows, constraint_type="qg_max", element_id=gen.id, bound=gen.qmax, value=qg[gen.id], margin=gen.qmax - qg[gen.id], units="pu", source=source, tol=tol)

    for idx, bus in enumerate(case.buses):
        vm = float(vm_array[idx])
        add_level(rows, constraint_type="vm_min", element_id=bus.id, bound=bus.vmin, value=vm, margin=vm - bus.vmin, units="pu", source=source, tol=tol)
        add_level(rows, constraint_type="vm_max", element_id=bus.id, bound=bus.vmax, value=vm, margin=bus.vmax - vm, units="pu", source=source, tol=tol)

    for branch in case.branches:
        if branch.status != 1:
            continue
        angle = float(va_array[case.bus_id_to_index[branch.from_bus]] - va_array[case.bus_id_to_index[branch.to_bus]])
        add_level(rows, constraint_type="angle_min", element_id=branch.id, bound=branch.angmin, value=angle, margin=angle - branch.angmin, units="radian", source=source, tol=tol)
        add_level(rows, constraint_type="angle_max", element_id=branch.id, bound=branch.angmax, value=angle, margin=branch.angmax - angle, units="radian", source=source, tol=tol)
        if branch.rate_a > 0.0 and branch.id in flows:
            flow = flows[branch.id]
            sf2 = flow["pf"] ** 2 + flow["qf"] ** 2
            st2 = flow["pt"] ** 2 + flow["qt"] ** 2
            limit = branch.rate_a**2
            add_level(rows, constraint_type="thermal_from", element_id=branch.id, bound=limit, value=sf2, margin=limit - sf2, units="pu_squared", source=source, tol=tol)
            add_level(rows, constraint_type="thermal_to", element_id=branch.id, bound=limit, value=st2, margin=limit - st2, units="pu_squared", source=source, tol=tol)

    return rows


def compare_inequality_levels(
    pm_rows: list[dict[str, object]],
    equality_rows: list[dict[str, object]],
    *,
    violation_tol: float,
) -> list[dict[str, object]]:
    pm_by_key = {(row["constraint_type"], row["element_id"]): row for row in pm_rows}
    eq_by_key = {(row["constraint_type"], row["element_id"]): row for row in equality_rows}
    compared: list[dict[str, object]] = []
    for key in sorted(set(pm_by_key) | set(eq_by_key)):
        constraint_type, element_id = key
        pm = pm_by_key.get(key)
        eq = eq_by_key.get(key)
        pm_value = safe_float(pm.get("value")) if pm else math.nan
        eq_value = safe_float(eq.get("value")) if eq else math.nan
        pm_margin = safe_float(pm.get("margin")) if pm else math.nan
        eq_margin = safe_float(eq.get("margin")) if eq else math.nan
        pm_binding = bool(pm.get("is_binding")) if pm else False
        eq_binding = bool(eq.get("is_binding")) if eq else False
        pm_violation = safe_float(pm.get("violation")) if pm else math.nan
        eq_violation = safe_float(eq.get("violation")) if eq else math.nan
        binding_relation = "same" if pm_binding == eq_binding else "different"
        compared.append(
            {
                "constraint_type": constraint_type,
                "element_id": element_id,
                "bound": safe_float(pm.get("bound") if pm else eq.get("bound") if eq else math.nan),
                "equality_only_value": eq_value,
                "pm_value": pm_value,
                "value_diff_equality_minus_pm": eq_value - pm_value if math.isfinite(eq_value) and math.isfinite(pm_value) else math.nan,
                "abs_value_diff": abs(eq_value - pm_value) if math.isfinite(eq_value) and math.isfinite(pm_value) else math.nan,
                "equality_only_margin": eq_margin,
                "pm_margin": pm_margin,
                "margin_diff_equality_minus_pm": eq_margin - pm_margin if math.isfinite(eq_margin) and math.isfinite(pm_margin) else math.nan,
                "abs_margin_diff": abs(eq_margin - pm_margin) if math.isfinite(eq_margin) and math.isfinite(pm_margin) else math.nan,
                "equality_only_violation": eq_violation,
                "pm_violation": pm_violation,
                "equality_only_binding": eq_binding,
                "pm_binding": pm_binding,
                "equality_only_violates_limit": math.isfinite(eq_violation) and eq_violation > violation_tol,
                "pm_violates_limit": math.isfinite(pm_violation) and pm_violation > violation_tol,
                "limit_violation_tolerance": violation_tol,
                "binding_relation": binding_relation,
                "level_units": str(pm.get("level_units") if pm else eq.get("level_units") if eq else ""),
            }
        )
    return compared


def family_of(constraint_type: str) -> str:
    if constraint_type.startswith("pg_"):
        return "pg"
    if constraint_type.startswith("qg_"):
        return "qg"
    if constraint_type.startswith("vm_"):
        return "vm"
    if constraint_type.startswith("thermal_"):
        return "thermal"
    if constraint_type.startswith("angle_"):
        return "angle"
    return constraint_type


def binding_summary_rows(base: dict[str, object], compared_levels: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = []
    families = sorted({family_of(str(row["constraint_type"])) for row in compared_levels})
    for family in ["all", *families]:
        subset = compared_levels if family == "all" else [
            row for row in compared_levels if family_of(str(row["constraint_type"])) == family
        ]
        if not subset:
            continue
        max_violation = max(safe_float(row["equality_only_violation"]) for row in subset)
        max_abs_value_diff = max(safe_float(row["abs_value_diff"]) for row in subset)
        max_abs_margin_diff = max(safe_float(row["abs_margin_diff"]) for row in subset)
        rows.append(
            {
                **base,
                "constraint_family": family,
                "n_constraints": len(subset),
                "n_binding_mismatches": sum(str(row["binding_relation"]) == "different" for row in subset),
                "n_only_equality_binding": sum(bool(row["equality_only_binding"]) and not bool(row["pm_binding"]) for row in subset),
                "n_only_pm_binding": sum(bool(row["pm_binding"]) and not bool(row["equality_only_binding"]) for row in subset),
                "n_equality_only_violations": sum(bool(row["equality_only_violates_limit"]) for row in subset),
                "n_pm_violations": sum(bool(row["pm_violates_limit"]) for row in subset),
                "max_equality_only_violation": max_violation,
                "max_abs_value_diff": max_abs_value_diff,
                "max_abs_margin_diff": max_abs_margin_diff,
            }
        )
    return rows


def find_reference_files(case_label: str, scenario: str) -> tuple[Path, Path, str]:
    stem = f"pglib_opf_case{case_label}"
    direct_case = REFERENCE_DIR / f"{stem}_{scenario}_case.json"
    direct_ref = REFERENCE_DIR / f"{stem}_{scenario}_reference.json"
    if disk_path(direct_case).exists() and disk_path(direct_ref).exists():
        return direct_case, direct_ref, scenario

    for reference_path in sorted(REFERENCE_DIR.glob(f"{stem}_*_reference.json")):
        suffix = reference_path.name[len(stem) + 1 : -len("_reference.json")]
        case_path = REFERENCE_DIR / f"{stem}_{suffix}_case.json"
        if not disk_path(case_path).exists():
            continue
        try:
            reference = load_powermodels_ac_reference(disk_path(reference_path))
        except OSError:
            continue
        scenario_candidates = {
            suffix,
            str(reference.get("scenario", "")),
            suffix.replace("p", "."),
        }
        if scenario in scenario_candidates:
            return case_path, reference_path, suffix

    raise FileNotFoundError(f"No PowerModels JSON case/reference files found for {case_label}/{scenario}.")


def material_reasons(row: pd.Series, args) -> list[str]:
    reasons: list[str] = []
    if abs(safe_float(row.get("objective_diff"))) > args.objective_abs_tol:
        reasons.append("objective_abs")
    if abs(safe_float(row.get("objective_rel_diff"))) > args.objective_rel_tol:
        reasons.append("objective_rel")
    for col, tol in [
        ("max_pg_diff", args.pg_tol),
        ("max_qg_diff", args.qg_tol),
        ("max_vm_diff", args.vm_tol),
        ("max_va_diff", args.va_tol),
        ("max_pf_diff", args.flow_tol),
        ("max_qf_diff", args.flow_tol),
    ]:
        if safe_float(row.get(col)) > tol:
            reasons.append(col)
    return reasons


def select_targets(args) -> list[dict[str, object]]:
    frame = pd.read_csv(SWEEP_SUMMARY_PATH)
    frame = frame[as_bool(frame["own_success"]) & as_bool(frame["pm_success"])].copy()
    if args.cases:
        frame = frame[frame["case"].astype(str).isin(set(args.cases))]
    if args.scenarios:
        frame = frame[frame["scenario"].astype(str).isin(set(args.scenarios))]

    selected_rows: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        reasons = material_reasons(row, args)
        if not reasons:
            continue
        selected = row.to_dict()
        selected["target_reasons"] = ";".join(reasons)
        selected_rows.append(selected)

    selected_rows.sort(
        key=lambda row: (
            -abs(safe_float(row.get("objective_rel_diff"))),
            -safe_float(row.get("max_pg_diff")),
            str(row.get("case")),
            str(row.get("scenario")),
        )
    )
    if args.max_targets is not None:
        selected_rows = selected_rows[: args.max_targets]
    return selected_rows


def append_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        pd.DataFrame(rows).to_csv(path, index=False)
    else:
        pd.DataFrame().to_csv(path, index=False)


def copy_outputs(paths: list[Path]) -> None:
    ORGANIZED_ROOT.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if path.exists():
            shutil.copy2(path, ORGANIZED_ROOT / f"python_pm_like_{path.name}")


def run_one(
    row: dict[str, object],
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
    case_path, reference_path, reference_suffix = find_reference_files(case_label, scenario)
    case = load_powercase_from_powermodels_json(disk_path(case_path))
    reference = load_powermodels_ac_reference(disk_path(reference_path))
    pm_outcome_class = classify_powermodels_ac_outcome(reference)
    target, pm_va_reference_offset = align_target_reference_angle(target_from_reference(reference), case.slack_bus_id)
    own = read_own_point(case_label, scenario)

    model = build_equality_only_model(case, target, own, reference, variant=variant, warm_start_source=warm_start_source)
    result, solve_time_sec, ipopt_diagnostics = solve_model(model, max_iter=max_iter, max_cpu_time=max_cpu_time)

    termination = str(result.status)
    solver_locally_solved = termination in SOLVED_TERMINATIONS
    max_pg, l1_pg = max_group_diff(result.pg, target["pg"])
    max_qg, l1_qg = max_group_diff(result.qg, target["qg"])
    max_vm, l1_vm = max_group_diff(result.vm, target["vm"])
    max_va, l1_va = max_group_diff(result.va, target["va"])
    l1_nonnegative = max(0.0, safe_float(result.objective))
    diagnostic_cost = generation_cost(case, result.pg, cost_power_units="pu") if result.pg else math.nan

    vm_eq, va_eq = result_arrays(case, result) if result.vm and result.va else (np.array([]), np.array([]))
    vm_pm, va_pm = target_arrays(case, target)
    data = model._ac_data
    eq_violations = (
        evaluate_ac_opf_constraint_violations(case, data, result.pg, result.qg, vm_eq, va_eq)
        if result.pg and result.qg and vm_eq.size
        else {}
    )
    pm_violations = evaluate_ac_opf_constraint_violations(case, data, target["pg"], target["qg"], vm_pm, va_pm)

    base_keys = {
        "case": case_label,
        "scenario": scenario,
        "reference_suffix": reference_suffix,
        "model_variant": "python_pm_like_zero",
        "diagnostic_mode": "equality_only",
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
        "equality_only_status": result.status,
        "equality_only_solver_locally_solved": solver_locally_solved,
        "equality_only_feasible_under_full_limits": bool(
            result.max_p_balance_violation <= 1e-5
            and result.max_q_balance_violation <= 1e-5
            and result.max_pg_limit_violation <= 1e-5
            and result.max_qg_limit_violation <= 1e-5
            and result.max_voltage_violation <= 1e-5
            and result.max_thermal_violation <= 1e-5
            and result.max_angle_violation <= 1e-5
        ),
        "equality_only_l1_deviation": result.objective,
        "equality_only_l1_deviation_nonnegative": l1_nonnegative,
        "equality_only_generation_cost": diagnostic_cost,
        "cost_diff_equality_minus_pm": diagnostic_cost - safe_float(reference.get("objective")) if math.isfinite(diagnostic_cost) else math.nan,
        "cost_rel_diff_equality_vs_pm": abs(diagnostic_cost - safe_float(reference.get("objective"))) / (1.0 + abs(safe_float(reference.get("objective")))) if math.isfinite(diagnostic_cost) else math.nan,
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
        **{f"equality_only_{key}": value for key, value in eq_violations.items()},
        **{f"pm_{key}": value for key, value in pm_violations.items()},
        **ipopt_diagnostics,
        "solver_message": result.message,
    }

    variable_rows: list[dict[str, object]] = []
    for variable, values, target_values in [
        ("pg", result.pg, target["pg"]),
        ("qg", result.qg, target["qg"]),
        ("vm", result.vm, target["vm"]),
        ("va", result.va, target["va"]),
    ]:
        for element_id, pm_value in target_values.items():
            if element_id not in values:
                continue
            eq_value = float(values[element_id])
            variable_rows.append(
                {
                    **base_keys,
                    "variable": variable,
                    "element_id": element_id,
                    "powermodels_value": float(pm_value),
                    "equality_only_value": eq_value,
                    "difference_equality_minus_pm": eq_value - float(pm_value),
                    "abs_difference": abs(eq_value - float(pm_value)),
                }
            )

    pm_level_rows = inequality_level_rows(case, target["pg"], target["qg"], vm_pm, va_pm, source="powermodels_reference", tol=binding_tol)
    eq_level_rows = inequality_level_rows(case, result.pg, result.qg, vm_eq, va_eq, source="equality_only_solution", tol=binding_tol) if result.pg and result.qg and vm_eq.size else []
    compared_levels = compare_inequality_levels(pm_level_rows, eq_level_rows, violation_tol=limit_violation_tol)
    for level_row in compared_levels:
        level_row.update(base_keys)
    binding_rows = binding_summary_rows(base_keys, compared_levels)
    return summary_row, variable_rows, compared_levels, binding_rows


def main() -> None:
    ensure_conda_solver_path()

    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", nargs="*", default=["full"], choices=sorted(VARIANT_TARGETS))
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

    selected = select_targets(args)
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
    write_csv(TARGETS_PATH, target_rows)
    copy_outputs([TARGETS_PATH])

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
                        "diagnostic_mode": "equality_only",
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
                        "equality_only_status": "ERROR",
                        "equality_only_solver_locally_solved": False,
                        "equality_only_feasible_under_full_limits": False,
                        "equality_only_l1_deviation": math.nan,
                        "equality_only_l1_deviation_nonnegative": math.nan,
                        "equality_only_generation_cost": math.nan,
                        "cost_diff_equality_minus_pm": math.nan,
                        "cost_rel_diff_equality_vs_pm": math.nan,
                        "solve_time_sec": math.nan,
                        "solve_iterations": math.nan,
                        "max_abs_pg_deviation": math.nan,
                        "total_l1_pg_deviation": math.nan,
                        "max_abs_qg_deviation": math.nan,
                        "total_l1_qg_deviation": math.nan,
                        "max_abs_vm_deviation": math.nan,
                        "total_l1_vm_deviation": math.nan,
                        "max_abs_va_deviation": math.nan,
                        "total_l1_va_deviation": math.nan,
                        "solver_message": str(exc),
                    }
                    variable_rows = []
                    level_rows = []
                    binding_rows = []
                    print(f"  ERROR: {exc}", flush=True)

                append_csv(SUMMARY_PATH, [summary_row])
                append_csv(VARIABLES_PATH, variable_rows)
                append_csv(LEVELS_PATH, level_rows)
                append_csv(BINDING_SUMMARY_PATH, binding_rows)
                print(
                    "  status="
                    f"{summary_row['equality_only_status']} "
                    f"L1={summary_row['equality_only_l1_deviation']} "
                    f"maxPg={summary_row['max_abs_pg_deviation']} "
                    f"maxVm={summary_row['max_abs_vm_deviation']}",
                    flush=True,
                )

    copy_outputs(outputs)
    print("Saved Python PM-like equality-only deviation outputs:", flush=True)
    for path in outputs:
        print(f"  {path}", flush=True)
    print(f"Copied meeting copies to {ORGANIZED_ROOT}", flush=True)


if __name__ == "__main__":
    main()
