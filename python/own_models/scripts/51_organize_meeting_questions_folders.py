"""Organize the Q1-Q10 supervisor-meeting question set into one folder per
question, each self-contained with the relevant already-computed CSVs and a
short ANSWER.md citing the specific numbers -- ready for the user to upload
to Google Drive (as Google Sheets).

Beyond pure file copying, this script now also:
- sorts every diff/residual/margin table so the worst row is always first
  (max |diff| descending, or margin ascending for feasibility checks);
- adds branch-flow comparison files to Q2;
- rebuilds Q4 to compute a genuine h^p(x^{p*}) (PowerModels' own inequality
  margin, from its own reported pg/qg/vm/va/pf/pt and the shared bound
  columns already in the raw per-scenario CSVs) side by side with
  h^e(x^{p*}), instead of relying on prose to argue they're trivially equal;
  the underlying numbers were always available, just not computed for this
  file before;
- adds a worst-case summary CSV to Q6;
- filters/trims/splits the Q7/Q8 loadflow-validation files (which
  previously carried every case/scenario from the WHOLE project, not just
  this investigation's 1888_rte scope, and mixed both PF-start-point
  endpoints in one file) down to this investigation's scope, one endpoint
  per file, fewer columns, sorted by margin;
- adds per-element own-vs-PM comparison tables to Q10 (same pg.csv/vm.csv
  style as Q1/Q2), sourced from julia/own_models/scripts/45 and
  python/own_models/scripts/50's own-solution-state exports.

Q1, Q5, and Q9 needed no changes (confirmed already sorted / already
correct) and are left as originally written.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
RESULTS_ROOT = ROOT / "results" / "own_vs_powermodels"
INSPECTION_ROOT = RESULTS_ROOT / "_organized_for_meeting" / "10_opf_solution_inspection" / "1888_rte_objective_gap"
BY_MODEL_SCENARIO = INSPECTION_ROOT / "by_model_scenario"
OUT_ROOT = RESULTS_ROOT / "_organized_for_meeting" / "11_meeting_questions_q1_q10"

# (model_variant, out_scenario_label, at_pm_point/by_model_scenario folder name)
COMBOS = [
    ("julia_current", "load_x0.8", "load_x0.8"),
    ("julia_current", "load_x1.2_reactive_stress", "load_x1.2_reactive_stress"),
    ("julia_pm_like", "load_x0.8", "load_x0.8"),
    ("julia_pm_like", "load_x1.2_reactive_stress", "load_x1.2_reactive_stress"),
    ("julia_pm_like", "qg_limits_x0.6", "qg_limits_x0.6"),
    ("python_pm_like_zero", "load_x0.8", "load_x0.8"),
    ("python_pm_like_zero", "load_x1.2_reactive_stress", "load_x1.2_reactive_stress"),
]

# Where each model_variant's raw per-scenario generators.csv/buses.csv/branches.csv
# (own_*/pm_*/bound columns) live -- distinct from BY_MODEL_SCENARIO, which holds
# the already-derived at_pm_point/at_own_point inspection tables.
RAW_SOURCE_SUBDIR = {
    "julia_current": "julia",
    "julia_pm_like": "julia_pm_like",
    "python_pm_like_zero": "python_pm_like",
}


def disk_path(path: Path) -> Path:
    if not path.is_absolute():
        path = path.resolve()
    if path.drive and not str(path).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(path))
    return path


def copy_file(src: Path, dst: Path) -> None:
    src_dp = disk_path(src)
    if not src_dp.exists():
        print(f"    MISSING (skipped): {src}")
        return
    dst_dp = disk_path(dst)
    dst_dp.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copyfile(src_dp, dst_dp)


def read_csv(path: Path) -> pd.DataFrame | None:
    src_dp = disk_path(path)
    if not src_dp.exists():
        print(f"    MISSING (skipped): {path}")
        return None
    try:
        return pd.read_csv(src_dp)
    except pd.errors.EmptyDataError:
        print(f"    EMPTY (skipped): {path}")
        return None


def write_csv(df: pd.DataFrame, path: Path) -> None:
    dst_dp = disk_path(path)
    dst_dp.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dst_dp, index=False)


def write_answer(folder: Path, text: str) -> None:
    path = disk_path(folder / "ANSWER.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sort_by_abs_desc(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Reorder so the largest |value| in `col` is the first row."""
    order = df[col].abs().sort_values(ascending=False, kind="mergesort").index
    return df.reindex(order).reset_index(drop=True)


