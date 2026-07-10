"""Network utilities for PyPowSyBl studies.

The helpers in this file intentionally keep PyPowSyBl imports local so the
module can still be imported in documentation or notebook environments where
the optional Java-backed dependency is not active yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pandas as pd


def require_pypowsybl():
    """Import PyPowSyBl with a useful error message."""
    try:
        import pypowsybl as pp
    except ImportError as exc:
        raise ImportError(
            "pypowsybl is not installed in this Python environment. "
            "Install the project requirements or activate the conda environment "
            "defined in python/environment.yml."
        ) from exc
    return pp


def load_network(source: str | Path):
    """Load a PyPowSyBl network from a file path or built-in IEEE name.

    Accepted built-in names include ``ieee14`` and ``ieee14cdf`` when supported
    by the installed PyPowSyBl version.
    """
    pp = require_pypowsybl()
    source_text = str(source)
    source_path = Path(source_text)

    if source_path.exists():
        return pp.network.load(str(source_path))

    name = source_text.lower().replace("-", "").replace("_", "")
    creators = {
        "ieee14": "create_ieee14",
        "ieee14cdf": "create_ieee14",
        "eurostag": "create_eurostag_tutorial_example1_network",
        "fourbus": "create_four_substations_node_breaker_network",
    }
    creator_name = creators.get(name)
    if creator_name is not None and hasattr(pp.network, creator_name):
        return getattr(pp.network, creator_name)()

    raise ValueError(f"Cannot load PyPowSyBl network from source: {source}")


def get_table(network: Any, getter_name: str) -> pd.DataFrame:
    """Return a network table as a DataFrame with an ``id`` column."""
    getter: Callable[[], pd.DataFrame] | None = getattr(network, getter_name, None)
    if getter is None:
        return pd.DataFrame()

    table = getter()
    if table is None:
        return pd.DataFrame()
    if not isinstance(table, pd.DataFrame):
        table = pd.DataFrame(table)

    result = table.copy()
    if result.index.name is None:
        result.index.name = "id"
    return result.reset_index()


def network_tables(network: Any) -> dict[str, pd.DataFrame]:
    """Return the main PyPowSyBl network tables."""
    return {
        "buses": get_table(network, "get_buses"),
        "lines": get_table(network, "get_lines"),
        "two_windings_transformers": get_table(
            network, "get_2_windings_transformers"
        ),
        "generators": get_table(network, "get_generators"),
        "loads": get_table(network, "get_loads"),
        "shunts": get_table(network, "get_shunt_compensators"),
        "voltage_levels": get_table(network, "get_voltage_levels"),
        "substations": get_table(network, "get_substations"),
    }


def branch_ids(network: Any, include_transformers: bool = True) -> list[str]:
    """Return line ids, optionally followed by two-winding transformer ids."""
    ids: list[str] = []
    lines = get_table(network, "get_lines")
    if not lines.empty and "id" in lines:
        ids.extend(lines["id"].astype(str).tolist())

    if include_transformers:
        transformers = get_table(network, "get_2_windings_transformers")
        if not transformers.empty and "id" in transformers:
            ids.extend(transformers["id"].astype(str).tolist())

    return ids


def save_network_tables(network: Any, output_dir: str | Path, prefix: str) -> None:
    """Write the main network tables to CSV files."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for name, table in network_tables(network).items():
        if not table.empty:
            table.to_csv(output_path / f"{prefix}_{name}.csv", index=False)
