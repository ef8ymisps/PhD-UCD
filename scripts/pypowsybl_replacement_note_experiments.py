"""PyPowSyBl/OpenLoadFlow experiments from PGLib replacement-note comments.

This module is designed for notebooks.  It loads a PGLib MATPOWER ``.mat`` file
with PyPowSyBl, parses replacement-note comments from the matching ``.m`` file,
applies selected generator targets, and runs OpenLoadFlow AC load flow variants.

Bus-voltage replacement notes can be applied as an initial state when the
installed PyPowSyBl exposes ``VoltageInitMode.PREVIOUS_VALUES``.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAT_DIR = PROJECT_ROOT / "data" / "raw" / "pglib" / "matpower_mat"
DEFAULT_M_DIR = PROJECT_ROOT / "data" / "raw" / "pglib" / "matpower_m"


SECTION_RE = re.compile(r"^%\s+INFO\s+:\s+===\s+(.+?)\s+===")
BUS_VOLTAGE_RE = re.compile(
    r"Bus\s+(?P<bus>\d+)\s*:\s*"
    r"V=(?P<V_from>[-+.\deE]+),\s*theta=(?P<theta_from>[-+.\deE]+)\s*->\s*"
    r"V=(?P<V_to>[-+.\deE]+),\s*theta=(?P<theta_to>[-+.\deE]+)"
)
LOAD_RE = re.compile(
    r"Bus\s+(?P<bus>\d+)\s*:\s*"
    r"Pd=(?P<Pd_from>[-+.\deE]+),\s*Qd=(?P<Qd_from>[-+.\deE]+)\s*->\s*"
    r"Pd=(?P<Pd_to>[-+.\deE]+),\s*Qd=(?P<Qd_to>[-+.\deE]+)"
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


@dataclass(frozen=True)
class ReplacementNotes:
    load: pd.DataFrame
    bus_voltage: pd.DataFrame
    gen_pgqg: pd.DataFrame
    gen_vg: pd.DataFrame


def require_pypowsybl():
    try:
        import pypowsybl as pp
    except ImportError as exc:
        raise ImportError(
            "pypowsybl is not installed in this Python environment. Activate the "
            "project environment from python/environment.yml or install "
            "python/requirements.txt."
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


def _float(value: str) -> float:
    return float(value)


def _records_to_frame(records: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(records, columns=columns)


def parse_replacement_notes(m_path: str | Path) -> ReplacementNotes:
    """Parse block-aware PGLib replacement notes from a MATPOWER ``.m`` file."""
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
                        "Pd_from": _float(groups["Pd_from"]),
                        "Qd_from": _float(groups["Qd_from"]),
                        "Pd_to": _float(groups["Pd_to"]),
                        "Qd_to": _float(groups["Qd_to"]),
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
                        "V_from": _float(groups["V_from"]),
                        "theta_from": _float(groups["theta_from"]),
                        "V_to": _float(groups["V_to"]),
                        "theta_to": _float(groups["theta_to"]),
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
                        "Pg_from": _float(groups["Pg_from"]),
                        "Qg_from": _float(groups["Qg_from"]),
                        "Pg_to": _float(groups["Pg_to"]),
                        "Qg_to": _float(groups["Qg_to"]),
                    }
                )
            elif match := GEN_VG_RE.search(line):
                groups = match.groupdict()
                gen_vg.append(
                    {
                        "line_no": line_no,
                        "section_id": section_id,
                        "bus": int(groups["bus"]),
                        "Vg_from": _float(groups["Vg_from"]),
                        "Vg_to": _float(groups["Vg_to"]),
                    }
                )

    return ReplacementNotes(
        load=_records_to_frame(
            load,
            ["line_no", "section_id", "bus", "Pd_from", "Qd_from", "Pd_to", "Qd_to"],
        ),
        bus_voltage=_records_to_frame(
            bus_voltage,
            ["line_no", "section_id", "bus", "V_from", "theta_from", "V_to", "theta_to"],
        ),
        gen_pgqg=_records_to_frame(
            gen_pgqg,
            ["line_no", "section_id", "bus", "Pg_from", "Qg_from", "Pg_to", "Qg_to"],
        ),
        gen_vg=_records_to_frame(
            gen_vg,
            ["line_no", "section_id", "bus", "Vg_from", "Vg_to"],
        ),
    )


def load_case_network(mat_path: str | Path):
    """Load a MATPOWER/PGLib ``.mat`` network with PyPowSyBl."""
    pp = require_pypowsybl()
    return pp.network.load(str(Path(mat_path)))


def _direction_column(base: str, direction: str) -> str:
    if direction not in {"from", "to"}:
        raise ValueError("direction must be 'from' or 'to'")
    return f"{base}_{direction}"


def _numeric_suffix(value: Any) -> str | None:
    match = re.search(r"(\d+)(?:\.0)?(?:$|[_\-\s])", str(value))
    return match.group(1) if match else None


def _bus_number_from_row(row: pd.Series) -> int | None:
    for column in ("bus_id", "bus_breaker_bus_id", "regulated_bus_id", "regulated_bus_breaker_bus_id"):
        if column in row and pd.notna(row[column]):
            suffix = _numeric_suffix(row[column])
            if suffix is not None:
                return int(suffix)
    for column in ("voltage_level_id", "regulated_element_id"):
        if column in row and pd.notna(row[column]):
            suffix = _numeric_suffix(row[column])
            if suffix is not None:
                return int(suffix)
    return None


def _generator_table_with_bus_numbers(network: Any) -> pd.DataFrame:
    generators = get_table(network, "get_generators")
    if generators.empty:
        generators["matpower_bus"] = pd.Series(dtype="Int64")
        generators["matpower_gen"] = pd.Series(dtype="Int64")
        return generators
    result = generators.copy()
    result["matpower_bus"] = [
        _bus_number_from_row(row) for _, row in result.iterrows()
    ]
    result["matpower_gen"] = [
        int(match.group(1)) if (match := re.match(r"GEN-(\d+)$", str(row.get("id", "")))) else None
        for _, row in result.iterrows()
    ]
    return result


def _load_table_with_bus_numbers(network: Any) -> pd.DataFrame:
    loads = get_table(network, "get_loads")
    if loads.empty:
        loads["matpower_bus"] = pd.Series(dtype="Int64")
        return loads
    result = loads.copy()
    result["matpower_bus"] = [
        _bus_number_from_row(row) for _, row in result.iterrows()
    ]
    return result


def _bus_table_with_bus_numbers(network: Any) -> pd.DataFrame:
    buses = get_table(network, "get_buses")
    if buses.empty:
        buses["matpower_bus"] = pd.Series(dtype="Int64")
        return buses
    result = buses.copy()
    result["matpower_bus"] = [
        _bus_number_from_row(row) for _, row in result.iterrows()
    ]
    return result


def _remember_bus_mapping(
    mapping: dict[int, str],
    conflicts: list[dict[str, Any]],
    matpower_bus: int,
    pypowsybl_bus_id: Any,
    source: str,
) -> None:
    if pd.isna(pypowsybl_bus_id):
        return
    bus_id = str(pypowsybl_bus_id)
    existing = mapping.get(matpower_bus)
    if existing is None:
        mapping[matpower_bus] = bus_id
    elif existing != bus_id:
        conflicts.append(
            {
                "matpower_bus": matpower_bus,
                "existing_bus_id": existing,
                "new_bus_id": bus_id,
                "source": source,
            }
        )


def _matpower_bus_to_pypowsybl_bus_map(network: Any) -> tuple[dict[int, str], pd.DataFrame]:
    mapping: dict[int, str] = {}
    conflicts: list[dict[str, Any]] = []

    for table_name, table, id_column, bus_column in (
        ("load", get_table(network, "get_loads"), "id", "bus_id"),
        ("generator", get_table(network, "get_generators"), "id", "bus_id"),
    ):
        if table.empty or id_column not in table or bus_column not in table:
            continue
        prefix = "LOAD" if table_name == "load" else "GEN"
        for _, row in table.iterrows():
            match = re.match(rf"{prefix}-(\d+)$", str(row[id_column]))
            if match:
                _remember_bus_mapping(
                    mapping,
                    conflicts,
                    int(match.group(1)),
                    row[bus_column],
                    f"{table_name}:{row[id_column]}",
                )

    for table_name, table in (
        ("line", get_table(network, "get_lines")),
        ("two_windings_transformer", get_table(network, "get_2_windings_transformers")),
    ):
        required = {"id", "bus1_id", "bus2_id"}
        if table.empty or not required.issubset(table.columns):
            continue
        for _, row in table.iterrows():
            numbers = re.findall(r"\d+", str(row["id"]).split("#", 1)[0])
            if len(numbers) < 2:
                continue
            _remember_bus_mapping(
                mapping,
                conflicts,
                int(numbers[0]),
                row["bus1_id"],
                f"{table_name}:{row['id']}:bus1",
            )
            _remember_bus_mapping(
                mapping,
                conflicts,
                int(numbers[1]),
                row["bus2_id"],
                f"{table_name}:{row['id']}:bus2",
            )

    return mapping, pd.DataFrame(conflicts)


def _voltage_level_nominal_by_id(network: Any) -> dict[str, float]:
    voltage_levels = get_table(network, "get_voltage_levels")
    if voltage_levels.empty:
        return {}
    nominal_column = next(
        (column for column in ("nominal_v", "nominal_voltage", "nominal_v_kv") if column in voltage_levels),
        None,
    )
    if nominal_column is None or "id" not in voltage_levels:
        return {}
    return {
        str(row["id"]): float(row[nominal_column])
        for _, row in voltage_levels.iterrows()
        if pd.notna(row[nominal_column])
    }


def _nominal_voltage_for_generator(row: pd.Series, nominal_by_vl: dict[str, float]) -> float | None:
    if "voltage_level_id" in row and pd.notna(row["voltage_level_id"]):
        nominal = nominal_by_vl.get(str(row["voltage_level_id"]))
        if nominal is not None:
            return nominal
    if "target_v" in row and pd.notna(row["target_v"]) and float(row["target_v"]) > 5.0:
        return float(row["target_v"])
    return None


def _nominal_voltage_for_bus(row: pd.Series, nominal_by_vl: dict[str, float]) -> float | None:
    if "voltage_level_id" in row and pd.notna(row["voltage_level_id"]):
        nominal = nominal_by_vl.get(str(row["voltage_level_id"]))
        if nominal is not None:
            return nominal
    if "v_mag" in row and pd.notna(row["v_mag"]) and float(row["v_mag"]) > 5.0:
        return float(row["v_mag"])
    return None


def _target_generation_mw(network: Any) -> float:
    generators = get_table(network, "get_generators")
    if generators.empty or "target_p" not in generators:
        return float("nan")
    return float(pd.to_numeric(generators["target_p"], errors="coerce").fillna(0.0).sum())


def _total_load_mw(network: Any) -> float:
    loads = get_table(network, "get_loads")
    if loads.empty:
        return float("nan")
    column = "p0" if "p0" in loads else "p"
    if column not in loads:
        return float("nan")
    return float(pd.to_numeric(loads[column], errors="coerce").fillna(0.0).sum())


def _network_balance(network: Any) -> dict[str, float]:
    generation = _target_generation_mw(network)
    load = _total_load_mw(network)
    return {
        "total_target_generation": generation,
        "total_load": load,
        "generation_load_balance": generation - load,
    }


def _notes_by_bus(notes: pd.DataFrame, bus: int) -> pd.DataFrame:
    if notes.empty or "bus" not in notes:
        return pd.DataFrame(columns=notes.columns)
    return notes[notes["bus"] == bus].reset_index(drop=True)


def _update_generators(network: Any, updates: pd.DataFrame) -> None:
    if updates.empty:
        return
    update_columns = [column for column in ("id", "target_p", "target_q", "target_v") if column in updates]
    payload = updates[update_columns].copy()
    payload["id"] = payload["id"].astype(str)
    payload = payload.groupby("id", as_index=False).last()
    update_columns = [column for column in ("id", "target_p", "target_q", "target_v") if column in payload]
    kwargs = {column: payload[column].tolist() for column in update_columns}
    network.update_generators(**kwargs)


def _update_loads(network: Any, updates: pd.DataFrame) -> None:
    if updates.empty:
        return
    update_columns = [column for column in ("id", "p0", "q0") if column in updates]
    payload = updates[update_columns].copy()
    payload["id"] = payload["id"].astype(str)
    kwargs = {column: payload[column].tolist() for column in update_columns}
    network.update_loads(**kwargs)


def _update_buses(network: Any, updates: pd.DataFrame) -> None:
    if updates.empty:
        return
    update_columns = [column for column in ("id", "v_mag", "v_angle") if column in updates]
    payload = updates[update_columns].copy()
    payload["id"] = payload["id"].astype(str)
    kwargs = {column: payload[column].tolist() for column in update_columns}
    network.update_buses(**kwargs)


def apply_replacement_notes_to_network(
    network: Any,
    notes: ReplacementNotes,
    *,
    direction: str = "from",
    groups: Iterable[str] = ("gen_pgqg", "gen_vg"),
    strict: bool = False,
    verbose: bool = True,
) -> dict[str, Any]:
    """Apply selected replacement-note groups to a PyPowSyBl network.

    Supported applied groups are ``load``, ``gen_pgqg``, ``gen_pg``,
    ``gen_qg``, ``gen_vg`` and ``bus_voltage``.
    """
    groups = set(groups)
    before = _network_balance(network)
    buses = _bus_table_with_bus_numbers(network)
    bus_id_by_matpower_bus, bus_mapping_conflicts = _matpower_bus_to_pypowsybl_bus_map(network)
    loads = _load_table_with_bus_numbers(network)
    generators = _generator_table_with_bus_numbers(network)
    nominal_by_vl = _voltage_level_nominal_by_id(network)
    bus_updates: list[dict[str, Any]] = []
    load_updates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    if "bus_voltage" in groups:
        buses_by_id = buses.set_index("id", drop=False) if "id" in buses else pd.DataFrame()
        for _, note in notes.bus_voltage.iterrows():
            bus = int(note["bus"])
            bus_id = bus_id_by_matpower_bus.get(bus)
            if bus_id is None:
                continue
            if bus_id not in buses_by_id.index:
                unmatched.append(
                    {
                        "group": "bus_voltage",
                        "bus": bus,
                        "reason": f"mapped PyPowSyBl bus {bus_id} not in get_buses()",
                    }
                )
                continue
            bus_row = buses_by_id.loc[bus_id]
            nominal_v = _nominal_voltage_for_bus(bus_row, nominal_by_vl)
            if nominal_v is None:
                message = f"Bus {bus}: cannot convert voltage pu to kV"
                diagnostics.append({"level": "warning", "message": message})
                if strict:
                    raise ValueError(message)
                continue
            bus_updates.append(
                {
                    "id": bus_id,
                    "v_mag": float(note[_direction_column("V", direction)]) * nominal_v,
                    "v_angle": float(note[_direction_column("theta", direction)]),
                }
            )

    if "load" in groups:
        for bus, bus_loads in loads.groupby("matpower_bus", dropna=True):
            bus = int(bus)
            bus_loads = bus_loads.reset_index(drop=True)
            load_notes = _notes_by_bus(notes.load, bus)
            if not load_notes.empty and len(load_notes) != len(bus_loads):
                message = (
                    f"Bus {bus}: {len(load_notes)} load notes for "
                    f"{len(bus_loads)} PyPowSyBl loads"
                )
                diagnostics.append({"level": "warning", "message": message})
                if strict:
                    raise ValueError(message)
            for local_index in range(min(len(load_notes), len(bus_loads))):
                load_row = bus_loads.iloc[local_index]
                note = load_notes.iloc[local_index]
                load_updates.append(
                    {
                        "id": load_row["id"],
                        "p0": float(note[_direction_column("Pd", direction)]),
                        "q0": float(note[_direction_column("Qd", direction)]),
                    }
                )

    consumed_generators: set[int] = set()
    fallback_cursor_by_bus: dict[int, int] = {}
    last_generator_for_note_bus: dict[int, pd.Series] = {}

    def match_generator(note_bus: int) -> pd.Series | None:
        exact = generators[
            (generators["matpower_gen"] == note_bus)
            & (~generators.index.isin(consumed_generators))
        ]
        if not exact.empty:
            row = exact.iloc[0]
            consumed_generators.add(int(row.name))
            return row

        candidates = generators[generators["matpower_bus"] == note_bus]
        cursor = fallback_cursor_by_bus.get(note_bus, 0)
        while cursor < len(candidates):
            row = candidates.iloc[cursor]
            cursor += 1
            if int(row.name) not in consumed_generators:
                fallback_cursor_by_bus[note_bus] = cursor
                consumed_generators.add(int(row.name))
                return row
        fallback_cursor_by_bus[note_bus] = cursor
        return None

    gen_note_rows: list[dict[str, Any]] = []
    if "gen_pgqg" in groups or "gen_pg" in groups or "gen_qg" in groups:
        for _, note in notes.gen_pgqg.iterrows():
            gen_note_rows.append({"kind": "gen_pgqg", "note": note})
    if "gen_vg" in groups:
        for _, note in notes.gen_vg.iterrows():
            gen_note_rows.append({"kind": "gen_vg", "note": note})
    gen_note_rows.sort(key=lambda item: int(item["note"]["line_no"]))

    for note_item in gen_note_rows:
        note = note_item["note"]
        kind = note_item["kind"]
        note_bus = int(note["bus"])

        if kind == "gen_vg" and note_bus in last_generator_for_note_bus:
            gen_row = last_generator_for_note_bus[note_bus]
        else:
            gen_row = match_generator(note_bus)

        if gen_row is None:
            unmatched.append(
                {
                    "group": kind,
                    "bus": note_bus,
                    "reason": "no PyPowSyBl generator mapped",
                }
            )
            continue

        update: dict[str, Any] = {"id": gen_row["id"]}
        if kind == "gen_pgqg":
            if "gen_pgqg" in groups or "gen_pg" in groups:
                update["target_p"] = float(note[_direction_column("Pg", direction)])
            if "gen_pgqg" in groups or "gen_qg" in groups:
                update["target_q"] = float(note[_direction_column("Qg", direction)])
            last_generator_for_note_bus[note_bus] = gen_row
        elif kind == "gen_vg":
            nominal_v = _nominal_voltage_for_generator(gen_row, nominal_by_vl)
            if nominal_v is None:
                message = f"Bus {note_bus}, generator {gen_row['id']}: cannot convert Vg pu to kV"
                diagnostics.append({"level": "warning", "message": message})
                if strict:
                    raise ValueError(message)
                continue
            update["target_v"] = float(note[_direction_column("Vg", direction)]) * nominal_v

        updates.append(update)

    if "bus_voltage" in groups and not notes.bus_voltage.empty:
        for bus in sorted(set(notes.bus_voltage["bus"].astype(int)) - set(bus_id_by_matpower_bus)):
            unmatched.append({"group": "bus_voltage", "bus": bus, "reason": "no PyPowSyBl bus mapped"})
    if not bus_mapping_conflicts.empty:
        diagnostics.append(
            {
                "level": "warning",
                "message": f"{len(bus_mapping_conflicts)} conflicting MATPOWER bus mappings found",
            }
        )

    load_buses = set(
        int(bus) for bus in loads["matpower_bus"].dropna().astype(int).tolist()
    ) if "matpower_bus" in loads else set()
    if "load" in groups and not notes.load.empty:
        for bus in sorted(set(notes.load["bus"].astype(int)) - load_buses):
            unmatched.append({"group": "load", "bus": bus, "reason": "no PyPowSyBl load mapped"})

    update_frame = pd.DataFrame(updates)
    bus_update_frame = pd.DataFrame(bus_updates)
    load_update_frame = pd.DataFrame(load_updates)
    _update_buses(network, bus_update_frame)
    _update_loads(network, load_update_frame)
    _update_generators(network, update_frame)
    after = _network_balance(network)

    if verbose:
        print(
            "Generator/load balance before: "
            f"gen={before['total_target_generation']:.6g} MW, "
            f"load={before['total_load']:.6g} MW, "
            f"balance={before['generation_load_balance']:.6g} MW"
        )
        print(
            "Generator/load balance after:  "
            f"gen={after['total_target_generation']:.6g} MW, "
            f"load={after['total_load']:.6g} MW, "
            f"balance={after['generation_load_balance']:.6g} MW"
        )
        if unmatched:
            print("Unmatched notes:")
            print(pd.DataFrame(unmatched).to_string(index=False))
        if diagnostics:
            print("Diagnostics:")
            print(pd.DataFrame(diagnostics).to_string(index=False))
    return {
        "before": before,
        "after": after,
        "bus_updates": bus_update_frame,
        "load_updates": load_update_frame,
        "updates": update_frame,
        "applied_bus_voltage": int(len(bus_update_frame)),
        "applied_load": int(len(load_update_frame)),
        "applied_gen_pg": int(update_frame[["target_p"]].dropna(how="all").shape[0])
        if not update_frame.empty and "target_p" in update_frame
        else 0,
        "applied_gen_qg": int(update_frame[["target_q"]].dropna(how="all").shape[0])
        if not update_frame.empty and "target_q" in update_frame
        else 0,
        "applied_gen_pgqg": int(
            update_frame[
                [column for column in ("target_p", "target_q") if column in update_frame]
            ].dropna(how="all").shape[0]
        )
        if not update_frame.empty and {"target_p", "target_q"}.intersection(update_frame.columns)
        else 0,
        "applied_gen_vg": int(update_frame[["target_v"]].dropna(how="all").shape[0])
        if not update_frame.empty and "target_v" in update_frame
        else 0,
        "unmatched": pd.DataFrame(unmatched),
        "diagnostics": pd.DataFrame(diagnostics),
    }


def openloadflow_parameters(voltage_init_mode: Any = None, **overrides: Any):
    pp = require_pypowsybl()
    lf = pp.loadflow
    if voltage_init_mode is None:
        voltage_init_mode = lf.VoltageInitMode.DC_VALUES
    kwargs = {
        "voltage_init_mode": voltage_init_mode,
        "distributed_slack": True,
        "balance_type": lf.BalanceType.PROPORTIONAL_TO_GENERATION_P_MAX,
        "use_reactive_limits": True,
        "transformer_voltage_control_on": False,
        "shunt_compensator_voltage_control_on": False,
        "phase_shifter_regulation_on": False,
        "provider_parameters": {
            "acSolverType": "NEWTON_RAPHSON",
            "maxNewtonRaphsonIterations": "800",
            "maxOuterLoopIterations": "800",
        },
    }
    kwargs.update(overrides)
    return lf.Parameters(**kwargs)


def voltage_init_capabilities() -> dict[str, Any]:
    pp = require_pypowsybl()
    values = [name for name in dir(pp.loadflow.VoltageInitMode) if name.isupper()]
    previous_like = [name for name in values if "PREVIOUS" in name or "WARM" in name]
    return {
        "available": values,
        "previous_or_warm_values": previous_like,
        "supports_previous_values": bool(previous_like),
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


def _first_component_summary(results: Any) -> dict[str, Any]:
    frame = _component_results_frame(results)
    if frame.empty:
        return {}
    row = frame.iloc[0].to_dict()
    return {
        "status": str(row.get("status", "UNKNOWN")),
        "status_text": row.get("status_text"),
        "iteration_count": row.get("iteration_count"),
        "reference_bus_id": row.get("reference_bus_id"),
        "slack_bus_id": row.get("slack_bus_id"),
        "slack_active_power_mismatch": row.get(
            "slack_active_power_mismatch",
            row.get("slack_bus_active_power_mismatch"),
        ),
        "distributed_active_power": row.get("distributed_active_power"),
    }


def run_openloadflow_variant(
    network: Any,
    params: Any | None = None,
    *,
    provider: str = "OpenLoadFlow",
) -> dict[str, Any]:
    """Run one OpenLoadFlow AC load flow and return result diagnostics."""
    pp = require_pypowsybl()
    if params is None:
        params = openloadflow_parameters()
    started = time.perf_counter()
    results = pp.loadflow.run_ac(network, parameters=params, provider=provider)
    elapsed = time.perf_counter() - started
    summary = _first_component_summary(results)
    summary["elapsed_seconds"] = elapsed
    summary["raw_results"] = results
    summary["component_results"] = _component_results_frame(results)
    return summary


def _case_paths(case_name: str, mat_dir: Path, m_dir: Path) -> tuple[Path, Path]:
    stem = Path(case_name).stem
    mat_path = mat_dir / f"{stem}.mat"
    m_path = m_dir / f"{stem}.m"
    return mat_path, m_path


def _variant_plan(
    include_all_from: bool = True,
    include_bus_voltage_variants: bool = False,
) -> list[dict[str, Any]]:
    variants = [
        {"variant": "original", "direction": None, "groups": ()},
        {"variant": "load_from", "direction": "from", "groups": ("load",)},
        {"variant": "load_to", "direction": "to", "groups": ("load",)},
        {"variant": "gen_pg_from", "direction": "from", "groups": ("gen_pg",)},
        {"variant": "gen_qg_from", "direction": "from", "groups": ("gen_qg",)},
        {"variant": "gen_pgqg_from", "direction": "from", "groups": ("gen_pgqg",)},
        {"variant": "gen_pgqg_to", "direction": "to", "groups": ("gen_pgqg",)},
        {
            "variant": "gen_pgqg_from_plus_gen_vg_from",
            "direction": "from",
            "groups": ("gen_pgqg", "gen_vg"),
        },
        {
            "variant": "gen_pgqg_to_plus_gen_vg_to",
            "direction": "to",
            "groups": ("gen_pgqg", "gen_vg"),
        },
    ]
    if include_bus_voltage_variants:
        variants.extend([
        {
            "variant": "bus_voltage_from_previous",
            "direction": "from",
            "groups": ("bus_voltage",),
            "voltage_init": "PREVIOUS_VALUES",
        },
        {
            "variant": "bus_voltage_to_previous",
            "direction": "to",
            "groups": ("bus_voltage",),
            "voltage_init": "PREVIOUS_VALUES",
        },
        ])
    if include_all_from:
        all_groups = ["load", "gen_pgqg", "gen_vg"]
        if include_bus_voltage_variants:
            all_groups.append("bus_voltage")
        all_variant = {
            "variant": "all_from",
            "direction": "from",
            "groups": tuple(all_groups),
        }
        if include_bus_voltage_variants:
            all_variant["voltage_init"] = "PREVIOUS_VALUES"
        variants.append(
            all_variant
        )
    return variants


def run_case_replacement_experiments(
    case_name: str,
    *,
    mat_dir: str | Path = DEFAULT_MAT_DIR,
    m_dir: str | Path = DEFAULT_M_DIR,
    params: Any | None = None,
    variants: list[dict[str, Any]] | None = None,
    include_bus_voltage_variants: bool = False,
    strict: bool = False,
    verbose: bool = True,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run the standard replacement-note OpenLoadFlow experiment suite."""
    mat_path, m_path = _case_paths(case_name, Path(mat_dir), Path(m_dir))
    if not mat_path.exists():
        raise FileNotFoundError(f"Missing .mat file: {mat_path}")
    if not m_path.exists():
        raise FileNotFoundError(f"Missing matching .m file: {m_path}")

    notes = parse_replacement_notes(m_path)
    if params is None:
        params = openloadflow_parameters()
    if variants is None:
        variants = _variant_plan(
            include_all_from=True,
            include_bus_voltage_variants=include_bus_voltage_variants,
        )

    capabilities = voltage_init_capabilities()
    if verbose:
        print("VoltageInitMode values:", capabilities["available"])
        print("Supports previous/warm values:", capabilities["supports_previous_values"])
        print(
            "Parsed notes: "
            f"bus_voltage={len(notes.bus_voltage)}, "
            f"load={len(notes.load)}, "
            f"gen_pgqg={len(notes.gen_pgqg)}, "
            f"gen_vg={len(notes.gen_vg)}"
        )

    rows: list[dict[str, Any]] = []
    artifacts: dict[str, Any] = {
        "notes": notes,
        "voltage_init_capabilities": capabilities,
        "variant_details": {},
    }

    for variant_spec in variants:
        variant_name = variant_spec["variant"]
        direction = variant_spec.get("direction")
        groups = tuple(variant_spec.get("groups", ()))
        if verbose:
            print(f"\n=== {case_name} | {variant_name} ===")
        if progress is not None:
            progress({"event": "start", "case": case_name, "variant": variant_name})

        network = load_case_network(mat_path)
        before = _network_balance(network)
        apply_info = {
            "applied_bus_voltage": 0,
            "applied_load": 0,
            "applied_gen_pg": 0,
            "applied_gen_qg": 0,
            "applied_gen_pgqg": 0,
            "applied_gen_vg": 0,
            "unmatched": pd.DataFrame(),
            "diagnostics": pd.DataFrame(),
            "after": before,
        }

        row = {
            "case_name": Path(case_name).stem,
            "variant_name": variant_name,
            "direction": direction,
            "n_bus_voltage_notes": len(notes.bus_voltage),
            "n_load_notes": len(notes.load),
            "n_gen_pgqg_notes": len(notes.gen_pgqg),
            "n_gen_vg_notes": len(notes.gen_vg),
            "total_target_generation_before": before["total_target_generation"],
            "total_load_before": before["total_load"],
            "generation_load_balance_before": before["generation_load_balance"],
            "applied_load_updates": 0,
            "applied_bus_voltage_updates": 0,
            "applied_gen_pg_updates": 0,
            "applied_gen_qg_updates": 0,
            "applied_gen_pgqg_updates": 0,
            "applied_gen_vg_updates": 0,
            "total_target_generation_after": before["total_target_generation"],
            "generation_load_balance_after": before["generation_load_balance"],
            "status": "NOT_RUN",
            "status_text": None,
            "iteration_count": None,
            "reference_bus_id": None,
            "slack_bus_id": None,
            "slack_active_power_mismatch": None,
            "distributed_active_power": None,
            "elapsed_seconds": None,
            "exception": None,
        }

        try:
            if groups:
                apply_info = apply_replacement_notes_to_network(
                    network,
                    notes,
                    direction=direction or "from",
                    groups=groups,
                    strict=strict,
                    verbose=verbose,
                )

            after = apply_info["after"]
            row.update(
                {
                    "applied_load_updates": apply_info["applied_load"],
                    "applied_bus_voltage_updates": apply_info["applied_bus_voltage"],
                    "applied_gen_pg_updates": apply_info["applied_gen_pg"],
                    "applied_gen_qg_updates": apply_info["applied_gen_qg"],
                    "applied_gen_pgqg_updates": apply_info["applied_gen_pgqg"],
                    "applied_gen_vg_updates": apply_info["applied_gen_vg"],
                    "total_target_generation_after": after["total_target_generation"],
                    "generation_load_balance_after": after["generation_load_balance"],
                }
            )
            run_params = params
            voltage_init = variant_spec.get("voltage_init")
            if voltage_init is not None:
                pp = require_pypowsybl()
                run_params = openloadflow_parameters(
                    voltage_init_mode=getattr(pp.loadflow.VoltageInitMode, voltage_init)
                )
            lf_info = run_openloadflow_variant(network, params=run_params)
            row.update(
                {
                    key: lf_info.get(key)
                    for key in (
                        "status",
                        "status_text",
                        "iteration_count",
                        "reference_bus_id",
                        "slack_bus_id",
                        "slack_active_power_mismatch",
                        "distributed_active_power",
                        "elapsed_seconds",
                    )
                }
            )
            artifacts["variant_details"][variant_name] = {
                "network": network,
                "apply_info": apply_info,
                "component_results": lf_info["component_results"],
                "raw_results": lf_info["raw_results"],
            }
        except Exception as exc:
            row["status"] = "EXCEPTION"
            row["exception"] = repr(exc)
            artifacts["variant_details"][variant_name] = {
                "network": network,
                "apply_info": apply_info,
                "exception": exc,
            }
            if verbose:
                print(f"Exception: {exc!r}")

        rows.append(row)
        if progress is not None:
            progress({"event": "finish", "case": case_name, "variant": variant_name, "row": row})

    return pd.DataFrame(rows), artifacts


def notebook_progress(event: dict[str, Any]) -> None:
    """Simple progress callback for notebooks."""
    if event["event"] == "start":
        print(f"Starting {event['case']} | {event['variant']}")
    elif event["event"] == "finish":
        row = event["row"]
        print(
            f"Finished {event['variant']}: status={row['status']}, "
            f"iterations={row['iteration_count']}, elapsed={row['elapsed_seconds']}"
        )
