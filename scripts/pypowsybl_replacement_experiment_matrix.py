"""PyPowSyBl/OpenLoadFlow replacement-note experiment matrix.

This module turns PGLib replacement-note comments in MATPOWER ``.m`` files into
audited PyPowSyBl/OpenLoadFlow experiments against the matching ``.mat`` files.
It is intentionally notebook-friendly, but can also be run as a script.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from statistics import median
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAT_DIR = PROJECT_ROOT / "data" / "raw" / "pglib" / "matpower_mat"
DEFAULT_M_DIR = PROJECT_ROOT / "data" / "raw" / "pglib" / "matpower_m"
DEFAULT_RESULT_ROOT = PROJECT_ROOT / "results" / "presentable" / "pypowsybl_lf_replacement_sweep"
DEFAULT_SUMMARY_CSV = DEFAULT_RESULT_ROOT / "pypowsybl_openloadflow_sensitivity_rows.csv"
DEFAULT_AUDIT_CSV = DEFAULT_RESULT_ROOT / "pypowsybl_replacement_mapping_audit.csv"
DEFAULT_INVENTORY_CSV = DEFAULT_RESULT_ROOT / "pypowsybl_scenario_inventory.csv"
DEFAULT_MAPPING_COVERAGE_CSV = DEFAULT_RESULT_ROOT / "pypowsybl_replacement_mapping_coverage.csv"
DEFAULT_FIGURE_DIR = DEFAULT_RESULT_ROOT / "figures"

SCENARIO_FOLDER_SPECS = {
    "base_case": {"suffix": "", "label": "Base"},
    "api": {"suffix": "__api", "label": "API"},
    "sad": {"suffix": "__sad", "label": "SAD"},
}

VARIANT_LABELS = {
    "original": "Original",
    "BV_from_warmstart": "VS init",
    "G_PGQG_from": "Gen P/Q",
    "G_PGQG_from_plus_BV_from": "Gen P/Q + VS",
    "G_PGQG_from_plus_GVG_from": "Gen P/Q + VG",
}

PF_CONTROL_LABELS = {
    "strict_single_slack": "Single slack",
    "single_slack_q_limits": "Single slack + Q limits",
    "distributed_q_limits": "Distributed + Q limits",
    "operational_controls": "Operational controls",
}


SECTION_RE = re.compile(r"^%\s+INFO\s+:\s+===\s+(.+?)\s+===")
LOAD_RE = re.compile(
    r"Bus\s+(?P<bus>\d+)\s*:\s*"
    r"Pd=(?P<Pd_from>[-+.\deE]+),\s*Qd=(?P<Qd_from>[-+.\deE]+)\s*->\s*"
    r"Pd=(?P<Pd_to>[-+.\deE]+),\s*Qd=(?P<Qd_to>[-+.\deE]+)"
)
BUS_VOLTAGE_RE = re.compile(
    r"Bus\s+(?P<bus>\d+)\s*:\s*"
    r"V=(?P<V_from>[-+.\deE]+),\s*theta=(?P<theta_from>[-+.\deE]+)\s*->\s*"
    r"V=(?P<V_to>[-+.\deE]+),\s*theta=(?P<theta_to>[-+.\deE]+)"
)
GEN_PQ_RE = re.compile(
    r"Gen at bus\s+(?P<bus>\d+)(?:\s+-\s+[^:\t]+)?\s*:\s*"
    r"Pg=(?P<Pg_from>[-+.\deE]+),\s*Qg=(?P<Qg_from>[-+.\deE]+)\s*->\s*"
    r"Pg=(?P<Pg_to>[-+.\deE]+),\s*Qg=(?P<Qg_to>[-+.\deE]+)"
)
GEN_VG_RE = re.compile(
    r"Gen at bus\s+(?P<bus>\d+)(?:\s+-\s+[^:\t]+)?\s*:\s*"
    r"Vg=(?P<Vg_from>[-+.\deE]+)\s*->\s*Vg=(?P<Vg_to>[-+.\deE]+)"
)
GEN_COST_RE = re.compile(r"Updated Generator Cost:")
GEN_CLASS_RE = re.compile(r"(?P<kind>[A-Z]+)\s+(?P<count>\d+)\s+-\s+(?P<share>[-+.\deE]+)")
GEN_BOUNDS_RE = re.compile(r"Gen at bus\s+\d+.*(?:Pmin|Pmax|Qmin|Qmax).*->")
BASE_KV_RE = re.compile(r"Bus\s+\d+.*base", re.IGNORECASE)
TRANSFORMER_SETTING_RE = re.compile(r"Transformer\s+")
LINE_CAPACITY_RE = re.compile(r"Updated Thermal Rating:")


@dataclass(frozen=True)
class ReplacementNotes:
    load: pd.DataFrame
    bus_voltage: pd.DataFrame
    gen_pgqg: pd.DataFrame
    gen_vg: pd.DataFrame


@dataclass(frozen=True)
class ReplacementVariant:
    name: str
    groups: tuple[str, ...]
    direction: str | None = None
    voltage_init: str | None = None


@dataclass(frozen=True)
class ParameterScenario:
    name: str
    params: Any
    provider: str = "OpenLoadFlow"
    settings: dict[str, Any] = field(default_factory=dict)
    provider_parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScenarioCase:
    scenario_folder: str
    case_name: str
    base_case_name: str
    mat_path: Path
    m_path: Path


def require_pypowsybl():
    try:
        import pypowsybl as pp
    except ImportError as exc:
        raise ImportError(
            "pypowsybl is not installed in this Python environment.\n"
            f"Active Python: {sys.executable}\n"
            "Activate the project environment from python/environment.yml or install "
            "python/requirements.txt into the active interpreter."
        ) from exc
    return pp


def get_table(network: Any, getter_name: str) -> pd.DataFrame:
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


def load_case_network(mat_path: str | Path):
    pp = require_pypowsybl()
    return pp.network.load(str(Path(mat_path)))


def _records_to_frame(records: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(records, columns=columns)


def _add_order_at_bus(records: list[dict[str, Any]]) -> None:
    counters: dict[tuple[int, int], int] = {}
    for record in records:
        bus = int(record["bus"])
        section_id = int(record.get("section_id", 0))
        key = (section_id, bus)
        counters[key] = counters.get(key, 0) + 1
        record["order_at_bus"] = counters[key]


def parse_replacement_notes(m_path: str | Path) -> ReplacementNotes:
    """Parse replacement-note blocks from a PGLib MATPOWER ``.m`` file."""
    m_path = Path(m_path)
    section = ""
    section_id = 0
    load: list[dict[str, Any]] = []
    bus_voltage: list[dict[str, Any]] = []
    gen_pgqg: list[dict[str, Any]] = []
    gen_vg: list[dict[str, Any]] = []

    for line_no, line in enumerate(m_path.read_text(encoding="utf-8").splitlines(), start=1):
        if section_match := SECTION_RE.match(line):
            section = section_match.group(1)
            section_id += 1
            continue

        if section == "Load Replacement Notes":
            if match := LOAD_RE.search(line):
                groups = match.groupdict()
                load.append(
                    {
                        "line_no": line_no,
                        "section_id": section_id,
                        "bus": int(groups["bus"]),
                        "Pd_from": float(groups["Pd_from"]),
                        "Qd_from": float(groups["Qd_from"]),
                        "Pd_to": float(groups["Pd_to"]),
                        "Qd_to": float(groups["Qd_to"]),
                    }
                )
        elif section == "Voltage Setpoint Replacement Notes":
            if match := BUS_VOLTAGE_RE.search(line):
                groups = match.groupdict()
                bus_voltage.append(
                    {
                        "line_no": line_no,
                        "section_id": section_id,
                        "bus": int(groups["bus"]),
                        "V_from": float(groups["V_from"]),
                        "theta_from": float(groups["theta_from"]),
                        "V_to": float(groups["V_to"]),
                        "theta_to": float(groups["theta_to"]),
                    }
                )
        elif section == "Generator Setpoint Replacement Notes":
            if match := GEN_PQ_RE.search(line):
                groups = match.groupdict()
                gen_pgqg.append(
                    {
                        "line_no": line_no,
                        "section_id": section_id,
                        "bus": int(groups["bus"]),
                        "Pg_from": float(groups["Pg_from"]),
                        "Qg_from": float(groups["Qg_from"]),
                        "Pg_to": float(groups["Pg_to"]),
                        "Qg_to": float(groups["Qg_to"]),
                    }
                )
            elif match := GEN_VG_RE.search(line):
                groups = match.groupdict()
                gen_vg.append(
                    {
                        "line_no": line_no,
                        "section_id": section_id,
                        "bus": int(groups["bus"]),
                        "Vg_from": float(groups["Vg_from"]),
                        "Vg_to": float(groups["Vg_to"]),
                    }
                )

    for records in (load, bus_voltage, gen_pgqg, gen_vg):
        _add_order_at_bus(records)

    return ReplacementNotes(
        load=_records_to_frame(
            load,
            ["line_no", "section_id", "bus", "order_at_bus", "Pd_from", "Qd_from", "Pd_to", "Qd_to"],
        ),
        bus_voltage=_records_to_frame(
            bus_voltage,
            ["line_no", "section_id", "bus", "order_at_bus", "V_from", "theta_from", "V_to", "theta_to"],
        ),
        gen_pgqg=_records_to_frame(
            gen_pgqg,
            ["line_no", "section_id", "bus", "order_at_bus", "Pg_from", "Qg_from", "Pg_to", "Qg_to"],
        ),
        gen_vg=_records_to_frame(
            gen_vg,
            ["line_no", "section_id", "bus", "order_at_bus", "Vg_from", "Vg_to"],
        ),
    )


def iter_note_sections(m_path: str | Path) -> list[dict[str, Any]]:
    """Return all ``% INFO : === section ===`` blocks and their raw note lines."""
    m_path = Path(m_path)
    lines = m_path.read_text(encoding="utf-8", errors="replace").splitlines()
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line_no, raw_line in enumerate(lines, start=1):
        if match := SECTION_RE.search(raw_line):
            if current is not None:
                current["end_line"] = line_no - 1
                sections.append(current)
            current = {
                "case_name": m_path.stem,
                "m_path": str(m_path),
                "section": match.group(1).strip(),
                "section_id": len(sections) + 1,
                "start_line": line_no,
                "end_line": line_no,
                "lines": [],
            }
            continue
        if current is not None:
            current["lines"].append((line_no, raw_line))

    if current is not None:
        current["end_line"] = len(lines)
        sections.append(current)
    return sections


def _classify_note_line(section: str, line: str) -> tuple[str, bool]:
    if section == "Load Replacement Notes" and LOAD_RE.search(line):
        return "load_pd_qd", True
    if section == "Voltage Setpoint Replacement Notes" and BUS_VOLTAGE_RE.search(line):
        return "bus_voltage_angle", True
    if section == "Generator Setpoint Replacement Notes" and GEN_PQ_RE.search(line):
        return "gen_pg_qg", True
    if section == "Generator Setpoint Replacement Notes" and GEN_VG_RE.search(line):
        return "gen_vg", True
    if section == "Generator Classification Notes" and GEN_CLASS_RE.search(line):
        return "generator_classification", False
    if section == "Generator Active Cost Stat Model Notes" and GEN_COST_RE.search(line):
        return "generator_active_cost", False
    if section == "Generator Bounds Update Notes" and GEN_BOUNDS_RE.search(line):
        return "generator_bounds", False
    if section == "Base KV Replacement Notes" and BASE_KV_RE.search(line):
        return "base_kv", False
    if section == "Transformer Setting Replacement Notes" and TRANSFORMER_SETTING_RE.search(line):
        return "transformer_setting", False
    if section == "Line Capacity UB Model Notes" and LINE_CAPACITY_RE.search(line):
        return "line_capacity", False
    return "unclassified", False


def scan_replacement_note_inventory(
    m_paths: str | Path | Iterable[str | Path] | None = None,
    *,
    m_dir: str | Path = DEFAULT_M_DIR,
    pattern: str = "pglib_opf_case*.m",
    output_csv: str | Path | None = None,
) -> pd.DataFrame:
    """Scan PGLib ``.m`` files and report note-block coverage before applying anything."""
    if m_paths is None:
        paths = sorted(Path(m_dir).glob(pattern))
    elif isinstance(m_paths, (str, Path)):
        path = Path(m_paths)
        paths = sorted(path.glob(pattern)) if path.is_dir() else [path]
    else:
        paths = [Path(path) for path in m_paths]

    rows: list[dict[str, Any]] = []
    for m_path in paths:
        for section in iter_note_sections(m_path):
            nonblank_lines = [
                line
                for _, line in section["lines"]
                if line.startswith("%") and line.strip() not in {"% INFO    :", "% WARNING :"}
            ]
            info_lines = [line for line in nonblank_lines if line.startswith("% INFO")]
            warning_lines = [line for line in nonblank_lines if line.startswith("% WARNING")]
            classifications: dict[str, int] = {}
            parsed_now = 0
            known_future = 0
            for line in nonblank_lines:
                kind, currently_parsed = _classify_note_line(section["section"], line)
                classifications[kind] = classifications.get(kind, 0) + 1
                if currently_parsed:
                    parsed_now += 1
                elif kind != "unclassified":
                    known_future += 1
            rows.append(
                {
                    "case_name": m_path.stem,
                    "section": section["section"],
                    "section_id": section["section_id"],
                    "start_line": section["start_line"],
                    "end_line": section["end_line"],
                    "n_info_lines": len(info_lines),
                    "n_warning_lines": len(warning_lines),
                    "n_note_lines": len(nonblank_lines),
                    "n_currently_parsed_lines": parsed_now,
                    "n_known_future_lines": known_future,
                    "n_unclassified_lines": classifications.get("unclassified", 0),
                    "line_kinds": ";".join(f"{key}:{value}" for key, value in sorted(classifications.items())),
                    "m_path": str(m_path),
                }
            )

    inventory = pd.DataFrame(rows)
    if output_csv is not None:
        output_csv = Path(output_csv)
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        inventory.to_csv(output_csv, index=False)
    return inventory


def _numeric_suffix(value: Any) -> int | None:
    match = re.search(r"(\d+)(?:\.0)?(?:$|[_\-\s])", str(value))
    return int(match.group(1)) if match else None


def _remember_bus_mapping(
    mapping: dict[int, str],
    sources: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    matpower_bus: int,
    bus_id: Any,
    source: str,
) -> None:
    if pd.isna(bus_id):
        return
    bus_id_text = str(bus_id)
    if not bus_id_text.strip():
        return
    sources.append({"matpower_bus": matpower_bus, "bus_id": bus_id_text, "source": source})
    existing = mapping.get(matpower_bus)
    if existing is None:
        mapping[matpower_bus] = bus_id_text
    elif existing != bus_id_text:
        conflicts.append(
            {
                "matpower_bus": matpower_bus,
                "existing_bus_id": existing,
                "new_bus_id": bus_id_text,
                "source": source,
            }
        )


def _nominal_by_voltage_level(network: Any) -> dict[str, float]:
    voltage_levels = get_table(network, "get_voltage_levels")
    if voltage_levels.empty:
        return {}
    nominal_column = next(
        (column for column in ("nominal_v", "nominal_voltage", "nominal_v_kv") if column in voltage_levels),
        None,
    )
    if nominal_column is None:
        return {}
    return {
        str(row["id"]): float(row[nominal_column])
        for _, row in voltage_levels.iterrows()
        if pd.notna(row[nominal_column])
    }


def build_pypowsybl_mapping(network: Any) -> dict[str, Any]:
    """Build explicit MATPOWER-to-PyPowSyBl mapping tables."""
    buses = get_table(network, "get_buses")
    generators = get_table(network, "get_generators")
    loads = get_table(network, "get_loads")
    lines = get_table(network, "get_lines")
    transformers = get_table(network, "get_2_windings_transformers")
    nominal_by_vl = _nominal_by_voltage_level(network)

    bus_id_by_matpower_bus: dict[int, str] = {}
    bus_sources: list[dict[str, Any]] = []
    bus_conflicts: list[dict[str, Any]] = []

    for table_name, table, prefix in (
        ("load", loads, "LOAD"),
        ("generator", generators, "GEN"),
    ):
        if table.empty or "id" not in table or "bus_id" not in table:
            continue
        for _, row in table.iterrows():
            match = re.match(rf"{prefix}-(\d+)$", str(row["id"]))
            if match:
                _remember_bus_mapping(
                    bus_id_by_matpower_bus,
                    bus_sources,
                    bus_conflicts,
                    int(match.group(1)),
                    row["bus_id"],
                    f"{table_name}:{row['id']}",
                )

    for table_name, table in (("line", lines), ("two_windings_transformer", transformers)):
        if table.empty or not {"id", "bus1_id", "bus2_id"}.issubset(table.columns):
            continue
        for _, row in table.iterrows():
            numbers = re.findall(r"\d+", str(row["id"]).split("#", 1)[0])
            if len(numbers) < 2:
                continue
            _remember_bus_mapping(
                bus_id_by_matpower_bus,
                bus_sources,
                bus_conflicts,
                int(numbers[0]),
                row["bus1_id"],
                f"{table_name}:{row['id']}:bus1",
            )
            _remember_bus_mapping(
                bus_id_by_matpower_bus,
                bus_sources,
                bus_conflicts,
                int(numbers[1]),
                row["bus2_id"],
                f"{table_name}:{row['id']}:bus2",
            )

    bus_rows = []
    buses_by_id = buses.set_index("id", drop=False) if "id" in buses else pd.DataFrame()
    for matpower_bus, bus_id in sorted(bus_id_by_matpower_bus.items()):
        row = buses_by_id.loc[bus_id] if bus_id in buses_by_id.index else pd.Series(dtype=object)
        voltage_level_id = row.get("voltage_level_id") if not row.empty else None
        bus_rows.append(
            {
                "matpower_bus": matpower_bus,
                "bus_id": bus_id,
                "voltage_level_id": voltage_level_id,
                "nominal_v": nominal_by_vl.get(str(voltage_level_id)),
            }
        )

    gen_rows = []
    for _, row in generators.iterrows():
        gen_num = _numeric_suffix(row["id"]) if "id" in row else None
        connected_bus = row.get("bus_id")
        connected_bus_numbers = [
            bus for bus, bus_id in bus_id_by_matpower_bus.items() if bus_id == connected_bus
        ]
        gen_rows.append(
            {
                "generator_id": row.get("id"),
                "matpower_gen_id": gen_num,
                "matpower_bus_candidates": tuple(sorted(connected_bus_numbers)),
                "bus_id": connected_bus,
                "voltage_level_id": row.get("voltage_level_id"),
                "target_p": row.get("target_p"),
                "target_q": row.get("target_q"),
                "target_v": row.get("target_v"),
                "min_q": row.get("min_q"),
                "max_q": row.get("max_q"),
                "max_p": row.get("max_p"),
            }
        )

    load_rows = []
    for _, row in loads.iterrows():
        load_num = _numeric_suffix(row["id"]) if "id" in row else None
        connected_bus = row.get("bus_id")
        connected_bus_numbers = [
            bus for bus, bus_id in bus_id_by_matpower_bus.items() if bus_id == connected_bus
        ]
        load_rows.append(
            {
                "load_id": row.get("id"),
                "matpower_load_id": load_num,
                "matpower_bus_candidates": tuple(sorted(connected_bus_numbers)),
                "bus_id": connected_bus,
                "voltage_level_id": row.get("voltage_level_id"),
                "p0": row.get("p0"),
                "q0": row.get("q0"),
            }
        )

    return {
        "buses": buses,
        "generators": generators,
        "loads": loads,
        "bus_map": pd.DataFrame(bus_rows),
        "bus_sources": pd.DataFrame(bus_sources),
        "bus_conflicts": pd.DataFrame(bus_conflicts),
        "generator_map": pd.DataFrame(gen_rows),
        "load_map": pd.DataFrame(load_rows),
        "nominal_by_voltage_level": nominal_by_vl,
        "bus_id_by_matpower_bus": bus_id_by_matpower_bus,
    }


def _direction_value(row: pd.Series, base: str, direction: str) -> Any:
    return row[f"{base}_{direction}"]


def _empty_audit() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "case_name",
            "variant_name",
            "parameter_scenario",
            "note_type",
            "matpower_bus",
            "order_at_bus",
            "field",
            "matched_object_kind",
            "matched_object_id",
            "old_value",
            "note_from_value",
            "note_to_value",
            "applied_value",
            "applied",
            "reason",
            "mapping_method",
        ]
    )


def _match_generator(
    note_bus: int,
    order_at_bus: int,
    mapping: dict[str, Any],
    consumed: set[str] | None = None,
) -> tuple[pd.Series | None, str, str]:
    generators = mapping["generators"]
    bus_id = mapping["bus_id_by_matpower_bus"].get(note_bus)
    consumed = consumed if consumed is not None else set()

    exact_id = f"GEN-{note_bus}"
    if "id" in generators and order_at_bus == 1:
        exact = generators[
            (generators["id"].astype(str) == exact_id)
            & (~generators["id"].astype(str).isin(consumed))
        ]
        if not exact.empty:
            return exact.iloc[0], "exact_generator_id_verified", ""

    if bus_id is not None and "bus_id" in generators:
        candidates = generators[generators["bus_id"].astype(str) == str(bus_id)].copy()
        if "id" in candidates:
            candidates = candidates.assign(
                _sort_id=candidates["id"].astype(str).map(
                    lambda value: (0, -1) if value == exact_id else (1, int(value.rsplit("#", 1)[1])) if "#" in value and value.rsplit("#", 1)[1].isdigit() else (2, value)
                )
            ).sort_values("_sort_id")
        if len(candidates) >= order_at_bus:
            candidate = candidates.iloc[order_at_bus - 1]
            if str(candidate["id"]) not in consumed:
                return candidate, "bus_order", ""
        unconsumed = candidates[~candidates["id"].astype(str).isin(consumed)] if "id" in candidates else candidates
        if len(unconsumed) == 1:
            return unconsumed.iloc[0], "bus_single", ""

    if "id" in generators:
        exact_candidates = generators[
            generators["id"].astype(str).str.fullmatch(rf"GEN-{note_bus}(?:#\d+)?", na=False)
            & (~generators["id"].astype(str).isin(consumed))
        ].copy()
        if len(exact_candidates) >= order_at_bus:
            return exact_candidates.iloc[order_at_bus - 1], "exact_generator_family_fallback", ""
        if not exact_candidates.empty:
            return exact_candidates.iloc[0], "exact_generator_family_fallback", ""

    if bus_id is None:
        return None, "", "no PyPowSyBl bus mapped for MATPOWER bus"
    return None, "", "no PyPowSyBl generator mapped"


def _match_load(note_bus: int, order_at_bus: int, mapping: dict[str, Any]) -> tuple[pd.Series | None, str, str]:
    loads = mapping["loads"]
    exact_id = f"LOAD-{note_bus}"
    if "id" in loads:
        exact = loads[loads["id"].astype(str) == exact_id]
        if not exact.empty:
            return exact.iloc[0], "exact_load_id", ""

    bus_id = mapping["bus_id_by_matpower_bus"].get(note_bus)
    if bus_id is not None and "bus_id" in loads:
        candidates = loads[loads["bus_id"].astype(str) == str(bus_id)]
        if len(candidates) >= order_at_bus:
            return candidates.iloc[order_at_bus - 1], "bus_order", ""
        if len(candidates) == 1:
            return candidates.iloc[0], "bus_single", ""

    if bus_id is None:
        return None, "", "no PyPowSyBl bus mapped for MATPOWER bus"
    return None, "", "no PyPowSyBl load mapped"


def _match_bus(note_bus: int, mapping: dict[str, Any]) -> tuple[pd.Series | None, str, str]:
    bus_id = mapping["bus_id_by_matpower_bus"].get(note_bus)
    buses = mapping["buses"]
    if bus_id is None:
        return None, "", "no PyPowSyBl bus mapped"
    if "id" not in buses:
        return None, "", "PyPowSyBl bus table has no id column"
    rows = buses[buses["id"].astype(str) == str(bus_id)]
    if rows.empty:
        return None, "", f"mapped PyPowSyBl bus {bus_id} not in get_buses()"
    return rows.iloc[0], "bus_map", ""


def _nominal_for_row(row: pd.Series, mapping: dict[str, Any], fallback_column: str) -> float | None:
    voltage_level_id = row.get("voltage_level_id")
    if pd.notna(voltage_level_id):
        nominal = mapping["nominal_by_voltage_level"].get(str(voltage_level_id))
        if nominal is not None:
            return float(nominal)
    fallback = row.get(fallback_column)
    if pd.notna(fallback) and float(fallback) > 5.0:
        return float(fallback)
    return None


def verify_replacement_mapping(
    network: Any,
    notes: ReplacementNotes,
    *,
    case_name: str = "",
    variant_name: str = "",
    parameter_scenario: str = "",
) -> pd.DataFrame:
    """Return an audit dataframe for all parsed notes without applying values."""
    mapping = build_pypowsybl_mapping(network)
    rows: list[dict[str, Any]] = []

    def append_row(
        note_type: str,
        note_row: pd.Series,
        field: str,
        matched_kind: str,
        matched_id: Any,
        old_value: Any,
        from_value: Any,
        to_value: Any,
        mapping_method: str,
        reason: str = "",
    ) -> None:
        rows.append(
            {
                "case_name": case_name,
                "variant_name": variant_name,
                "parameter_scenario": parameter_scenario,
                "note_type": note_type,
                "matpower_bus": int(note_row["bus"]),
                "order_at_bus": int(note_row["order_at_bus"]),
                "field": field,
                "matched_object_kind": matched_kind,
                "matched_object_id": None if pd.isna(matched_id) else str(matched_id),
                "old_value": old_value,
                "note_from_value": from_value,
                "note_to_value": to_value,
                "applied_value": None,
                "applied": False,
                "reason": reason,
                "mapping_method": mapping_method,
            }
        )

    for _, note in notes.load.iterrows():
        load, method, reason = _match_load(int(note["bus"]), int(note["order_at_bus"]), mapping)
        for field, column, from_col, to_col in (
            ("p0", "p0", "Pd_from", "Pd_to"),
            ("q0", "q0", "Qd_from", "Qd_to"),
        ):
            append_row(
                "load",
                note,
                field,
                "load" if load is not None else "",
                load.get("id") if load is not None else None,
                load.get(column) if load is not None else None,
                note[from_col],
                note[to_col],
                method,
                reason,
            )

    for _, note in notes.bus_voltage.iterrows():
        bus, method, reason = _match_bus(int(note["bus"]), mapping)
        for field, column, from_col, to_col in (
            ("v_mag", "v_mag", "V_from", "V_to"),
            ("v_angle", "v_angle", "theta_from", "theta_to"),
        ):
            append_row(
                "bus_voltage",
                note,
                field,
                "bus" if bus is not None else "",
                bus.get("id") if bus is not None else None,
                bus.get(column) if bus is not None else None,
                note[from_col],
                note[to_col],
                method,
                reason,
            )

    consumed_by_section: dict[int, set[str]] = {}
    for _, note in notes.gen_pgqg.iterrows():
        consumed = consumed_by_section.setdefault(int(note["section_id"]), set())
        gen, method, reason = _match_generator(int(note["bus"]), int(note["order_at_bus"]), mapping, consumed)
        if gen is not None:
            consumed.add(str(gen["id"]))
        for field, column, from_col, to_col in (
            ("target_p", "target_p", "Pg_from", "Pg_to"),
            ("target_q", "target_q", "Qg_from", "Qg_to"),
        ):
            append_row(
                "gen_pgqg",
                note,
                field,
                "generator" if gen is not None else "",
                gen.get("id") if gen is not None else None,
                gen.get(column) if gen is not None else None,
                note[from_col],
                note[to_col],
                method,
                reason,
            )

    consumed_vg_by_section: dict[int, set[str]] = {}
    for _, note in notes.gen_vg.iterrows():
        consumed_vg = consumed_vg_by_section.setdefault(int(note["section_id"]), set())
        gen, method, reason = _match_generator(int(note["bus"]), int(note["order_at_bus"]), mapping, consumed_vg)
        if gen is not None:
            consumed_vg.add(str(gen["id"]))
        append_row(
            "gen_vg",
            note,
            "target_v",
            "generator" if gen is not None else "",
            gen.get("id") if gen is not None else None,
            gen.get("target_v") if gen is not None else None,
            note["Vg_from"],
            note["Vg_to"],
            method,
            reason,
        )

    return pd.DataFrame(rows, columns=_empty_audit().columns)


def make_replacement_variants(include_bus_voltage: bool = False) -> dict[str, ReplacementVariant]:
    variants = [
        ReplacementVariant("original", ()),
        ReplacementVariant("G_PGQG_from", ("G_PG", "G_QG"), "from"),
        ReplacementVariant("G_PGQG_from_plus_GVG_from", ("G_PG", "G_QG", "G_VG"), "from"),
    ]
    if include_bus_voltage:
        variants.insert(1, ReplacementVariant("BV_from_warmstart", ("BV",), "from", "PREVIOUS_VALUES"))
        variants.insert(3, ReplacementVariant("G_PGQG_from_plus_BV_from", ("G_PG", "G_QG", "BV"), "from", "PREVIOUS_VALUES"))
    return {variant.name: variant for variant in variants}


def _clean_update_records(updates: pd.DataFrame, columns: tuple[str, ...]) -> tuple[list[dict[str, Any]], int]:
    if updates.empty:
        return [], 0
    skipped = 0
    by_id: dict[str, dict[str, Any]] = {}
    for _, row in updates.iterrows():
        if "id" not in row or pd.isna(row["id"]):
            skipped += 1
            continue
        record = by_id.setdefault(str(row["id"]), {"id": str(row["id"])})
        for column in columns:
            if column not in updates.columns:
                continue
            value = row.get(column)
            if pd.isna(value):
                skipped += 1
                continue
            record[column] = value
    records = [record for record in by_id.values() if len(record) > 1]
    return records, skipped


def _update_generators(network: Any, updates: pd.DataFrame) -> int:
    records, skipped = _clean_update_records(updates, ("target_p", "target_q", "target_v"))
    for record in records:
        network.update_generators(**{column: [value] for column, value in record.items()})
    return skipped


def _update_loads(network: Any, updates: pd.DataFrame) -> int:
    records, skipped = _clean_update_records(updates, ("p0", "q0"))
    for record in records:
        network.update_loads(**{column: [value] for column, value in record.items()})
    return skipped


def _update_buses(network: Any, updates: pd.DataFrame) -> int:
    records, skipped = _clean_update_records(updates, ("v_mag", "v_angle"))
    for record in records:
        network.update_buses(**{column: [value] for column, value in record.items()})
    return skipped


def apply_replacement_variant(
    network: Any,
    notes: ReplacementNotes,
    variant: ReplacementVariant,
    *,
    case_name: str = "",
    parameter_scenario: str = "",
    strict: bool = False,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Apply a replacement variant and return a per-field mapping audit."""
    direction = variant.direction or "from"
    groups = set(variant.groups)
    mapping = build_pypowsybl_mapping(network)
    audit = verify_replacement_mapping(
        network,
        notes,
        case_name=case_name,
        variant_name=variant.name,
        parameter_scenario=parameter_scenario,
    )

    generator_updates: list[dict[str, Any]] = []
    load_updates: list[dict[str, Any]] = []
    bus_updates: list[dict[str, Any]] = []

    audit_by_key = {
        (
            row["note_type"],
            int(row["matpower_bus"]),
            int(row["order_at_bus"]),
            row["field"],
        ): idx
        for idx, row in audit.iterrows()
    }

    def mark_applied(note_type: str, note: pd.Series, field: str, applied_value: Any) -> None:
        key = (note_type, int(note["bus"]), int(note["order_at_bus"]), field)
        idx = audit_by_key.get(key)
        if idx is not None:
            audit.at[idx, "applied"] = True
            audit.at[idx, "applied_value"] = applied_value
            audit.at[idx, "reason"] = ""

    consumed_by_section: dict[int, set[str]] = {}
    for _, note in notes.gen_pgqg.iterrows():
        consumed = consumed_by_section.setdefault(int(note["section_id"]), set())
        gen, _, reason = _match_generator(int(note["bus"]), int(note["order_at_bus"]), mapping, consumed)
        if gen is None:
            if strict and ("G_PG" in groups or "G_QG" in groups):
                raise ValueError(f"Unmatched generator Pg/Qg note at bus {int(note['bus'])}: {reason}")
            continue
        consumed.add(str(gen["id"]))
        update: dict[str, Any] = {"id": gen["id"]}
        if "G_PG" in groups:
            value = _direction_value(note, "Pg", direction)
            update["target_p"] = value
            mark_applied("gen_pgqg", note, "target_p", value)
        if "G_QG" in groups:
            value = _direction_value(note, "Qg", direction)
            update["target_q"] = value
            mark_applied("gen_pgqg", note, "target_q", value)
        if len(update) > 1:
            generator_updates.append(update)

    consumed_vg_by_section: dict[int, set[str]] = {}
    for _, note in notes.gen_vg.iterrows():
        if "G_VG" not in groups:
            continue
        consumed_vg = consumed_vg_by_section.setdefault(int(note["section_id"]), set())
        gen, _, reason = _match_generator(int(note["bus"]), int(note["order_at_bus"]), mapping, consumed_vg)
        if gen is None:
            if strict:
                raise ValueError(f"Unmatched generator Vg note at bus {int(note['bus'])}: {reason}")
            continue
        consumed_vg.add(str(gen["id"]))
        nominal_v = _nominal_for_row(gen, mapping, "target_v")
        if nominal_v is None:
            if strict:
                raise ValueError(f"Could not convert Vg for generator {gen['id']}")
            continue
        value = _direction_value(note, "Vg", direction) * nominal_v
        generator_updates.append({"id": gen["id"], "target_v": value})
        mark_applied("gen_vg", note, "target_v", value)

    for _, note in notes.load.iterrows():
        if "L" not in groups:
            continue
        load, _, reason = _match_load(int(note["bus"]), int(note["order_at_bus"]), mapping)
        if load is None:
            if strict:
                raise ValueError(f"Unmatched load note at bus {int(note['bus'])}: {reason}")
            continue
        update = {"id": load["id"]}
        p_value = _direction_value(note, "Pd", direction)
        q_value = _direction_value(note, "Qd", direction)
        update["p0"] = p_value
        update["q0"] = q_value
        load_updates.append(update)
        mark_applied("load", note, "p0", p_value)
        mark_applied("load", note, "q0", q_value)

    for _, note in notes.bus_voltage.iterrows():
        if "BV" not in groups:
            continue
        bus, _, reason = _match_bus(int(note["bus"]), mapping)
        if bus is None:
            if strict:
                raise ValueError(f"Unmatched bus-voltage note at bus {int(note['bus'])}: {reason}")
            continue
        nominal_v = _nominal_for_row(bus, mapping, "v_mag")
        if nominal_v is None:
            if strict:
                raise ValueError(f"Could not convert bus voltage at bus {int(note['bus'])}")
            continue
        v_value = _direction_value(note, "V", direction) * nominal_v
        angle_value = _direction_value(note, "theta", direction)
        bus_updates.append({"id": bus["id"], "v_mag": v_value, "v_angle": angle_value})
        mark_applied("bus_voltage", note, "v_mag", v_value)
        mark_applied("bus_voltage", note, "v_angle", angle_value)

    n_nan_updates_skipped = (
        _update_buses(network, pd.DataFrame(bus_updates))
        + _update_loads(network, pd.DataFrame(load_updates))
        + _update_generators(network, pd.DataFrame(generator_updates))
    )

    counts = {
        "n_applied_gen_pg": int((audit["note_type"].eq("gen_pgqg") & audit["field"].eq("target_p") & audit["applied"]).sum()),
        "n_applied_gen_qg": int((audit["note_type"].eq("gen_pgqg") & audit["field"].eq("target_q") & audit["applied"]).sum()),
        "n_applied_gen_vg": int((audit["note_type"].eq("gen_vg") & audit["field"].eq("target_v") & audit["applied"]).sum()),
        "n_applied_loads": int((audit["note_type"].eq("load") & audit["applied"]).sum() / 2),
        "n_applied_bus_voltage": int((audit["note_type"].eq("bus_voltage") & audit["field"].eq("v_mag") & audit["applied"]).sum()),
        "n_nan_updates_skipped": n_nan_updates_skipped,
    }
    return audit, counts


