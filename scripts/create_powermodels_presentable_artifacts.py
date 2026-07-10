"""Create focused PowerModels LF presentation artifacts from existing CSVs.

This script does not rerun PowerModels. It reads the existing outputs under
``results/presentable/powermodels_lf_replacement_sweep`` and creates compact
tables/figures for discussion.
"""

from __future__ import annotations

import csv
import math
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULT_ROOT = PROJECT_ROOT / "results" / "presentable" / "powermodels_lf_replacement_sweep"
OUTPUT_DIR = RESULT_ROOT / "presentation_artifacts"
FIGURE_DIR = OUTPUT_DIR / "figures"

VARIANT_ORDER = [
    "original",
    "BV_from_warmstart",
    "G_PGQG_from",
    "G_PGQG_from_plus_BV_from",
    "G_PGQG_from_plus_GVG_from",
]

REPLACEMENT_VARIANTS = [variant for variant in VARIANT_ORDER if variant != "original"]

VARIANT_LABELS = {
    "original": "Original",
    "BV_from_warmstart": "VS setpoints",
    "G_PGQG_from": "Gen power",
    "G_PGQG_from_plus_BV_from": "Gen power + VS",
    "G_PGQG_from_plus_GVG_from": "Gen power + VG",
}

CASE_LABELS = {
    "case300_ieee": "300 IEEE",
    "case1354_pegase": "1354 PEGASE",
    "case1888_rte": "1888 RTE",
    "case6470_rte": "6470 RTE",
    "case9241_pegase": "9241 PEGASE",
    "case13659_pegase": "13659 PEGASE",
    "case78484_epigrids": "78484 EpiGrid",
}

LARGE_CASES = [
    "case300_ieee",
    "case1354_pegase",
    "case1888_rte",
    "case6470_rte",
    "case9241_pegase",
    "case13659_pegase",
    "case78484_epigrids",
]

SMALL_CASE_MAX_BUSES = 118

SCENARIO_FOLDERS = {
    "base": "base_full",
    "api": "api",
    "sad": "sad",
}

MATPOWER_ROOT = PROJECT_ROOT / "data" / "raw" / "pglib" / "matpower_m"
MATPOWER_SCENARIO_DIRS = {
    "base": MATPOWER_ROOT / "base_case",
    "api": MATPOWER_ROOT / "api",
    "sad": MATPOWER_ROOT / "sad",
}

GEN_COLUMNS = {
    "bus": 0,
    "qmax": 3,
    "qmin": 4,
    "status": 7,
    "pmax": 8,
    "pmin": 9,
}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def as_bool(value: object) -> bool:
    return str(value).strip().lower() == "true"


def as_float(value: object) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value: object) -> int:
    number = as_float(value)
    return int(number) if number is not None else 0


def is_valid_solution(row: dict[str, str]) -> bool:
    exception = str(row.get("exception_message", "")).strip()
    return as_bool(row.get("converged")) and str(row.get("termination_status", "")).lower() == "true" and exception == ""


def load_ac_rows(scenario: str) -> list[dict[str, str]]:
    folder = SCENARIO_FOLDERS[scenario]
    rows = read_csv(RESULT_ROOT / folder / "powermodels_ac_pf_sweep_rows.csv")
    cleaned: list[dict[str, str]] = []
    for row in rows:
        # Defensive cleanup for an earlier contaminated SAD folder.
        if scenario == "sad" and row.get("case_name") == "case118_ieee" and row.get("n_load_notes") == "99":
            continue
        row = dict(row)
        row["scenario"] = scenario
        cleaned.append(row)
    return cleaned


def load_dc_rows(scenario: str) -> list[dict[str, str]]:
    folder = SCENARIO_FOLDERS[scenario]
    rows = read_csv(RESULT_ROOT / folder / "powermodels_dc_pf_sweep_rows.csv")
    for row in rows:
        row["scenario"] = scenario
    return rows


def matpower_path_for_case(scenario: str, case_name: str) -> Path | None:
    directory = MATPOWER_SCENARIO_DIRS[scenario]
    suffixes = {
        "base": [""],
        "api": ["__api", ""],
        "sad": ["__sad", ""],
    }[scenario]
    for suffix in suffixes:
        path = directory / f"pglib_opf_{case_name}{suffix}.m"
        if path.exists():
            return path
    return None


