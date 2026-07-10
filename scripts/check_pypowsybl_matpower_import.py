"""Validate that PyPowSyBl can import and solve a converted MATPOWER .mat file."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd


def _table(network: Any, getter_name: str) -> pd.DataFrame:
    getter = getattr(network, getter_name, None)
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


def _print_table(name: str, table: pd.DataFrame, max_rows: int) -> None:
    print(f"\n{name} ({len(table)} rows)")
    if table.empty:
        print("<empty>")
        return
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(table.head(max_rows).to_string(index=False))


def _result_status(result: Any) -> str:
    values = result if isinstance(result, list) else [result]
    statuses = [str(getattr(value, "status", value)) for value in values]
    return ";".join(statuses) if statuses else "UNKNOWN"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load a MATPOWER .mat file with PyPowSyBl and run AC load flow."
    )
    parser.add_argument("mat_file", type=Path, help="Converted MATPOWER .mat file.")
    parser.add_argument(
        "--max-rows",
        type=int,
        default=10,
        help="Maximum rows to print from each network table.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mat_file = args.mat_file.resolve()
    if not mat_file.exists():
        raise SystemExit(f"MATPOWER .mat file does not exist: {mat_file}")
    if mat_file.suffix.lower() != ".mat":
        raise SystemExit(f"Expected a .mat file: {mat_file}")

    try:
        import pypowsybl as pp
    except ImportError as exc:
        raise SystemExit(
            "pypowsybl is not installed. Install python/requirements.txt before "
            "running this validation."
        ) from exc

    try:
        network = pp.network.load(str(mat_file))
    except Exception as exc:
        raise SystemExit(
            "PyPowSyBl did not accept the generated .mat file. Inspect the "
            "MATLAB struct layout written by scipy.io.savemat before changing "
            f"load-flow settings. Original error: {exc}"
        ) from exc

    buses = _table(network, "get_buses")
    generators = _table(network, "get_generators")
    loads = _table(network, "get_loads")
    lines = _table(network, "get_lines")

    print(f"Loaded network: {mat_file}")
    print(
        "Network summary: "
        f"buses={len(buses)}, generators={len(generators)}, "
        f"loads={len(loads)}, lines={len(lines)}"
    )

    _print_table("Buses", buses, args.max_rows)
    _print_table("Generators", generators, args.max_rows)
    _print_table("Loads", loads, args.max_rows)
    _print_table("Lines", lines, args.max_rows)

    print("\nRunning OpenLoadFlow AC Newton-Raphson load flow...")
    try:
        result = pp.loadflow.run_ac(network)
    except Exception as exc:
        raise SystemExit(f"Load flow failed before convergence reporting: {exc}") from exc

    status = _result_status(result)
    print(f"Convergence status: {status}")

    component_results = result if isinstance(result, list) else [result]
    for idx, component in enumerate(component_results):
        print(f"\nComponent {idx}")
        for name in (
            "status",
            "status_text",
            "iteration_count",
            "slack_bus_id",
            "slack_bus_active_power_mismatch",
            "distributed_active_power",
        ):
            if hasattr(component, name):
                print(f"{name}: {getattr(component, name)}")


if __name__ == "__main__":
    main()
