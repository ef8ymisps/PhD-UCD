from __future__ import annotations

import pandas as pd

from own_models.src.data.case import PowerCase


def classify_binding(own_flag: bool, pm_flag: bool) -> str:
    """Classify whether a constraint is binding in own model, PowerModels, both, or neither."""
    if own_flag and pm_flag:
        return "both"
    if own_flag:
        return "only_own"
    if pm_flag:
        return "only_pm"
    return "neither"


def classify_mismatch_reason(
    abs_diff: float, scale: float, *, tol: float = 1e-6, rel_threshold: float = 0.01
) -> str:
    """Classify an only_own/only_pm mismatch as numerical noise vs a real modelling difference.

    ``scale`` should be the constraint's natural range (e.g. pmax - pmin); mismatches
    within ``tol`` of the boundary are noise, mismatches beyond ``rel_threshold`` of
    the natural scale are flagged as a modelling difference, anything else is "unclear".
    """
    if abs_diff <= tol:
        return "numerical_noise"
    if scale > 0.0 and abs_diff > rel_threshold * scale:
        return "modelling_difference"
    return "unclear"


def _binding_counts(classes: pd.Series) -> dict[str, int]:
    counts = classes.value_counts()
    return {
        "both": int(counts.get("both", 0)),
        "only_own": int(counts.get("only_own", 0)),
        "only_pm": int(counts.get("only_pm", 0)),
    }


def binding_set_comparison(
    generator_rows: pd.DataFrame,
    *,
    bus_rows: pd.DataFrame | None = None,
    branch_rows: pd.DataFrame | None = None,
) -> dict[str, int]:
    """Aggregate per-element own-vs-PM binding classifications into one case/scenario row.

    Accepts the per-element comparison rows produced by the ``compare_*_with_powermodels``
    functions and returns counts of both/only_own/only_pm for each constraint type.
    """
    result: dict[str, int] = {}

    def _add(name: str, own_col: str, pm_col: str, rows: pd.DataFrame) -> None:
        if rows.empty or own_col not in rows.columns or pm_col not in rows.columns:
            return
        classes = rows.apply(lambda row: classify_binding(row[own_col], row[pm_col]), axis=1)
        for key, value in _binding_counts(classes).items():
            result[f"n_{name}_{key}"] = value

    if not generator_rows.empty:
        _add("pmin", "own_at_pmin", "pm_at_pmin", generator_rows)
        _add("pmax", "own_at_pmax", "pm_at_pmax", generator_rows)
        if "own_at_qmin" in generator_rows.columns:
            _add("qmin", "own_at_qmin", "pm_at_qmin", generator_rows)
            _add("qmax", "own_at_qmax", "pm_at_qmax", generator_rows)

    if bus_rows is not None and not bus_rows.empty:
        _add("vmin", "own_at_vmin", "pm_at_vmin", bus_rows)
        _add("vmax", "own_at_vmax", "pm_at_vmax", bus_rows)

    if branch_rows is not None and not branch_rows.empty:
        _add("thermal", "own_thermal_binding", "pm_thermal_binding", branch_rows)
        _add("angle_lower", "own_angle_lower_binding", "pm_angle_lower_binding", branch_rows)
        _add("angle_upper", "own_angle_upper_binding", "pm_angle_upper_binding", branch_rows)

    return result


