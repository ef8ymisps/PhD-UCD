from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "data" / "raw" / "pglib" / "matpower_m" / "base_case"
OUT_DIR = (
    ROOT
    / "results"
    / "presentable"
    / "pypowsybl_lf_replacement_sweep"
    / "pglib_translation_note_audit"
)

HEADING_RE = re.compile(r"^%\s+INFO\s+:\s+===\s*(?P<section>.*?)\s*===\s*$")
NOTE_RE = re.compile(r"^%\s+(?P<severity>INFO|WARNING)\s+:\s*(?P<message>.*)$")
CASE_RE = re.compile(r"pglib_opf_(?P<case>case.*)\.m$")

TAP_RE = re.compile(
    r"(?P<asset>Branch|Transformer)\s+"
    r"(?P<from_bus>\d+)-(?P<to_bus>\d+).*?"
    r"changing tap ratio\s+(?P<tap_before>[-+0-9.eE]+)\s*=>\s*(?P<tap_after>[-+0-9.eE]+)"
)
PHASE_RE = re.compile(r"phase shift of\s+(?P<phase_shift>[-+0-9.eE]+)")
VLEVEL_RE = re.compile(
    r"voltage levels\s+\((?P<v_from>[-+0-9.eE]+),\s*(?P<v_to>[-+0-9.eE]+)\)"
)
RATING_RE = re.compile(
    r"Updated Thermal Rating:\s+on\s+(?P<asset>line|transformer)\s+"
    r"(?P<from_bus>\d+)-(?P<to_bus>\d+)"
)

CONTROL_KEYWORDS = (
    "controlled bus",
    "target voltage",
    "voltage target",
    "deadband",
    "regulating",
    "regulation",
    "tap step",
    "tap min",
    "tap max",
    "minimum tap",
    "maximum tap",
    "phase regulation",
    "shunt regulation",
)


def case_name(path: Path) -> str:
    match = CASE_RE.match(path.name)
    return match.group("case") if match else path.stem


def classify(section: str, message: str) -> str:
    text = f"{section} {message}".lower()
    if "changing tap ratio" in text:
        return "transformer_static_tap_ratio_replacement"
    if "updated thermal rating" in text:
        return "thermal_rating_replacement"
    if section == "Base KV Replacement Notes":
        return "base_kv_replacement"
    if section == "Voltage Setpoint Replacement Notes":
        return "voltage_setpoint_replacement"
    if section == "Generator Setpoint Replacement Notes":
        return "generator_pq_vg_setpoint_replacement"
    if "generator bounds" in text:
        return "generator_bounds_replacement"
    if "line capacity" in text:
        return "line_capacity_replacement"
    if "monotonicity" in text:
        return "line_capacity_monotonicity"
    return "other_translation_note"