def parse_matpower_matrix(path: Path, matrix_name: str) -> list[list[float]]:
    start_re = re.compile(rf"^mpc\.{re.escape(matrix_name)}\s*=\s*\[")
    rows: list[list[float]] = []
    in_matrix = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not in_matrix and start_re.match(stripped):
            in_matrix = True
            continue
        if in_matrix and stripped.startswith("];"):
            break
        if not in_matrix:
            continue
        data_part = line.partition("%")[0].strip().rstrip(";").strip()
        if not data_part:
            continue
        values = data_part.split()
        try:
            rows.append([float(value) for value in values])
        except ValueError:
            continue
    return rows


def slack_limits_for_case(scenario: str, case_name: str, slack_bus: int) -> dict[str, object]:
    path = matpower_path_for_case(scenario, case_name)
    if path is None:
        return {
            "slack_limit_status": "case_file_missing",
            "slack_pmin": "",
            "slack_pmax": "",
            "slack_qmin": "",
            "slack_qmax": "",
            "n_online_slack_generators": 0,
            "case_file": "",
        }
    gen_rows = parse_matpower_matrix(path, "gen")
    matching = []
    for row in gen_rows:
        if len(row) <= max(GEN_COLUMNS.values()):
            continue
        bus = int(round(row[GEN_COLUMNS["bus"]]))
        status = int(round(row[GEN_COLUMNS["status"]]))
        if bus == slack_bus and status != 0:
            matching.append(row)
    if not matching:
        return {
            "slack_limit_status": "no_online_generator_at_slack_bus",
            "slack_pmin": "",
            "slack_pmax": "",
            "slack_qmin": "",
            "slack_qmax": "",
            "n_online_slack_generators": 0,
            "case_file": str(path.relative_to(PROJECT_ROOT)),
        }
    return {
        "slack_limit_status": "ok",
        "slack_pmin": sum(row[GEN_COLUMNS["pmin"]] for row in matching),
        "slack_pmax": sum(row[GEN_COLUMNS["pmax"]] for row in matching),
        "slack_qmin": sum(row[GEN_COLUMNS["qmin"]] for row in matching),
        "slack_qmax": sum(row[GEN_COLUMNS["qmax"]] for row in matching),
        "n_online_slack_generators": len(matching),
        "case_file": str(path.relative_to(PROJECT_ROOT)),
    }


