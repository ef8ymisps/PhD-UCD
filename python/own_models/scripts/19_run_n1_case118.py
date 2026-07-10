"""Run N-1 branch and generator post-contingency screening on case118.

Per the tool decision (docs/formulations/security_constraints.md): the own
model is OPF-only. Post-contingency power flow for Python N-1 screening uses
pypowsybl + OpenLoadFlow exclusively (use_reactive_limits=True for PV->PQ
switching, distributed_slack=False with the slack pinned to
case.slack_bus_id, matching the OPF's single-slack convention) -- the own
Newton-Raphson PF path in post_contingency.py is not used here.

Loads case118 directly from its matpower .mat file since N-1 screening is a
self-consistency check that doesn't need a PowerModels reference.
"""

from __future__ import annotations

from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
REPO_ROOT = PYTHON_DIR.parent
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat  # noqa: E402
from own_models.src.analysis.pypowsybl_security_validation import run_pypowsybl_n1_screening  # noqa: E402
from own_models.src.opf.ac import solve_ac_opf_pyomo  # noqa: E402


def main() -> None:
    case_path = REPO_ROOT / "data" / "raw" / "pglib" / "matpower_mat" / "base_case" / "pglib_opf_case118_ieee.mat"
    output_dir = REPO_ROOT / "results" / "own_python" / "security"
    output_dir.mkdir(parents=True, exist_ok=True)

    case = load_powercase_from_matpower_mat(case_path)
    print(f"Solving base AC OPF for {case.name}")
    # voltage_bounds=None keeps the case's native limits rather than the
    # solve_ac_opf_pyomo default of (0.95, 1.05), which would tighten case118's
    # actual feasible region.
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

    results_path = output_dir / "n1_pypowsybl_case118_contingencies.csv"
    results.to_csv(results_path, index=False)
    print(f"  Saved results to: {results_path}")


if __name__ == "__main__":
    main()
