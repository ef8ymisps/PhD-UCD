"""N-1 security utilities for PyPowSyBl studies."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

try:
    from .loadflow_utils import branch_rows, bus_rows, run_ac_loadflow
    from .network_utils import branch_ids, get_table
except ImportError:  # Allows direct execution from this folder.
    from loadflow_utils import branch_rows, bus_rows, run_ac_loadflow
    from network_utils import branch_ids, get_table


@dataclass
class NMinusOneStudyResult:
    """Container for N-1 contingency DataFrames."""

    base_summary: pd.DataFrame
    contingency_summary: pd.DataFrame
    contingency_branches: pd.DataFrame
    contingency_buses: pd.DataFrame


def _clone_network(network: Any):
    if hasattr(network, "clone"):
        return network.clone()
    return copy.deepcopy(network)


def _disconnect_with_update(network: Any, branch_id: str, getter: str, updater: str) -> bool:
    table = get_table(network, getter)
    if table.empty or branch_id not in set(table["id"].astype(str)):
        return False
    if not hasattr(network, updater):
        return False

    update = pd.DataFrame(index=[branch_id])
    update["connected1"] = False
    update["connected2"] = False
    getattr(network, updater)(update)
    return True


def disconnect_branch(network: Any, branch_id: str) -> None:
    """Disconnect a line or two-winding transformer in a PyPowSyBl network."""
    if hasattr(network, "disconnect"):
        try:
            network.disconnect(branch_id)
            return
        except Exception:
            pass

    if _disconnect_with_update(network, branch_id, "get_lines", "update_lines"):
        return
    if _disconnect_with_update(
        network,
        branch_id,
        "get_2_windings_transformers",
        "update_2_windings_transformers",
    ):
        return

    raise ValueError(f"Cannot disconnect branch {branch_id!r} with this PyPowSyBl API")


def voltage_violations(
    buses: pd.DataFrame,
    *,
    min_vm_kv: float | None = None,
    max_vm_kv: float | None = None,
) -> pd.DataFrame:
    """Return buses outside optional absolute kV voltage bounds."""
    if buses.empty or "vm_kv" not in buses:
        return pd.DataFrame()

    mask = pd.Series(False, index=buses.index)
    if min_vm_kv is not None:
        mask |= buses["vm_kv"] < min_vm_kv
    if max_vm_kv is not None:
        mask |= buses["vm_kv"] > max_vm_kv
    return buses.loc[mask].copy()


def branch_loading_violations(
    branches: pd.DataFrame,
    *,
    max_loading_pct: float = 100.0,
) -> pd.DataFrame:
    """Return branches whose apparent-power loading exceeds the threshold."""
    if branches.empty or "loading_pct" not in branches:
        return pd.DataFrame()
    return branches.loc[branches["loading_pct"] > max_loading_pct].copy()


def classify_contingency(
    status: str,
    branches: pd.DataFrame,
    buses: pd.DataFrame,
    *,
    max_loading_pct: float = 100.0,
    min_vm_kv: float | None = None,
    max_vm_kv: float | None = None,
) -> tuple[str, int, int, float, float, float]:
    """Classify a contingency and return compact diagnostic metrics."""
    branch_violations = branch_loading_violations(
        branches,
        max_loading_pct=max_loading_pct,
    )
    bus_violations = voltage_violations(
        buses,
        min_vm_kv=min_vm_kv,
        max_vm_kv=max_vm_kv,
    )

    converged = "CONVERGED" in str(status).upper()
    secure = converged and branch_violations.empty and bus_violations.empty
    classification = "secure" if secure else "insecure"

    max_loading = (
        float(branches["loading_pct"].max()) if "loading_pct" in branches else np.nan
    )
    min_vm = float(buses["vm_kv"].min()) if "vm_kv" in buses else np.nan
    max_vm = float(buses["vm_kv"].max()) if "vm_kv" in buses else np.nan

    return (
        classification,
        int(len(branch_violations)),
        int(len(bus_violations)),
        max_loading,
        min_vm,
        max_vm,
    )


def run_branch_outage(
    base_network: Any,
    branch_id: str,
    *,
    max_loading_pct: float = 100.0,
    min_vm_kv: float | None = None,
    max_vm_kv: float | None = None,
    parameters: Any = None,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """Run one branch-outage AC load-flow contingency."""
    network = _clone_network(base_network)
    disconnect_branch(network, branch_id)

    scenario = f"outage_{branch_id}"
    result = run_ac_loadflow(
        network,
        scenario=scenario,
        source="contingency",
        parameters=parameters,
    )
    branches = pd.DataFrame(branch_rows(network, scenario=scenario))
    buses = pd.DataFrame(bus_rows(network, scenario=scenario))

    (
        classification,
        n_branch_violations,
        n_voltage_violations,
        max_branch_loading_pct,
        min_vm_kv_result,
        max_vm_kv_result,
    ) = classify_contingency(
        result.status,
        branches,
        buses,
        max_loading_pct=max_loading_pct,
        min_vm_kv=min_vm_kv,
        max_vm_kv=max_vm_kv,
    )

    summary = {
        "contingency_id": branch_id,
        "scenario": scenario,
        "status": result.status,
        "classification": classification,
        "n_branch_violations": n_branch_violations,
        "n_voltage_violations": n_voltage_violations,
        "max_branch_loading_pct": max_branch_loading_pct,
        "min_vm_kv": min_vm_kv_result,
        "max_vm_kv": max_vm_kv_result,
    }
    return summary, branches, buses


def run_n_minus_one_branch_study(
    network: Any,
    *,
    contingencies: list[str] | None = None,
    include_transformers: bool = True,
    max_loading_pct: float = 100.0,
    min_vm_kv: float | None = None,
    max_vm_kv: float | None = None,
    parameters: Any = None,
) -> NMinusOneStudyResult:
    """Run AC load flow after each selected branch outage."""
    base = _clone_network(network)
    base_result = run_ac_loadflow(
        base,
        scenario="base",
        source="base",
        parameters=parameters,
    )

    selected = contingencies
    if selected is None:
        selected = branch_ids(network, include_transformers=include_transformers)

    summary_rows: list[dict[str, Any]] = []
    branch_frames: list[pd.DataFrame] = []
    bus_frames: list[pd.DataFrame] = []

    for branch_id in selected:
        summary, branches, buses = run_branch_outage(
            network,
            branch_id,
            max_loading_pct=max_loading_pct,
            min_vm_kv=min_vm_kv,
            max_vm_kv=max_vm_kv,
            parameters=parameters,
        )
        summary_rows.append(summary)
        branch_frames.append(branches)
        bus_frames.append(buses)

    return NMinusOneStudyResult(
        base_summary=base_result.summary,
        contingency_summary=pd.DataFrame(summary_rows),
        contingency_branches=pd.concat(branch_frames, ignore_index=True)
        if branch_frames
        else pd.DataFrame(),
        contingency_buses=pd.concat(bus_frames, ignore_index=True)
        if bus_frames
        else pd.DataFrame(),
    )