def rows_by_case_variant(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    return {(row["case_name"], row["variant_name"]): row for row in rows}


def base_large_rows() -> list[dict[str, str]]:
    rows = load_ac_rows("base")
    return [row for row in rows if row.get("case_name") in LARGE_CASES]


def create_base_convergence_summary(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    summary: list[dict[str, object]] = []
    for variant in VARIANT_ORDER:
        variant_rows = [row for row in rows if row.get("variant_name") == variant]
        converged = sum(is_valid_solution(row) for row in variant_rows)
        total = len(variant_rows)
        summary.append(
            {
                "variant_name": variant,
                "n_large_cases": total,
                "n_valid_converged": converged,
                "convergence_rate": round(converged / total, 2) if total else "",
            }
        )
    return summary


def create_convergence_gain_table(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    by_cv = rows_by_case_variant(rows)
    output: list[dict[str, object]] = []
    for variant in REPLACEMENT_VARIANTS:
        both_converged: list[str] = []
        convergence_gain: list[str] = []
        convergence_loss: list[str] = []
        both_failed: list[str] = []
        for case in LARGE_CASES:
            original = by_cv.get((case, "original"))
            candidate = by_cv.get((case, variant))
            if original is None or candidate is None:
                continue
            original_ok = is_valid_solution(original)
            candidate_ok = is_valid_solution(candidate)
            if original_ok and candidate_ok:
                both_converged.append(case)
            elif not original_ok and candidate_ok:
                convergence_gain.append(case)
            elif original_ok and not candidate_ok:
                convergence_loss.append(case)
            else:
                both_failed.append(case)
        output.append(
            {
                "variant_name": variant,
                "n_both_converged": len(both_converged),
                "n_convergence_gain": len(convergence_gain),
                "n_convergence_loss": len(convergence_loss),
                "n_both_failed": len(both_failed),
                "convergence_gain_cases": "; ".join(convergence_gain),
                "convergence_loss_cases": "; ".join(convergence_loss),
            }
        )
    return output


def create_speedup_table(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    by_cv = rows_by_case_variant(rows)
    output: list[dict[str, object]] = []
    for case in LARGE_CASES:
        original = by_cv.get((case, "original"))
        if original is None or not is_valid_solution(original):
            continue
        original_time = as_float(original.get("solve_time_sec"))
        if original_time is None or original_time <= 0:
            continue
        for variant in REPLACEMENT_VARIANTS:
            candidate = by_cv.get((case, variant))
            if candidate is None or not is_valid_solution(candidate):
                continue
            candidate_time = as_float(candidate.get("solve_time_sec"))
            if candidate_time is None or candidate_time <= 0:
                continue
            output.append(
                {
                    "case_name": case,
                    "n_buses": as_int(candidate.get("n_buses")),
                    "variant_name": variant,
                    "original_solve_time_sec": round(original_time, 2),
                    "variant_solve_time_sec": round(candidate_time, 2),
                    "time_delta_sec": round(original_time - candidate_time, 2),
                    "speedup_vs_original": round(original_time / candidate_time, 2),
                }
            )
    return output


def create_scenario_comparison() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for scenario in ("base", "api", "sad"):
        ac_rows = load_ac_rows(scenario)
        by_case: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in ac_rows:
            by_case[row["case_name"]].append(row)
        for case, case_rows in sorted(by_case.items(), key=lambda item: as_int(item[1][0].get("n_buses"))):
            valid = sum(is_valid_solution(row) for row in case_rows)
            total = len(case_rows)
            times = [as_float(row.get("solve_time_sec")) for row in case_rows if is_valid_solution(row)]
            times = [time for time in times if time is not None]
            rows.append(
                {
                    "case_name": case,
                    "scenario": scenario,
                    "n_buses": as_int(case_rows[0].get("n_buses")),
                    "valid_ac_converged_variants": valid,
                    "total_ac_variants": total,
                    "ac_convergence_rate": round(valid / total, 2) if total else "",
                    "mean_valid_solve_time_sec": round(mean(times), 2) if times else "",
                    "median_valid_solve_time_sec": round(median(times), 2) if times else "",
                }
            )
    return rows


def create_dc_summary() -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for scenario in ("base", "api", "sad"):
        rows = load_dc_rows(scenario)
        by_variant: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            by_variant[row["variant_name"]].append(row)
        for variant in sorted(by_variant):
            variant_rows = by_variant[variant]
            available = sum(as_bool(row.get("variant_available")) for row in variant_rows)
            converged = sum(is_valid_solution(row) for row in variant_rows)
            output.append(
                {
                    "scenario": scenario,
                    "variant_name": variant,
                    "n_rows": len(variant_rows),
                    "n_available": available,
                    "n_valid_converged": converged,
                }
            )
    return output


def create_slack_feasibility_detail() -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    limit_cache: dict[tuple[str, str, int], dict[str, object]] = {}
    for scenario in ("base", "api", "sad"):
        for row in load_ac_rows(scenario):
            case_name = row.get("case_name", "")
            slack_bus = as_int(row.get("slack_bus"))
            valid = is_valid_solution(row)
            slack_pg = as_float(row.get("slack_pg"))
            slack_qg = as_float(row.get("slack_qg"))
            limits = {
                "slack_limit_status": "not_applicable_nonconverged",
                "slack_pmin": "",
                "slack_pmax": "",
                "slack_qmin": "",
                "slack_qmax": "",
                "n_online_slack_generators": 0,
                "case_file": "",
            }
            if valid and slack_bus:
                cache_key = (scenario, case_name, slack_bus)
                if cache_key not in limit_cache:
                    limit_cache[cache_key] = slack_limits_for_case(scenario, case_name, slack_bus)
                limits = limit_cache[cache_key]

            pmin = as_float(limits["slack_pmin"])
            pmax = as_float(limits["slack_pmax"])
            qmin = as_float(limits["slack_qmin"])
            qmax = as_float(limits["slack_qmax"])
            p_feasible = valid and slack_pg is not None and pmin is not None and pmax is not None and pmin <= slack_pg <= pmax
            q_feasible = valid and slack_qg is not None and qmin is not None and qmax is not None and qmin <= slack_qg <= qmax
            slack_feasible = p_feasible and q_feasible

            output.append(
                {
                    "scenario": scenario,
                    "case_name": case_name,
                    "n_buses": as_int(row.get("n_buses")),
                    "variant_name": row.get("variant_name", ""),
                    "valid_ac_solution": valid,
                    "slack_bus": slack_bus if slack_bus else "",
                    "slack_pg": "" if slack_pg is None else round(slack_pg, 2),
                    "slack_qg": "" if slack_qg is None else round(slack_qg, 2),
                    "slack_pmin": "" if pmin is None else round(pmin, 2),
                    "slack_pmax": "" if pmax is None else round(pmax, 2),
                    "slack_qmin": "" if qmin is None else round(qmin, 2),
                    "slack_qmax": "" if qmax is None else round(qmax, 2),
                    "n_online_slack_generators": limits["n_online_slack_generators"],
                    "slack_p_feasible": p_feasible,
                    "slack_q_feasible": q_feasible,
                    "slack_feasible": slack_feasible,
                    "slack_limit_status": limits["slack_limit_status"],
                    "case_file": limits["case_file"],
                }
            )
    return output


def create_slack_feasibility_summary(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    by_group: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_group[(str(row["scenario"]), str(row["variant_name"]))].append(row)
    for (scenario, variant), group_rows in sorted(by_group.items()):
        valid_rows = [row for row in group_rows if row["valid_ac_solution"]]
        output.append(
            {
                "scenario": scenario,
                "variant_name": variant,
                "n_rows": len(group_rows),
                "n_valid_ac_solutions": len(valid_rows),
                "n_slack_p_feasible": sum(bool(row["slack_p_feasible"]) for row in valid_rows),
                "n_slack_q_feasible": sum(bool(row["slack_q_feasible"]) for row in valid_rows),
                "n_slack_fully_feasible": sum(bool(row["slack_feasible"]) for row in valid_rows),
            }
        )
    return output


def create_small_system_ac_reliability_table() -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for scenario in ("base", "api", "sad"):
        rows = load_ac_rows(scenario)
        by_case: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            if as_int(row.get("n_buses")) <= SMALL_CASE_MAX_BUSES:
                by_case[row["case_name"]].append(row)
        for case, case_rows in sorted(by_case.items(), key=lambda item: as_int(item[1][0].get("n_buses"))):
            valid = sum(is_valid_solution(row) for row in case_rows)
            total = len(case_rows)
            output.append(
                {
                    "case_name": case,
                    "scenario": scenario,
                    "n_buses": as_int(case_rows[0].get("n_buses")),
                    "valid_ac_converged_variants": valid,
                    "total_ac_variants": total,
                    "all_variants_converged": valid == total,
                    "ac_convergence_rate": round(valid / total, 2) if total else "",
                }
            )
    return output


def create_small_system_ac_reliability_summary(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    by_scenario: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_scenario[str(row["scenario"])].append(row)
    output: list[dict[str, object]] = []
    for scenario in ("base", "api", "sad"):
        scenario_rows = by_scenario.get(scenario, [])
        total_variants = sum(int(row["total_ac_variants"]) for row in scenario_rows)
        total_valid = sum(int(row["valid_ac_converged_variants"]) for row in scenario_rows)
        output.append(
            {
                "scenario": scenario,
                "n_small_cases": len(scenario_rows),
                "n_ac_variant_runs": total_variants,
                "n_valid_converged_runs": total_valid,
                "all_small_case_runs_converged": total_variants == total_valid,
                "ac_convergence_rate": round(total_valid / total_variants, 2) if total_variants else "",
            }
        )
    return output


def plot_base_heatmap(rows: list[dict[str, str]]) -> None:
    by_cv = rows_by_case_variant(rows)
    values = []
    for case in LARGE_CASES:
        values.append([1 if is_valid_solution(by_cv.get((case, variant), {})) else 0 for variant in VARIANT_ORDER])
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.imshow(values, cmap=matplotlib.colors.ListedColormap(["#b94b48", "#4f7f62"]), vmin=0, vmax=1)
    ax.set_xticks(range(len(VARIANT_ORDER)))
    ax.set_xticklabels([VARIANT_LABELS[variant] for variant in VARIANT_ORDER], rotation=25, ha="right")
    ax.set_yticks(range(len(LARGE_CASES)))
    ax.set_yticklabels([CASE_LABELS.get(case, case) for case in LARGE_CASES])
    ax.set_title("Base large-case AC PF convergence")
    for i, row in enumerate(values):
        for j, value in enumerate(row):
            ax.text(j, i, "Y" if value else "N", ha="center", va="center", color="white", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "base_large_ac_convergence_heatmap.png", dpi=160)
    plt.close(fig)


def plot_base_convergence_bar(summary: list[dict[str, object]]) -> None:
    labels = [VARIANT_LABELS.get(str(row["variant_name"]), str(row["variant_name"])) for row in summary]
    values = [int(row["n_valid_converged"]) for row in summary]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(labels, values, color="#4f7f62")
    ax.set_ylabel("Valid converged large cases")
    ax.set_title("Base large-case convergence by variant")
    ax.set_ylim(0, max(values) + 1)
    ax.tick_params(axis="x", rotation=30)
    for index, value in enumerate(values):
        ax.text(index, value + 0.05, str(value), ha="center")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "base_large_ac_convergence_by_variant.png", dpi=160)
    plt.close(fig)


def plot_solve_time_vs_buses(rows: list[dict[str, str]]) -> None:
    valid_rows = [row for row in rows if is_valid_solution(row)]
    fig, ax = plt.subplots(figsize=(8, 4.8))
    for variant in VARIANT_ORDER:
        points = [row for row in valid_rows if row.get("variant_name") == variant]
        x = [as_int(row.get("n_buses")) for row in points]
        y = [as_float(row.get("solve_time_sec")) for row in points]
        pairs = [(xx, yy) for xx, yy in zip(x, y) if yy is not None and yy > 0 and xx > 0]
        if not pairs:
            continue
        ax.scatter([p[0] for p in pairs], [p[1] for p in pairs], label=variant, s=28)

    labelled_cases: set[str] = set()
    for row in valid_rows:
        case = row.get("case_name", "")
        buses = as_int(row.get("n_buses"))
        solve_time = as_float(row.get("solve_time_sec"))
        if case not in LARGE_CASES or solve_time is None or solve_time <= 0 or buses <= 0:
            continue
        if case in labelled_cases:
            continue
        labelled_cases.add(case)
        ax.annotate(
            CASE_LABELS.get(case, case),
            (buses, solve_time),
            textcoords="offset points",
            xytext=(5, 4),
            fontsize=7,
        )

    trend_points = [
        (as_int(row.get("n_buses")), as_float(row.get("solve_time_sec")))
        for row in valid_rows
    ]
    trend_points = [(x, y) for x, y in trend_points if x > 0 and y is not None and y > 0]
    if len(trend_points) >= 2:
        xs = [math.log10(x) for x, _ in trend_points]
        ys = [math.log10(y) for _, y in trend_points]
        x_mean = mean(xs)
        y_mean = mean(ys)
        denom = sum((x - x_mean) ** 2 for x in xs)
        if denom > 0:
            slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denom
            intercept = y_mean - slope * x_mean
            x_min, x_max = min(x for x, _ in trend_points), max(x for x, _ in trend_points)
            line_x = [x_min, x_max]
            line_y = [10 ** (intercept + slope * math.log10(x)) for x in line_x]
            ax.plot(line_x, line_y, color="black", linewidth=1.2, linestyle="--", label="log-log trend")

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Number of buses")
    ax.set_ylabel("Solve time, valid AC runs only (s)")
    ax.set_title("Base AC solve time versus system size")
    handles, labels = ax.get_legend_handles_labels()
    labels = [VARIANT_LABELS.get(label, label) for label in labels]
    ax.legend(handles, labels, fontsize=7)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "base_valid_ac_solve_time_vs_buses.png", dpi=160)
    plt.close(fig)


def plot_scenario_heatmap() -> None:
    rows = []
    for scenario in ("base", "api", "sad"):
        rows.extend(load_ac_rows(scenario))
    by_key = {(row["case_name"], row["scenario"], row["variant_name"]): row for row in rows}
    cases = [
        case
        for case in LARGE_CASES
        if any(row["case_name"] == case for row in rows)
    ]
    y_labels = []
    values = []
    for case in cases:
        for scenario in ("base", "api", "sad"):
            if not any(row["case_name"] == case and row["scenario"] == scenario for row in rows):
                continue
            y_labels.append(f"{CASE_LABELS.get(case, case)} {scenario.upper()}")
            values.append([1 if is_valid_solution(by_key.get((case, scenario, variant), {})) else 0 for variant in VARIANT_ORDER])
    fig, ax = plt.subplots(figsize=(9, max(5, 0.42 * len(y_labels))))
    ax.imshow(values, cmap=matplotlib.colors.ListedColormap(["#b94b48", "#4f7f62"]), vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(VARIANT_ORDER)))
    ax.set_xticklabels([VARIANT_LABELS[variant] for variant in VARIANT_ORDER], rotation=25, ha="right")
    ax.set_yticks(range(len(y_labels)))
    ax.set_yticklabels(y_labels, fontsize=7)
    ax.set_title("Large/problematic AC convergence by scenario")
    for i, row in enumerate(values):
        for j, value in enumerate(row):
            ax.text(j, i, "Y" if value else "N", ha="center", va="center", color="white", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "base_api_sad_ac_convergence_heatmap.png", dpi=160)
    plt.close(fig)


def plot_small_system_reliability(rows: list[dict[str, object]]) -> None:
    labels = [f"{row['case_name']} {row['scenario']}" for row in rows]
    values = [float(row["ac_convergence_rate"]) for row in rows]
    fig, ax = plt.subplots(figsize=(8.5, max(4, 0.28 * len(labels))))
    ax.barh(labels, values, color="#4f7f62")
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("AC convergence rate across variants")
    ax.set_title(f"Small-system AC PF reliability (<= {SMALL_CASE_MAX_BUSES} buses)")
    ax.axvline(1.0, color="black", linewidth=1.0, linestyle="--")
    ax.tick_params(axis="y", labelsize=7)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "small_system_ac_reliability.png", dpi=160)
    plt.close(fig)


def plot_small_system_solve_time_comparison() -> None:
    selected_variants = ["original", "BV_from_warmstart", "G_PGQG_from_plus_GVG_from"]
    scenarios = ["base", "api", "sad"]
    rows = []
    for scenario in scenarios:
        for row in load_ac_rows(scenario):
            if (
                as_int(row.get("n_buses")) <= SMALL_CASE_MAX_BUSES
                and row.get("variant_name") in selected_variants
                and is_valid_solution(row)
            ):
                rows.append(row)

    cases = sorted({row["case_name"] for row in rows}, key=lambda case: max(as_int(row.get("n_buses")) for row in rows if row["case_name"] == case))
    if not cases:
        return

    floor_time = 0.005
    offsets = {
        "original": -0.18,
        "BV_from_warmstart": 0.0,
        "G_PGQG_from_plus_GVG_from": 0.18,
    }
    colors = {
        "original": "#4c78a8",
        "BV_from_warmstart": "#f58518",
        "G_PGQG_from_plus_GVG_from": "#54a24b",
    }

    def draw_scenario(ax: plt.Axes, scenario: str, show_ylabel: bool) -> None:
        scenario_rows = [row for row in rows if row["scenario"] == scenario]
        by_key = {(row["case_name"], row["variant_name"]): row for row in scenario_rows}
        x_positions = list(range(len(cases)))
        for variant in selected_variants:
            values = []
            positions = []
            for index, case in enumerate(cases):
                row = by_key.get((case, variant))
                if row is None:
                    continue
                solve_time = as_float(row.get("solve_time_sec"))
                if solve_time is None:
                    continue
                plotted_time = solve_time if solve_time > 0 else floor_time
                values.append(plotted_time)
                positions.append(index + offsets[variant])
            ax.scatter(positions, values, color=colors[variant], label=VARIANT_LABELS[variant], s=46)
            for position, value in zip(positions, values):
                if value >= 0.01:
                    ax.text(position, value * 1.08, f"{value:.2f}", ha="center", va="bottom", fontsize=7)

        ax.set_title(scenario.upper())
        ax.set_xticks(x_positions)
        ax.set_xticklabels([str(max(as_int(row.get("n_buses")) for row in rows if row["case_name"] == case)) for case in cases])
        ax.set_xlabel("Bus count")
        ax.set_yscale("log")
        ax.set_ylim(floor_time * 0.75, 0.25)
        ax.grid(axis="y", alpha=0.25)
        if show_ylabel:
            ax.set_ylabel("Solve time, valid AC runs only (s, log scale)")
        ax.text(
            0.02,
            0.96,
            "0.00 s entries plotted as <0.01",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=7,
            color="#555555",
        )

    fig, axes = plt.subplots(1, len(scenarios), figsize=(12.5, 4.6), sharey=True)
    for ax, scenario in zip(axes, scenarios):
        draw_scenario(ax, scenario, show_ylabel=(ax is axes[0]))

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(selected_variants), frameon=False, bbox_to_anchor=(0.5, -0.04))
    fig.suptitle("Small-system AC solve time comparison", y=0.98)
    fig.tight_layout(rect=(0, 0.14, 1, 0.93))
    fig.savefig(FIGURE_DIR / "small_system_ac_solve_time_base_api_sad.png", dpi=160, bbox_inches="tight")
    plt.close(fig)

    for scenario in scenarios:
        scenario_fig, scenario_ax = plt.subplots(figsize=(7.2, 4.8))
        draw_scenario(scenario_ax, scenario, show_ylabel=True)
        scenario_ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.28), ncol=len(selected_variants), frameon=False, fontsize=9)
        scenario_fig.tight_layout(rect=(0, 0.12, 1, 1))
        scenario_fig.savefig(FIGURE_DIR / f"small_system_ac_solve_time_{scenario}.png", dpi=160, bbox_inches="tight")
        plt.close(scenario_fig)


