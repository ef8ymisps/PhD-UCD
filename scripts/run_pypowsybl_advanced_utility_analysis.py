"""Run focused PyPowSyBl advanced-utility checks.

This script sits after the extended OpenLoadFlow configuration sweep. It uses
the matched single-slack baseline as the reference split:

* voltage initializer is tried on matched-baseline failures, then LF is retried;
* AC security and AC sensitivity are tried on matched-baseline solved states.

The goal is not to perform a full security or sensitivity study yet. It records
which utilities are operational in the current environment and whether they can
be used as the next diagnostic layer.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import run_pypowsybl_extended_configuration_analysis as ext  # noqa: E402
from pypowsybl_replacement_experiment_matrix import (  # noqa: E402
    VARIANT_LABELS,
    _is_converged_status,
    apply_replacement_variant,
    get_table,
    load_case_network,
    make_replacement_variants,
    parse_replacement_notes,
    require_pypowsybl,
)


OUTPUT_DIR = ext.OUTPUT_DIR
FIGURE_DIR = ext.FIGURE_DIR
BASELINE_ROWS = OUTPUT_DIR / "extended_configuration_rows.csv"
UTILITY_ROWS = OUTPUT_DIR / "advanced_utility_rows.csv"

UTILITY_LABELS = {
    "voltage_initializer_then_lf": "Voltage init + LF",
    "security_ac_single_contingency": "Security AC",
    "sensitivity_ac_branch_flow": "Sensitivity AC",
}
UTILITY_ORDER = list(UTILITY_LABELS)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def save_dataframe_with_retry(frame: pd.DataFrame, path: Path, attempts: int = 20) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    last_exc: PermissionError | None = None
    for attempt in range(attempts):
        try:
            frame.to_csv(path, index=False)
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(min(0.5 * (attempt + 1), 5.0))
    if last_exc is not None:
        raise last_exc


def load_existing(path: Path) -> pd.DataFrame:
    if path.exists() and path.stat().st_size:
        return pd.read_csv(path)
    return pd.DataFrame()


def bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().eq("true") | series.eq(True)


def enum_name(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "name", value)).split(".")[-1]


def baseline_rows() -> pd.DataFrame:
    if not BASELINE_ROWS.exists():
        raise FileNotFoundError(f"Missing baseline rows: {BASELINE_ROWS}")
    rows = pd.read_csv(BASELINE_ROWS)
    rows = rows[rows["extended_config_name"].eq("matched_single_slack")].copy()
    if rows.empty:
        raise ValueError("No matched_single_slack rows found in extended configuration output.")
    rows["baseline_valid_converged"] = bool_series(rows["valid_converged"])
    return rows


def row_key(row: pd.Series | dict[str, Any]) -> tuple[str, str, str]:
    return (str(row["case_name"]), str(row["variant_name"]), str(row["utility_name"]))


def common_row(base: pd.Series, utility_name: str) -> dict[str, Any]:
    return {
        "case_name": base["case_name"],
        "case_label": base.get("case_label", ext.CASE_LABELS.get(base["case_name"], base["case_name"])),
        "variant_name": base["variant_name"],
        "variant_label": VARIANT_LABELS.get(base["variant_name"], base.get("variant_label", base["variant_name"])),
        "baseline_config_name": "matched_single_slack",
        "baseline_valid_converged": bool(base["baseline_valid_converged"]),
        "baseline_status": base.get("status"),
        "baseline_iteration_count": base.get("iteration_count"),
        "utility_name": utility_name,
        "utility_label": UTILITY_LABELS[utility_name],
        "utility_attempted": False,
        "utility_valid": False,
        "utility_status": "NOT_RUN",
        "utility_exception": None,
        "utility_elapsed_seconds": None,
        "lf_after_utility_status": None,
        "lf_after_utility_valid_converged": None,
        "lf_after_utility_iteration_count": None,
        "selected_branch_id": None,
        "selected_generator_id": None,
        "security_pre_status": None,
        "security_post_statuses": None,
        "security_limit_violations": None,
        "sensitivity_values": None,
        "sensitivity_max_abs": None,
        "sensitivity_reference_flow": None,
    }


def prepare_network(case_name: str, variant_name: str, parameter_scenario: str) -> Any:
    variants = make_replacement_variants(include_bus_voltage=True)
    variant = variants[variant_name]
    mat_path, m_path = ext._case_paths(case_name, ext.BASE_CASE_MAT_DIR, ext.BASE_CASE_M_DIR)
    network = load_case_network(mat_path)
    notes = parse_replacement_notes(m_path)
    apply_replacement_variant(
        network,
        notes,
        variant,
        case_name=case_name,
        parameter_scenario=parameter_scenario,
        strict=False,
    )
    return network


def matched_scenario(case_name: str) -> Any:
    return ext.build_scenario(ext.CONFIG_BY_NAME["matched_single_slack"], case_name)


def valid_lf_result(result: dict[str, Any]) -> bool:
    return (
        bool(result.get("converged"))
        and bool(result.get("all_components_converged"))
        and _is_converged_status(result.get("status"))
    )


def first_id(network: Any, getter_name: str) -> str:
    table = get_table(network, getter_name)
    if table.empty or "id" not in table:
        return ""
    return str(table.iloc[0]["id"])


def run_voltage_initializer_then_lf(base: pd.Series) -> dict[str, Any]:
    pp = require_pypowsybl()
    case_name = str(base["case_name"])
    variant_name = str(base["variant_name"])
    row = common_row(base, "voltage_initializer_then_lf")
    row["utility_attempted"] = True
    start = time.perf_counter()
    try:
        network = prepare_network(case_name, variant_name, "advanced_voltage_initializer")
        vi_params = pp.voltage_initializer.VoltageInitializerParameters()
        vi_result = pp.voltage_initializer.run(network, vi_params)
        row["utility_status"] = f"VI_{type(vi_result).__name__}"
        scenario = matched_scenario(case_name)
        lf_result = ext.run_openloadflow(network, scenario.params, provider=scenario.provider)
        row["lf_after_utility_status"] = enum_name(lf_result.get("status"))
        row["lf_after_utility_valid_converged"] = valid_lf_result(lf_result)
        row["lf_after_utility_iteration_count"] = lf_result.get("iteration_count")
        row["utility_valid"] = bool(row["lf_after_utility_valid_converged"])
    except Exception as exc:
        row["utility_status"] = "EXCEPTION"
        row["utility_exception"] = repr(exc)
    row["utility_elapsed_seconds"] = time.perf_counter() - start
    return row


def solve_baseline_network(case_name: str, variant_name: str, parameter_scenario: str) -> tuple[Any, Any, dict[str, Any]]:
    network = prepare_network(case_name, variant_name, parameter_scenario)
    scenario = matched_scenario(case_name)
    variant = make_replacement_variants(include_bus_voltage=True)[variant_name]
    if variant.voltage_init == "PREVIOUS_VALUES":
        scenario = ext.force_previous_values(scenario)
    result = ext.run_openloadflow(network, scenario.params, provider=scenario.provider)
    return network, scenario, result


def run_security_ac(base: pd.Series) -> dict[str, Any]:
    pp = require_pypowsybl()
    case_name = str(base["case_name"])
    variant_name = str(base["variant_name"])
    row = common_row(base, "security_ac_single_contingency")
    row["utility_attempted"] = True
    start = time.perf_counter()
    try:
        network, scenario, lf_result = solve_baseline_network(case_name, variant_name, "advanced_security_ac")
        if not valid_lf_result(lf_result):
            row["utility_status"] = "BASE_LF_NOT_CONVERGED"
            row["lf_after_utility_status"] = enum_name(lf_result.get("status"))
            row["lf_after_utility_valid_converged"] = False
            return row

        branch_id = first_id(network, "get_lines")
        row["selected_branch_id"] = branch_id
        if not branch_id:
            row["utility_status"] = "NO_BRANCH"
            return row

        analysis = pp.security.create_analysis()
        analysis.add_single_element_contingency(branch_id)
        result = analysis.run_ac(network, parameters=scenario.params, provider=scenario.provider)
        pre_status = enum_name(getattr(getattr(result, "pre_contingency_result", None), "status", None))
        post_results = getattr(result, "post_contingency_results", {}) or {}
        post_statuses = [enum_name(getattr(post, "status", None)) for post in post_results.values()]
        row["security_pre_status"] = pre_status
        row["security_post_statuses"] = ";".join(status or "" for status in post_statuses)
        row["security_limit_violations"] = int(len(getattr(result, "limit_violations", [])))
        row["utility_status"] = "OK"
        row["utility_valid"] = pre_status == "CONVERGED" and all(status == "CONVERGED" for status in post_statuses)
    except Exception as exc:
        row["utility_status"] = "EXCEPTION"
        row["utility_exception"] = repr(exc)
    finally:
        row["utility_elapsed_seconds"] = time.perf_counter() - start
    return row


def run_sensitivity_ac(base: pd.Series) -> dict[str, Any]:
    pp = require_pypowsybl()
    case_name = str(base["case_name"])
    variant_name = str(base["variant_name"])
    row = common_row(base, "sensitivity_ac_branch_flow")
    row["utility_attempted"] = True
    start = time.perf_counter()
    try:
        network, scenario, lf_result = solve_baseline_network(case_name, variant_name, "advanced_sensitivity_ac")
        if not valid_lf_result(lf_result):
            row["utility_status"] = "BASE_LF_NOT_CONVERGED"
            row["lf_after_utility_status"] = enum_name(lf_result.get("status"))
            row["lf_after_utility_valid_converged"] = False
            return row

        branch_id = first_id(network, "get_lines")
        generator_id = first_id(network, "get_generators")
        row["selected_branch_id"] = branch_id
        row["selected_generator_id"] = generator_id
        if not branch_id or not generator_id:
            row["utility_status"] = "MISSING_BRANCH_OR_GENERATOR"
            return row

        analysis = pp.sensitivity.create_ac_analysis()
        analysis.set_branch_flow_factor_matrix([branch_id], [generator_id])
        result = analysis.run(network, parameters=scenario.params, provider=scenario.provider)
        matrix = result.get_sensitivity_matrix()
        reference = result.get_reference_flows()
        values = pd.to_numeric(matrix.stack(), errors="coerce") if isinstance(matrix, pd.DataFrame) else pd.Series(dtype=float)
        ref_values = (
            pd.to_numeric(reference.stack(), errors="coerce")
            if isinstance(reference, pd.DataFrame)
            else pd.Series(dtype=float)
        )
        row["sensitivity_values"] = int(values.notna().sum())
        row["sensitivity_max_abs"] = float(values.abs().max()) if not values.dropna().empty else None
        row["sensitivity_reference_flow"] = float(ref_values.iloc[0]) if not ref_values.dropna().empty else None
        row["utility_status"] = "OK"
        row["utility_valid"] = bool(row["sensitivity_values"])
    except Exception as exc:
        row["utility_status"] = "EXCEPTION"
        row["utility_exception"] = repr(exc)
    finally:
        row["utility_elapsed_seconds"] = time.perf_counter() - start
    return row


def planned_utility_rows(args: argparse.Namespace) -> list[tuple[str, pd.Series]]:
    base = baseline_rows()
    if args.case:
        base = base[base["case_name"].isin(args.case)]
    if args.variant:
        base = base[base["variant_name"].isin(args.variant)]

    planned: list[tuple[str, pd.Series]] = []
    utilities = args.utility or UTILITY_ORDER
    failures = base[~base["baseline_valid_converged"]]
    solved = base[base["baseline_valid_converged"]]
    for utility in utilities:
        if utility == "voltage_initializer_then_lf":
            planned.extend((utility, row) for _, row in failures.iterrows())
        elif utility in {"security_ac_single_contingency", "sensitivity_ac_branch_flow"}:
            planned.extend((utility, row) for _, row in solved.iterrows())
    return planned


def build_summary(rows: pd.DataFrame) -> list[dict[str, Any]]:
    if rows.empty:
        return []
    result: list[dict[str, Any]] = []
    for utility_name, group in rows.groupby("utility_name", sort=False):
        valid = bool_series(group["utility_valid"])
        exceptions = group["utility_exception"].notna() & group["utility_exception"].astype(str).ne("")
        result.append(
            {
                "utility_name": utility_name,
                "utility_label": UTILITY_LABELS.get(utility_name, utility_name),
                "n_attempted": int(bool_series(group["utility_attempted"]).sum()),
                "n_valid": int(valid.sum()),
                "n_exceptions": int(exceptions.sum()),
                "statuses": "; ".join(sorted(set(group["utility_status"].dropna().astype(str)))),
                "cases": "; ".join(sorted(set(group["case_label"].dropna().astype(str)))),
            }
        )
    return result


def plot_utility_heatmap(rows: pd.DataFrame) -> None:
    if rows.empty:
        return
    row_labels: list[str] = []
    for case in ext.CASE_NAMES:
        case_rows = rows[rows["case_name"].eq(case)]
        for variant in ext.VARIANT_ORDER:
            if case_rows[case_rows["variant_name"].eq(variant)].empty:
                continue
            row_labels.append(f"{ext.CASE_LABELS.get(case, case)} | {VARIANT_LABELS.get(variant, variant)}")

    if not row_labels:
        return

    values: list[list[float]] = []
    annotations: list[list[str]] = []
    for label in row_labels:
        case_label, variant_label = label.split(" | ", maxsplit=1)
        label_rows = rows[rows["case_label"].eq(case_label) & rows["variant_label"].eq(variant_label)]
        value_row: list[float] = []
        annotation_row: list[str] = []
        for utility in UTILITY_ORDER:
            cell = label_rows[label_rows["utility_name"].eq(utility)]
            if cell.empty:
                value_row.append(-1.0)
                annotation_row.append("")
                continue
            first = cell.iloc[0]
            if bool(first.get("utility_valid")):
                value_row.append(1.0)
                annotation_row.append("OK")
            elif str(first.get("utility_status")) == "EXCEPTION":
                value_row.append(0.0)
                annotation_row.append("ERR")
            else:
                value_row.append(0.0)
                annotation_row.append("N")
        values.append(value_row)
        annotations.append(annotation_row)

    cmap = mcolors.ListedColormap(["#eeeeee", "#b94b48", "#4f7f62"])
    norm = mcolors.BoundaryNorm([-1.5, -0.5, 0.5, 1.5], cmap.N)
    fig, ax = plt.subplots(figsize=(8.0, max(4.5, 0.35 * len(row_labels))))
    ax.imshow(values, cmap=cmap, norm=norm, aspect="auto")
    ax.set_xticks(range(len(UTILITY_ORDER)))
    ax.set_xticklabels([UTILITY_LABELS[name] for name in UTILITY_ORDER], rotation=25, ha="right")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=8)
    ax.set_title("PyPowSyBl advanced utility checks")
    for i, row in enumerate(annotations):
        for j, text in enumerate(row):
            ax.text(j, i, text, ha="center", va="center", color="white" if text else "black", fontsize=8)
    fig.subplots_adjust(left=0.38, right=0.98, bottom=0.22, top=0.90)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_DIR / "advanced_utility_status_heatmap.png", dpi=180)
    plt.close(fig)


def write_interpretation(rows: pd.DataFrame, summary: pd.DataFrame) -> None:
    lines = [
        "# PyPowSyBl Advanced Utility Analysis",
        "",
        "This pass uses the matched single-slack baseline split from the extended OpenLoadFlow configuration matrix.",
        "",
        "- `voltage_initializer.run` is tried only on matched-baseline failures, then the matched LF is retried if initialization succeeds.",
        "- `security.create_analysis().run_ac` is tried only on matched-baseline solved states with one deterministic line contingency.",
        "- `sensitivity.create_ac_analysis` is tried only on matched-baseline solved states using one branch-flow sensitivity against one generator active-power variable.",
        "",
        "## Utility Summary",
        "",
        "| Utility | Attempted | Valid | Exceptions | Statuses |",
        "|---|---:|---:|---:|---|",
    ]
    if summary.empty:
        lines.append("| none | 0 | 0 | 0 | none |")
    else:
        for _, row in summary.iterrows():
            lines.append(
                f"| {row['utility_label']} | {row['n_attempted']} | {row['n_valid']} | {row['n_exceptions']} | {row['statuses']} |"
            )

    vi_rows = rows[rows["utility_name"].eq("voltage_initializer_then_lf")] if not rows.empty else rows
    vi_exceptions = sorted(set(vi_rows["utility_exception"].dropna().astype(str))) if not vi_rows.empty else []
    if vi_exceptions:
        lines.extend(["", "## Voltage Initializer Finding", ""])
        lines.append("The voltage initializer could not be exercised in this environment. The observed exception was:")
        lines.append("")
        lines.append(f"`{vi_exceptions[0]}`")
        lines.append("")
        lines.append("So this pass does not yet prove whether voltage initialization can recover the failed LF starting points. It shows that AMPL/module configuration is needed first.")

    lines.extend(
        [
            "",
            "## Reading The Utility Rows",
            "",
            "- Security and sensitivity rows are capability probes, not final engineering studies.",
            "- A successful security row means the pre-contingency state and the selected single-line post-contingency state solved.",
            "- A successful sensitivity row means OpenLoadFlow produced the requested AC sensitivity matrix for the selected branch/generator pair.",
        ]
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "advanced_utility_interpretation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_artifacts() -> None:
    rows = load_existing(UTILITY_ROWS)
    summary_rows = build_summary(rows)
    summary = pd.DataFrame(summary_rows)
    write_csv(OUTPUT_DIR / "advanced_utility_summary.csv", summary_rows)
    # On this Windows/PyPowSyBl environment, saving the small utility heatmap can
    # trigger a native matplotlib crash after a voltage-initializer exception.
    # Keep the utility pass robust and rely on CSV/Markdown artifacts here.
    write_interpretation(rows, summary)


def run_matrix(args: argparse.Namespace) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    planned = planned_utility_rows(args)
    existing = pd.DataFrame() if args.fresh else load_existing(UTILITY_ROWS)
    completed = {row_key(row) for _, row in existing.iterrows()} if not existing.empty else set()
    rows = existing.to_dict("records") if not existing.empty else []

    runners = {
        "voltage_initializer_then_lf": run_voltage_initializer_then_lf,
        "security_ac_single_contingency": run_security_ac,
        "sensitivity_ac_branch_flow": run_sensitivity_ac,
    }
    n_run = 0
    for utility, base in planned:
        key = (str(base["case_name"]), str(base["variant_name"]), utility)
        if args.resume and key in completed:
            print(f"Skipping completed {base['case_name']} | {base['variant_name']} | {utility}")
            continue
        print(f"\n=== {base['case_name']} | {base['variant_name']} | {utility} ===")
        row = runners[utility](base)
        rows.append(row)
        completed.add(key)
        n_run += 1
        save_dataframe_with_retry(pd.DataFrame(rows), UTILITY_ROWS)
        print(
            f"utility_status={row['utility_status']} utility_valid={row['utility_valid']} "
            f"exception={row['utility_exception']}"
        )
        if args.max_rows and n_run >= args.max_rows:
            create_artifacts()
            print(f"Stopped after --max-rows={args.max_rows}")
            return

    create_artifacts()
    print(f"Wrote advanced utility artifacts to {OUTPUT_DIR}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run focused PyPowSyBl advanced utility checks.")
    parser.add_argument("--case", action="append", choices=ext.CASE_NAMES, help="Case to include; may be repeated.")
    parser.add_argument("--variant", action="append", choices=ext.VARIANT_ORDER, help="Variant to include; may be repeated.")
    parser.add_argument("--utility", action="append", choices=UTILITY_ORDER, help="Utility to include; may be repeated.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--fresh", action="store_true", help="Ignore any existing utility rows and overwrite the utility artifacts.")
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--artifacts-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.artifacts_only:
        create_artifacts()
        print(f"Wrote advanced utility artifacts to {OUTPUT_DIR}")
        return
    run_matrix(args)


if __name__ == "__main__":
    main()
