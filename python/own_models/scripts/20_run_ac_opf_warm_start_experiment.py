"""Warm-start ablation for AC-OPF, all 8 default PGLib cases.

Background: the Julia validation sweep (`compact_opf_validation_summary.csv`)
shows the own model needing far more Ipopt iterations than PowerModels.jl on
some cases -- most dramatically `6470_rte`'s base case (own 564 vs PM 92,
6.1x) -- despite own model warm-starting from the case file's real dispatch
while PowerModels always flat-starts (Pg=Qg=0, Vm=1.0, Va=0; see
`docs/notes/solver_tolerances.md`). A first pass on just `6470_rte`/`1888_rte`
also surfaced a new, unexplained finding: Julia's own model is far more
sensitive to a poor (`case`) starting point than Python's own model is
(status doc §7 item 7). This script extends the ablation to all 8 of the
main sweep's default PGLib cases, to check whether that sensitivity gap is
specific to `6470_rte` or general.

This script solves each case with the *own* Python AC-OPF model under three
warm-start modes --

- ``case``: the case file's real Pg/Qg/Vm/Va (own model's current default,
  what the Julia sweep above used)
- ``flat``: hybrid -- real Pg, flat Qg/Vm/Va
- ``zero``: fully flat, matching PowerModels' actual default exactly

Cases: the same 8 default PGLib cases as
`julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl`'s
`DEFAULT_CASES` (5_pjm, 14_ieee, 30_ieee, 118_ieee, 300_ieee, 1354_pegase,
1888_rte, 6470_rte), loaded directly from the raw PGLib `.mat` files -- no
scenario-perturbation machinery needed -- plus a Python reconstruction of the
one 1888_rte scenario that also shows a real (non-numerical-noise) dispatch
mismatch against PowerModels: `1888_rte_load_x1.2` / `reactive_power_stress`
(Qd *= 1.2, Pd unchanged -- matches
`scale_loads!(data, 1.2; scale_p=false, scale_q=true)` in
`julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl:566`).

This script does NOT solve PowerModels itself (Python has no PowerModels.jl
binding) -- pair its output with
`julia/own_models/scripts/11_run_ac_opf_warm_start_experiment.jl`'s CSV
(which solves PowerModels natively for every one of these cases in this same
pass) to get the full comparison, rather than reusing a stale/hardcoded PM
number as the single-case pilot did.
"""

from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
REPO_ROOT = PYTHON_DIR.parent
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat  # noqa: E402
from own_models.src.opf.ac.model import solve_ac_opf_pyomo  # noqa: E402

WARM_START_MODES = ["case", "flat", "zero"]

# Matches julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl's
# DEFAULT_CASES, in the same order.
DEFAULT_CASES = [
    "pglib_opf_case5_pjm",
    "pglib_opf_case14_ieee",
    "pglib_opf_case30_ieee",
    "pglib_opf_case118_ieee",
    "pglib_opf_case300_ieee",
    "pglib_opf_case1354_pegase",
    "pglib_opf_case1888_rte",
    "pglib_opf_case6470_rte",
]

MAT_DIR = REPO_ROOT / "data" / "raw" / "pglib" / "matpower_mat" / "base_case"


def build_cases() -> dict[str, object]:
    """Load all 8 default base cases, plus the derived 1888_rte Qd x1.2 variant."""
    cases: dict[str, object] = {}
    base_1888 = None
    short_name_1888 = None

    for case_file in DEFAULT_CASES:
        short_name = case_file.removeprefix("pglib_opf_case")
        case = load_powercase_from_matpower_mat(MAT_DIR / f"{case_file}.mat")
        cases[f"{short_name} (base)"] = case
        if case_file == "pglib_opf_case1888_rte":
            base_1888 = case
            short_name_1888 = short_name

    assert base_1888 is not None
    scaled_loads = tuple(dataclasses.replace(load, qd=load.qd * 1.2) for load in base_1888.loads)
    cases[f"{short_name_1888} (Qd x1.2)"] = dataclasses.replace(base_1888, loads=scaled_loads)

    return cases


def run_experiment() -> pd.DataFrame:
    cases = build_cases()
    rows = []

    for case_label, case in cases.items():
        for warm_start in WARM_START_MODES:
            print(f"Solving {case_label} | warm_start={warm_start} ...")
            start = time.time()
            # voltage_bounds=None uses the case's own native vmin/vmax (matches
            # PowerModels and Julia's own model) -- the default (0.95, 1.05) is
            # an artificial restriction that made every solve here infeasible.
            result = solve_ac_opf_pyomo(
                case, warm_start=warm_start, tee=False, voltage_bounds=None
            )
            elapsed = time.time() - start

            rows.append(
                {
                    "case_label": case_label,
                    "n_buses": len(case.buses),
                    "warm_start": warm_start,
                    "own_success": result.success,
                    "own_status": result.status,
                    "own_objective": result.objective,
                    "own_solve_iterations": result.solve_iterations,
                    "own_solve_time_sec": elapsed,
                }
            )
            print(
                f"  -> success={result.success} status={result.status} "
                f"iterations={result.solve_iterations} objective={result.objective:.4f} "
                f"time={elapsed:.1f}s"
            )

    return pd.DataFrame(rows)


def main() -> None:
    output_dir = REPO_ROOT / "results" / "own_python" / "ac_opf_warm_start_experiment"
    output_dir.mkdir(parents=True, exist_ok=True)

    df = run_experiment()
    out_path = output_dir / "warm_start_iteration_comparison.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
