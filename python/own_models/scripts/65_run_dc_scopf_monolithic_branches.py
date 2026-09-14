"""Python analog of julia/own_models/scripts/82_run_dc_scopf_monolithic_
branches.jl -- solves the monolithic preventive DC-SCOPF for K = the
FINAL, already-corrected contingency list per system --
topologies/<case>/final_k.csv (Total branch/transformer contingencies
minus islanding minus infeasible-alone minus conflicting-pair exclusions;
see 62_run_final_contingency_list.py, the single canonical source both
languages read). Islanding contingencies are ALWAYS out of scope for
preventive SCOPF, so that is not a separate "approach" variant worth
naming -- just "monolithic_branches" (LINE AND TRANSFORMER outages
together).

Evidence written to results/scopf/dc/<case>/monolithic_branches/ -- SAME
three-file schema as the Julia side (own_models.src.analysis.
scopf_evidence, a direct port of scopf_evidence.jl), `language=python`
distinguishing these rows from Julia's own in the SAME shared files.
`solve_iterations` here comes from HiGHS's console log (regex-parsed --
see own_models.src.opf.solver_iterations's own docstring for why; Julia's
side uses a native MOI attribute instead, a genuine capability
difference between the two solver interfaces, not a port gap).

`stage_timings.csv`'s `master_solve` is the wall-clock around the WHOLE
Pyomo `opt.solve()` call -- for Python this ALSO includes Pyomo's own
model-to-HiGHS translation overhead (writing the LP out, reading the
solution back), which is NOT part of the solver's own work. Confirmed
directly on 118_ieee: HiGHS itself reports ~1.4s (comparable to Julia's
own master_solve), while `master_solve` here is ~20s -- almost all of
the gap is that translation overhead, not the LP solver doing more work.
`solver_reported_time` (HiGHS's own number, parsed from its log) is
recorded ALONGSIDE `master_solve`, not instead of it, so both the honest
end-to-end number and the solver-only number are available.
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from generate_topology_diagrams import CASES, TOPOLOGIES_DIR, REPO_ROOT, load_case  # noqa: E402

from own_models.src.analysis.contingencies import generate_branch_contingencies  # noqa: E402
from own_models.src.analysis.scopf_evidence import (  # noqa: E402
    SCOPFSummaryRow,
    peak_memory_mb,
    scopf_stage_timing_rows,
    total_constraint_count,
    total_variable_count,
    write_scopf_evidence,
)
from own_models.src.data.branch_classification import is_transformer  # noqa: E402
from own_models.src.opf.dc_scopf.model import build_dc_scopf_pyomo_model  # noqa: E402
from own_models.src.opf.dc_scopf.solve import solve_dc_scopf_pyomo  # noqa: E402

RESULTS_ROOT = REPO_ROOT / "results" / "scopf"


def final_k_branch_ids(label: str) -> set[str]:
    """Branch ids in the final K, read straight out of
    topologies/<label>/final_k.csv -- no re-derivation."""
    path = TOPOLOGIES_DIR / label / "final_k.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found -- run 62_run_final_contingency_list.py for {label} first.")
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return {row["branch_id"] for row in reader}


def anchor_pg(label: str) -> dict[str, float]:
    """The single canonical anchor dispatch every language's DC-SCOPF build
    must derive its automatic-response weights from -- see
    75_run_anchor_pg.py's own module docstring for why this is required
    (not just convenient) for correctness on an LP-degenerate anchor like
    60nordic's."""
    path = TOPOLOGIES_DIR / label / "anchor_pg.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found -- run 75_run_anchor_pg.py for {label} first.")
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return {row["generator_id"]: float(row["pg"]) for row in reader}


def run_case(label: str, filename: str) -> None:
    case = load_case(filename)
    final_ids = final_k_branch_ids(label)
    K = [k for k in generate_branch_contingencies(case) if k.outaged_branch_ids[0] in final_ids]
    branch_by_id = {b.id: b for b in case.branches}
    n_line = sum(1 for k in K if not is_transformer(case, branch_by_id[k.outaged_branch_ids[0]]))
    n_trans = len(K) - n_line
    anchor = anchor_pg(label)

    t_total_start = time.time()
    t0 = time.time()
    model, skipped = build_dc_scopf_pyomo_model(case, K, anchor_pg=anchor)
    t_build = time.time() - t0
    t0 = time.time()
    _model, result, _skipped2 = solve_dc_scopf_pyomo(case, K, anchor_pg=anchor)
    t_solve = time.time() - t0
    t_total = time.time() - t_total_start

    summary = SCOPFSummaryRow(
        case_name=label, formulation="dc", approach="monolithic_branches",
        termination_status=result.termination_condition, objective=result.objective or float("nan"),
        n_buses=len(case.buses), n_branches=len(case.branches), n_generators=len(case.generators),
        n_contingencies_total=len(K), n_contingencies_branch=len(K), n_contingencies_generator=0,
        n_contingencies_skipped=len(skipped), n_variables=total_variable_count(model),
        n_binary_variables=0, n_constraints=total_constraint_count(model),
        solve_iterations=result.solve_iterations, master_solve_time_s=t_solve, total_runtime_s=t_total,
    )
    # NOT scopf_contingency_rows -- contingencies.csv would be entirely
    # redundant with topologies/<system>/final_k.csv and
    # contingency_screening.csv here (both written ONCE, not per-language,
    # by 62_run_final_contingency_list.py) and worse: status/skip_reason
    # are always constant ("enforced"/blank), since K is already the
    # pre-filtered final_k, and separately writing it from BOTH this
    # script and Julia's own into the SAME shared file produced two
    # confusing near-identical blocks (same branches, differently
    # formatted ids -- "branch_2" here vs "outage::line::2" there) with no
    # language column to explain why. That schema stays available in
    # scopf_evidence.py's own scopf_contingency_rows for whenever a real
    # decomposition approach needs to track PER-CONTINGENCY enforced/
    # filtered status that actually varies -- just not exercised here.
    stage_rows = scopf_stage_timing_rows(
        label, "monolithic_branches",
        {
            "model_build_including_anchor_solve": t_build,
            "master_solve": t_solve,
            # HiGHS's OWN reported time -- NOT the same as "master_solve"
            # above, which is the wall-clock around the whole opt.solve()
            # call and therefore also includes Pyomo's own model-to-HiGHS
            # translation overhead (writing the LP out, reading the
            # solution back) -- confirmed directly to be the dominant cost
            # at 118_ieee's scale (HiGHS itself: ~1.4s; master_solve
            # wall-clock: ~20s). Kept alongside, not instead of,
            # master_solve -- see this script's own module docstring.
            "solver_reported_time": result.solver_reported_time_s,
            "total_runtime": t_total,
        },
    )
    output_dir = RESULTS_ROOT / "dc" / label / "monolithic_branches"
    write_scopf_evidence(output_dir, summary_row=summary, contingency_rows=[], stage_timing_rows=stage_rows)

    mem = peak_memory_mb()
    mem_str = f"{mem:6.1f}MB" if mem is not None else "   n/a"
    objective_str = "NaN" if result.objective is None else f"{result.objective:.4f}"
    print(
        f"{label:<10} |K|={len(K):3d} (lines={n_line:3d}, trans={n_trans:2d}) "
        f"status={result.termination_condition:<12} objective={objective_str:>12} "
        f"build={t_build:6.2f}s solve={t_solve:6.2f}s mem={mem_str}"
    )


def main() -> None:
    for label, filename in CASES:
        run_case(label, filename)


if __name__ == "__main__":
    main()
