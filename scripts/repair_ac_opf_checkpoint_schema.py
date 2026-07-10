"""Repair AC OPF checkpoint rows written with the scenario-checkpoint schema bug."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import shutil


OLD_HEADER = [
    "case_name",
    "n_buses",
    "n_branches",
    "n_generators",
    "scenario",
    "target_type",
    "target_id",
    "parameter_name",
    "parameter_value",
    "success",
    "status",
    "termination",
    "objective",
    "objective_delta_vs_base",
    "total_pg",
    "total_qg",
    "total_pd",
    "total_qd",
    "min_vm",
    "max_vm",
    "max_p_balance_violation",
    "max_q_balance_violation",
    "max_voltage_violation",
    "max_thermal_violation",
    "max_angle_violation",
    "max_branch_loading_pct",
    "n_thermal_binding",
    "n_angle_binding",
    "n_gen_at_pmin",
    "n_gen_at_pmax",
    "n_qg_at_qmin",
    "n_qg_at_qmax",
    "loadability_factor",
    "total_load_curtailment",
    "warm_start_source",
    "solve_time_sec",
    "message",
    "expected_observation",
    "observation_note",
    "pm_status",
    "pm_objective",
    "objective_diff_vs_pm",
    "branch_id",
    "base_flow_pu",
    "rate_a_pu",
    "base_loading_pct",
    "selection_reason",
    "loading_rank",
    "flow_rank",
    "gen_id",
    "bus_id",
    "base_pg_pu",
    "pmax_pu",
    "cost_c1",
    "cost_c2",
    "cost_rank",
    "pg_rank",
]


BUGGY_WRITTEN_COLUMNS = [
    "case_name",
    "n_buses",
    "n_branches",
    "n_generators",
    "scenario",
    "target_type",
    "target_id",
    "selection_reason",
    "loading_rank",
    "flow_rank",
    "cost_rank",
    "pg_rank",
    "parameter_name",
    "parameter_value",
    "success",
    "status",
    "termination",
    "objective",
    "objective_delta_vs_base",
    "total_pg",
    "total_qg",
    "total_pd",
    "total_qd",
    "min_vm",
    "max_vm",
    "max_p_balance_violation",
    "max_q_balance_violation",
    "max_voltage_violation",
    "max_thermal_violation",
    "max_angle_violation",
    "max_branch_loading_pct",
    "n_thermal_binding",
    "n_angle_binding",
    "n_gen_at_pmin",
    "n_gen_at_pmax",
    "n_qg_at_qmin",
    "n_qg_at_qmax",
    "loadability_factor",
    "total_load_curtailment",
    "warm_start_source",
    "solve_time_sec",
    "message",
    "expected_observation",
    "observation_note",
    "pm_status",
    "pm_objective",
    "objective_diff_vs_pm",
    "branch_id",
    "base_flow_pu",
    "rate_a_pu",
    "base_loading_pct",
    "gen_id",
    "bus_id",
    "base_pg_pu",
    "pmax_pu",
    "cost_c1",
    "cost_c2",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()

    path = args.path
    backup_path = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup_path)

    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = list(reader)

    if header != OLD_HEADER:
        raise SystemExit("Unexpected CSV header; refusing repair.")

    repaired = 0
    output_rows = []
    for row in rows:
        parsed = dict(zip(header, row, strict=True))
        if parsed.get("success") in {"True", "False"}:
            output_rows.append(parsed)
            continue

        if parsed.get("total_pg") not in {"True", "False"}:
            output_rows.append(parsed)
            continue

        remapped_source = dict(zip(BUGGY_WRITTEN_COLUMNS, row, strict=True))
        output_rows.append({column: remapped_source.get(column, "") for column in header})
        repaired += 1

    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Repaired {repaired} rows in {path}")
    print(f"Backup written to {backup_path}")


if __name__ == "__main__":
    main()
