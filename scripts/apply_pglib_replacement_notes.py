"""Generate MATPOWER case variants from PGLib replacement-note comments.

The PGLib ``.m`` files often include comment blocks such as::

    % INFO    : Bus 2 : Pd=300.0, Qd=98.61 -> Pd=806.09, Qd=98.61
    % INFO    : Gen at bus 1 : Pg=20.0, Qg=0.0 -> Pg=309.0, Qg=46.0

This script parses those notes and writes patched MATPOWER ``.m`` copies.
It intentionally writes to a derived output directory by default, leaving
``data/raw`` untouched.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_GLOB = "data/raw/pglib/matpower_m/pglib_opf_case*.m"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "matpower_m"


BUS_COLUMNS = {"bus_i": 0, "Pd": 2, "Qd": 3, "Vm": 7, "Va": 8}
GEN_COLUMNS = {
    "bus": 0,
    "Pg": 1,
    "Qg": 2,
    "Qmax": 3,
    "Qmin": 4,
    "Vg": 5,
    "Pmax": 8,
    "Pmin": 9,
}


SECTION_RE = re.compile(r"^%\s+INFO\s+:\s+===\s+(.+?)\s+===")
LOAD_RE = re.compile(
    r"Bus\s+(?P<bus>\d+)\s*:\s*"
    r"Pd=(?P<Pd_from>[-+.\deE]+),\s*Qd=(?P<Qd_from>[-+.\deE]+)\s*->\s*"
    r"Pd=(?P<Pd_to>[-+.\deE]+),\s*Qd=(?P<Qd_to>[-+.\deE]+)"
)
VOLTAGE_RE = re.compile(
    r"Bus\s+(?P<bus>\d+)\s*:\s*"
    r"V=(?P<Vm_from>[-+.\deE]+),\s*theta=(?P<Va_from>[-+.\deE]+)\s*->\s*"
    r"V=(?P<Vm_to>[-+.\deE]+),\s*theta=(?P<Va_to>[-+.\deE]+)"
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


@dataclass
class MatrixRow:
    line_index: int
    values: list[str]
    comment: str


@dataclass
class ParsedCase:
    lines: list[str]
    bus_rows: list[MatrixRow]
    gen_rows: list[MatrixRow]


@dataclass
class NoteOp:
    section: str
    section_id: int
    kind: str
    bus: int
    values_from: dict[str, str]
    values_to: dict[str, str]
    target_index: int | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply PGLib replacement-note values to MATPOWER .m case copies."
    )
    parser.add_argument(
        "--input-glob",
        default=DEFAULT_INPUT_GLOB,
        help="Glob of input .m files, relative to the project root unless absolute.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for patched .m files.",
    )
    parser.add_argument(
        "--suffix",
        default="notes_applied",
        help="Suffix inserted before .m in each output file name.",
    )
    parser.add_argument(
        "--direction",
        choices=("to", "from"),
        default="to",
        help="Use values after the arrow ('to') or before the arrow ('from').",
    )
    parser.add_argument(
        "--sections",
        nargs="+",
        choices=("load", "voltage", "gen"),
        default=("load", "voltage", "gen"),
        help="Replacement-note families to apply.",
    )
    parser.add_argument(
        "--gen-setpoint-pass",
        choices=("first", "last", "all"),
        default="last",
        help="Which 'Generator Setpoint Replacement Notes' section to apply.",
    )
    parser.add_argument(
        "--include",
        nargs="*",
        default=(),
        help="Optional substrings; only input paths containing one are processed.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and summarize without writing output files.",
    )
    parser.add_argument(
        "--write-mode",
        choices=("batch", "grouped", "separate", "both"),
        default="batch",
        help=(
            "Write one case with all selected notes, one case per note family, "
            "one case per selected note, or batch plus separate."
        ),
    )
    parser.add_argument(
        "--no-rename-function",
        action="store_true",
        help="Keep the original MATPOWER function name inside generated files.",
    )
    return parser.parse_args()


def resolve_input_glob(pattern: str) -> str:
    path = Path(pattern)
    if path.is_absolute():
        return str(path)
    return str(PROJECT_ROOT / path)


def expand_input_paths(pattern: str) -> list[Path]:
    path = Path(pattern)
    if path.is_absolute():
        return sorted(path.parent.glob(path.name))
    return sorted(Path().glob(pattern))


def parse_matrix(lines: list[str], matrix_name: str) -> list[MatrixRow]:
    start_re = re.compile(rf"^mpc\.{re.escape(matrix_name)}\s*=\s*\[")
    rows: list[MatrixRow] = []
    in_matrix = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not in_matrix and start_re.match(stripped):
            in_matrix = True
            continue
        if in_matrix and stripped.startswith("];"):
            break
        if in_matrix:
            data_part, _, comment_part = line.partition("%")
            data_part = data_part.strip()
            if not data_part:
                continue
            data_part = data_part.rstrip(";").strip()
            values = data_part.split()
            if values:
                rows.append(
                    MatrixRow(
                        line_index=idx,
                        values=values,
                        comment=("%" + comment_part.rstrip("\n")) if comment_part else "",
                    )
                )
    return rows


def parse_case(path: Path) -> ParsedCase:
    lines = path.read_text(encoding="utf-8").splitlines()
    return ParsedCase(
        lines=lines,
        bus_rows=parse_matrix(lines, "bus"),
        gen_rows=parse_matrix(lines, "gen"),
    )


def parse_notes(lines: Iterable[str]) -> list[NoteOp]:
    section = ""
    section_id = 0
    ops: list[NoteOp] = []
    for line in lines:
        section_match = SECTION_RE.match(line)
        if section_match:
            section = section_match.group(1)
            section_id += 1
            continue

        if section == "Load Replacement Notes":
            if match := LOAD_RE.search(line):
                groups = match.groupdict()
                ops.append(
                    NoteOp(
                        section=section,
                        section_id=section_id,
                        kind="load",
                        bus=int(groups["bus"]),
                        values_from={"Pd": groups["Pd_from"], "Qd": groups["Qd_from"]},
                        values_to={"Pd": groups["Pd_to"], "Qd": groups["Qd_to"]},
                    )
                )
        elif section == "Voltage Setpoint Replacement Notes":
            if match := VOLTAGE_RE.search(line):
                groups = match.groupdict()
                ops.append(
                    NoteOp(
                        section=section,
                        section_id=section_id,
                        kind="voltage",
                        bus=int(groups["bus"]),
                        values_from={"Vm": groups["Vm_from"], "Va": groups["Va_from"]},
                        values_to={"Vm": groups["Vm_to"], "Va": groups["Va_to"]},
                    )
                )
        elif section == "Generator Setpoint Replacement Notes":
            if match := GEN_PQ_RE.search(line):
                groups = match.groupdict()
                ops.append(
                    NoteOp(
                        section=section,
                        section_id=section_id,
                        kind="gen_pq",
                        bus=int(groups["bus"]),
                        values_from={"Pg": groups["Pg_from"], "Qg": groups["Qg_from"]},
                        values_to={"Pg": groups["Pg_to"], "Qg": groups["Qg_to"]},
                    )
                )
            elif match := GEN_VG_RE.search(line):
                groups = match.groupdict()
                ops.append(
                    NoteOp(
                        section=section,
                        section_id=section_id,
                        kind="gen_vg",
                        bus=int(groups["bus"]),
                        values_from={"Vg": groups["Vg_from"]},
                        values_to={"Vg": groups["Vg_to"]},
                    )
                )
    return ops


def select_gen_pass(ops: list[NoteOp], mode: str) -> list[NoteOp]:
    selected: list[tuple[int, list[NoteOp]]] = []
    current_id: int | None = None
    current: list[NoteOp] = []
    for op in ops:
        if current_id is not None and op.section_id != current_id:
            selected.append((current_id, current))
            current = []
        current_id = op.section_id
        current.append(op)
    if current and current_id is not None:
        selected.append((current_id, current))

    if mode == "all":
        return ops
    if not selected:
        return []
    if mode == "first":
        return selected[0][1]
    return selected[-1][1]


def filter_ops(ops: list[NoteOp], sections: set[str], gen_setpoint_pass: str) -> list[NoteOp]:
    load_voltage = [
        op
        for op in ops
        if (op.kind == "load" and "load" in sections)
        or (op.kind == "voltage" and "voltage" in sections)
    ]
    gen_ops = [op for op in ops if op.kind.startswith("gen_")]
    if "gen" in sections:
        load_voltage.extend(select_gen_pass(gen_ops, gen_setpoint_pass))
    assign_gen_targets(load_voltage)
    return load_voltage


def assign_gen_targets(ops: list[NoteOp]) -> None:
    gen_cursor: dict[int, int] = {}
    last_gen_for_bus: dict[int, int] = {}
    previous_gen_note: tuple[str, int] | None = None

    for op in ops:
        if op.kind == "gen_pq":
            target_index = gen_cursor.get(op.bus, 0)
            gen_cursor[op.bus] = target_index + 1
            last_gen_for_bus[op.bus] = target_index
            op.target_index = target_index
            previous_gen_note = (op.kind, op.bus)
        elif op.kind == "gen_vg":
            if previous_gen_note == ("gen_pq", op.bus):
                target_index = last_gen_for_bus.get(op.bus, gen_cursor.get(op.bus, 0))
            else:
                target_index = gen_cursor.get(op.bus, 0)
                gen_cursor[op.bus] = target_index + 1
            last_gen_for_bus[op.bus] = target_index
            op.target_index = target_index
            previous_gen_note = (op.kind, op.bus)
        else:
            previous_gen_note = None


def rows_by_bus(rows: list[MatrixRow], bus_column: int) -> dict[int, list[MatrixRow]]:
    grouped: dict[int, list[MatrixRow]] = {}
    for row in rows:
        grouped.setdefault(int(float(row.values[bus_column])), []).append(row)
    return grouped


def apply_values(row: MatrixRow, columns: dict[str, int], values: dict[str, str]) -> int:
    changed = 0
    for name, value in values.items():
        column = columns[name]
        if column < len(row.values) and row.values[column] != value:
            row.values[column] = value
            changed += 1
    return changed


def render_row(row: MatrixRow) -> str:
    body = "\t" + "\t ".join(row.values) + ";"
    if row.comment:
        body += " " + row.comment
    return body


def apply_notes(case: ParsedCase, ops: list[NoteOp], direction: str) -> dict[str, int]:
    values_attr = "values_to" if direction == "to" else "values_from"
    bus_rows = rows_by_bus(case.bus_rows, BUS_COLUMNS["bus_i"])
    gen_rows = rows_by_bus(case.gen_rows, GEN_COLUMNS["bus"])
    gen_cursor = {bus: 0 for bus in gen_rows}
    last_gen_for_bus: dict[int, MatrixRow] = {}
    previous_gen_note: tuple[str, int] | None = None
    stats = {"load_ops": 0, "voltage_ops": 0, "gen_ops": 0, "cell_changes": 0, "missed_ops": 0}

    for op in ops:
        values = getattr(op, values_attr)
        if op.kind == "load":
            rows = bus_rows.get(op.bus, [])
            if not rows:
                stats["missed_ops"] += 1
                continue
            stats["cell_changes"] += apply_values(rows[0], BUS_COLUMNS, values)
            stats["load_ops"] += 1
        elif op.kind == "voltage":
            rows = bus_rows.get(op.bus, [])
            if not rows:
                stats["missed_ops"] += 1
                continue
            stats["cell_changes"] += apply_values(rows[0], BUS_COLUMNS, values)
            stats["voltage_ops"] += 1
        elif op.kind == "gen_pq":
            rows = gen_rows.get(op.bus, [])
            cursor = op.target_index if op.target_index is not None else gen_cursor.get(op.bus, 0)
            if cursor >= len(rows):
                stats["missed_ops"] += 1
                continue
            row = rows[cursor]
            if op.target_index is None:
                gen_cursor[op.bus] = cursor + 1
            last_gen_for_bus[op.bus] = row
            stats["cell_changes"] += apply_values(row, GEN_COLUMNS, values)
            stats["gen_ops"] += 1
            previous_gen_note = (op.kind, op.bus)
        elif op.kind == "gen_vg":
            row = None
            if previous_gen_note == ("gen_pq", op.bus):
                row = last_gen_for_bus.get(op.bus)
            if row is None:
                rows = gen_rows.get(op.bus, [])
                cursor = op.target_index if op.target_index is not None else gen_cursor.get(op.bus, 0)
                if cursor >= len(rows):
                    stats["missed_ops"] += 1
                    continue
                row = rows[cursor]
                if op.target_index is None:
                    gen_cursor[op.bus] = cursor + 1
                last_gen_for_bus[op.bus] = row
            stats["cell_changes"] += apply_values(row, GEN_COLUMNS, values)
            stats["gen_ops"] += 1
            previous_gen_note = (op.kind, op.bus)

    for row in case.bus_rows + case.gen_rows:
        case.lines[row.line_index] = render_row(row)

    return stats


def rename_function(lines: list[str], function_name: str) -> None:
    function_re = re.compile(r"^(function\s+mpc\s*=\s*)\w+")
    for idx, line in enumerate(lines[:20]):
        if match := function_re.match(line):
            lines[idx] = match.group(1) + function_name
            return


def output_path_for(input_path: Path, output_dir: Path, suffix: str) -> Path:
    return output_dir / f"{input_path.stem}__{suffix}{input_path.suffix}"


def grouped_ops(ops: list[NoteOp]) -> list[tuple[str, list[NoteOp]]]:
    groups = [
        ("load", [op for op in ops if op.kind == "load"]),
        ("voltage", [op for op in ops if op.kind == "voltage"]),
        ("gen", [op for op in ops if op.kind.startswith("gen_")]),
    ]
    return [(name, group_ops) for name, group_ops in groups if group_ops]


def op_label(index: int, op: NoteOp) -> str:
    row_part = ""
    if op.target_index is not None:
        row_part = f"_row{op.target_index + 1}"
    if op.kind == "load":
        family = "load"
    elif op.kind == "voltage":
        family = "voltage"
    elif op.kind == "gen_pq":
        family = "gen_pq"
    else:
        family = "gen_vg"
    return f"{index:04d}_{family}_bus{op.bus}{row_part}"


def op_record(op: NoteOp, direction: str) -> dict[str, object]:
    return {
        "section": op.section,
        "section_id": op.section_id,
        "kind": op.kind,
        "bus": op.bus,
        "target_gen_row": None if op.target_index is None else op.target_index + 1,
        "values_applied": op.values_to if direction == "to" else op.values_from,
        "values_from": op.values_from,
        "values_to": op.values_to,
    }


def relative_or_absolute(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path)


def write_case_variant(
    input_path: Path,
    output_path: Path,
    ops: list[NoteOp],
    direction: str,
    rename: bool,
) -> dict[str, int]:
    parsed = parse_case(input_path)
    stats = apply_notes(parsed, ops, direction)
    if rename:
        rename_function(parsed.lines, output_path.stem)
    output_path.write_text("\n".join(parsed.lines) + "\n", encoding="utf-8")
    return stats


def generate_replacement_variants(
    input_glob: str = DEFAULT_INPUT_GLOB,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    suffix: str = "replacement_experiment",
    direction: str = "from",
    sections: Iterable[str] = ("load", "voltage", "gen"),
    gen_setpoint_pass: str = "last",
    include: Iterable[str] = (),
    write_mode: str = "grouped",
    dry_run: bool = False,
    rename_function_names: bool = True,
) -> list[dict[str, object]]:
    """Generate replacement-note variants and return a structured manifest.

    This is the notebook/API equivalent of the command-line interface.
    Each generated variant starts from the original input file, so grouped
    experiments are independent by construction.
    """
    output_dir = Path(output_dir)
    input_paths = expand_input_paths(resolve_input_glob(input_glob))
    include = tuple(include)
    if include:
        input_paths = [path for path in input_paths if any(part in str(path) for part in include)]

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict[str, object]] = []
    for input_path in input_paths:
        source_case = parse_case(input_path)
        all_ops = parse_notes(source_case.lines)
        ops = filter_ops(all_ops, set(sections), gen_setpoint_pass)

        record: dict[str, object] = {
            "input": relative_or_absolute(input_path),
            "parsed_note_ops": len(all_ops),
            "selected_note_ops": len(ops),
            "outputs": [],
        }
        outputs = record["outputs"]

        if write_mode in {"batch", "both"}:
            output_path = output_path_for(input_path, output_dir, suffix)
            batch_record: dict[str, object] = {
                "mode": "batch",
                "output": relative_or_absolute(output_path),
                "ops": [op_record(op, direction) for op in ops],
            }
            if dry_run:
                parsed = parse_case(input_path)
                batch_record.update(apply_notes(parsed, ops, direction))
            else:
                batch_record.update(
                    write_case_variant(
                        input_path,
                        output_path,
                        ops,
                        direction,
                        rename_function_names,
                    )
                )
            outputs.append(batch_record)

        if write_mode == "grouped":
            for group_name, group_ops in grouped_ops(ops):
                output_path = output_path_for(input_path, output_dir, f"{suffix}_{group_name}_only")
                group_record: dict[str, object] = {
                    "mode": "grouped",
                    "group": group_name,
                    "output": relative_or_absolute(output_path),
                    "ops": [op_record(op, direction) for op in group_ops],
                }
                if dry_run:
                    parsed = parse_case(input_path)
                    group_record.update(apply_notes(parsed, group_ops, direction))
                else:
                    group_record.update(
                        write_case_variant(
                            input_path,
                            output_path,
                            group_ops,
                            direction,
                            rename_function_names,
                        )
                    )
                outputs.append(group_record)

        if write_mode in {"separate", "both"}:
            separate_dir = output_dir / f"{input_path.stem}__{suffix}_separate"
            if not dry_run:
                separate_dir.mkdir(parents=True, exist_ok=True)
            for op_index, op in enumerate(ops, start=1):
                label = op_label(op_index, op)
                output_path = separate_dir / f"{input_path.stem}__{suffix}__{label}{input_path.suffix}"
                separate_record: dict[str, object] = {
                    "mode": "separate",
                    "output": relative_or_absolute(output_path),
                    "op_index": op_index,
                    "op": op_record(op, direction),
                }
                if dry_run:
                    parsed = parse_case(input_path)
                    separate_record.update(apply_notes(parsed, [op], direction))
                else:
                    separate_record.update(
                        write_case_variant(
                            input_path,
                            output_path,
                            [op],
                            direction,
                            rename_function_names,
                        )
                    )
                outputs.append(separate_record)

            manifest_path = separate_dir / "manifest.json"
            record["separate_manifest"] = relative_or_absolute(manifest_path)
            if not dry_run:
                manifest_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

        summary.append(record)

    return summary


def main() -> None:
    args = parse_args()
    summary = generate_replacement_variants(
        input_glob=args.input_glob,
        output_dir=args.output_dir,
        suffix=args.suffix,
        direction=args.direction,
        sections=args.sections,
        gen_setpoint_pass=args.gen_setpoint_pass,
        include=args.include,
        write_mode=args.write_mode,
        dry_run=args.dry_run,
        rename_function_names=not args.no_rename_function,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