def _enum_name(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "name", value)).split(".")[-1]


def make_parameter_scenarios(*, include_operational: bool = True) -> dict[str, ParameterScenario]:
    pp = require_pypowsybl()
    lf = pp.loadflow

    def scenario(name: str, **kwargs: Any) -> ParameterScenario:
        provider_parameters = {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "1000",
        }
        provider_parameters.update(kwargs.pop("provider_parameters", {}))
        params = lf.Parameters(provider_parameters=provider_parameters, **kwargs)
        settings = {
            "voltage_init_mode": _enum_name(kwargs.get("voltage_init_mode")),
            "use_reactive_limits": kwargs.get("use_reactive_limits"),
            "distributed_slack": kwargs.get("distributed_slack"),
            "balance_type": _enum_name(kwargs.get("balance_type")),
            "transformer_voltage_control_on": kwargs.get("transformer_voltage_control_on"),
            "shunt_compensator_voltage_control_on": kwargs.get("shunt_compensator_voltage_control_on"),
            "phase_shifter_regulation_on": kwargs.get("phase_shifter_regulation_on"),
            "read_slack_bus": kwargs.get("read_slack_bus"),
            "write_slack_bus": kwargs.get("write_slack_bus"),
            "component_mode": _enum_name(kwargs.get("component_mode")),
        }
        return ParameterScenario(
            name,
            params,
            provider="OpenLoadFlow",
            settings=settings,
            provider_parameters={key: str(value) for key, value in provider_parameters.items()},
        )

    common_controls_off = {
        "transformer_voltage_control_on": False,
        "shunt_compensator_voltage_control_on": False,
        "phase_shifter_regulation_on": False,
        "read_slack_bus": True,
        "write_slack_bus": True,
        "component_mode": lf.ComponentMode.MAIN_CONNECTED,
    }

    scenarios = {
        "strict_single_slack": scenario(
            "strict_single_slack",
            voltage_init_mode=lf.VoltageInitMode.UNIFORM_VALUES,
            distributed_slack=False,
            use_reactive_limits=False,
            **common_controls_off,
        ),
        "single_slack_q_limits": scenario(
            "single_slack_q_limits",
            voltage_init_mode=lf.VoltageInitMode.UNIFORM_VALUES,
            distributed_slack=False,
            use_reactive_limits=True,
            **common_controls_off,
        ),
        "distributed_q_limits": scenario(
            "distributed_q_limits",
            voltage_init_mode=lf.VoltageInitMode.DC_VALUES,
            distributed_slack=True,
            balance_type=lf.BalanceType.PROPORTIONAL_TO_GENERATION_P_MAX,
            use_reactive_limits=True,
            **common_controls_off,
            provider_parameters={"slackBusSelectionMode": "LARGEST_GENERATOR"},
        ),
    }

    if include_operational:
        scenarios["operational_controls"] = scenario(
            "operational_controls",
            voltage_init_mode=lf.VoltageInitMode.DC_VALUES,
            distributed_slack=True,
            balance_type=lf.BalanceType.PROPORTIONAL_TO_GENERATION_P_MAX,
            use_reactive_limits=True,
            transformer_voltage_control_on=True,
            shunt_compensator_voltage_control_on=True,
            phase_shifter_regulation_on=True,
            read_slack_bus=True,
            write_slack_bus=True,
            component_mode=lf.ComponentMode.MAIN_CONNECTED,
            hvdc_ac_emulation=True,
            provider_parameters={"slackBusSelectionMode": "LARGEST_GENERATOR"},
        )

    return scenarios


