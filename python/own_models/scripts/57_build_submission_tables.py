"""Final submission tables: per-case, per-objective three-way comparison.

Produces, for each of the 8 default PGLib cases and each of the two
objectives (generation-cost, active-losses), one CSV with header exactly

    scenario,pm_objective,julia_objective,python_objective

covering every scenario in the compact_scenarios grid. Where a solve did
not converge (own model or PowerModels), that cell reads "NOT CONVERGED"
instead of a number, rather than silently dropping the row.

Sources (already produced by prior sweep runs, not re-solved here):
- Julia generation-cost: results/own_vs_powermodels/julia_pm_like/summary.csv
- Python generation-cost: results/own_vs_powermodels/python_pm_like/summary.csv
- Julia active-losses: results/own_vs_powermodels/three_way_active_losses/julia_full_sweep.csv
- Python active-losses: results/own_vs_powermodels/three_way_active_losses/python_full_sweep.csv

Output: results/own_vs_powermodels/submission_tables/{cost,losses}/{case}.csv
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
JULIA_COST_PATH = ROOT / "results" / "own_vs_powermodels" / "julia_pm_like" / "summary.csv"
PYTHON_COST_PATH = ROOT / "results" / "own_vs_powermodels" / "python_pm_like" / "summary.csv"
JULIA_LOSSES_PATH = ROOT / "results" / "own_vs_powermodels" / "three_way_active_losses" / "julia_full_sweep.csv"
PYTHON_LOSSES_PATH = ROOT / "results" / "own_vs_powermodels" / "three_way_active_losses" / "python_full_sweep.csv"
OUT_ROOT = ROOT / "results" / "own_vs_powermodels" / "submission_tables"

CASES = ["5_pjm", "14_ieee", "30_ieee", "118_ieee", "300_ieee", "1354_pegase", "1888_rte", "6470_rte"]

CONVERGED_STATUSES = {"optimal", "locally_solved", "almost_locally_solved", "almost_optimal"}
NO_DATA = "NO DATA"  # distinct from a genuine solver status: this side was never attempted/recorded at all

# python_pm_like/summary.csv is shared by two scripts with different column
# layouts (23_run_own_vs_powermodels_tables.py's 13-column schema owns the
# header; 34_run_own_vs_powermodels_pm_like_full_sweep.py's wider schema is
# appended underneath it, same positions for these 6 fields either way).
_PY_COST_FALLBACK_POSITIONS = {
    "case": 0, "scenario": 1, "own_status": 4, "pm_status": 5, "own_objective": 7, "pm_objective": 8,
}


def is_converged(status: str) -> bool:
    return str(status).strip().lower() in CONVERGED_STATUSES


def load_rows_generic(path: Path, *, fallback_positions: dict[str, int] | None = None) -> dict[tuple[str, str], dict[str, str]]:
    """Read an append-only summary CSV tolerantly (ragged rows from
    schema drift across script versions -- see scripts 53/56's docstrings
    for the full explanation), keeping the LAST matching row per
    (case, scenario)."""
    latest: dict[tuple[str, str], dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        n_cols = len(header)
        for row in reader:
            if len(row) == n_cols:
                record = dict(zip(header, row))
                latest[(record["case"], record["scenario"])] = record
            elif fallback_positions is not None and max(fallback_positions.values()) < len(row):
                latest[(row[fallback_positions["case"]], row[fallback_positions["scenario"]])] = {
                    name: row[pos] for name, pos in fallback_positions.items()
                }
            elif len(row) < n_cols:
                # A shorter row than the header is the OLDER-schema-header /
                # NEWER-narrower-row situation seen in julia_pm_like/summary.csv:
                # its header carries 2 extra trailing columns
                # (pm_like_coverage_status/note) from an earlier backfill run,
                # but every row appended by the current 25/27/58/60/61 scripts
                # only ever writes the first 13 fields, in the same order --
                # i.e. the row is a genuine prefix of the header, not a
                # different layout. `zip` naturally truncates to the shorter
                # of the two, so this reuses the exact-match branch's own
                # dict-building line rather than a fixed position map.
                record = dict(zip(header, row))
                latest[(record["case"], record["scenario"])] = record
    return latest


# Every raw scenario_id compact_scenarios generates
# (julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl's
# scenario_id()) follows a fixed 6-field grammar joined by "__":
# case_name__scenario_type__target_type__target_id__parameter_name__parameter_value,
# with "none" placeholders for any field a scenario category doesn't use.
# That's necessary to guarantee uniqueness while the scenario is being
# generated, but most of it is either a placeholder or restates something
# the case-name-derived prefix already says -- e.g.
# "6470_rte__slack_without_reference__none__none__fix_reference_angle__false"
# says "not fixed" three different ways. `clean_scenario_name` rebuilds a
# short, human-readable name per scenario TYPE (dispatching on the 2nd
# field) using only the fields that actually carry information for that
# type, still prefixed with `case` so rows stay unambiguous once flattened
# into one table across all 8 cases (see `consolidate`).
_FRIENDLY_LABEL_CLEAN = {
    # 1888_rte-only historical short labels (see KNOWN_LABELS/rekey_known_labels
    # below) -- already-short strings that never went through scenario_id()'s
    # 6-field grammar, so they need their own explicit mapping here.
    "base": lambda case: f"{case}_base",
    "load_x0.8": lambda case: f"{case}_load_x0p8",
    "load_x1.2_reactive_stress": lambda case: f"{case}_qload_x1p2",
    "qg_limits_x0.8": lambda case: f"{case}_qg_limits_x0p8",
}


def clean_scenario_name(case: str, raw_scenario: str) -> str:
    if raw_scenario in _FRIENDLY_LABEL_CLEAN:
        return _FRIENDLY_LABEL_CLEAN[raw_scenario](case)

    parts = raw_scenario.split("__")
    if len(parts) != 6:
        return raw_scenario  # unrecognized shape -- leave untouched rather than mangling it

    _case_name, scenario_type, target_type, target_id, _parameter_name, parameter_value = parts

    if scenario_type == "base":
        return f"{case}_base"
    if scenario_type == "relaxed_limits":
        return f"{case}_relaxed_limits"
    if scenario_type == "branch_congestion":
        return f"{case}_branch_rates_x{parameter_value}"
    if scenario_type == "tight_voltage_limits":
        # parameter_value is e.g. "0p95m1p05" ("0.95-1.05" with "." -> "p",
        # "-" -> "m"); the "m" here is only ever this range separator.
        return f"{case}_voltage_bounds_{parameter_value.replace('m', '_')}"
    if scenario_type in ("generator_capacity_reduction", "generator_capacity_expansion"):
        # Both categories emit an identical pmax x1.0 entry (the unperturbed
        # baseline, present in both the reduction sweep [1.0,0.8,0.6] and the
        # expansion sweep [1.0,1.2,1.5]) -- collapsing both to the same clean
        # name here is intentional (per explicit choice), letting the
        # per-case dedup below drop the second occurrence.
        return f"{case}_gen_{target_id}_pmax_x{parameter_value}"
    if scenario_type == "load_scaling":
        return f"{case}_load_x{parameter_value}"
    if scenario_type == "system_angle_limits":
        # Angle limits are always whole-number degrees (30/20/10/5) --
        # parameter_value is e.g. "10p0"; drop the redundant "p0".
        value = parameter_value[:-2] if parameter_value.endswith("p0") else parameter_value
        return f"{case}_angle_limit_{value}deg"
    if scenario_type == "reactive_power_stress":
        return f"{case}_qload_x{parameter_value}"
    if scenario_type == "reactive_generation_limits":
        return f"{case}_qg_limits_x{parameter_value}"
    if scenario_type == "slack_consistency":
        return f"{case}_slack_bus_{target_id}"
    if scenario_type == "slack_without_reference":
        return f"{case}_slack_without_reference"
    if scenario_type == "branch_outage":
        return f"{case}_branch_{target_id}_out"
    if scenario_type == "generator_outage":
        return f"{case}_gen_{target_id}_out"
    return raw_scenario  # unrecognized scenario type -- leave untouched


def cell(status_text: str, value_text: str) -> str:
    """Return the numeric objective when converged; otherwise the actual
    solver-reported termination status (e.g. "LOCALLY_INFEASIBLE",
    "ITERATION_LIMIT", "TIME_LIMIT", "infeasible") rather than a generic
    placeholder, so a reader can tell *why* a cell has no number without
    cross-referencing the raw sweep CSVs. `NO_DATA` is reserved for the
    separate case where this side was never even attempted/recorded."""
    if status_text == "":
        return NO_DATA
    if not is_converged(status_text):
        return status_text
    if value_text == "":
        return NO_DATA
    try:
        return repr(float(value_text))
    except ValueError:
        return status_text


def build_cost_tables(*, allowed_keys: set[tuple[str, str]]) -> None:
    """`allowed_keys` restricts output to the scenario set the active-losses
    sweep actually covers (Julia's script 48, driven by a live
    `compact_scenarios` call -- the current, reproducible-today scenario
    grid). Both source CSVs here are accumulated, append-only logs spanning
    multiple historical runs (see module docstring), and each independently
    picked up extra scenarios the current codebase no longer generates:
    Python's cost sweep discovers scenarios by globbing every reference
    JSON on disk, including leftovers from an unrelated older validation-case
    generator (`relaxed`/`branch_congested`/`generator_limited`/
    `load_scaled` -- see `julia/own_models/src/analysis/validation_cases.jl`,
    never part of the compact_scenarios grid at all); Julia's own cost sweep
    (`julia_pm_like/summary.csv`) has itself accumulated rows from more than
    one historical `compact_scenarios` run, whose top-k branch-outage
    ranking depends on the base-case dispatch used to rank branches by
    loading -- e.g. case6470_rte carries 9 distinct branch-outage entries
    even though a single run only ever selects up to 6. Filtering to
    `allowed_keys` keeps the tables scoped to today's live, reproducible
    scenario grid rather than that accumulated history."""
    julia_rows = load_rows_generic(JULIA_COST_PATH)
    python_rows = load_rows_generic(PYTHON_COST_PATH, fallback_positions=_PY_COST_FALLBACK_POSITIONS)
    out_dir = OUT_ROOT / "generation_cost"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_keys = sorted((set(julia_rows) | set(python_rows)) & allowed_keys)
    for case in CASES:
        case_keys = [k for k in all_keys if k[0] == case]
        out_path = out_dir / f"{case}.csv"
        seen_clean_names: set[str] = set()
        rows_written = 0
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["scenario", "pm_objective", "julia_objective", "python_objective"])
            for _, scenario in case_keys:
                clean_name = clean_scenario_name(case, scenario)
                if clean_name in seen_clean_names:
                    continue  # generator_capacity_reduction/expansion pmax x1.0 duplicate
                seen_clean_names.add(clean_name)
                jrow = julia_rows.get((case, scenario), {})
                prow = python_rows.get((case, scenario), {})
                pm_status = jrow.get("pm_status", "") or prow.get("pm_status", "")
                pm_value = jrow.get("pm_objective", "") or prow.get("pm_objective", "")
                julia_cell = cell(jrow.get("own_status", ""), jrow.get("own_objective", ""))
                python_cell = cell(prow.get("own_status", ""), prow.get("own_objective", ""))
                pm_cell = cell(pm_status, pm_value)
                writer.writerow([clean_name, pm_cell, julia_cell, python_cell])
                rows_written += 1
        print(f"Wrote {out_path} ({rows_written} scenarios)")


# Every own-vs-PowerModels script this session that discovers scenarios by
# globbing exported reference JSON files (23/34, and Julia scripts that
# source-include 25) renames these specific raw compact_scenarios ids to
# short, friendly labels (KNOWN_LABELS in
# julia/own_models/scripts/27_run_own_vs_powermodels_full_sweep.jl and its
# Python counterparts) -- but scripts 48/55 (this session's new
# active-losses sweep) never apply that renaming, since they were written
# fresh against compact_scenarios directly. Without reconciling the two
# naming conventions, these 4 scenarios look like 4 different mismatches
# (missing from one table or the other) when they are actually the exact
# same 4 scenarios, just addressed by two different strings.
KNOWN_LABELS = {
    "1888_rte__base__none__none__none__none": "base",
    "1888_rte_load_x0p8__load_scaling__none__none__load_multiplier__0p8": "load_x0.8",
    "1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2": "load_x1.2_reactive_stress",
    "1888_rte_qg_limits_x0p8__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p8": "qg_limits_x0.8",
}


def base_raw_id(case: str) -> str:
    return f"{case}__base__none__none__none__none"


def rekey_known_labels(rows: dict[tuple[str, str], dict[str, str]]) -> None:
    """Rename any row whose (case, scenario) key matches a known raw-id ->
    friendly-label mapping, in place. `base_raw_id` covers every case (the
    base scenario exists for all 8); the other 3 are 1888_rte-specific
    (the Q1-Q10 spotlight scenarios)."""
    for case in CASES:
        raw_key = (case, base_raw_id(case))
        if raw_key in rows:
            rows[(case, "base")] = rows.pop(raw_key)
    for raw_id, friendly in KNOWN_LABELS.items():
        if raw_id == base_raw_id("1888_rte"):
            continue  # already handled generically above
        raw_key = ("1888_rte", raw_id)
        if raw_key in rows:
            rows[("1888_rte", friendly)] = rows.pop(raw_key)


def build_losses_tables() -> set[tuple[str, str]]:
    julia_rows = load_rows_generic(JULIA_LOSSES_PATH)
    python_rows = load_rows_generic(PYTHON_LOSSES_PATH)

    rekey_known_labels(julia_rows)
    rekey_known_labels(python_rows)

    out_dir = OUT_ROOT / "active_losses"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_keys = sorted(set(julia_rows) | set(python_rows))
    for case in CASES:
        case_keys = [k for k in all_keys if k[0] == case]
        out_path = out_dir / f"{case}.csv"
        seen_clean_names: set[str] = set()
        rows_written = 0
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["scenario", "pm_objective", "julia_objective", "python_objective"])
            for _, scenario in case_keys:
                clean_name = clean_scenario_name(case, scenario)
                if clean_name in seen_clean_names:
                    continue  # generator_capacity_reduction/expansion pmax x1.0 duplicate
                seen_clean_names.add(clean_name)

                jrow = julia_rows.get((case, scenario), {})
                prow = python_rows.get((case, scenario), {})

                pm_status = jrow.get("pm_status", "") or prow.get("pm_status", "")
                pm_value = jrow.get("pm_loss_objective", "") or prow.get("pm_loss_objective", "")
                julia_status = jrow.get("julia_status", "")
                julia_value = jrow.get("julia_loss_objective", "") or prow.get("julia_loss_objective", "")
                python_status = prow.get("python_status", "")
                python_value = prow.get("python_loss_objective", "")

                pm_cell = cell(pm_status, pm_value)
                julia_cell = cell(julia_status, julia_value)
                python_cell = cell(python_status, python_value)
                writer.writerow([clean_name, pm_cell, julia_cell, python_cell])
                rows_written += 1
        print(f"Wrote {out_path} ({rows_written} scenarios)")

    return set(all_keys)


def consolidate(objective_subdir: str, out_name: str) -> None:
    """Concatenate the 8 per-case CSVs into one combined table. Every
    scenario name is already clean and case-qualified by the time the
    per-case CSVs are written (`clean_scenario_name`), so this is a plain
    concatenation with no renaming."""
    out_dir = OUT_ROOT / objective_subdir
    out_path = out_dir / out_name
    total = 0
    with out_path.open("w", newline="", encoding="utf-8") as out_handle:
        writer = csv.writer(out_handle)
        writer.writerow(["scenario", "pm_objective", "julia_objective", "python_objective"])
        for case in CASES:
            case_path = out_dir / f"{case}.csv"
            with case_path.open(newline="", encoding="utf-8") as in_handle:
                reader = csv.reader(in_handle)
                next(reader)  # skip the per-case header
                for row in reader:
                    writer.writerow(row)
                    total += 1
    print(f"Wrote {out_path} ({total} scenario rows across {len(CASES)} cases)")


def main() -> None:
    losses_keys = build_losses_tables()
    build_cost_tables(allowed_keys=losses_keys)
    consolidate("generation_cost", "generation_cost_validation_scenarios.csv")
    consolidate("active_losses", "active_losses_validation_scenarios.csv")


if __name__ == "__main__":
    main()