def _add_binding_classification(frame: pd.DataFrame, checks: list[tuple]) -> pd.DataFrame:
    """Add ``<prefix>_binding_class``/``<prefix>_mismatch_reason`` columns.

    ``checks`` is a list of ``(prefix, own_col, pm_col, diff_col, scale)`` where
    ``scale`` is either a column name (per-row natural range, e.g. ``pmax - pmin``)
    or a constant float (e.g. 100.0 for a percentage-based column).
    """
    if frame.empty:
        return frame
    frame = frame.copy()
    for prefix, own_col, pm_col, diff_col, scale in checks:
        if own_col not in frame.columns or pm_col not in frame.columns or diff_col not in frame.columns:
            continue
        scale_series = frame[scale] if isinstance(scale, str) else pd.Series(scale, index=frame.index)

        def _classify(row, own_col=own_col, pm_col=pm_col, diff_col=diff_col, scale_series=scale_series):
            cls = classify_binding(bool(row[own_col]), bool(row[pm_col]))
            if cls in ("only_own", "only_pm"):
                reason = classify_mismatch_reason(abs(row[diff_col]), scale_series[row.name])
            else:
                reason = "n/a"
            return pd.Series({f"{prefix}_binding_class": cls, f"{prefix}_mismatch_reason": reason})

        frame[[f"{prefix}_binding_class", f"{prefix}_mismatch_reason"]] = frame.apply(_classify, axis=1)
    return frame