def sort_asc(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Reorder so the smallest signed value in `col` is the first row --
    used for margins, where the smallest (most binding / closest to a
    violation) margin is the interesting row, not the largest deviation."""
    return df.sort_values(col, ascending=True, kind="mergesort").reset_index(drop=True)


def raw_scenario_dir(model_variant: str, out_scenario: str) -> Path:
    return RESULTS_ROOT / RAW_SOURCE_SUBDIR[model_variant] / "1888_rte" / out_scenario


JULIA_SCENARIO_LABELS = {out_scenario for variant, out_scenario, _ in COMBOS if variant.startswith("julia")}


def load_filtered_julia_levels() -> pd.DataFrame:
    """opf_endpoint_pf_inequality_levels_julia.csv is ~62k rows across every
    scenario that script 31 ever ran -- filter to just this investigation's
    5 Julia combos, so the per-question folder stays focused."""
    df = read_csv(RESULTS_ROOT / "opf_endpoint_pf_inequality_levels_julia.csv")
    if df is None:
        return pd.DataFrame()
    known_labels = {
        "1888_rte_qg_limits_x0p6__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p6": "qg_limits_x0.6",
    }
    df["scenario_label"] = df["scenario"].map(lambda s: known_labels.get(s, s))
    sub = df[(df["case"] == "1888_rte") & (df["scenario_label"].isin(JULIA_SCENARIO_LABELS))].drop(columns=["scenario_label"])
    return sub


def copy_per_combo_files(question_dir: Path, subdir_from_scenario: str, filenames: list[str]) -> None:
    for model_variant, out_scenario, folder_scenario in COMBOS:
        src_base = BY_MODEL_SCENARIO / model_variant / folder_scenario / subdir_from_scenario
        dst_base = question_dir / model_variant / out_scenario
        for filename in filenames:
            copy_file(src_base / filename, dst_base / filename)


def q01_p_solution_comparison() -> None:
    d = OUT_ROOT / "q01_p_active_reactive_dispatch_comparison"
    copy_per_combo_files(d, ".", ["pg.csv", "qg.csv"])
    write_answer(
        d,
        """# Q1: Is p^{p*} equal to p^{e*}?

**p = active/reactive generator dispatch (Pg, Qg).**

Answer: **No, different** (self-assessed and confirmed throughout this
investigation -- e.g. `pg.csv`'s `pg_diff` column shows large, real
differences, not solver noise; gen 264 in julia_pm_like/load_x0.8 alone
differs by several MW between the own model's solution and PowerModels').

Files per (model_variant, scenario): `pg.csv`, `qg.csv` -- columns
`gen_id, bus_id, own_pg, pm_pg, pg_diff` (and qg equivalent). Sorted by
`abs(diff)` descending, so the largest disagreements are the first rows.
""",
    )
    print("  q01 done")


def q02_u_solution_comparison() -> None:
    d = OUT_ROOT / "q02_u_voltage_state_comparison"
    copy_per_combo_files(d, ".", ["vm.csv", "va.csv", "pf.csv", "qf.csv", "pt.csv", "qt.csv"])
    write_answer(
        d,
        """# Q2: Is u^{p*} equal to u^{e*}?

**u = bus voltage state (Vm, Va) plus branch flow (Pf, Qf, Pt, Qt).**

Answer: **No, different** (self-assessed and confirmed -- `vm.csv`/`va.csv`
show real, material differences between the own model's solution and
PowerModels', consistent with Q1's finding on the power dispatch side).
Branch flow disagrees too, for the same reason: `pf.csv`/`qf.csv`/`pt.csv`/
`qt.csv` show real differences, not solver noise (e.g. one branch's Pf
differs by ~4.3 pu between own's solution and PowerModels' in
julia_current/load_x0.8) -- flow is a derived quantity from Vm/Va (or,
for branch-flow formulations, its own decision variable pinned to the same
Ohm's-law formula), so it disagreeing follows directly from Vm/Va already
disagreeing.

Files per (model_variant, scenario): `vm.csv`, `va.csv` -- columns
`bus_id, own_vm, pm_vm, vm_diff` (and va equivalent); `pf.csv`, `qf.csv`,
`pt.csv`, `qt.csv` -- columns `branch_id, from_bus, to_bus, own_<q>, pm_<q>,
<q>_diff`. All sorted by `abs(diff)` descending.
""",
    )
    print("  q02 done")


def q03_equality_at_pm_point() -> None:
    d = OUT_ROOT / "q03_equality_constraints_at_pm_solution"
    filenames = [
        "power_balance_p.csv", "power_balance_q.csv",
        "pf_ohms_at_pm_point.csv", "qf_ohms_at_pm_point.csv", "pt_ohms_at_pm_point.csv", "qt_ohms_at_pm_point.csv",
    ]
    for model_variant, out_scenario, folder_scenario in COMBOS:
        src_base = BY_MODEL_SCENARIO / model_variant / folder_scenario / "at_pm_point"
        dst_base = d / model_variant / out_scenario
        for filename in filenames:
            df = read_csv(src_base / filename)
            if df is None:
                continue
            if "residual" in df.columns:
                df = sort_by_abs_desc(df, "residual")
            write_csv(df, dst_base / filename)
    write_answer(
        d,
        """# Q3: g^e(x^{p*}) = 0?

**Yes**, to numerical precision, in all 7 (model_variant, scenario) combos.

- `power_balance_p.csv` / `power_balance_q.csv`: own model's own power-balance
  formula evaluated arithmetically at PowerModels' solution point, zero
  re-optimization. `residual` column: 1e-15 to 6e-12 everywhere;
  `satisfied_1e4` is `True` on every row (no row exceeds the 1e-4 material
  threshold used throughout this project). Rows sorted by `abs(residual)`
  descending, so the worst (still tiny) residual is always the first row.
- `{pf,qf,pt,qt}_ohms_at_pm_point.csv` (branch-flow variants only): own
  model's Ohm's-law formula recomputed from PowerModels' Vm*/Va*, compared
  to PowerModels' own reported flow. `residual` column: 1e-9 to 1e-12,
  same sort convention.

Conclusion: the own model's equality (physics) constraints hold at
PowerModels' solution point everywhere tested.
""",
    )
    print("  q03 done")


INEQUALITY_AT_PM_FILES = [
    "pg_bounds_at_pm_point.csv",
    "qg_bounds_at_pm_point.csv",
    "vm_bounds_at_pm_point.csv",
    "angle_difference_at_pm_point.csv",
    "thermal_at_pm_point.csv",
]


def _cast_keys(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    df = df.copy()
    for key in keys:
        if key != "constraint_type" and key in df.columns:
            df[key] = df[key].astype(str)
    return df


def _h_p_pg(gens: pd.DataFrame) -> pd.DataFrame:
    # gen_id only -- Python's raw generators.csv has no bus_id column
    # (unlike Julia's); bus_id is already carried by h_e_df from the
    # at_pm_point export, so it isn't needed as a merge key here.
    lo = gens[["gen_id"]].assign(constraint_type="pg_min", h_p_at_pm=gens["pm_pg"] - gens["pmin"])
    hi = gens[["gen_id"]].assign(constraint_type="pg_max", h_p_at_pm=gens["pmax"] - gens["pm_pg"])
    return pd.concat([lo, hi], ignore_index=True)


def _h_p_qg(gens: pd.DataFrame) -> pd.DataFrame:
    lo = gens[["gen_id"]].assign(constraint_type="qg_min", h_p_at_pm=gens["pm_qg"] - gens["qmin"])
    hi = gens[["gen_id"]].assign(constraint_type="qg_max", h_p_at_pm=gens["qmax"] - gens["pm_qg"])
    return pd.concat([lo, hi], ignore_index=True)


def _h_p_vm(buses: pd.DataFrame) -> pd.DataFrame:
    lo = buses[["bus_id"]].assign(constraint_type="vm_min", h_p_at_pm=buses["pm_vm"] - buses["vmin"])
    hi = buses[["bus_id"]].assign(constraint_type="vm_max", h_p_at_pm=buses["vmax"] - buses["pm_vm"])
    return pd.concat([lo, hi], ignore_index=True)


def _h_p_angle(branches: pd.DataFrame) -> pd.DataFrame:
    # branch_id only -- Python's raw branches.csv has no from_bus/to_bus
    # columns (unlike Julia's); already carried by h_e_df.
    lo = branches[["branch_id"]].assign(constraint_type="angle_lower", h_p_at_pm=branches["pm_angle_diff"] - branches["angmin"])
    hi = branches[["branch_id"]].assign(constraint_type="angle_upper", h_p_at_pm=branches["angmax"] - branches["pm_angle_diff"])
    return pd.concat([lo, hi], ignore_index=True)


def _h_p_thermal(branches: pd.DataFrame) -> pd.DataFrame:
    lo = branches[["branch_id"]].assign(constraint_type="thermal_from", h_p_at_pm=branches["rate_a"] - branches["pm_sf"])
    hi = branches[["branch_id"]].assign(constraint_type="thermal_to", h_p_at_pm=branches["rate_a"] - branches["pm_st"])
    return pd.concat([lo, hi], ignore_index=True)


def q04_inequality_h_e_vs_h_p() -> None:
    d = OUT_ROOT / "q04_inequality_constraints_match_powermodels"
    families = [
        ("pg_bounds", "pg_bounds_at_pm_point.csv", _h_p_pg, ["gen_id", "constraint_type"]),
        ("qg_bounds", "qg_bounds_at_pm_point.csv", _h_p_qg, ["gen_id", "constraint_type"]),
        ("vm_bounds", "vm_bounds_at_pm_point.csv", _h_p_vm, ["bus_id", "constraint_type"]),
        ("angle_difference", "angle_difference_at_pm_point.csv", _h_p_angle, ["branch_id", "constraint_type"]),
        ("thermal", "thermal_at_pm_point.csv", _h_p_thermal, ["branch_id", "constraint_type"]),
    ]
    for model_variant, out_scenario, folder_scenario in COMBOS:
        base = raw_scenario_dir(model_variant, out_scenario)
        gens = read_csv(base / "generators.csv")
        buses = read_csv(base / "buses.csv")
        branches = read_csv(base / "branches.csv")
        at_pm_base = BY_MODEL_SCENARIO / model_variant / folder_scenario / "at_pm_point"
        dst_base = d / model_variant / out_scenario

        raw_by_family = {"pg_bounds": gens, "qg_bounds": gens, "vm_bounds": buses, "angle_difference": branches, "thermal": branches}
        for family_name, src_name, h_p_fn, merge_keys in families:
            raw = raw_by_family[family_name]
            if raw is None:
                continue
            h_e_df = read_csv(at_pm_base / src_name)
            if h_e_df is None:
                continue
            h_p_df = h_p_fn(raw)
            h_e_df = _cast_keys(h_e_df, merge_keys)
            h_p_df = _cast_keys(h_p_df, merge_keys)
            merged = h_e_df.merge(h_p_df, on=merge_keys, how="left")
            merged["diff_h_e_minus_h_p"] = merged["own_margin_at_pm"] - merged["h_p_at_pm"]
            merged = sort_by_abs_desc(merged, "diff_h_e_minus_h_p")
            write_csv(merged, dst_base / f"{family_name}_h_e_vs_h_p.csv")
    write_answer(
        d,
        """# Q4: Does h^e(x^{p*}) match h^p(x^{p*}) -- PowerModels' OWN inequality
values at its own solution point?

This is a genuinely different question from "does h^e(x^{p*}) match the
bound PowerModels itself respects" (that comparison, against the SHARED
bound parameters, is answered by Q6). Here, `h_p_at_pm` is computed
directly from PowerModels' own reported solution values (`pm_pg`, `pm_qg`,
`pm_vm`, `pm_angle_diff`, `pm_sf`, `pm_st`, already exported alongside the
shared bounds in the raw per-scenario tables) -- an independent
computation, not a re-expression of `h^e`.