def contains_control_metadata(message: str) -> bool:
    lower = message.lower()
    return any(keyword in lower for keyword in CONTROL_KEYWORDS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    section_counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    detailed_rows: list[dict[str, object]] = []
    transformer_rows: list[dict[str, object]] = []
    case_summary: dict[str, Counter] = defaultdict(Counter)

    for path in sorted(CASE_DIR.glob("*.m")):
        case = case_name(path)
        current_section = "file_header"

        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_no, raw_line in enumerate(handle, start=1):
                line = raw_line.rstrip("\n")

                heading = HEADING_RE.match(line)
                if heading:
                    current_section = heading.group("section").strip()
                    case_summary[case]["sections"] += 1
                    continue

                note = NOTE_RE.match(line)
                if not note:
                    continue

                severity = note.group("severity")
                message = note.group("message").strip()
                if not message:
                    continue

                key = (case, current_section)
                section_counts[key]["notes"] += 1
                section_counts[key][severity.lower()] += 1
                case_summary[case]["total_note_lines"] += 1

                note_class = classify(current_section, message)
                case_summary[case][note_class] += 1

                has_control_metadata = contains_control_metadata(message)
                if has_control_metadata:
                    case_summary[case]["control_metadata_keyword_hits"] += 1

                replacement_related = (
                    "Replacement Notes" in current_section
                    or "Line Capacity" in current_section
                    or "Generator Bounds" in current_section
                    or "Monotonicity" in current_section
                    or note_class != "other_translation_note"
                )
                if replacement_related:
                    detailed_rows.append(
                        {
                            "case_name": case,
                            "source_file": str(path.relative_to(ROOT)),
                            "line_number": line_no,
                            "section": current_section,
                            "severity": severity,
                            "note_class": note_class,
                            "has_control_metadata_keyword": int(has_control_metadata),
                            "message": message,
                        }
                    )

                tap = TAP_RE.search(message)
                if tap:
                    phase = PHASE_RE.search(message)
                    vlevels = VLEVEL_RE.search(message)
                    transformer_rows.append(
                        {
                            "case_name": case,
                            "source_file": str(path.relative_to(ROOT)),
                            "line_number": line_no,
                            "section": current_section,
                            "asset_label": tap.group("asset"),
                            "from_bus": tap.group("from_bus"),
                            "to_bus": tap.group("to_bus"),
                            "tap_before": tap.group("tap_before"),
                            "tap_after": tap.group("tap_after"),
                            "phase_shift_note": phase.group("phase_shift") if phase else "",
                            "voltage_level_from_kv": vlevels.group("v_from") if vlevels else "",
                            "voltage_level_to_kv": vlevels.group("v_to") if vlevels else "",
                            "message": message,
                        }
                    )

                rating = RATING_RE.search(message)
                if rating:
                    case_summary[case][f"{rating.group('asset')}_thermal_rating_notes"] += 1

    section_path = OUT_DIR / "pglib_base_case_note_section_counts.csv"
    with section_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["case_name", "section", "note_count", "info_count", "warning_count"]
        )
        writer.writeheader()
        for (case, section), counts in sorted(section_counts.items()):
            writer.writerow(
                {
                    "case_name": case,
                    "section": section,
                    "note_count": counts["notes"],
                    "info_count": counts["info"],
                    "warning_count": counts["warning"],
                }
            )

    detail_path = OUT_DIR / "pglib_base_case_replacement_notes.csv"
    with detail_path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "case_name",
            "source_file",
            "line_number",
            "section",
            "severity",
            "note_class",
            "has_control_metadata_keyword",
            "message",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(detailed_rows)

    transformer_path = OUT_DIR / "pglib_transformer_tap_replacement_notes.csv"
    with transformer_path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "case_name",
            "source_file",
            "line_number",
            "section",
            "asset_label",
            "from_bus",
            "to_bus",
            "tap_before",
            "tap_after",
            "phase_shift_note",
            "voltage_level_from_kv",
            "voltage_level_to_kv",
            "message",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(transformer_rows)

    summary_path = OUT_DIR / "pglib_operational_control_note_assessment.csv"
    summary_fields = [
        "case_name",
        "sections",
        "total_note_lines",
        "transformer_static_tap_ratio_replacement",
        "thermal_rating_replacement",
        "line_thermal_rating_notes",
        "transformer_thermal_rating_notes",
        "base_kv_replacement",
        "voltage_setpoint_replacement",
        "generator_pq_vg_setpoint_replacement",
        "generator_bounds_replacement",
        "control_metadata_keyword_hits",
        "operational_control_assessment",
    ]
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields)
        writer.writeheader()
        for case, counts in sorted(case_summary.items()):
            assessment = (
                "candidate_control_metadata_present"
                if counts["control_metadata_keyword_hits"]
                else "static_replacement_notes_only"
            )
            writer.writerow(
                {
                    field: (
                        case
                        if field == "case_name"
                        else assessment
                        if field == "operational_control_assessment"
                        else counts[field]
                    )
                    for field in summary_fields
                }
            )

    interpretation_path = OUT_DIR / "pglib_translation_note_interpretation.md"
    with interpretation_path.open("w", encoding="utf-8") as handle:
        handle.write(
            "# PGLib Translation Note Audit\n\n"
            "The MATPOWER case files include translation notes. These notes are useful evidence, "
            "but most of them describe static replacements made while producing the PGLib case, "
            "not runtime operational-control metadata.\n\n"
            "## What the notes can support\n\n"
            "- Static transformer/tap-ratio sanity checks: branches/transformers whose MATPOWER tap ratio was changed.\n"
            "- Thermal-rating/loading analysis: line and transformer Rate A/B/C replacements.\n"
            "- Base-kV and voltage-setpoint provenance checks.\n"
            "- Generator P/Q/Vg replacement provenance, which is directly relevant to the VS, Gen P/Q, and Gen P/Q + VG experiments.\n"
            "- Generator reactive bounds and PV/PQ switching experiments, because Q limits exist in the MATPOWER generator table.\n\n"
            "## What the notes do not provide\n\n"
            "The audit did not find the fields needed to reconstruct real OpenLoadFlow operational controls: "
            "controlled bus/terminal, target voltage, deadband, regulating flag, tap min/max/step curves, "
            "phase-regulation mode, or shunt voltage-control sections. Therefore these notes should not be "
            "used to claim that the original PGLib cases contain operational-control metadata.\n\n"
            "A defensible extension is to use these notes for a static-parameter sensitivity/audit, and to run "
            "true operational-control tests only on native IIDM/CGMES/PSS/E/UCTE-style networks or on clearly "
            "labelled synthetic augmentations of PGLib.\n"
        )

    print(f"Wrote {section_path.relative_to(ROOT)}")
    print(f"Wrote {detail_path.relative_to(ROOT)}")
    print(f"Wrote {transformer_path.relative_to(ROOT)}")
    print(f"Wrote {summary_path.relative_to(ROOT)}")
    print(f"Wrote {interpretation_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
