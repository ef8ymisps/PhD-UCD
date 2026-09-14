"""Compute and persist the SINGLE canonical anchor dispatch (plain DC-OPF
`pg`) that every monolithic preventive DC-SCOPF build -- both languages,
both branch- and generator-outage contingency sets -- must use to derive
its fixed automatic-response weights `gamma_g` (generator_response_weights,
opf/dc_scopf/automatic_response.jl / .py).

Why this is needed (not just a convenience): `gamma_g` is a fixed
CONSTRAINT coefficient, derived once from the plain anchor DC-OPF's own
solved `pg`, not re-derived from the SCOPF model's own (still-unsolved) Pg
-- see `build_dc_scopf_pyomo_model`'s/`build_dc_scopf_model`'s own
docstrings. When neither language is told what anchor to use, each solves
its OWN plain DC-OPF independently. For most systems that anchor DC-OPF has
a unique optimal dispatch, so both languages land on the same vertex
regardless. But 60nordic's plain DC-OPF is itself LP-degenerate (multiple
dispatch vertices share the same optimal cost) -- confirmed directly:
Python and Julia's independently-computed anchors have different `pg`
values AND different nonzero participant sets, which propagates into
genuinely different `gamma_g` per language, which means the two languages'
generator-outage DC-SCOPF are mathematically DIFFERENT problems for
60nordic specifically (real objective gap, ~0.33%, not solver noise or MIP
gap -- confirmed after switching both languages to Gurobi, see
results/scopf/dc/60nordic/monolithic_generators/summary.csv).

The fix mirrors 62_run_final_contingency_list.py's own pattern for
`final_k.csv`: compute the canonical artifact ONCE (here), persist it,
have every other script (Python or Julia) READ it rather than re-derive
it. WHICH vertex gets picked doesn't matter for correctness (any optimal
anchor gives a valid, if arbitrary, set of response weights) -- only that
BOTH languages use the SAME one. Computed here via Python's own
`solve_dc_opf_pyomo` (which itself now defaults to Gurobi via appsi, see
own_models.src.opf.dc.solve.make_solver) purely because this is a Python
script; the specific vertex Gurobi happens to return is not the point.

Writes topologies/<system>/anchor_pg.csv -- columns `generator_id`, `pg`
(one row per generator, plain DC-OPF units already applied, i.e. MW).
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from generate_topology_diagrams import CASES, TOPOLOGIES_DIR, load_case  # noqa: E402

from own_models.src.opf.dc.solve import solve_dc_opf_pyomo  # noqa: E402


def run_case(label: str, filename: str) -> None:
    case = load_case(filename)
    _model, _raw, result = solve_dc_opf_pyomo(case)
    if not result.success:
        raise RuntimeError(
            f"Anchor DC-OPF for {label} did not reach optimal "
            f"(termination={result.termination_condition}); cannot write a canonical anchor."
        )

    out_dir = TOPOLOGIES_DIR / label
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "anchor_pg.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["generator_id", "pg"])
        for gid in sorted(result.pg.keys()):
            writer.writerow([gid, result.pg[gid]])

    print(f"{label:<10} objective={result.objective:>14.4f} -> {out_path}")


def main() -> None:
    for label, filename in CASES:
        run_case(label, filename)


if __name__ == "__main__":
    main()
