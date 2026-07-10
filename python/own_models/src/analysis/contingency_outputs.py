from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pandas as pd


def _row_from_result(result) -> dict:
    row = asdict(result)
    row["outaged_branch_ids"] = ";".join(result.outaged_branch_ids)
    row["outaged_generator_ids"] = ";".join(result.outaged_generator_ids)
    row["violation_labels"] = ";".join(result.violation_labels)
    row["responding_generator_ids"] = ";".join(result.responding_generator_ids)
    row["saturated_generator_ids"] = ";".join(result.saturated_generator_ids)
    for key in ("scheduled_pg_by_generator", "actual_pg_by_generator", "pg_adjustment_after_pf"):
        row[key] = ";".join(f"{gen_id}:{value}" for gen_id, value in row[key].items())
    return row


def contingency_results_dataframe(results) -> pd.DataFrame:
    return pd.DataFrame([_row_from_result(result) for result in results])


def contingency_summary_dataframe(results) -> pd.DataFrame:
    detail = contingency_results_dataframe(results)
    if detail.empty:
        return pd.DataFrame()

    rows = []
    for case_name, group in detail.groupby("case_name", sort=True):
        dc_group = group[group["dc_evaluated"]]
        numeric = group[group["severity_score"].notna()]
        if numeric.empty:
            worst_id = ""
            max_severity = float("nan")
        else:
            idx = numeric["severity_score"].idxmax()
            worst_id = str(numeric.loc[idx, "contingency_id"])
            max_severity = float(numeric.loc[idx, "severity_score"])

        rows.append(
            {
                "case_name": case_name,
                "n_contingencies": int(len(group)),
                "n_secure": int(group["secure"].sum()),
                "n_insecure": int((~group["secure"]).sum()),
                "n_dc_evaluated": int(len(dc_group)),
                "n_dc_secure": int(dc_group["dc_secure"].sum()),
                "n_dc_insecure": int((~dc_group["dc_secure"]).sum()),
                "n_dc_pf_nonconverged": int((~dc_group["dc_pf_converged"]).sum()),
                "n_dc_active_generation_violation": int(dc_group["dc_has_active_generation_violation"].sum()),
                "n_dc_thermal_violation": int(dc_group["dc_has_thermal_violation"].sum()),
                "n_dc_angle_violation": int(dc_group["dc_has_angle_violation"].sum()),
                "n_islanding": int(group["has_islanding"].sum()),
                "n_nonconverged": int(group["has_nonconvergence"].sum()),
                "n_generation_imbalance": int(group["has_generation_imbalance"].sum()),
                "n_active_generation_violation": int(group["has_active_generation_violation"].sum()),
                "n_voltage_violation": int(group["has_voltage_violation"].sum()),
                "n_reactive_violation": int(group["has_reactive_violation"].sum()),
                "n_thermal_violation": int(group["has_thermal_violation"].sum()),
                "n_angle_violation": int(group["has_angle_violation"].sum()),
                "max_severity_score": max_severity,
                "worst_numeric_contingency_id": worst_id,
            }
        )
    return pd.DataFrame(rows)


def write_contingency_outputs(
    results,
    output_dir: str | Path,
    *,
    stem: str = "n1_branch",
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = output_dir / f"{stem}_contingencies.csv"
    summary_path = output_dir / f"{stem}_summary.csv"
    contingency_results_dataframe(results).to_csv(detail_path, index=False)
    contingency_summary_dataframe(results).to_csv(summary_path, index=False)
    return {"detail": detail_path, "summary": summary_path}