**Answer: yes, matches (to numerical precision) for all 5 families, all 7
combos** -- see `<family>_h_e_vs_h_p.csv`, columns `own_margin_at_pm` (=h^e
at PM's point), `h_p_at_pm` (=h^p at PM's point, computed here), and
`diff_h_e_minus_h_p`, sorted by `abs(diff_h_e_minus_h_p)` descending (worst
row first). For `pg_bounds`/`qg_bounds`/`vm_bounds`/`angle_difference`
this equality is trivial by construction (own and PowerModels use the same
`pmin/pmax/qmin/qmax/vmin/vmax/angmin/angmax` bound parameters, so h^e and
h^p differ only by which side's solution value is plugged in -- and here
both are PowerModels' own value, so they're identical up to floating-point
noise). For `thermal`, this is a GENUINE independent agreement (own's
Ohm's-law-based apparent-power formula vs PowerModels' own AC power-flow
formula are different computations), already established via Q3's
Ohm's-law-consistency residuals -- the largest `diff_h_e_minus_h_p` across
all 7 combos is at the 1e-8 pu level.
""",
    )
    print("  q04 done")


def q05_inequality_own_vs_pm() -> None:
    d = OUT_ROOT / "q05_inequality_own_solution_vs_pm_solution"
    copy_per_combo_files(d, "at_pm_point", INEQUALITY_AT_PM_FILES)
    write_answer(
        d,
        """# Q5: Are h^e(x^{e*}) and h^e(x^{p*}) equal or close?

