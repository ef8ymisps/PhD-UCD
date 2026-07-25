"""Run Python PM-like OPF exports for selected 1888_rte objective-gap scenarios.

Requires PowerModels reference JSONs under results/reference/powermodels/ac_opf,
which are produced by the selected Julia runner:

    julia/own_models/scripts/40_run_selected_1888_objective_gap_exports.jl
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


pm_like = _load_module(
    SCRIPT_DIR / "34_run_own_vs_powermodels_pm_like_full_sweep.py",
    "python_pm_like_full_sweep",
)

SELECTED = [
    (
        "pglib_opf_case1888_rte",
        "1888_rte",
        "load_x0.8",
        "load_x0.8",
    ),
    (
        "pglib_opf_case1888_rte",
        "1888_rte",
        "load_x1.2_reactive_stress",
        "load_x1.2_reactive_stress",
    ),
]


def main() -> None:
    pm_like.ensure_conda_solver_path()
    max_iter = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    print(f"Selected Python PM-like 1888_rte export; max_iter={max_iter}", flush=True)
    for stem, label, scenario_label, suffix in SELECTED:
        case_json = pm_like.REFERENCE_DIR / f"{stem}_{suffix}_case.json"
        ref_json = pm_like.REFERENCE_DIR / f"{stem}_{suffix}_reference.json"
        if not case_json.exists() or not ref_json.exists():
            print(f"SKIP {label}/{scenario_label}: missing reference JSONs", flush=True)
            continue
        pm_like.process_scenario(
            stem,
            label,
            scenario_label,
            suffix,
            max_iter=max_iter,
            max_cpu_time=None,
        )
    pm_like.copy_compact_outputs()


if __name__ == "__main__":
    main()
