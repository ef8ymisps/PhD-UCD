"""Generator-outage analog of 65_run_dc_scopf_monolithic_branches.py --
solves the monolithic preventive DC-SCOPF for K^G = the FINAL generator-
outage contingency list per system -- topologies/<case>/
final_k_generators.csv (every active generator's own outage, minus
infeasible-alone minus conflicting-pair exclusions; see
72_run_generator_final_contingency_list.py, the single canonical source
both languages read). No islanding exclusion here -- a generator outage
never disconnects the network (only branches can), so that step simply
doesn't apply; see 72's own module docstring for the full argument.

Evidence written to results/scopf/dc/<case>/monolithic_generators/ -- SAME
three-file schema as monolithic_branches (own_models.src.analysis.
scopf_evidence), `language=python` distinguishing these rows from Julia's
own in the SAME shared files, and from monolithic_branches' own rows via
the `approach` column.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from generate_topology_diagrams import CASES, TOPOLOGIES_DIR, REPO_ROOT, load_case  # noqa: E402

from own_models.src.analysis.contingencies import generate_generator_contingencies  # noqa: E402
from own_models.src.analysis.scopf_evidence import (  # noqa: E402
    SCOPFSummaryRow,
    peak_memory_mb,
    scopf_stage_timing_rows,
    total_constraint_count,
    total_variable_count,
    write_scopf_evidence,
)
from own_models.src.opf.dc_scopf.model import build_dc_scopf_pyomo_model  # noqa: E402
from own_models.src.opf.dc_scopf.solve import solve_dc_scopf_pyomo  # noqa: E402

import pyomo.environ as pyo  # noqa: E402

RESULTS_ROOT = REPO_ROOT / "results" / "scopf"


def _n_binary_variables(model) -> int:
    """Count of binary Var components -- ALWAYS 0 for monolithic_branches
    (branch outages have no saturation MILP), but generator outages'
    water-filling response mechanism adds one z_i^k saturation binary per
    participating generator per contingency, so this is worth reporting
    unlike the branch-side script's hardcoded 0."""
    return sum(1 for v in model.component_data_objects(pyo.Var, descend_into=True) if v.is_binary())


def final_k_generator_ids(label: str) -> set[str]:
    """Generator ids in the final K^G, read straight out of
    topologies/<label>/final_k_generators.csv -- no re-derivation."""
    import csv

    path = TOPOLOGIES_DIR / label / "final_k_generators.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found -- run 72_run_generator_final_contingency_list.py for {label} first.")
    with path.open("r", newline="", encoding="utf-8") as handle:
        return {row["generator_id"] for row in csv.DictReader(handle)}


def anchor_pg(label: str) -> dict[str, float]:
    """The single canonical anchor dispatch every language's DC-SCOPF build
    must derive its automatic-response weights from -- see
    75_run_anchor_pg.py's own module docstring for why this is required
    (not just convenient) for correctness on an LP-degenerate anchor like
    60nordic's."""
    import csv

    path = TOPOLOGIES_DIR / label / "anchor_pg.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found -- run 75_run_anchor_pg.py for {label} first.")
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return {row["generator_id"]: float(row["pg"]) for row in reader}


def run_case(label: str, filename: str) -> None:
    case = load_case(filename)
    final_ids = final_k_generator_ids(label)
    K = [k for k in generate_generator_contingencies(case) if k.elements[0].element_id in final_ids]
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
        case_name=label, formulation="dc", approach="monolithic_generators",
        termination_status=result.termination_condition, objective=result.objective or float("nan"),
        n_buses=len(case.buses), n_branches=len(case.branches), n_generators=len(case.generators),
        n_contingencies_total=len(K), n_contingencies_branch=0, n_contingencies_generator=len(K),
        n_contingencies_skipped=len(skipped), n_variables=total_variable_count(model),
        n_binary_variables=_n_binary_variables(model),
        n_constraints=total_constraint_count(model),
        solve_iterations=result.solve_iterations, master_solve_time_s=t_solve, total_runtime_s=t_total,
    )
    stage_rows = scopf_stage_timing_rows(
        label, "monolithic_generators",
        {
            "model_build_including_anchor_solve": t_build,
            "master_solve": t_solve,
            "solver_reported_time": result.solver_reported_time_s,
            "total_runtime": t_total,
        },
    )
    output_dir = RESULTS_ROOT / "dc" / label / "monolithic_generators"
    write_scopf_evidence(output_dir, summary_row=summary, contingency_rows=[], stage_timing_rows=stage_rows)

    mem = peak_memory_mb()
    mem_str = f"{mem:6.1f}MB" if mem is not None else "   n/a"
    objective_str = "NaN" if result.objective is None else f"{result.objective:.4f}"
    print(
        f"{label:<10} |K^G|={len(K):3d} "
        f"status={result.termination_condition:<12} objective={objective_str:>12} "
        f"build={t_build:6.2f}s solve={t_solve:6.2f}s mem={mem_str}"
    )


def main() -> None:
    for label, filename in CASES:
        run_case(label, filename)


if __name__ == "__main__":
    main()
