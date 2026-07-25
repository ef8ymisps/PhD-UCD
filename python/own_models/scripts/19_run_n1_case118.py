"""Run N-1 branch and generator post-contingency screening.

Per the tool decision (docs/formulations/security_constraints.md): the own
model is OPF-only. Post-contingency power flow for Python N-1 screening uses
pypowsybl + OpenLoadFlow exclusively (use_reactive_limits=True for PV->PQ
switching, distributed_slack=False with the slack pinned to
case.slack_bus_id, matching the OPF's single-slack convention) -- the own
Newton-Raphson PF path in post_contingency.py is not used here.

Loads cases directly from their matpower .mat files since N-1 screening is a
self-consistency check that doesn't need a PowerModels reference.

Default case list: case118 (original scope) plus case300 (status doc §7
item 6, extended 2026-07-11). case1354_pegase was tried on the Julia side
and dropped the same day -- 2,251 contingencies took over 5.7 hours (still
actively computing, not hung) with no sign of finishing, dramatically
slower per-contingency than case118/case300 -- so it's not attempted here
either. case1888_rte/case6470_rte are deliberately excluded -- 2,828/10,335
contingencies each is a lot of post-contingency power flow solves for what
those two RTE cases would add on top of what's already been learned about
them from the warm-start ablation (docs/notes/solver_tolerances.md).

Cases with phase-shifting transformers are skipped for this (Python)
pipeline specifically: `build_pypowsybl_network_from_powercase` only ever
builds plain two-winding transformers, never pypowsybl's PhaseTapChanger,
so it deliberately raises on a non-zero `branch.shift` rather than silently
producing a wrong network (see `pypowsybl_security_validation.py`'s
`SHIFT_TOLERANCE` check). `case300_ieee` has 1 shifted branch and would
hit this too, except it's still included below (Julia's PowerModels-based
N-1 pipeline has no such gap and screens it fine) -- the skip logic just
means Python's run will log and skip it rather than crash.
"""

from __future__ import annotations

from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
REPO_ROOT = PYTHON_DIR.parent
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat  # noqa: E402
from own_models.src.analysis.pypowsybl_security_validation import (  # noqa: E402
    SHIFT_TOLERANCE,
    run_pypowsybl_n1_screening,
)
from own_models.src.opf.ac import solve_ac_opf_pyomo  # noqa: E402

DEFAULT_CASE_FILES = [
    "pglib_opf_case118_ieee",
    "pglib_opf_case300_ieee",
]


def phase_shifted_branch_ids(case) -> list[str]:
    """Branch ids the pypowsybl v1 builder can't represent (non-zero shift)."""
    return [branch.id for branch in case.branches if abs(branch.shift) > SHIFT_TOLERANCE]


def screen_one_case(case_file: str, output_dir: Path) -> bool:
    """Screen one case; returns False (skipped) if it has phase-shifting branches."""
    case_path = REPO_ROOT / "data" / "raw" / "pglib" / "matpower_mat" / "base_case" / f"{case_file}.mat"
    short_name = case_file.removeprefix("pglib_opf_case")

    case = load_powercase_from_matpower_mat(case_path)

    shifted = phase_shifted_branch_ids(case)
    if shifted:
        print(
            f"Skipping {case.name}: {len(shifted)} branch(es) with non-zero phase "
            f"shift ({shifted[:5]}{'...' if len(shifted) > 5 else ''}) -- pypowsybl "
            "v1 builder cannot represent these; see module docstring."
        )
        return False

    print(f"Solving base AC OPF for {case.name}")
    # voltage_bounds=None keeps the case's native limits rather than the
    # solve_ac_opf_pyomo default of (0.95, 1.05), which would tighten the
    # case's actual feasible region.
    ac_opf_result = solve_ac_opf_pyomo(case, cost_power_units="pu", voltage_bounds=None)
    print(f"  AC OPF success={ac_opf_result.success} objective={ac_opf_result.objective:.6f}")
    if not ac_opf_result.success:
        raise RuntimeError(f"Base AC OPF did not solve for {case.name}; cannot screen contingencies.")

    print("pypowsybl/OpenLoadFlow N-1 screening (branches + generators):")
    results = run_pypowsybl_n1_screening(case, ac_opf_result, include_branches=True, include_generators=True)
    n_secure = int(results["secure"].sum()) if not results.empty else 0
    print(f"  Screened {len(results)} contingencies; secure={n_secure}; insecure={len(results) - n_secure}")
    for element_type in ("branch", "generator"):
        subset = results[results["element_type"] == element_type]
        n_secure_type = int(subset["secure"].sum()) if not subset.empty else 0
        print(f"    {element_type}: {len(subset)} screened, {n_secure_type} secure")

    results_path = output_dir / f"n1_pypowsybl_case{short_name}_contingencies.csv"
    results.to_csv(results_path, index=False)
    print(f"  Saved results to: {results_path}")
    return True


def main() -> None:
    output_dir = REPO_ROOT / "results" / "own_python" / "security"
    output_dir.mkdir(parents=True, exist_ok=True)

    screened, skipped = [], []
    for case_file in DEFAULT_CASE_FILES:
        (screened if screen_one_case(case_file, output_dir) else skipped).append(case_file)

    print(f"\nDone: {len(screened)} case(s) screened, {len(skipped)} skipped (phase shifters).")
    if skipped:
        print(f"  Skipped: {skipped}")


if __name__ == "__main__":
    main()