def classify_dc_generator_rows(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if not frame.empty:
        frame["pmax_minus_pmin"] = frame["pmax"] - frame["pmin"]
    return _add_binding_classification(
        frame,
        [
            ("pmin", "own_at_pmin", "pm_at_pmin", "pg_diff", "pmax_minus_pmin"),
            ("pmax", "own_at_pmax", "pm_at_pmax", "pg_diff", "pmax_minus_pmin"),
        ],
    )


def classify_dc_branch_rows(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if not frame.empty:
        frame["angle_range"] = (frame["angmax"] - frame["angmin"]).clip(lower=1e-9)
        frame["angle_diff_delta"] = frame["angle_diff"] - frame["pm_angle_diff"]
    return _add_binding_classification(
        frame,
        [
            ("thermal", "own_thermal_binding", "pm_thermal_binding", "abs_loading_pct_diff", 100.0),
            ("angle_lower", "own_angle_lower_binding", "pm_angle_lower_binding", "angle_diff_delta", "angle_range"),
            ("angle_upper", "own_angle_upper_binding", "pm_angle_upper_binding", "angle_diff_delta", "angle_range"),
        ],
    )


def classify_ac_generator_rows(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if not frame.empty:
        frame["pmax_minus_pmin"] = frame["pmax"] - frame["pmin"]
        frame["qmax_minus_qmin"] = frame["qmax"] - frame["qmin"]
    return _add_binding_classification(
        frame,
        [
            ("pmin", "own_at_pmin", "pm_at_pmin", "pg_diff", "pmax_minus_pmin"),
            ("pmax", "own_at_pmax", "pm_at_pmax", "pg_diff", "pmax_minus_pmin"),
            ("qmin", "own_at_qmin", "pm_at_qmin", "qg_diff", "qmax_minus_qmin"),
            ("qmax", "own_at_qmax", "pm_at_qmax", "qg_diff", "qmax_minus_qmin"),
        ],
    )


def classify_ac_bus_rows(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if not frame.empty:
        frame["vmax_minus_vmin"] = frame["vmax"] - frame["vmin"]
    return _add_binding_classification(
        frame,
        [
            ("vmin", "own_at_vmin", "pm_at_vmin", "vm_diff", "vmax_minus_vmin"),
            ("vmax", "own_at_vmax", "pm_at_vmax", "vm_diff", "vmax_minus_vmin"),
        ],
    )


def classify_ac_branch_rows(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if not frame.empty:
        frame["angle_range"] = (frame["angmax"] - frame["angmin"]).clip(lower=1e-9)
        frame["angle_diff_delta"] = frame["angle_diff"] - frame["pm_angle_diff"]
    return _add_binding_classification(
        frame,
        [
            ("thermal", "own_thermal_binding", "pm_thermal_binding", "abs_loading_pct_diff", 100.0),
            ("angle_lower", "own_angle_lower_binding", "pm_angle_lower_binding", "angle_diff_delta", "angle_range"),
            ("angle_upper", "own_angle_upper_binding", "pm_angle_upper_binding", "angle_diff_delta", "angle_range"),
        ],
    )


def ac_branch_flow_convention_check(branch_rows: pd.DataFrame, *, near_rating_pct: float = 80.0) -> pd.DataFrame:
    """For branches near their rating, compare pf/qf/pt/qt sign and magnitude.

    Large disagreement here (own vs PowerModels) is the signature of a
    flow/apparent-power convention mismatch rather than solver noise.
    AC only (needs qf/pt/qt, which DC branch rows don't carry).
    """
    if branch_rows.empty:
        return branch_rows.iloc[0:0]
    near_rating = branch_rows[
        branch_rows[["own_loading_pct", "pm_loading_pct"]].max(axis=1) >= near_rating_pct
    ].copy()
    if near_rating.empty:
        return near_rating
    near_rating["pf_sign_match"] = (near_rating["own_pf"] > 0) == (near_rating["pm_pf"] > 0)
    near_rating["qf_sign_match"] = (near_rating["own_qf"] > 0) == (near_rating["pm_qf"] > 0)
    near_rating["pt_sign_match"] = (near_rating["own_pt"] > 0) == (near_rating["pm_pt"] > 0)
    near_rating["qt_sign_match"] = (near_rating["own_qt"] > 0) == (near_rating["pm_qt"] > 0)
    columns = [
        "branch_id", "own_loading_pct", "pm_loading_pct",
        "own_pf", "pm_pf", "pf_sign_match",
        "own_qf", "pm_qf", "qf_sign_match",
        "own_pt", "pm_pt", "pt_sign_match",
        "own_qt", "pm_qt", "qt_sign_match",
        "abs_loading_pct_diff",
    ]
    return near_rating[[c for c in columns if c in near_rating.columns]]


def dc_opf_generator_rows(case: PowerCase, result, *, tol: float = 1e-6) -> pd.DataFrame:
    rows = []
    for gen in case.generators:
        if gen.status == 0 or gen.id not in result.pg:
            continue
        pg = result.pg[gen.id]
        rows.append(
            {
                "gen_id": gen.id,
                "bus_id": gen.bus,
                "pg": pg,
                "pmin": gen.pmin,
                "pmax": gen.pmax,
                "at_pmin": abs(pg - gen.pmin) <= tol,
                "at_pmax": abs(pg - gen.pmax) <= tol,
                "pmin_violation": max(0.0, gen.pmin - pg),
                "pmax_violation": max(0.0, pg - gen.pmax),
                "cost_c2": gen.cost_c2,
                "cost_c1": gen.cost_c1,
                "cost_c0": gen.cost_c0,
            }
        )
    return pd.DataFrame(rows)


def dc_opf_branch_rows(case: PowerCase, result, *, tol: float = 1e-6) -> pd.DataFrame:
    rows = []
    for branch in case.branches:
        if branch.status == 0 or branch.id not in result.branch_flows:
            continue
        flow = result.branch_flows[branch.id]
        angle_diff = result.theta[branch.from_bus] - result.theta[branch.to_bus]
        thermal_violation = max(0.0, abs(flow) - branch.rate_a) if branch.rate_a > 0.0 else 0.0
        rows.append(
            {
                "branch_id": branch.id,
                "branch_type": branch.branch_type,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "pf": flow,
                "rate_a": branch.rate_a,
                "thermal_binding": branch.rate_a > 0.0 and abs(abs(flow) - branch.rate_a) <= tol,
                "thermal_violation": thermal_violation,
                "angle_diff": angle_diff,
                "angmin": branch.angmin,
                "angmax": branch.angmax,
                "angle_lower_binding": abs(angle_diff - branch.angmin) <= tol,
                "angle_upper_binding": abs(angle_diff - branch.angmax) <= tol,
                "angle_violation": max(0.0, branch.angmin - angle_diff, angle_diff - branch.angmax),
            }
        )
    return pd.DataFrame(rows)


def dc_opf_summary_row(case: PowerCase, result, *, scenario: str = "base", tol: float = 1e-6) -> pd.DataFrame:
    gen_rows = dc_opf_generator_rows(case, result, tol=tol)
    branch_rows = dc_opf_branch_rows(case, result, tol=tol)
    return pd.DataFrame(
        [
            {
                "scenario": scenario,
                "success": result.success,
                "status": result.status,
                "message": result.message,
                "objective": result.objective,
                "max_power_balance_violation": result.max_power_balance_violation,
                "max_generator_limit_violation": max(
                    gen_rows["pmin_violation"].max() if not gen_rows.empty else 0.0,
                    gen_rows["pmax_violation"].max() if not gen_rows.empty else 0.0,
                ),
                "max_branch_thermal_violation": branch_rows["thermal_violation"].max()
                if not branch_rows.empty
                else 0.0,
                "max_branch_angle_violation": branch_rows["angle_violation"].max()
                if not branch_rows.empty
                else 0.0,
                "n_generators_at_pmin": int(gen_rows["at_pmin"].sum()) if not gen_rows.empty else 0,
                "n_generators_at_pmax": int(gen_rows["at_pmax"].sum()) if not gen_rows.empty else 0,
                "n_branches_thermal_binding": int(branch_rows["thermal_binding"].sum())
                if not branch_rows.empty
                else 0,
                "n_branches_angle_binding": int(
                    (branch_rows["angle_lower_binding"] | branch_rows["angle_upper_binding"]).sum()
                )
                if not branch_rows.empty
                else 0,
            }
        ]
    )


def check_violations(case: PowerCase, result):
    return {
        "summary": dc_opf_summary_row(case, result),
        "generators": dc_opf_generator_rows(case, result),
        "branches": dc_opf_branch_rows(case, result),
    }


def ac_opf_generator_rows(case: PowerCase, result, *, tol: float = 1e-6) -> pd.DataFrame:
    rows = []
    for gen in case.generators:
        if gen.status == 0 or gen.id not in result.pg:
            continue
        pg = result.pg[gen.id]
        qg = result.qg[gen.id]
        rows.append(
            {
                "gen_id": gen.id,
                "bus_id": gen.bus,
                "pg": pg,
                "qg": qg,
                "pmin": gen.pmin,
                "pmax": gen.pmax,
                "qmin": gen.qmin,
                "qmax": gen.qmax,
                "at_pmin": abs(pg - gen.pmin) <= tol,
                "at_pmax": abs(pg - gen.pmax) <= tol,
                "at_qmin": abs(qg - gen.qmin) <= tol,
                "at_qmax": abs(qg - gen.qmax) <= tol,
                "p_limit_violation": max(0.0, gen.pmin - pg, pg - gen.pmax),
                "q_limit_violation": max(0.0, gen.qmin - qg, qg - gen.qmax),
                "cost_c2": gen.cost_c2,
                "cost_c1": gen.cost_c1,
                "cost_c0": gen.cost_c0,
            }
        )
    return pd.DataFrame(rows)


def ac_opf_bus_rows(case: PowerCase, result, *, tol: float = 1e-6) -> pd.DataFrame:
    rows = []
    for bus in case.buses:
        vm = result.vm[bus.id]
        va = result.va[bus.id]
        rows.append(
            {
                "bus_id": bus.id,
                "vm": vm,
                "va": va,
                "vmin": bus.vmin,
                "vmax": bus.vmax,
                "at_vmin": abs(vm - bus.vmin) <= tol,
                "at_vmax": abs(vm - bus.vmax) <= tol,
                "voltage_violation": max(0.0, bus.vmin - vm, vm - bus.vmax),
            }
        )
    return pd.DataFrame(rows)


def ac_opf_branch_rows(case: PowerCase, result, *, tol: float = 1e-6) -> pd.DataFrame:
    rows = []
    for branch in case.branches:
        if branch.status == 0 or branch.id not in result.branch_flows:
            continue
        flow = result.branch_flows[branch.id]
        sf = (flow["pf"] ** 2 + flow["qf"] ** 2) ** 0.5
        st = (flow["pt"] ** 2 + flow["qt"] ** 2) ** 0.5
        angle_diff = result.va[branch.from_bus] - result.va[branch.to_bus]
        thermal_violation = max(0.0, sf - branch.rate_a, st - branch.rate_a) if branch.rate_a > 0.0 else 0.0
        rows.append(
            {
                "branch_id": branch.id,
                "branch_type": branch.branch_type,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "pf": flow["pf"],
                "qf": flow["qf"],
                "pt": flow["pt"],
                "qt": flow["qt"],
                "sf": sf,
                "st": st,
                "rate_a": branch.rate_a,
                "thermal_binding": branch.rate_a > 0.0
                and (abs(sf - branch.rate_a) <= tol or abs(st - branch.rate_a) <= tol),
                "thermal_violation": thermal_violation,
                "angle_diff": angle_diff,
                "angmin": branch.angmin,
                "angmax": branch.angmax,
                "angle_lower_binding": abs(angle_diff - branch.angmin) <= tol,
                "angle_upper_binding": abs(angle_diff - branch.angmax) <= tol,
                "angle_violation": max(0.0, branch.angmin - angle_diff, angle_diff - branch.angmax),
            }
        )
    return pd.DataFrame(rows)


def ac_opf_constraints_row(case: PowerCase, result, *, scenario: str = "base") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "case_name": case.name,
                "scenario": scenario,
                "max_p_balance_violation": result.max_p_balance_violation,
                "max_q_balance_violation": result.max_q_balance_violation,
                "max_pg_limit_violation": result.max_pg_limit_violation,
                "max_qg_limit_violation": result.max_qg_limit_violation,
                "max_voltage_violation": result.max_voltage_violation,
                "max_thermal_violation": result.max_thermal_violation,
                "max_angle_violation": result.max_angle_violation,
            }
        ]
    )


def ac_opf_summary_row(case: PowerCase, result, *, scenario: str = "base", tol: float = 1e-6) -> pd.DataFrame:
    gen_rows = ac_opf_generator_rows(case, result, tol=tol)
    bus_rows = ac_opf_bus_rows(case, result, tol=tol)
    branch_rows = ac_opf_branch_rows(case, result, tol=tol)
    return pd.DataFrame(
        [
            {
                "scenario": scenario,
                "success": result.success,
                "status": result.status,
                "message": result.message,
                "objective": result.objective,
                "max_p_balance_violation": result.max_p_balance_violation,
                "max_q_balance_violation": result.max_q_balance_violation,
                "max_pg_limit_violation": result.max_pg_limit_violation,
                "max_qg_limit_violation": result.max_qg_limit_violation,
                "max_voltage_violation": result.max_voltage_violation,
                "max_thermal_violation": result.max_thermal_violation,
                "max_angle_violation": result.max_angle_violation,
                "n_generators_at_pmin": int(gen_rows["at_pmin"].sum()) if not gen_rows.empty else 0,
                "n_generators_at_pmax": int(gen_rows["at_pmax"].sum()) if not gen_rows.empty else 0,
                "n_generators_at_qmin": int(gen_rows["at_qmin"].sum()) if not gen_rows.empty else 0,
                "n_generators_at_qmax": int(gen_rows["at_qmax"].sum()) if not gen_rows.empty else 0,
                "n_buses_voltage_binding": int((bus_rows["at_vmin"] | bus_rows["at_vmax"]).sum())
                if not bus_rows.empty
                else 0,
                "n_branches_thermal_binding": int(branch_rows["thermal_binding"].sum())
                if not branch_rows.empty
                else 0,
                "n_branches_angle_binding": int(
                    (branch_rows["angle_lower_binding"] | branch_rows["angle_upper_binding"]).sum()
                )
                if not branch_rows.empty
                else 0,
            }
        ]
    )
