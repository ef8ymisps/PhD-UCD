"""Build compact agreement-audit tables for the Python PM-like AC OPF sweep."""

from __future__ import annotations

from pathlib import Path
import shutil

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SWEEP_ROOT = ROOT / "results" / "own_vs_powermodels" / "python_pm_like"
ORGANIZED_ROOT = (
    ROOT
    / "results"
    / "own_vs_powermodels"
    / "_organized_for_meeting"
    / "08_python_ac_opf_debugging"
)

SUMMARY_PATH = SWEEP_ROOT / "summary.csv"
STATUS_BY_CASE_PATH = SWEEP_ROOT / "status_by_case.csv"
NONOPTIMAL_PATH = SWEEP_ROOT / "nonoptimal_status_details.csv"
AGREEMENT_AUDIT_PATH = SWEEP_ROOT / "agreement_audit.csv"
AGREEMENT_COUNTS_PATH = SWEEP_ROOT / "agreement_counts.csv"

PM_SOLVED = {"OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED"}
OWN_SOLVED = {"OPTIMAL", "LOCALLYOPTIMAL", "GLOBALLYOPTIMAL"}


def status_family(status: str, *, is_pm: bool) -> str:
    text = str(status or "").strip()
    upper = text.upper()
    if not text:
        return "unknown"
    if is_pm and upper in PM_SOLVED:
        return "solved_feasible"
    if not is_pm and upper in OWN_SOLVED:
        return "solved_feasible"
    if "INFEASIBLE" in upper:
        return "infeasible"
    if "ITERATION" in upper or "MAXITER" in upper or "TIME_LIMIT" in upper or "MAXIMUMCPU" in upper:
        return "solver_limit"
    if "INTERNALSOLVERERROR" in upper or "ERROR" in upper:
        return "solver_error"
    if "MISSING_REFERENCE" in upper or "UNKNOWN" == upper:
        return "missing_or_unknown"
    return "other_not_solved"


def agreement_row(row: pd.Series) -> dict[str, object]:
    own_status = str(row.get("own_status", ""))
    pm_status = str(row.get("pm_status", ""))
    own_family = status_family(own_status, is_pm=False)
    pm_family = status_family(pm_status, is_pm=True)
    has_reference = own_status != "MISSING_REFERENCE" and pm_family != "missing_or_unknown"

    if not has_reference:
        agreement = "blocked_missing_powermodels_reference"
        strict_ok = False
        broad_ok = False
        action = "Backfill the missing PowerModels reference JSON, then rerun this scenario."
    elif own_family == "solved_feasible" and pm_family == "solved_feasible":
        agreement = "both_solved_feasible"
        strict_ok = True
        broad_ok = True
        action = "Use objective and variable-difference columns for numerical agreement."
    elif own_family != "solved_feasible" and pm_family != "solved_feasible":
        broad_ok = True
        if own_family == pm_family:
            agreement = f"same_non_solved_family:{own_family}"
            strict_ok = True
            action = "No dispatch table expected; both models report the same non-solved family."
        else:
            agreement = f"both_not_solved_different_exit:{own_family}_vs_{pm_family}"
            strict_ok = False
            action = "Treat as broad feasibility agreement only; rerun with aligned limits/options if exact solver exit matters."
    else:
        agreement = f"solved_status_mismatch:{own_family}_vs_{pm_family}"
        strict_ok = False
        broad_ok = False
        action = "Investigate immediately; one model found a feasible OPF point and the other did not."

    return {
        "case": row.get("case", ""),
        "scenario": row.get("scenario", ""),
        "own_status": own_status,
        "pm_status": pm_status,
        "own_family": own_family,
        "pm_family": pm_family,
        "has_powermodels_reference": has_reference,
        "agreement_class": agreement,
        "strict_status_agreement_ok": strict_ok,
        "broad_feasibility_agreement_ok": broad_ok,
        "action_required": not strict_ok,
        "recommended_action": action,
        "own_objective": row.get("own_objective", ""),
        "pm_objective": row.get("pm_objective", ""),
        "objective_diff": row.get("objective_diff", ""),
        "objective_rel_diff": row.get("objective_rel_diff", ""),
        "own_solve_time_sec": row.get("own_solve_time_sec", ""),
        "pm_solve_time_sec": row.get("pm_solve_time_sec", ""),
        "own_solve_iterations": row.get("own_solve_iterations", ""),
        "pm_solve_iterations": row.get("pm_solve_iterations", ""),
        "error": row.get("error", ""),
    }


def write_status_by_case(summary: pd.DataFrame) -> None:
    statuses = ["optimal", "infeasible", "maxIterations", "MISSING_REFERENCE", "internalSolverError"]
    rows = []
    for case, group in summary.groupby("case", sort=True):
        out = {"case": case, "total": len(group)}
        for status in statuses:
            out[status] = int((group["own_status"] == status).sum())
        rows.append(out)
    pd.DataFrame(rows).to_csv(STATUS_BY_CASE_PATH, index=False)


def main() -> None:
    summary = pd.read_csv(SUMMARY_PATH, dtype=str, keep_default_na=False)
    audit = pd.DataFrame([agreement_row(row) for _, row in summary.iterrows()])
    audit.to_csv(AGREEMENT_AUDIT_PATH, index=False)

    counts = (
        audit.groupby(["agreement_class", "strict_status_agreement_ok", "broad_feasibility_agreement_ok"], dropna=False)
        .size()
        .reset_index(name="n_scenarios")
        .sort_values(["strict_status_agreement_ok", "broad_feasibility_agreement_ok", "agreement_class"])
    )
    counts.to_csv(AGREEMENT_COUNTS_PATH, index=False)

    write_status_by_case(summary)
    summary.loc[
        summary["own_status"].ne("optimal") & summary["own_status"].ne("infeasible"),
        ["case", "scenario", "own_status", "pm_status", "error"],
    ].to_csv(NONOPTIMAL_PATH, index=False)

    ORGANIZED_ROOT.mkdir(parents=True, exist_ok=True)
    for path in (
        SUMMARY_PATH,
        SWEEP_ROOT / "solver_diagnostics.csv",
        STATUS_BY_CASE_PATH,
        NONOPTIMAL_PATH,
        AGREEMENT_AUDIT_PATH,
        AGREEMENT_COUNTS_PATH,
    ):
        if path.exists():
            shutil.copy2(path, ORGANIZED_ROOT / f"python_pm_like_full_sweep_{path.name}")

    print(f"Agreement audit: {AGREEMENT_AUDIT_PATH}")
    print(f"Agreement counts: {AGREEMENT_COUNTS_PATH}")
    print(f"Status by case: {STATUS_BY_CASE_PATH}")


if __name__ == "__main__":
    main()