def plot_slack_feasibility_summary(rows: list[dict[str, object]]) -> None:
    large_rows = [row for row in rows if str(row["case_name"]) in LARGE_CASES and row["valid_ac_solution"]]
    if not large_rows:
        return
    labels = []
    feasible_counts = []
    infeasible_counts = []
    by_scenario: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in large_rows:
        by_scenario[str(row["scenario"])].append(row)
    for scenario in ("base", "api", "sad"):
        group = by_scenario.get(scenario, [])
        labels.append(scenario)
        feasible_counts.append(sum(bool(row["slack_feasible"]) for row in group))
        infeasible_counts.append(sum(not bool(row["slack_feasible"]) for row in group))
    fig, ax = plt.subplots(figsize=(6.5, 4.0))
    ax.bar(labels, feasible_counts, label="slack feasible", color="#4f7f62")
    ax.bar(labels, infeasible_counts, bottom=feasible_counts, label="slack infeasible", color="#b94b48")
    ax.set_ylabel("Valid large-case AC runs")
    ax.set_title("Slack feasibility among valid large-case AC solutions")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "large_ac_slack_feasibility_summary.png", dpi=160)
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)

    base_rows = base_large_rows()
    base_summary = create_base_convergence_summary(base_rows)
    gain_table = create_convergence_gain_table(base_rows)
    speedup_table = create_speedup_table(base_rows)
    scenario_comparison = create_scenario_comparison()
    dc_summary = create_dc_summary()
    small_reliability = create_small_system_ac_reliability_table()
    small_reliability_summary = create_small_system_ac_reliability_summary(small_reliability)
    slack_detail = create_slack_feasibility_detail()
    slack_summary = create_slack_feasibility_summary(slack_detail)

    write_csv(OUTPUT_DIR / "base_large_ac_convergence_summary.csv", base_summary)
    write_csv(OUTPUT_DIR / "base_large_ac_convergence_gain_table.csv", gain_table)
    write_csv(OUTPUT_DIR / "base_large_ac_speedup_when_both_converged.csv", speedup_table)
    write_csv(OUTPUT_DIR / "base_api_sad_ac_case_comparison.csv", scenario_comparison)
    write_csv(OUTPUT_DIR / "dc_convergence_summary.csv", dc_summary)
    write_csv(OUTPUT_DIR / "small_system_ac_reliability.csv", small_reliability)
    write_csv(OUTPUT_DIR / "small_system_ac_reliability_summary.csv", small_reliability_summary)
    write_csv(OUTPUT_DIR / "slack_feasibility_detail.csv", slack_detail)
    write_csv(OUTPUT_DIR / "slack_feasibility_summary.csv", slack_summary)

    plot_base_heatmap(base_rows)
    plot_base_convergence_bar(base_summary)
    plot_solve_time_vs_buses(load_ac_rows("base"))
    plot_scenario_heatmap()
    plot_small_system_reliability(small_reliability)
    plot_small_system_solve_time_comparison()
    plot_slack_feasibility_summary(slack_detail)

    print(f"Wrote presentation artifacts to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