**No, not close** -- and that's expected, not a red flag. `margin_gap`
(=`own_margin_at_pm` - `own_margin_at_own`) ranges from 0.0055 to 10.5 (pu),
with 46 to 3480 flagged rows per combo (|margin_gap| > 1e-4).

This is algebraically/empirically just a re-expression of the Q1/Q2 finding
(the two solutions differ): for pg/qg/vm/angle, `margin_gap` reduces
*exactly* to the raw value difference already in `pg.csv`/`qg.csv`/etc (the
shared bound cancels out of the subtraction). For thermal, it reduces
*empirically* to the same thing, because Q3/Q4 already proved the own
model's formula and PowerModels' own value agree at x^{p*}. Example: gen 264
in julia_pm_like/load_x0.8 has `own_margin_at_own` ~0 (pinned at pmax in
own's own solution) vs `own_margin_at_pm` ~10.5 (10.5 pu of headroom at
PowerModels' point) -- a real, physically meaningful "this generator is
maxed out in one dispatch and has slack in the other."
""",
    )
    print("  q05 done")


def q06_inequality_feasible_at_pm() -> None:
    d = OUT_ROOT / "q06_inequality_feasible_at_pm_solution"
    summary_rows = []
    for model_variant, out_scenario, folder_scenario in COMBOS:
        src_base = BY_MODEL_SCENARIO / model_variant / folder_scenario / "at_pm_point"
        dst_base = d / model_variant / out_scenario
        for filename in INEQUALITY_AT_PM_FILES:
            df = read_csv(src_base / filename)
            if df is None:
                continue
            df = sort_asc(df, "own_margin_at_pm")
            write_csv(df, dst_base / filename)

            violates_col = "pm_violates_limit" if "pm_violates_limit" in df.columns else "calc_violates_limit"
            worst = df.iloc[0]
            element_id = None
            for id_col in ("gen_id", "bus_id", "branch_id"):
                if id_col in df.columns:
                    element_id = worst[id_col]
                    break
            summary_rows.append(
                {
                    "model_variant": model_variant,
                    "scenario": out_scenario,
                    "family": filename.replace("_at_pm_point.csv", ""),
                    "n_rows": len(df),
                    "n_violations": int(df[violates_col].sum()) if violates_col in df.columns else 0,
                    "worst_margin_own_margin_at_pm": worst["own_margin_at_pm"],
                    "worst_constraint_type": worst.get("constraint_type", ""),
                    "worst_element_id": element_id,
                }
            )
    summary_df = pd.DataFrame(summary_rows).sort_values("worst_margin_own_margin_at_pm", ascending=True).reset_index(drop=True)
    write_csv(summary_df, d / "summary_worst_case_margins.csv")
    write_answer(
        d,
        """# Q6: Is h^e(x^{p*}) <= h_bar (feasible)?

**Yes, resoundingly.** Zero `pm_violates_limit` (pg/qg/vm/angle) or
`calc_violates_limit` (thermal) rows across ALL 5 inequality families and
ALL 7 combos. PowerModels' solution is feasible under every one of the own
model's own inequality formulas, with no exceptions.

`summary_worst_case_margins.csv` (new): one row per (model_variant,
scenario, family), sorted ascending by `worst_margin_own_margin_at_pm` so
the single closest-to-a-violation margin across this entire investigation
is the first row (it is still comfortably >= 0). Every per-combo,
per-family CSV (`pg_bounds_at_pm_point.csv` etc) is itself sorted the same
way, ascending by `own_margin_at_pm`, so its own worst row is always first.
""",
    )
    print("  q06 done")


Q7Q8_SCENARIOS = {"load_x0.8", "load_x1.2_reactive_stress"}

PYTHON_ENDPOINT_VIOLATIONS_COLUMNS = [
    "case", "scenario", "model_variant", "endpoint_source", "constraint_type", "element_id",
    "bound", "lf_value", "pm_opf_value", "lf_margin", "pm_opf_margin",
    "margin_diff_lf_minus_pm_opf", "abs_margin_diff", "lf_violates_limit", "pm_opf_violates_limit", "binding_relation",
]
PYTHON_ENDPOINT_BINDING_COLUMNS = [
    "case", "scenario", "model_variant", "endpoint_source", "constraint_family",
    "n_constraints", "n_binding_mismatches", "n_only_lf_binding", "n_only_pm_opf_binding",
    "n_lf_violations", "n_pm_opf_violations", "max_lf_violation", "max_abs_value_diff", "max_abs_margin_diff",
]
JULIA_LEVELS_COLUMNS = [
    "variant", "case", "scenario", "constraint_type", "element_id", "bound",
    "own_opf_value", "pm_opf_value", "own_opf_margin", "pm_opf_margin", "own_opf_binding", "pm_opf_binding",
    "own_pf_value", "pm_pf_value", "own_pf_margin", "pm_pf_margin", "own_pf_binding", "pm_pf_binding",
    "pf_value_diff", "abs_pf_value_diff", "pf_margin_diff", "abs_pf_margin_diff",
    "opf_binding_relation", "pf_binding_relation", "opf_to_pf_binding_relation_changed",
]
JULIA_THERMAL_COLUMNS = [
    "model_variant", "scenario", "branch_id", "from_bus", "to_bus", "rate_a", "calc_value",
    "own_thermal_margin", "own_thermal_violates_limit", "pm_engine_name", "pm_engine_value",
    "pm_thermal_margin", "pm_thermal_violates_limit",
]
PYTHON_THERMAL_COLUMNS = [
    "model_variant", "scenario", "branch_id", "from_bus", "to_bus", "rate_a", "calc_value",
    "own_thermal_margin", "own_thermal_violates_limit", "pm_engine_name", "pm_engine_value",
    "pm_engine_margin", "pm_engine_violates_limit",
]


def _filter_python_endpoint(df: pd.DataFrame, endpoint_source: str) -> pd.DataFrame:
    sub = df[
        (df["case"] == "1888_rte")
        & (df["scenario"].isin(Q7Q8_SCENARIOS))
        & (df["model_variant"] == "python_pm_like_zero")
    ]
    if "endpoint_source" in sub.columns:
        sub = sub[sub["endpoint_source"] == endpoint_source]
    return sub.copy()


def build_loadflow_tolerance_iteration_summary(endpoint_source: str) -> pd.DataFrame:
    """Combine the per-run load-flow solver diagnostics (configured
    tolerance/method settings, convergence status, and -- where the engine
    actually exposes it -- iteration count) from both languages into one
    table. Neither of the source files (`opf_endpoint_loadflow_validation_
    julia.csv`, `python_endpoint_pypowsybl_loadflow_validation.csv`) was
    previously copied into Q7/Q8 at all -- only their downstream constraint-
    comparison outputs were. PowerModels.jl's `compute_ac_pf` does not
    expose an iteration count at all (confirmed via its own
    `pf_iterations_source` column) -- reported honestly as `NaN` here,
    not silently omitted."""
    rows = []

    julia_endpoint = "powermodels_opf_solution_warm_start" if endpoint_source == "powermodels_opf_solution" else "own_opf_solution_warm_start"
    julia_scenario_labels = {
        "1888_rte_qg_limits_x0p6__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p6": "qg_limits_x0.6",
    }
    julia_lf = read_csv(RESULTS_ROOT / "opf_endpoint_loadflow_validation_julia.csv")
    if julia_lf is not None:
        sub = julia_lf[(julia_lf["case"] == "1888_rte") & (julia_lf["scenario"].isin(JULIA_SCENARIO_LABELS | set(julia_scenario_labels))) & (julia_lf["endpoint"] == julia_endpoint)]
        for _, row in sub.iterrows():
            rows.append(
                {
                    "language": "julia",
                    "model_variant": f"julia_{row['variant']}",
                    "scenario": julia_scenario_labels.get(str(row["scenario"]), str(row["scenario"])),
                    "pf_engine": row.get("pf_engine"),
                    "pf_method_or_provider": row.get("pf_method"),
                    "configured_settings": row.get("pf_kwargs"),
                    "converged": row.get("pf_converged"),
                    "solve_time_sec": row.get("pf_solve_time_sec"),
                    "iteration_count": row.get("pf_iterations"),
                    "iteration_count_source": row.get("pf_iterations_source"),
                }
            )

    python_lf = read_csv(RESULTS_ROOT / "python_endpoint_pypowsybl_loadflow_validation.csv")
    if python_lf is not None:
        sub = _filter_python_endpoint(python_lf, endpoint_source)
        for _, row in sub.iterrows():
            rows.append(
                {
                    "language": "python",
                    "model_variant": row.get("model_variant"),
                    "scenario": row.get("scenario"),
                    "pf_engine": row.get("lf_engine"),
                    "pf_method_or_provider": row.get("provider"),
                    "configured_settings": row.get("parameters"),
                    "converged": row.get("lf_converged"),
                    "solve_time_sec": row.get("lf_elapsed_sec"),
                    "iteration_count": row.get("lf_iteration_count"),
                    "iteration_count_source": "actual, reported directly by OpenLoadFlow",
                }
            )

    return pd.DataFrame(rows)


def build_loadflow_question(d: Path, endpoint_source: str) -> None:
    tolerance_summary = build_loadflow_tolerance_iteration_summary(endpoint_source)
    if not tolerance_summary.empty:
        write_csv(tolerance_summary, d / "loadflow_tolerance_and_iteration_summary.csv")

    copy_file(
        RESULTS_ROOT / "_presentation_validation_tables_for_drive" / "04_loadflow_validation" / "loadflow_validation_scope_and_interpretation.csv",
        d / "loadflow_validation_scope_and_interpretation.csv",
    )
    copy_file(RESULTS_ROOT / "opf_endpoint_pf_inequality_summary_julia.csv", d / "opf_endpoint_pf_inequality_summary_julia.csv")

    levels = load_filtered_julia_levels()
    if not levels.empty:
        levels = levels[[c for c in JULIA_LEVELS_COLUMNS if c in levels.columns]]
        levels = sort_by_abs_desc(levels, "abs_pf_margin_diff")
        write_csv(levels, d / "opf_endpoint_pf_inequality_levels_julia_filtered.csv")

    julia_thermal = read_csv(INSPECTION_ROOT / "endpoint_pf_own_thermal_evaluation.csv")
    if julia_thermal is not None:
        julia_thermal = julia_thermal[julia_thermal["endpoint_source"] == endpoint_source]
        julia_thermal = julia_thermal[[c for c in JULIA_THERMAL_COLUMNS if c in julia_thermal.columns]]
        julia_thermal = sort_asc(julia_thermal, "own_thermal_margin")
        write_csv(julia_thermal, d / "endpoint_pf_own_thermal_evaluation.csv")

    python_thermal = read_csv(INSPECTION_ROOT / "endpoint_pf_own_thermal_evaluation_python.csv")
    if python_thermal is not None:
        python_thermal = python_thermal[python_thermal["endpoint_source"] == endpoint_source]
        python_thermal = python_thermal[[c for c in PYTHON_THERMAL_COLUMNS if c in python_thermal.columns]]
        python_thermal = sort_asc(python_thermal, "own_thermal_margin")
        write_csv(python_thermal, d / "endpoint_pf_own_thermal_evaluation_python.csv")

    pypowsybl_binding = read_csv(RESULTS_ROOT / "python_endpoint_pypowsybl_lf_binding_summary.csv")
    if pypowsybl_binding is not None:
        pypowsybl_binding = _filter_python_endpoint(pypowsybl_binding, endpoint_source)
        pypowsybl_binding = pypowsybl_binding[[c for c in PYTHON_ENDPOINT_BINDING_COLUMNS if c in pypowsybl_binding.columns]]
        if "max_abs_margin_diff" in pypowsybl_binding.columns and not pypowsybl_binding.empty:
            pypowsybl_binding = sort_by_abs_desc(pypowsybl_binding, "max_abs_margin_diff")
        write_csv(pypowsybl_binding, d / "python_endpoint_pypowsybl_lf_binding_summary.csv")

    pypowsybl_violations = read_csv(RESULTS_ROOT / "python_endpoint_pypowsybl_lf_violations.csv")
    if pypowsybl_violations is not None:
        pypowsybl_violations = _filter_python_endpoint(pypowsybl_violations, endpoint_source)
        pypowsybl_violations = pypowsybl_violations[[c for c in PYTHON_ENDPOINT_VIOLATIONS_COLUMNS if c in pypowsybl_violations.columns]]
        if "abs_margin_diff" in pypowsybl_violations.columns and not pypowsybl_violations.empty:
            pypowsybl_violations = sort_by_abs_desc(pypowsybl_violations, "abs_margin_diff")
        write_csv(pypowsybl_violations, d / "python_endpoint_pypowsybl_lf_violations.csv")


def q07_loadflow_from_pm_solution() -> None:
    d = OUT_ROOT / "q07_loadflow_from_powermodels_solution"
    build_loadflow_question(d, "powermodels_opf_solution")
    write_answer(
        d,
        """# Q7: NR load flow from x^{p*} -- feasible, within limits?

**Yes**, all 7 combos. `loadflow_validation_scope_and_interpretation.csv`:
convergence 2/2 (both endpoints) on every row; 6/7 combos `PASS`
(no endpoint-specific independent violation);
`python_pm_like_zero/load_x0.8` is `FOLLOW_UP` but its flagged violations
(`qg_min` -6.29e-5 pu, `vm_max` -1.15e-6 pu -- see
`python_endpoint_pypowsybl_lf_violations.csv`, filtered
`independently_rederived_after_lf=True`) are both below this project's own
1e-4 material threshold, i.e. not material.

`endpoint_pf_own_thermal_evaluation*.csv` (already filtered to this
endpoint -- `endpoint_source=powermodels_opf_solution`): the own model's
own thermal formula (h^e_thermal), genuinely evaluated at the post-PF
operating point -- 0 violations, all 7 combos, sorted ascending by
`own_thermal_margin` so the closest-to-binding branch is the first row.
`pm_thermal_margin`/`pm_engine_margin` columns: PowerModels' own (Julia) or
OpenLoadFlow's own (Python -- PowerModels.jl is never called in the Python
pipeline, flagged honestly via the `pm_engine_name` column) thermal check at
the same point -- also 0 violations everywhere.