def make_power_limit_parameter_scenarios() -> dict[str, ParameterScenario]:
    return make_parameter_scenarios(include_operational=False)


def _balance(network: Any) -> dict[str, float]:
    generators = get_table(network, "get_generators")
    loads = get_table(network, "get_loads")
    total_gen = float(pd.to_numeric(generators.get("target_p", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    total_load = float(pd.to_numeric(loads.get("p0", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
    return {
        "total_generation": total_gen,
        "total_load": total_load,
        "gen_minus_load": total_gen - total_load,
    }


def _feasibility(network: Any) -> dict[str, Any]:
    generators = get_table(network, "get_generators")
    if generators.empty:
        return {
            "total_max_generation_after": 0.0,
            "total_headroom_after": 0.0,
            "number_of_generators_with_max_p_zero": 0,
            "number_of_generators_with_target_p_above_max_p": 0,
            "number_of_generators_with_target_q_outside_limits": 0,
        }
    target_p = pd.to_numeric(generators.get("target_p", pd.Series(dtype=float)), errors="coerce")
    max_p = pd.to_numeric(generators.get("max_p", pd.Series(dtype=float)), errors="coerce")
    target_q = pd.to_numeric(generators.get("target_q", pd.Series(dtype=float)), errors="coerce")
    min_q = pd.to_numeric(generators.get("min_q", pd.Series(dtype=float)), errors="coerce")
    max_q = pd.to_numeric(generators.get("max_q", pd.Series(dtype=float)), errors="coerce")
    return {
        "total_max_generation_after": float(max_p.fillna(0.0).sum()),
        "total_headroom_after": float((max_p - target_p).fillna(0.0).sum()),
        "number_of_generators_with_max_p_zero": int((max_p.fillna(0.0) == 0.0).sum()),
        "number_of_generators_with_target_p_above_max_p": int((target_p > max_p).fillna(False).sum()),
        "number_of_generators_with_target_q_outside_limits": int(((target_q < min_q) | (target_q > max_q)).fillna(False).sum()),
    }


def _component_results_frame(results: Any) -> pd.DataFrame:
    values = results if isinstance(results, list) else [results]
    rows = []
    for index, result in enumerate(values):
        row = {"component": index}
        for name in (
            "status",
            "status_text",
            "iteration_count",
            "reference_bus_id",
            "slack_bus_id",
            "slack_bus_active_power_mismatch",
            "distributed_active_power",
        ):
            if hasattr(result, name):
                row[name] = getattr(result, name)
        slack_results = getattr(result, "slack_bus_results", None)
        if slack_results:
            first_slack = slack_results[0]
            row["slack_bus_id"] = getattr(first_slack, "id", row.get("slack_bus_id"))
            row["slack_active_power_mismatch"] = getattr(first_slack, "active_power_mismatch", None)
        rows.append(row)
    return pd.DataFrame(rows)


def _is_converged_status(status: Any) -> bool:
    text = str(status).split(".")[-1].upper()
    return text == "CONVERGED"


def run_openloadflow(network: Any, params: Any, provider: str = "OpenLoadFlow") -> dict[str, Any]:
    pp = require_pypowsybl()
    started = time.perf_counter()
    results = pp.loadflow.run_ac(network, parameters=params, provider=provider)
    elapsed = time.perf_counter() - started
    frame = _component_results_frame(results)
    row = frame.iloc[0].to_dict() if not frame.empty else {}
    status = str(row.get("status", "UNKNOWN"))
    all_components_converged = bool(len(frame)) and bool(frame["status"].map(_is_converged_status).all()) if "status" in frame else False
    return {
        "status": status,
        "status_text": row.get("status_text"),
        "converged": _is_converged_status(status),
        "component_count": int(len(frame)),
        "main_component_status": status,
        "all_components_converged": all_components_converged,
        "iteration_count": row.get("iteration_count"),
        "reference_bus_id": row.get("reference_bus_id"),
        "slack_bus_id": row.get("slack_bus_id"),
        "slack_active_power_mismatch": row.get("slack_active_power_mismatch", row.get("slack_bus_active_power_mismatch")),
        "distributed_active_power": row.get("distributed_active_power"),
        "elapsed_seconds": elapsed,
        "elapsed_seconds_single": elapsed,
        "component_results": frame,
        "raw_results": results,
    }


def _case_paths(case_name: str, mat_dir: Path, m_dir: Path) -> tuple[Path, Path]:
    stem = Path(case_name).stem
    return mat_dir / f"{stem}.mat", m_dir / f"{stem}.m"


def _base_case_name(stem: str) -> str:
    return re.sub(r"__(api|sad)$", "", stem)


def _scenario_accepts_stem(scenario_folder: str, stem: str) -> tuple[bool, str]:
    suffix = SCENARIO_FOLDER_SPECS[scenario_folder]["suffix"]
    if scenario_folder == "base_case":
        if re.search(r"__(api|sad)$", stem):
            return False, "base_case accepts only unsuffixed files"
        return True, "selected"
    if stem.endswith(str(suffix)):
        return True, "selected"
    return False, f"{scenario_folder} accepts only files ending in {suffix}"


def build_presentable_scenario_inventory(
    *,
    mat_root: str | Path = DEFAULT_MAT_DIR,
    m_root: str | Path = DEFAULT_M_DIR,
    output_csv: str | Path | None = DEFAULT_INVENTORY_CSV,
) -> tuple[pd.DataFrame, list[ScenarioCase]]:
    mat_root = Path(mat_root)
    m_root = Path(m_root)
    rows: list[dict[str, Any]] = []
    selected_cases: list[ScenarioCase] = []

    for scenario_folder in SCENARIO_FOLDER_SPECS:
        mat_dir = mat_root / scenario_folder
        m_dir = m_root / scenario_folder
        candidate_stems = sorted(
            set(path.stem for path in mat_dir.glob("pglib_opf_case*.mat"))
            | set(path.stem for path in m_dir.glob("pglib_opf_case*.m"))
        )
        for stem in candidate_stems:
            mat_path = mat_dir / f"{stem}.mat"
            m_path = m_dir / f"{stem}.m"
            suffix_selected, suffix_reason = _scenario_accepts_stem(scenario_folder, stem)
            selected = bool(suffix_selected and mat_path.exists() and m_path.exists())
            if not mat_path.exists():
                reason = "missing .mat file"
            elif not m_path.exists():
                reason = "missing .m file"
            else:
                reason = suffix_reason
            row = {
                "scenario_folder": scenario_folder,
                "scenario_label": SCENARIO_FOLDER_SPECS[scenario_folder]["label"],
                "case_name": stem,
                "base_case_name": _base_case_name(stem),
                "mat_path": str(mat_path),
                "m_path": str(m_path),
                "mat_exists": mat_path.exists(),
                "m_exists": m_path.exists(),
                "selected": selected,
                "reason": reason,
            }
            rows.append(row)
            if selected:
                selected_cases.append(
                    ScenarioCase(
                        scenario_folder=scenario_folder,
                        case_name=stem,
                        base_case_name=_base_case_name(stem),
                        mat_path=mat_path,
                        m_path=m_path,
                    )
                )

    inventory = pd.DataFrame(rows)
    if output_csv is not None:
        output_csv = Path(output_csv)
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        inventory.to_csv(output_csv, index=False)
    return inventory, selected_cases


def _previous_values_available() -> bool:
    pp = require_pypowsybl()
    return hasattr(pp.loadflow.VoltageInitMode, "PREVIOUS_VALUES")


def _pypowsybl_version() -> str | None:
    pp = require_pypowsybl()
    return getattr(pp, "__version__", None)


def _with_previous_values(scenario: ParameterScenario) -> ParameterScenario:
    pp = require_pypowsybl()
    lf = pp.loadflow
    scenarios = make_parameter_scenarios(include_operational=True)
    base = scenarios[scenario.name]
    kwargs = {
        "voltage_init_mode": lf.VoltageInitMode.PREVIOUS_VALUES,
        "distributed_slack": base.settings.get("distributed_slack"),
        "use_reactive_limits": base.settings.get("use_reactive_limits"),
        "transformer_voltage_control_on": base.settings.get("transformer_voltage_control_on"),
        "shunt_compensator_voltage_control_on": base.settings.get("shunt_compensator_voltage_control_on"),
        "phase_shifter_regulation_on": base.settings.get("phase_shifter_regulation_on"),
        "read_slack_bus": base.settings.get("read_slack_bus"),
        "write_slack_bus": base.settings.get("write_slack_bus"),
        "component_mode": lf.ComponentMode.MAIN_CONNECTED,
        "provider_parameters": base.provider_parameters,
    }
    if base.settings.get("balance_type") == "PROPORTIONAL_TO_GENERATION_P_MAX":
        kwargs["balance_type"] = lf.BalanceType.PROPORTIONAL_TO_GENERATION_P_MAX
    previous_params = lf.Parameters(**kwargs)
    settings = dict(base.settings)
    settings["voltage_init_mode"] = "PREVIOUS_VALUES"
    return ParameterScenario(
        scenario.name,
        previous_params,
        scenario.provider,
        settings=settings,
        provider_parameters=base.provider_parameters,
    )


def run_case_experiment_matrix(
    case_name: str,
    replacement_variants: dict[str, ReplacementVariant] | Iterable[ReplacementVariant] | None = None,
    parameter_scenarios: dict[str, ParameterScenario] | Iterable[ParameterScenario] | None = None,
    *,
    mat_dir: str | Path = DEFAULT_MAT_DIR,
    m_dir: str | Path = DEFAULT_M_DIR,
    summary_csv: str | Path | None = DEFAULT_SUMMARY_CSV,
    audit_csv: str | Path | None = DEFAULT_AUDIT_CSV,
    include_bus_voltage: bool = False,
    strict: bool = False,
    timing_repetitions: int = 3,
    verbose: bool = True,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run all replacement variants across parameter scenarios for one case."""
    mat_path, m_path = _case_paths(case_name, Path(mat_dir), Path(m_dir))
    if not mat_path.exists():
        raise FileNotFoundError(f"Missing .mat file: {mat_path}")
    if not m_path.exists():
        raise FileNotFoundError(f"Missing matching .m file: {m_path}")

    notes = parse_replacement_notes(m_path)
    variants_value = replacement_variants or make_replacement_variants(include_bus_voltage=include_bus_voltage)
    variants = list(variants_value.values()) if isinstance(variants_value, dict) else list(variants_value)
    scenarios_value = parameter_scenarios or make_parameter_scenarios()
    scenarios = list(scenarios_value.values()) if isinstance(scenarios_value, dict) else list(scenarios_value)
    has_previous = _previous_values_available()
    pypowsybl_version = _pypowsybl_version()

    summary_rows: list[dict[str, Any]] = []
    audit_frames: list[pd.DataFrame] = []

    for variant in variants:
        for scenario in scenarios:
            if progress:
                progress({"event": "start", "case": case_name, "variant": variant.name, "scenario": scenario.name})
            if verbose:
                print(f"\n=== {case_name} | {variant.name} | {scenario.name} ===")

            row: dict[str, Any] = {
                "case_name": Path(case_name).stem,
                "base_case_name": re.sub(r"__(api|sad)$", "", Path(case_name).stem),
                "variant_name": variant.name,
                "variant_label": VARIANT_LABELS.get(variant.name, variant.name),
                "parameter_scenario": scenario.name,
                "pf_control_label": PF_CONTROL_LABELS.get(scenario.name, scenario.name),
                "replacement_groups": "+".join(variant.groups),
                "direction": variant.direction,
                "provider": scenario.provider,
                "pypowsybl_version": pypowsybl_version,
                **scenario.settings,
                "provider_parameters": ";".join(f"{key}={value}" for key, value in sorted(scenario.provider_parameters.items())),
                "mapping_success": False,
                "n_bus_voltage_notes": len(notes.bus_voltage),
                "n_gen_pgqg_notes": len(notes.gen_pgqg),
                "n_gen_vg_notes": len(notes.gen_vg),
                "n_load_notes": len(notes.load),
                "n_notes_parsed": len(notes.bus_voltage) + len(notes.gen_pgqg) + len(notes.gen_vg) + len(notes.load),
                "n_notes_applied": 0,
                "n_notes_unmatched": 0,
                "n_applied_gen_pg": 0,
                "n_applied_gen_qg": 0,
                "n_applied_gen_vg": 0,
                "n_applied_loads": 0,
                "n_applied_bus_voltage": 0,
                "n_nan_updates_skipped": 0,
                "n_unmatched_notes": 0,
                "total_generation_before": None,
                "total_load_before": None,
                "gen_minus_load_before": None,
                "total_generation_after": None,
                "total_load_after": None,
                "gen_minus_load_after": None,
                "status": "NOT_RUN",
                "status_text": None,
                "converged": False,
                "component_count": 0,
                "main_component_status": None,
                "all_components_converged": False,
                "iteration_count": None,
                "reference_bus_id": None,
                "slack_bus_id": None,
                "slack_active_power_mismatch": None,
                "distributed_active_power": None,
                "elapsed_seconds": None,
                "elapsed_seconds_single": None,
                "elapsed_seconds_median": None,
                "elapsed_seconds_min": None,
                "elapsed_seconds_max": None,
                "n_timing_repetitions": 0,
                "exception": None,
            }

            try:
                if variant.voltage_init == "PREVIOUS_VALUES" and not has_previous:
                    row["status"] = "SKIPPED"
                    row["exception"] = "VoltageInitMode.PREVIOUS_VALUES not available"
                    network = load_case_network(mat_path)
                    audit = verify_replacement_mapping(network, notes, case_name=Path(case_name).stem, variant_name=variant.name, parameter_scenario=scenario.name)
                else:
                    run_scenario = _with_previous_values(scenario) if variant.voltage_init == "PREVIOUS_VALUES" else scenario
                    row.update(run_scenario.settings)

                    def prepare_network() -> tuple[Any, pd.DataFrame, dict[str, Any], dict[str, float], dict[str, float], dict[str, Any]]:
                        prepared = load_case_network(mat_path)
                        before = _balance(prepared)
                        audit_frame, count_values = apply_replacement_variant(
                            prepared,
                            notes,
                            variant,
                            case_name=Path(case_name).stem,
                            parameter_scenario=scenario.name,
                            strict=strict,
                        )
                        after = _balance(prepared)
                        return prepared, audit_frame, count_values, before, after, _feasibility(prepared)

                    network, audit, counts, before, after, feasibility = prepare_network()
                    unmatched = audit[
                        audit["matched_object_id"].isna()
                        & audit["note_type"].isin(["load", "bus_voltage", "gen_pgqg", "gen_vg"])
                    ]
                    applied_fields = int(audit["applied"].fillna(False).sum()) if "applied" in audit else 0
                    row.update(
                        {
                            **counts,
                            "n_notes_applied": applied_fields,
                            "n_notes_unmatched": int(len(unmatched)),
                            "n_unmatched_notes": int(len(unmatched)),
                            "mapping_success": int(len(unmatched)) == 0,
                            "total_generation_before": before["total_generation"],
                            "total_load_before": before["total_load"],
                            "gen_minus_load_before": before["gen_minus_load"],
                            "total_generation_after": after["total_generation"],
                            "total_load_after": after["total_load"],
                            "gen_minus_load_after": after["gen_minus_load"],
                            **feasibility,
                        }
                    )

                    run_openloadflow(network, run_scenario.params, provider=run_scenario.provider)
                    measured: list[dict[str, Any]] = []
                    for _ in range(max(1, timing_repetitions)):
                        timed_network, _, _, _, _, _ = prepare_network()
                        measured.append(run_openloadflow(timed_network, run_scenario.params, provider=run_scenario.provider))

                    elapsed_values = [float(item["elapsed_seconds_single"]) for item in measured if item.get("elapsed_seconds_single") is not None]
                    lf_result = measured[0] if measured else {}
                    row.update({key: value for key, value in lf_result.items() if key not in {"component_results", "raw_results", "elapsed_seconds"}})
                    if elapsed_values:
                        row.update(
                            {
                                "elapsed_seconds": median(elapsed_values),
                                "elapsed_seconds_single": elapsed_values[0],
                                "elapsed_seconds_median": median(elapsed_values),
                                "elapsed_seconds_min": min(elapsed_values),
                                "elapsed_seconds_max": max(elapsed_values),
                                "n_timing_repetitions": len(elapsed_values),
                            }
                        )
                audit_frames.append(audit)
            except Exception as exc:
                row["status"] = "EXCEPTION"
                row["exception"] = repr(exc)
                network = load_case_network(mat_path)
                audit = verify_replacement_mapping(network, notes, case_name=Path(case_name).stem, variant_name=variant.name, parameter_scenario=scenario.name)
                audit_frames.append(audit)

            summary_rows.append(row)
            if verbose:
                print(
                    f"status={row['status']} iterations={row['iteration_count']} "
                    f"balance_after={row['gen_minus_load_after']}"
                )
            if progress:
                progress({"event": "finish", "case": case_name, "variant": variant.name, "scenario": scenario.name, "row": row})

    summary = pd.DataFrame(summary_rows)
    audit_all = pd.concat(audit_frames, ignore_index=True) if audit_frames else _empty_audit()

    for output_path, frame in ((summary_csv, summary), (audit_csv, audit_all)):
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(output_path, index=False)

    return summary, audit_all


def verify_mapping_coverage_for_cases(
    case_names: Iterable[str] | None = None,
    *,
    mat_dir: str | Path = DEFAULT_MAT_DIR,
    m_dir: str | Path = DEFAULT_M_DIR,
    coverage_csv: str | Path | None = DEFAULT_MAPPING_COVERAGE_CSV,
    audit_csv: str | Path | None = None,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Verify parsed replacement-note mapping for cases without running load flow."""
    mat_dir = Path(mat_dir)
    m_dir = Path(m_dir)
    if case_names is None:
        case_names = sorted(path.stem for path in mat_dir.glob("pglib_opf_case*.mat") if (m_dir / f"{path.stem}.m").exists())

    coverage_rows: list[dict[str, Any]] = []
    audit_frames: list[pd.DataFrame] = []
    for case_name in case_names:
        case_name = Path(case_name).stem
        mat_path, m_path = _case_paths(case_name, mat_dir, m_dir)
        row: dict[str, Any] = {
            "case_name": case_name,
            "status": "NOT_RUN",
            "n_bus_voltage_notes": 0,
            "n_load_notes": 0,
            "n_gen_pgqg_notes": 0,
            "n_gen_vg_notes": 0,
            "n_audit_rows": 0,
            "n_unmatched_rows": 0,
            "mapping_success": False,
            "exception": None,
        }
        try:
            if verbose:
                print(f"Verifying mapping for {case_name}")
            notes = parse_replacement_notes(m_path)
            row.update(
                {
                    "n_bus_voltage_notes": len(notes.bus_voltage),
                    "n_load_notes": len(notes.load),
                    "n_gen_pgqg_notes": len(notes.gen_pgqg),
                    "n_gen_vg_notes": len(notes.gen_vg),
                }
            )
            network = load_case_network(mat_path)
            audit = verify_replacement_mapping(network, notes, case_name=case_name)
            unmatched = audit[audit["matched_object_id"].isna()]
            by_type = audit.groupby("note_type")["matched_object_id"].agg(total="size", mapped=lambda values: values.notna().sum())
            for note_type, counts in by_type.iterrows():
                row[f"{note_type}_audit_rows"] = int(counts["total"])
                row[f"{note_type}_mapped_rows"] = int(counts["mapped"])
                row[f"{note_type}_unmatched_rows"] = int(counts["total"] - counts["mapped"])
            row.update(
                {
                    "status": "OK",
                    "n_audit_rows": int(len(audit)),
                    "n_unmatched_rows": int(len(unmatched)),
                    "mapping_success": int(len(unmatched)) == 0,
                }
            )
            audit_frames.append(audit)
        except Exception as exc:
            row["status"] = "EXCEPTION"
            row["exception"] = repr(exc)
        coverage_rows.append(row)

    coverage = pd.DataFrame(coverage_rows)
    audit_all = pd.concat(audit_frames, ignore_index=True) if audit_frames else _empty_audit()

    for output_path, frame in ((coverage_csv, coverage), (audit_csv, audit_all)):
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(output_path, index=False)
    return coverage, audit_all


def verify_presentable_mapping_coverage(
    selected_cases: Iterable[ScenarioCase],
    *,
    coverage_csv: str | Path | None = DEFAULT_MAPPING_COVERAGE_CSV,
    audit_csv: str | Path | None = None,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    coverage_rows: list[dict[str, Any]] = []
    audit_frames: list[pd.DataFrame] = []
    for case in selected_cases:
        row: dict[str, Any] = {
            "scenario_folder": case.scenario_folder,
            "scenario_label": SCENARIO_FOLDER_SPECS[case.scenario_folder]["label"],
            "case_name": case.case_name,
            "base_case_name": case.base_case_name,
            "status": "NOT_RUN",
            "n_notes_parsed": 0,
            "n_audit_rows": 0,
            "n_unmatched_rows": 0,
            "mapping_success": False,
            "exception": None,
        }
        try:
            if verbose:
                print(f"Verifying mapping for {case.scenario_folder}/{case.case_name}")
            notes = parse_replacement_notes(case.m_path)
            row["n_notes_parsed"] = len(notes.bus_voltage) + len(notes.gen_pgqg) + len(notes.gen_vg) + len(notes.load)
            network = load_case_network(case.mat_path)
            audit = verify_replacement_mapping(network, notes, case_name=case.case_name)
            unmatched = audit[audit["matched_object_id"].isna()]
            row.update(
                {
                    "status": "OK",
                    "n_audit_rows": int(len(audit)),
                    "n_unmatched_rows": int(len(unmatched)),
                    "mapping_success": int(len(unmatched)) == 0,
                }
            )
            audit.insert(0, "scenario_folder", case.scenario_folder)
            audit.insert(1, "base_case_name", case.base_case_name)
            audit_frames.append(audit)
        except Exception as exc:
            row["status"] = "EXCEPTION"
            row["exception"] = repr(exc)
        coverage_rows.append(row)

    coverage = pd.DataFrame(coverage_rows)
    audit_all = pd.concat(audit_frames, ignore_index=True) if audit_frames else _empty_audit()
    for output_path, frame in ((coverage_csv, coverage), (audit_csv, audit_all)):
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(output_path, index=False)
    return coverage, audit_all


def _case_size(case_name: str) -> int | None:
    match = re.search(r"case(\d+)", case_name)
    return int(match.group(1)) if match else None


def _valid_converged_rows(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary.copy()
    rows = summary.copy()
    rows["converged"] = rows["converged"].astype(str).str.lower().eq("true") | rows["converged"].eq(True)
    rows["all_components_converged"] = rows["all_components_converged"].astype(str).str.lower().eq("true") | rows["all_components_converged"].eq(True)
    return rows[
        rows["converged"]
        & rows["all_components_converged"]
        & rows["elapsed_seconds_median"].notna()
        & rows["status"].astype(str).map(_is_converged_status)
    ].copy()


def _row_key_columns() -> list[str]:
    return ["scenario_folder", "case_name", "variant_name", "parameter_scenario"]


def _dedupe_result_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    key_columns = [column for column in _row_key_columns() if column in frame.columns]
    if not key_columns:
        return frame
    return frame.drop_duplicates(subset=key_columns, keep="last").reset_index(drop=True)


def _completed_case_keys(existing: pd.DataFrame, variants: dict[str, ReplacementVariant], scenarios: dict[str, ParameterScenario]) -> set[tuple[str, str]]:
    if existing.empty or not set(_row_key_columns()).issubset(existing.columns):
        return set()
    expected = len(variants) * len(scenarios)
    completed: set[tuple[str, str]] = set()
    grouped = existing.groupby(["scenario_folder", "case_name"])
    for key, group in grouped:
        present = group[["variant_name", "parameter_scenario"]].drop_duplicates()
        selected = present[
            present["variant_name"].isin(variants)
            & present["parameter_scenario"].isin(scenarios)
        ]
        if len(selected) >= expected:
            completed.add((str(key[0]), str(key[1])))
    return completed


def create_presentable_artifacts(
    summary: pd.DataFrame,
    inventory: pd.DataFrame,
    mapping_coverage: pd.DataFrame,
    *,
    output_dir: str | Path = DEFAULT_RESULT_ROOT,
) -> None:
    output_dir = Path(output_dir)
    figure_dir = output_dir / "figures"
    output_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)

    inventory.to_csv(output_dir / "inventory_correctness.csv", index=False)
    mapping_coverage.to_csv(output_dir / "mapping_coverage.csv", index=False)
    if summary.empty:
        return

    summary = summary.copy()
    if "variant_name" in summary:
        variant_labels = summary["variant_name"].astype(str).map(VARIANT_LABELS)
        if "variant_label" in summary:
            summary["variant_label"] = variant_labels.fillna(summary["variant_label"])
        else:
            summary["variant_label"] = variant_labels.fillna(summary["variant_name"])
    summary["bus_count"] = summary["base_case_name"].map(_case_size)
    summary["scenario_label"] = summary["scenario_folder"].map(lambda value: SCENARIO_FOLDER_SPECS.get(str(value), {}).get("label", value))
    summary["pf_control_label"] = summary["parameter_scenario"].map(lambda value: PF_CONTROL_LABELS.get(str(value), value))
    summary["valid_converged"] = False
    valid = _valid_converged_rows(summary)
    summary.loc[valid.index, "valid_converged"] = True

    summary.to_csv(output_dir / "presentable_summary_rows.csv", index=False)

    valid_for_speedup = valid.copy()
    speedup_rows: list[dict[str, Any]] = []
    key_columns = ["scenario_folder", "base_case_name", "parameter_scenario"]
    originals = valid_for_speedup[valid_for_speedup["variant_name"].eq("original")].set_index(key_columns)
    for _, row in valid_for_speedup[~valid_for_speedup["variant_name"].eq("original")].iterrows():
        key = tuple(row[column] for column in key_columns)
        if key not in originals.index:
            continue
        original = originals.loc[key]
        if isinstance(original, pd.DataFrame):
            original = original.iloc[0]
        original_time = float(original["elapsed_seconds_median"])
        variant_time = float(row["elapsed_seconds_median"])
        if original_time <= 0 or variant_time <= 0:
            continue
        speedup_rows.append(
            {
                "scenario_folder": row["scenario_folder"],
                "scenario_label": row["scenario_label"],
                "base_case_name": row["base_case_name"],
                "bus_count": row["bus_count"],
                "parameter_scenario": row["parameter_scenario"],
                "pf_control_label": row["pf_control_label"],
                "variant_name": row["variant_name"],
                "variant_label": row["variant_label"],
                "original_elapsed_seconds_median": original_time,
                "variant_elapsed_seconds_median": variant_time,
                "speedup_vs_original": original_time / variant_time,
            }
        )
    speedups = pd.DataFrame(speedup_rows)
    speedups.to_csv(output_dir / "speedup_vs_original.csv", index=False)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception as exc:
        (output_dir / "figure_generation_skipped.txt").write_text(f"matplotlib unavailable: {exc!r}\n", encoding="utf-8")
        return

    plotted = summary[~summary["parameter_scenario"].eq("strict_single_slack")].copy()
    plotted = plotted[plotted["variant_name"].isin(VARIANT_LABELS)]
    large = plotted[plotted["bus_count"].fillna(0) >= 300].copy()
    if not large.empty:
        large["row_label"] = (
            large["scenario_label"].astype(str)
            + " "
            + large["base_case_name"].astype(str).str.replace("pglib_opf_", "", regex=False)
            + " | "
            + large["pf_control_label"].astype(str)
        )
        row_labels = list(dict.fromkeys(large["row_label"].tolist()))
        col_labels = [VARIANT_LABELS[name] for name in VARIANT_LABELS]
        matrix = []
        for row_label in row_labels:
            group = large[large["row_label"].eq(row_label)]
            matrix.append(
                [
                    1 if bool(group[group["variant_name"].eq(variant)]["valid_converged"].any()) else 0
                    for variant in VARIANT_LABELS
                ]
            )
        fig_height = max(5.0, 0.28 * len(row_labels))
        fig, ax = plt.subplots(figsize=(8.5, fig_height))
        ax.imshow(np.array(matrix), aspect="auto", cmap="RdYlGn", vmin=0, vmax=1)
        ax.set_xticks(range(len(col_labels)), labels=col_labels, rotation=20, ha="right")
        ax.set_yticks(range(len(row_labels)), labels=row_labels, fontsize=8)
        for y, values in enumerate(matrix):
            for x, value in enumerate(values):
                ax.text(x, y, "Y" if value else "N", ha="center", va="center", fontsize=8)
        ax.set_title("Large/problematic OpenLoadFlow AC convergence")
        fig.tight_layout()
        fig.savefig(figure_dir / "large_ac_convergence_heatmap.png", dpi=160, bbox_inches="tight")
        plt.close(fig)

    valid_plot = valid[~valid["parameter_scenario"].eq("strict_single_slack")].copy()
    valid_plot["bus_count"] = valid_plot["base_case_name"].map(_case_size)
    valid_plot = valid_plot[valid_plot["bus_count"].notna()]
    if not valid_plot.empty:
        fig, ax = plt.subplots(figsize=(8.5, 5.0))
        for variant_name, group in valid_plot.groupby("variant_name"):
            ax.scatter(group["bus_count"], group["elapsed_seconds_median"], label=VARIANT_LABELS.get(variant_name, variant_name), alpha=0.8)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("Number of buses")
        ax.set_ylabel("Median AC solve time (s)")
        ax.set_title("Valid converged OpenLoadFlow solve times")
        ax.legend(frameon=False)
        fig.tight_layout()
        fig.savefig(figure_dir / "valid_ac_solve_time_vs_buses.png", dpi=160, bbox_inches="tight")
        plt.close(fig)

    if not speedups.empty:
        fig, ax = plt.subplots(figsize=(8.5, 5.0))
        for variant_name, group in speedups.groupby("variant_name"):
            ax.scatter(group["bus_count"], group["speedup_vs_original"], label=VARIANT_LABELS.get(variant_name, variant_name), alpha=0.85)
        ax.axhline(1.0, color="0.4", linewidth=1.0)
        ax.set_xscale("log")
        ax.set_xlabel("Number of buses")
        ax.set_ylabel("Median speedup vs Original")
        ax.set_title("Replacement-note speedup for valid converged runs")
        ax.legend(frameon=False)
        fig.tight_layout()
        fig.savefig(figure_dir / "speedup_vs_original.png", dpi=160, bbox_inches="tight")
        plt.close(fig)


def run_presentable_sensitivity_sweep(
    *,
    mat_root: str | Path = DEFAULT_MAT_DIR,
    m_root: str | Path = DEFAULT_M_DIR,
    output_dir: str | Path = DEFAULT_RESULT_ROOT,
    scenario_folders: Iterable[str] | None = None,
    case_names: Iterable[str] | None = None,
    variant_names: Iterable[str] | None = None,
    pf_controls: Iterable[str] | None = None,
    inventory_only: bool = False,
    mapping_only: bool = False,
    timing_repetitions: int = 3,
    strict: bool = False,
    resume: bool = False,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    output_dir = Path(output_dir)
    inventory_csv = output_dir / "pypowsybl_scenario_inventory.csv"
    mapping_csv = output_dir / "pypowsybl_replacement_mapping_coverage.csv"
    summary_csv = output_dir / "pypowsybl_openloadflow_sensitivity_rows.csv"
    audit_csv = output_dir / "pypowsybl_replacement_mapping_audit.csv"

    inventory, selected_cases = build_presentable_scenario_inventory(
        mat_root=mat_root,
        m_root=m_root,
        output_csv=inventory_csv,
    )
    if scenario_folders is not None:
        wanted = set(scenario_folders)
        selected_cases = [case for case in selected_cases if case.scenario_folder in wanted]
    if case_names is not None:
        wanted_cases = {_base_case_name(Path(case).stem) for case in case_names}
        selected_cases = [case for case in selected_cases if case.base_case_name in wanted_cases or case.case_name in wanted_cases]

    if inventory_only:
        return inventory, pd.DataFrame(), pd.DataFrame()

    require_pypowsybl()

    mapping_coverage, _ = verify_presentable_mapping_coverage(
        selected_cases,
        coverage_csv=mapping_csv,
        audit_csv=None,
        verbose=verbose,
    )
    if mapping_only:
        return inventory, mapping_coverage, pd.DataFrame()

    variants = make_replacement_variants(include_bus_voltage=True)
    scenarios = make_parameter_scenarios(include_operational=True)
    if variant_names is not None:
        wanted_variants = set(variant_names)
        variants = {name: variant for name, variant in variants.items() if name in wanted_variants}
    if pf_controls is not None:
        wanted_controls = set(pf_controls)
        scenarios = {name: scenario for name, scenario in scenarios.items() if name in wanted_controls}
    if not variants:
        raise ValueError("No replacement variants selected.")
    if not scenarios:
        raise ValueError("No PF-control scenarios selected.")
    existing_summary = pd.read_csv(summary_csv) if resume and summary_csv.exists() else pd.DataFrame()
    existing_audit = pd.read_csv(audit_csv) if resume and audit_csv.exists() else pd.DataFrame()
    completed_keys = _completed_case_keys(existing_summary, variants, scenarios)
    summary_frames: list[pd.DataFrame] = [existing_summary] if not existing_summary.empty else []
    audit_frames: list[pd.DataFrame] = [existing_audit] if not existing_audit.empty else []

    for case in selected_cases:
        if resume and (case.scenario_folder, case.case_name) in completed_keys:
            if verbose:
                print(f"Skipping completed {case.scenario_folder}/{case.case_name}")
            continue
        if verbose:
            print(f"\n### Running {case.scenario_folder}/{case.case_name}")
        summary, audit = run_case_experiment_matrix(
            case.case_name,
            replacement_variants=variants,
            parameter_scenarios=scenarios,
            mat_dir=case.mat_path.parent,
            m_dir=case.m_path.parent,
            summary_csv=None,
            audit_csv=None,
            include_bus_voltage=True,
            strict=strict,
            timing_repetitions=timing_repetitions,
            verbose=verbose,
        )
        summary.insert(0, "scenario_folder", case.scenario_folder)
        summary.insert(1, "scenario_label", SCENARIO_FOLDER_SPECS[case.scenario_folder]["label"])
        summary["base_case_name"] = case.base_case_name
        audit.insert(0, "scenario_folder", case.scenario_folder)
        audit.insert(1, "base_case_name", case.base_case_name)
        summary_frames.append(summary)
        audit_frames.append(audit)
        all_summary = _dedupe_result_rows(pd.concat(summary_frames, ignore_index=True)) if summary_frames else pd.DataFrame()
        all_audit = pd.concat(audit_frames, ignore_index=True) if audit_frames else _empty_audit()
        output_dir.mkdir(parents=True, exist_ok=True)
        all_summary.to_csv(summary_csv, index=False)
        all_audit.to_csv(audit_csv, index=False)

    all_summary = _dedupe_result_rows(pd.concat(summary_frames, ignore_index=True)) if summary_frames else pd.DataFrame()
    all_audit = pd.concat(audit_frames, ignore_index=True) if audit_frames else _empty_audit()
    output_dir.mkdir(parents=True, exist_ok=True)
    all_summary.to_csv(summary_csv, index=False)
    all_audit.to_csv(audit_csv, index=False)
    create_presentable_artifacts(all_summary, inventory, mapping_coverage, output_dir=output_dir)
    return inventory, mapping_coverage, all_summary


def notebook_progress(event: dict[str, Any]) -> None:
    if event["event"] == "start":
        print(f"Starting {event['case']} | {event['variant']} | {event['scenario']}")
    elif event["event"] == "finish":
        row = event["row"]
        print(
            f"Finished {event['variant']} | {event['scenario']}: "
            f"status={row['status']} iterations={row['iteration_count']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PyPowSyBl replacement-note experiment matrix.")
    parser.add_argument("--case-name", default=None)
    parser.add_argument("--presentable", action="store_true", help="Run the presentable base/API/SAD sensitivity sweep.")
    parser.add_argument("--scenario", action="append", choices=sorted(SCENARIO_FOLDER_SPECS), help="Scenario folder to include in --presentable; may be repeated.")
    parser.add_argument("--variant", action="append", choices=sorted(VARIANT_LABELS), help="Replacement variant to include in --presentable; may be repeated.")
    parser.add_argument("--pf-control", action="append", choices=sorted(PF_CONTROL_LABELS), help="PF-control scenario to include in --presentable; may be repeated.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--inventory-only", action="store_true", help="Only write presentable scenario inventory.")
    parser.add_argument("--mapping-only", action="store_true", help="Only write presentable inventory and mapping coverage.")
    parser.add_argument("--timing-repetitions", type=int, default=3)
    parser.add_argument("--resume", action="store_true", help="Skip selected case blocks already present in the summary CSV.")
    parser.add_argument("--include-bus-voltage", action="store_true")
    parser.add_argument("--summary-csv", type=Path, default=DEFAULT_SUMMARY_CSV)
    parser.add_argument("--audit-csv", type=Path, default=DEFAULT_AUDIT_CSV)
    parser.add_argument("--inventory", action="store_true", help="Only scan .m note blocks; do not run load flow.")
    parser.add_argument("--inventory-csv", type=Path, default=DEFAULT_INVENTORY_CSV)
    parser.add_argument("--inventory-pattern", default="pglib_opf_case*.m")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.presentable:
        inventory, mapping, summary = run_presentable_sensitivity_sweep(
            output_dir=args.output_dir,
            scenario_folders=args.scenario,
            case_names=None if args.case_name in {None, "", "*"} else [args.case_name],
            variant_names=args.variant,
            pf_controls=args.pf_control,
            inventory_only=args.inventory_only,
            mapping_only=args.mapping_only,
            timing_repetitions=args.timing_repetitions,
            resume=args.resume,
            verbose=not args.quiet,
        )
        print(f"Wrote inventory rows={len(inventory)} to {args.output_dir / 'pypowsybl_scenario_inventory.csv'}")
        if not mapping.empty:
            print(f"Wrote mapping rows={len(mapping)} to {args.output_dir / 'pypowsybl_replacement_mapping_coverage.csv'}")
        if not summary.empty:
            print(f"Wrote summary rows={len(summary)} to {args.output_dir / 'pypowsybl_openloadflow_sensitivity_rows.csv'}")
            print(f"Wrote artifacts under {args.output_dir}")
        return

    if args.inventory:
        case_name = args.case_name or "pglib_opf_case300_ieee"
        target = DEFAULT_M_DIR / f"{Path(case_name).stem}.m" if case_name else DEFAULT_M_DIR
        if case_name == "*":
            target = DEFAULT_M_DIR
        inventory = scan_replacement_note_inventory(
            target,
            pattern=args.inventory_pattern,
            output_csv=args.inventory_csv,
        )
        print(inventory[["case_name", "section", "n_note_lines", "n_currently_parsed_lines", "n_known_future_lines", "n_unclassified_lines"]].to_string(index=False))
        print(f"Wrote inventory rows={len(inventory)} to {args.inventory_csv}")
        return

    summary, audit = run_case_experiment_matrix(
        args.case_name or "pglib_opf_case300_ieee",
        include_bus_voltage=args.include_bus_voltage,
        summary_csv=args.summary_csv,
        audit_csv=args.audit_csv,
        verbose=not args.quiet,
    )
    print(summary[["case_name", "variant_name", "parameter_scenario", "status", "iteration_count", "converged"]].to_string(index=False))
    print(f"Wrote summary rows={len(summary)}, audit rows={len(audit)}")


if __name__ == "__main__":
    main()
