from __future__ import annotations

import numpy as np
import pandas as pd

from own_models.src.data.case import PowerCase


def _indexed(table: pd.DataFrame, id_col: str = "id") -> pd.DataFrame:
    if table.empty:
        return table
    if id_col in table.columns:
        return table.set_index(id_col, drop=False)
    return table


def compare_ac_pf_buses_with_pypowsybl(
    case: PowerCase,
    own_result,
    pypowsybl_buses: pd.DataFrame,
) -> pd.DataFrame:
    buses = _indexed(pypowsybl_buses)
    rows = []
    for bus in case.buses:
        if bus.id not in buses.index:
            continue
        pm_bus = buses.loc[bus.id]
        own_vm_kv = own_result.vm[bus.index - 1] * bus.nominal_v_kv
        own_va_deg = np.degrees(own_result.va[bus.index - 1])
        pp_vm_kv = float(pm_bus["v_mag"])
        pp_va_deg = float(pm_bus["v_angle"])
        rows.append(
            {
                "bus_id": bus.id,
                "own_vm_pu": own_result.vm[bus.index - 1],
                "own_vm_kv": own_vm_kv,
                "pypowsybl_vm_kv": pp_vm_kv,
                "vm_kv_diff": own_vm_kv - pp_vm_kv,
                "own_va_rad": own_result.va[bus.index - 1],
                "own_va_deg": own_va_deg,
                "pypowsybl_va_deg": pp_va_deg,
                "va_deg_diff": own_va_deg - pp_va_deg,
            }
        )
    return pd.DataFrame(rows)


def _branch_table(pypowsybl_lines: pd.DataFrame, pypowsybl_transformers: pd.DataFrame) -> pd.DataFrame:
    frames = []
    if not pypowsybl_lines.empty:
        lines = pypowsybl_lines.copy()
        lines["branch_type"] = "line"
        frames.append(lines)
    if not pypowsybl_transformers.empty:
        transformers = pypowsybl_transformers.copy()
        transformers["branch_type"] = "two_windings_transformer"
        frames.append(transformers)
    if not frames:
        return pd.DataFrame()
    return _indexed(pd.concat(frames, ignore_index=True))


def compare_ac_pf_branches_with_pypowsybl(
    case: PowerCase,
    own_result,
    pypowsybl_lines: pd.DataFrame,
    pypowsybl_transformers: pd.DataFrame,
) -> pd.DataFrame:
    branches = _branch_table(pypowsybl_lines, pypowsybl_transformers)
    rows = []
    for branch in case.branches:
        if branch.id not in own_result.branch_flows or branch.id not in branches.index:
            continue
        pp_branch = branches.loc[branch.id]
        own = own_result.branch_flows[branch.id]
        own_pf_mw = own["pf"] * case.base_mva
        own_qf_mvar = own["qf"] * case.base_mva
        own_pt_mw = own["pt"] * case.base_mva
        own_qt_mvar = own["qt"] * case.base_mva
        rows.append(
            {
                "branch_id": branch.id,
                "branch_type": branch.branch_type,
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "own_pf_mw": own_pf_mw,
                "pypowsybl_p1_mw": float(pp_branch["p1"]),
                "pf_mw_diff": own_pf_mw - float(pp_branch["p1"]),
                "own_qf_mvar": own_qf_mvar,
                "pypowsybl_q1_mvar": float(pp_branch["q1"]),
                "qf_mvar_diff": own_qf_mvar - float(pp_branch["q1"]),
                "own_pt_mw": own_pt_mw,
                "pypowsybl_p2_mw": float(pp_branch["p2"]),
                "pt_mw_diff": own_pt_mw - float(pp_branch["p2"]),
                "own_qt_mvar": own_qt_mvar,
                "pypowsybl_q2_mvar": float(pp_branch["q2"]),
                "qt_mvar_diff": own_qt_mvar - float(pp_branch["q2"]),
            }
        )
    return pd.DataFrame(rows)


def ac_pf_comparison_summary(bus_comparison: pd.DataFrame, branch_comparison: pd.DataFrame) -> pd.DataFrame:
    row = {
        "max_vm_kv_diff": np.nan,
        "max_va_deg_diff": np.nan,
        "max_pf_mw_diff": np.nan,
        "max_qf_mvar_diff": np.nan,
        "max_pt_mw_diff": np.nan,
        "max_qt_mvar_diff": np.nan,
    }
    if not bus_comparison.empty:
        row["max_vm_kv_diff"] = float(bus_comparison["vm_kv_diff"].abs().max())
        row["max_va_deg_diff"] = float(bus_comparison["va_deg_diff"].abs().max())
    if not branch_comparison.empty:
        row["max_pf_mw_diff"] = float(branch_comparison["pf_mw_diff"].abs().max())
        row["max_qf_mvar_diff"] = float(branch_comparison["qf_mvar_diff"].abs().max())
        row["max_pt_mw_diff"] = float(branch_comparison["pt_mw_diff"].abs().max())
        row["max_qt_mvar_diff"] = float(branch_comparison["qt_mvar_diff"].abs().max())
    return pd.DataFrame([row])


def compare_with_pypowsybl(case: PowerCase, own_ac_pf_result, pypowsybl_network) -> dict[str, pd.DataFrame]:
    buses = pypowsybl_network.get_buses().reset_index()
    lines = pypowsybl_network.get_lines().reset_index()
    transformers = pypowsybl_network.get_2_windings_transformers().reset_index()
    bus_comparison = compare_ac_pf_buses_with_pypowsybl(case, own_ac_pf_result, buses)
    branch_comparison = compare_ac_pf_branches_with_pypowsybl(
        case,
        own_ac_pf_result,
        lines,
        transformers,
    )
    return {
        "bus_comparison": bus_comparison,
        "branch_comparison": branch_comparison,
        "summary": ac_pf_comparison_summary(bus_comparison, branch_comparison),
    }