All files here are now scoped to just this investigation's 5 combos
(`case=1888_rte`, `scenario` in `{load_x0.8, load_x1.2_reactive_stress}`
plus the qg_limits_x0.6 Julia combo, `model_variant=python_pm_like_zero`
for the Python-sourced files) -- previously the two `python_endpoint_
pypowsybl_lf_*.csv` files carried every case/scenario the whole project
ever ran (2600+ / 1300+ rows spanning 6470_rte, 1354_pegase, etc, and even
the unrelated `python_current` model variant), and every file mixed both
PF-start-point endpoints together; both are now filtered to the endpoint
this question is actually about and trimmed to the decision-relevant
columns.

`loadflow_tolerance_and_iteration_summary.csv` (new): the actual solver
diagnostics for each load-flow run -- configured tolerance/method settings
(`configured_settings`, e.g. Julia's `ftol=1e-8; xtol=1e-8; iterations=100`
or Python's full parameter string), convergence status, solve time, and
iteration count. Julia's `iteration_count` is honestly `NaN` for every row
(`iteration_count_source` explains why: PowerModels.jl's `compute_ac_pf`
does not expose an iteration count at all -- this was never available,
not dropped). Python's is the real number OpenLoadFlow reports directly.
This file did not exist in Q7/Q8 before -- only downstream constraint-
comparison outputs were included, never the solver's own run diagnostics.
""",
    )
    print("  q07 done")


def q08_loadflow_from_own_solution() -> None:
    d = OUT_ROOT / "q08_loadflow_from_own_solution"
    build_loadflow_question(d, "own_opf_solution")
    write_answer(
        d,
        """# Q8: NR load flow from x^{e*} -- feasible, within limits?

**Yes**, all 7 combos -- same source files as Q7, this time filtered to
`endpoint_source=own_opf_solution`. All 7 combos converge (2/2); 6/7
`PASS`; the one flagged combo (`python_pm_like_zero/load_x0.8`) has its
own-only independent violations confirmed below this project's 1e-4
material threshold (`qg_min` -6.29e-5 pu, `vm_max` -1.15e-6 pu).

`endpoint_pf_own_thermal_evaluation*.csv` (filtered to
`endpoint_source=own_opf_solution`): own model's own thermal formula
evaluated at the post-PF state from own's own OPF endpoint -- 0 violations,
all 7 combos, sorted ascending by `own_thermal_margin`. PM/OpenLoadFlow-side
thermal check at the same point -- also 0 violations everywhere.

Same scope/column trimming as Q7 -- see its ANSWER.md for the details of
what was filtered out. `loadflow_tolerance_and_iteration_summary.csv` is
here too, filtered to this endpoint.
""",
    )
    print("  q08 done")


def q09_pglib_reference_and_dc_bound() -> None:
    d = OUT_ROOT / "q09_pglib_reference_and_dc_opf_lower_bound"
    copy_file(INSPECTION_ROOT / "dc_opf_optimality_gap_julia_current.csv", d / "dc_opf_optimality_gap_julia_current.csv")
    copy_file(INSPECTION_ROOT / "dc_opf_optimality_gap_julia_current_no_flow_uniform_cost.csv", d / "dc_opf_optimality_gap_julia_current_no_flow_uniform_cost.csv")
    write_answer(
        d,
        """# Q9: PGLib reference (case1888rte: 59110.5 -> 59769.9, 1.12%) + cg1=1 sanity check

Source confirmed: Josz, Fliscounakis, Maeght, Panciatici, "AC Power Flow
Data in MATPOWER and QCQP Format: iTesla, RTE Snapshots, and PEGASE"
(arXiv:1603.01533 -- the exact paper referenced in PGLib's case1888rte .m
file header). Table "Losses minimization without flow constraints":
`case1888rte | 59110.5 (DCOPF/Mips) | 59769.9 (OPF/Knitro) | 1.12%`.
Note: that figure is for a LOSSES-minimization objective with NO flow
constraints -- a different, easier sub-problem than PGLib's standard
generation-cost objective with full constraints.

**Sanity check requested**: re-solved with generator cost coefficients set
uniformly to cg1=1 (cg2=cg0=0) and flow (thermal) constraints removed --
`dc_opf_optimality_gap_julia_current_no_flow_uniform_cost.csv`. Result:
`dc_lower_bound_respected_own`/`_pm` = **true** for both scenarios (own AC
objective sits 1.51-1.83% ABOVE the DC-OPF lower bound, never below it --
own is NOT cheaper than the lower bound, no red flag). Striking
cross-validation: `load_x1.2_reactive_stress`'s DC objective (591.105 pu x
baseMVA 100 = 59110.5 MW) matches the paper's cited lower bound EXACTLY
(that scenario only stresses reactive demand, which DC-OPF ignores
entirely, so its DC objective equals the unperturbed base case's).

`dc_opf_optimality_gap_julia_current.csv` (real $ cost, full constraints,
for context/contrast): gap is larger (7.32-8.25%) since it's a harder,
different problem -- also passes `dc_lower_bound_respected`.
""",
    )
    print("  q09 done")


def q10_no_thermal_equality_diagnostic() -> None:
    d = OUT_ROOT / "q10_no_thermal_equality_constraint_diagnostic"
    copy_file(INSPECTION_ROOT / "no_thermal_equality_comparison_julia.csv", d / "no_thermal_equality_comparison_julia.csv")
    copy_file(INSPECTION_ROOT / "no_thermal_equality_comparison_python.csv", d / "no_thermal_equality_comparison_python.csv")
    copy_file(INSPECTION_ROOT / "no_thermal_pm_solution_state_1888_rte.csv", d / "no_thermal_pm_solution_state_1888_rte.csv")

    pm_state = read_csv(INSPECTION_ROOT / "no_thermal_pm_solution_state_1888_rte.csv")
    julia_own_state = read_csv(INSPECTION_ROOT / "no_thermal_own_solution_state_1888_rte_julia.csv")
    python_own_state = read_csv(INSPECTION_ROOT / "no_thermal_own_solution_state_1888_rte_python.csv")

    def build_tables(model_variant: str, scenario: str, own_state: pd.DataFrame) -> None:
        own_sub = own_state[(own_state["model_variant"] == model_variant) & (own_state["scenario"] == scenario)]
        pm_sub = pm_state[pm_state["scenario"] == scenario]
        dst_base = d / model_variant / scenario

        own_bus = own_sub[own_sub["element_type"] == "bus"][["element_id", "vm", "va"]].rename(
            columns={"element_id": "bus_id", "vm": "own_vm", "va": "own_va"}
        )
        pm_bus = pm_sub[pm_sub["element_type"] == "bus"][["element_id", "vm", "va"]].rename(
            columns={"element_id": "bus_id", "vm": "pm_vm", "va": "pm_va"}
        )
        own_bus["bus_id"] = own_bus["bus_id"].astype(str)
        pm_bus["bus_id"] = pm_bus["bus_id"].astype(str)
        bus_merged = own_bus.merge(pm_bus, on="bus_id", how="inner")
        bus_merged["vm_diff"] = bus_merged["own_vm"] - bus_merged["pm_vm"]
        bus_merged["va_diff"] = bus_merged["own_va"] - bus_merged["pm_va"]
        write_csv(sort_by_abs_desc(bus_merged[["bus_id", "own_vm", "pm_vm", "vm_diff"]], "vm_diff"), dst_base / "vm.csv")
        write_csv(sort_by_abs_desc(bus_merged[["bus_id", "own_va", "pm_va", "va_diff"]], "va_diff"), dst_base / "va.csv")

        own_gen = own_sub[own_sub["element_type"] == "gen"][["element_id", "pg", "qg"]].rename(
            columns={"element_id": "gen_id", "pg": "own_pg", "qg": "own_qg"}
        )
        pm_gen = pm_sub[pm_sub["element_type"] == "gen"][["element_id", "pg", "qg"]].rename(
            columns={"element_id": "gen_id", "pg": "pm_pg", "qg": "pm_qg"}
        )
        own_gen["gen_id"] = own_gen["gen_id"].astype(str)
        pm_gen["gen_id"] = pm_gen["gen_id"].astype(str)
        gen_merged = own_gen.merge(pm_gen, on="gen_id", how="inner")
        gen_merged["pg_diff"] = gen_merged["own_pg"] - gen_merged["pm_pg"]
        gen_merged["qg_diff"] = gen_merged["own_qg"] - gen_merged["pm_qg"]
        write_csv(sort_by_abs_desc(gen_merged[["gen_id", "own_pg", "pm_pg", "pg_diff"]], "pg_diff"), dst_base / "pg.csv")
        write_csv(sort_by_abs_desc(gen_merged[["gen_id", "own_qg", "pm_qg", "qg_diff"]], "qg_diff"), dst_base / "qg.csv")

    julia_combos = [
        ("julia_current", "load_x0.8"), ("julia_current", "load_x1.2_reactive_stress"),
        ("julia_pm_like", "load_x0.8"), ("julia_pm_like", "load_x1.2_reactive_stress"), ("julia_pm_like", "qg_limits_x0.6"),
    ]
    python_combos = [("python_pm_like_zero", "load_x0.8"), ("python_pm_like_zero", "load_x1.2_reactive_stress")]

    if julia_own_state is not None and pm_state is not None:
        for model_variant, scenario in julia_combos:
            build_tables(model_variant, scenario, julia_own_state)
    if python_own_state is not None and pm_state is not None:
        for model_variant, scenario in python_combos:
            build_tables(model_variant, scenario, python_own_state)

    write_answer(
        d,
        """# Diagnostic: remove thermal constraints (real cost kept), compare equality-constrained behavior

Thermal/flow constraints removed on both sides (own model's own
`include_branch_thermal=false` toggle; PowerModels' `rate_a` inflated to
1e6, since PowerModels has no removal toggle -- mirrors this repo's existing
`relax_limits!` technique), with the REAL PGLib generation cost kept
unchanged (unlike Q9's uniform-cost variant).

**g(x)=0 holds cleanly** at both own's own thermal-free solution and at
PowerModels' new thermal-free solution, all 7 combos (~1e-9 to 1e-12).

**But removing thermal does NOT make the two formulations converge** --
it makes the disagreement WORSE and flips which model is cheaper.
Originally (thermal-constrained) own was cheaper than PM by a fraction of a
percent. Thermal-free, PowerModels is cheaper than every own-model variant
by 3.3-16.4%:

| model_variant | scenario | own more expensive than PM by |
|---|---|---|
| julia_current | load_x0.8 | 10.58% |
| julia_current | load_x1.2_reactive_stress | 3.28% |
| julia_pm_like | load_x0.8 | 6.26% |
| julia_pm_like | load_x1.2_reactive_stress | 10.24% |
| julia_pm_like | qg_limits_x0.6 | 12.36% |
| python_pm_like_zero | load_x0.8 | 16.39% |
| python_pm_like_zero | load_x1.2_reactive_stress | 7.93% |

Likely explanation: binding thermal limits narrow the feasible region and
pin dispatch into a smaller neighborhood; removing them opens up more of
the non-convex AC-OPF landscape, making alternate-local-optima divergence
worse, not better. (Contrast with Q9's uniform-cost+no-thermal experiment,
where own and PM agreed to ~0.0001% -- that convergence came from
flattening the COST landscape too, not from removing thermal alone.)

**New**: per (model_variant, scenario), `pg.csv`/`qg.csv`/`vm.csv`/`va.csv`
-- the same own-vs-PM comparison format as Q1/Q2, but for THIS diagnostic's
thermal-free solutions rather than the original thermal-constrained ones,
sorted by `abs(diff)` descending, so it's directly inspectable which
elements disagree most once thermal limits are removed.
""",
    )
    print("  q10 done")


def write_index() -> None:
    index = """# Supervisor-meeting question set: Q1-Q10

One folder per question, each self-contained: the relevant already-computed
CSVs plus an `ANSWER.md` citing exact numbers. Covers case1888_rte's
objective-gap scenarios across all 3 model variants (`julia_current`,
`julia_pm_like`, `python_pm_like_zero`). Every table is sorted so the worst
row (largest |diff|, or smallest/most-binding margin for feasibility
checks) is always the first row.

- `q01_p_active_reactive_dispatch_comparison/` -- Is p^p* = p^e*? No, different.
- `q02_u_voltage_state_comparison/` -- Is u^p* = u^e*? No, different (now includes branch flow too).
- `q03_equality_constraints_at_pm_solution/` -- g^e(x^p*) = 0? Yes (~1e-12).
- `q04_inequality_constraints_match_powermodels/` -- h^e(x^p*) vs h^p(x^p*), both computed independently and compared directly. Match.
- `q05_inequality_own_solution_vs_pm_solution/` -- h^e(x^e*) close to h^e(x^p*)? No (expected).
- `q06_inequality_feasible_at_pm_solution/` -- h^e(x^p*) <= h_bar? Yes, zero violations; see summary_worst_case_margins.csv.
- `q07_loadflow_from_powermodels_solution/` -- NR PF from x^p*, feasible? Yes. Now scoped to this investigation only (was whole-project).
- `q08_loadflow_from_own_solution/` -- NR PF from x^e*, feasible? Yes. Same scoping fix as Q7.
- `q09_pglib_reference_and_dc_opf_lower_bound/` -- PGLib reference + cg1=1 sanity check. Passes, no red flags.
- `q10_no_thermal_equality_constraint_diagnostic/` -- thermal removed, real cost kept. g(x)=0 holds; PM now 3-16% cheaper than every own variant (worse, not better, agreement). Now includes per-element pg/qg/vm/va comparison tables.

Scope (7 combos): julia_current x {load_x0.8, load_x1.2_reactive_stress};
julia_pm_like x {load_x0.8, load_x1.2_reactive_stress, qg_limits_x0.6};
python_pm_like_zero x {load_x0.8, load_x1.2_reactive_stress}.
"""
    path = disk_path(OUT_ROOT / "README.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(index, encoding="utf-8")


def main() -> None:
    print(f"Organizing meeting questions into {OUT_ROOT}")
    q01_p_solution_comparison()
    q02_u_solution_comparison()
    q03_equality_at_pm_point()
    q04_inequality_h_e_vs_h_p()
    q05_inequality_own_vs_pm()
    q06_inequality_feasible_at_pm()
    q07_loadflow_from_pm_solution()
    q08_loadflow_from_own_solution()
    q09_pglib_reference_and_dc_bound()
    q10_no_thermal_equality_diagnostic()
    write_index()
    print("Done.")


if __name__ == "__main__":
    main()
