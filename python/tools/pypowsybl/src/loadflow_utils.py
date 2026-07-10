"""Load-flow utilities for PyPowSyBl studies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

try:
    from .network_utils import get_table, load_network, require_pypowsybl
except ImportError:  # Allows direct execution from this folder.
    from network_utils import get_table, load_network, require_pypowsybl


@dataclass
class PyPowSyBlLoadFlowResult:
    """DataFrame-ready PyPowSyBl AC load-flow result."""

    source: str
    status: str
    component_results: pd.DataFrame
    buses: pd.DataFrame
    generators: pd.DataFrame
    branches: pd.DataFrame
    lines: pd.DataFrame
    transformers: pd.DataFrame
    loads: pd.DataFrame
    summary: pd.DataFrame


def _result_status(component_results: Any) -> str:
    if component_results is None:
        return "UNKNOWN"
    if isinstance(component_results, list):
        if not component_results:
            return "UNKNOWN"
        statuses = [str(getattr(result, "status", "UNKNOWN")) for result in component_results]
        if all(status in {"CONVERGED", "Converged"} for status in statuses):
            return "CONVERGED"
        return ";".join(statuses)
    return str(getattr(component_results, "status", component_results))


def _component_results_frame(component_results: Any) -> pd.DataFrame:
    if component_results is None:
        return pd.DataFrame()

    rows = []
    values = component_results if isinstance(component_results, list) else [component_results]
    for idx, result in enumerate(values):
        row = {"component": idx}
        for name in (
            "status",
            "status_text",
            "connected_component_num",
            "synchronous_component_num",
            "iteration_count",
            "slack_bus_id",
            "slack_bus_active_power_mismatch",
            "distributed_active_power",
        ):
            if hasattr(result, name):
                row[name] = getattr(result, name)
        rows.append(row)
    return pd.DataFrame(rows)


def _pick(row: pd.Series, *names: str, default=np.nan):
    for name in names:
        if name in row:
            return row[name]
    return default


def _loading_pct(p: float, q: float, limit: float) -> float:
    if pd.isna(limit) or limit == 0:
        return np.nan
    return 100.0 * float(np.hypot(p, q)) / abs(float(limit))


def _max_loading_pct(*values: float) -> float:
    finite_values = [value for value in values if not pd.isna(value)]
    return max(finite_values) if finite_values else np.nan


def bus_rows(network: Any, scenario: str = "base") -> list[dict[str, Any]]:
    rows = []
    buses = get_table(network, "get_buses")
    for _, bus in buses.iterrows():
        rows.append(
            {
                "scenario": scenario,
                "bus_id": bus["id"],
                "voltage_level_id": _pick(bus, "voltage_level_id"),
                "connected_component": _pick(bus, "connected_component"),
                "synchronous_component": _pick(bus, "synchronous_component"),
                "vm_kv": _pick(bus, "v_mag", "v"),
                "va_deg": _pick(bus, "v_angle", "angle"),
            }
        )
    return rows


def generator_rows(network: Any, scenario: str = "base") -> list[dict[str, Any]]:
    rows = []
    generators = get_table(network, "get_generators")
    for _, gen in generators.iterrows():
        rows.append(
            {
                "scenario": scenario,
                "generator_id": gen["id"],
                "bus_id": _pick(gen, "bus_id", "bus"),
                "voltage_level_id": _pick(gen, "voltage_level_id"),
                "target_p_mw": _pick(gen, "target_p"),
                "target_q_mvar": _pick(gen, "target_q"),
                "target_v_kv": _pick(gen, "target_v"),
                "p_mw": _pick(gen, "p"),
                "q_mvar": _pick(gen, "q"),
                "min_p_mw": _pick(gen, "min_p"),
                "max_p_mw": _pick(gen, "max_p"),
                "voltage_regulator_on": _pick(gen, "voltage_regulator_on"),
            }
        )
    return rows


def load_rows(network: Any, scenario: str = "base") -> list[dict[str, Any]]:
    rows = []
    loads = get_table(network, "get_loads")
    for _, load in loads.iterrows():
        rows.append(
            {
                "scenario": scenario,
                "load_id": load["id"],
                "bus_id": _pick(load, "bus_id", "bus"),
                "voltage_level_id": _pick(load, "voltage_level_id"),
                "p0_mw": _pick(load, "p0"),
                "q0_mvar": _pick(load, "q0"),
                "p_mw": _pick(load, "p"),
                "q_mvar": _pick(load, "q"),
            }
        )
    return rows


def branch_rows(network: Any, scenario: str = "base") -> list[dict[str, Any]]:
    rows = []

    for branch_type, getter_name in (
        ("line", "get_lines"),
        ("two_windings_transformer", "get_2_windings_transformers"),
    ):
        table = get_table(network, getter_name)
        for _, branch in table.iterrows():
            p1 = _pick(branch, "p1")
            q1 = _pick(branch, "q1")
            p2 = _pick(branch, "p2")
            q2 = _pick(branch, "q2")
            rate = _pick(branch, "rated_s", "rate_a", "permanent_limit")
            loading1 = _loading_pct(p1, q1, rate)
            loading2 = _loading_pct(p2, q2, rate)
            rows.append(
                {
                    "scenario": scenario,
                    "branch_id": branch["id"],
                    "branch_type": branch_type,
                    "bus1_id": _pick(branch, "bus1_id", "bus1"),
                    "bus2_id": _pick(branch, "bus2_id", "bus2"),
                    "voltage_level1_id": _pick(branch, "voltage_level1_id"),
                    "voltage_level2_id": _pick(branch, "voltage_level2_id"),
                    "p1_mw": p1,
                    "q1_mvar": q1,
                    "p2_mw": p2,
                    "q2_mvar": q2,
                    "s1_mva": np.hypot(p1, q1) if not pd.isna(p1) else np.nan,
                    "s2_mva": np.hypot(p2, q2) if not pd.isna(p2) else np.nan,
                    "rate_mva": rate,
                    "loading1_pct": loading1,
                    "loading2_pct": loading2,
                    "loading_pct": _max_loading_pct(loading1, loading2),
                    "connected1": _pick(branch, "connected1"),
                    "connected2": _pick(branch, "connected2"),
                }
            )

    return rows


def loadflow_summary_row(
    network: Any,
    status: str,
    scenario: str = "base",
    source: str = "",
) -> dict[str, Any]:
    branches = pd.DataFrame(branch_rows(network, scenario=scenario))
    buses = pd.DataFrame(bus_rows(network, scenario=scenario))
    generators = pd.DataFrame(generator_rows(network, scenario=scenario))
    loads = pd.DataFrame(load_rows(network, scenario=scenario))

    return {
        "scenario": scenario,
        "source": source,
        "status": status,
        "n_buses": len(buses),
        "n_generators": len(generators),
        "n_loads": len(loads),
        "n_branches": len(branches),
        "total_generation_p_mw": generators["p_mw"].sum()
        if "p_mw" in generators
        else np.nan,
        "total_load_p_mw": loads["p_mw"].sum() if "p_mw" in loads else np.nan,
        "max_vm_kv": buses["vm_kv"].max() if "vm_kv" in buses else np.nan,
        "min_vm_kv": buses["vm_kv"].min() if "vm_kv" in buses else np.nan,
        "max_branch_loading_pct": branches["loading_pct"].max()
        if "loading_pct" in branches
        else np.nan,
    }


def run_ac_loadflow(
    network: Any,
    *,
    scenario: str = "base",
    source: str = "",
    parameters: Any = None,
) -> PyPowSyBlLoadFlowResult:
    """Run PyPowSyBl AC load flow and return inspection tables."""
    pp = require_pypowsybl()
    result = pp.loadflow.run_ac(network, parameters=parameters)
    status = _result_status(result)

    summary = pd.DataFrame(
        [loadflow_summary_row(network, status, scenario=scenario, source=source)]
    )

    return PyPowSyBlLoadFlowResult(
        source=source,
        status=status,
        component_results=_component_results_frame(result),
        buses=pd.DataFrame(bus_rows(network, scenario=scenario)),
        generators=pd.DataFrame(generator_rows(network, scenario=scenario)),
        branches=pd.DataFrame(branch_rows(network, scenario=scenario)),
        lines=get_table(network, "get_lines"),
        transformers=get_table(network, "get_2_windings_transformers"),
        loads=pd.DataFrame(load_rows(network, scenario=scenario)),
        summary=summary,
    )


def run_ac_loadflow_from_source(
    source: str,
    *,
    scenario: str = "base",
    parameters: Any = None,
) -> tuple[Any, PyPowSyBlLoadFlowResult]:
    """Load a network source and run AC load flow."""
    network = load_network(source)
    return network, run_ac_loadflow(
        network,
        scenario=scenario,
        source=str(source),
        parameters=parameters,
    )
