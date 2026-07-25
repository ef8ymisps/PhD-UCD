"""Full-scenario Set B sweep: mirrors
julia/own_models/scripts/27_run_own_vs_powermodels_full_sweep.jl, but driven
by *whatever PowerModels reference JSONs already exist* in
results/reference/powermodels/ac_opf/, rather than its own copy of
compact_scenarios (that scenario-generation logic is Julia-only). This means
it's naturally incremental: run it any time after the Julia sweep has
exported some references, rerun it later to pick up whatever's new -- no
need to wait for the Julia sweep to fully finish.

Reuses 23_run_own_vs_powermodels_tables.py's process_scenario (and
everything it calls) via importlib rather than duplicating it -- that
module's filename starts with a digit, so it can't be imported with a plain
`import` statement.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import time
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(PYTHON_DIR))


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


set_b = _load_module(SCRIPT_DIR / "23_run_own_vs_powermodels_tables.py", "own_vs_powermodels_set_b")

REFERENCE_DIR = set_b.REFERENCE_DIR
SUMMARY_PATH = set_b.SUMMARY_PATH
CASES = set_b.CASES

# Scenarios already covered under a friendly name by 23_...py's own
# base/SPOTLIGHT_SCENARIOS runs -- map their reference-file suffix back to
# that friendly name so this sweep doesn't reprocess them under a different
# (equally valid, but now-duplicate) folder name.
KNOWN_SUFFIXES = {
    "base": "base",
    "1888_rte_load_x0p8__load_scaling__none__none__load_multiplier__0p8": "load_x0.8",
    "1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2": "load_x1.2_reactive_stress",
    "1888_rte_qg_limits_x0p8__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p8": "qg_limits_x0.8",
}


def discover_scenarios(stem: str) -> list[str]:
    """Every `<stem>_<suffix>_case.json` in REFERENCE_DIR, returning `<suffix>`."""
    pattern = re.compile(rf"^{re.escape(stem)}_(.+)_case\.json$")
    suffixes = []
    for path in REFERENCE_DIR.glob(f"{stem}_*_case.json"):
        m = pattern.match(path.name)
        if m:
            suffixes.append(m.group(1))
    return sorted(suffixes)


def completed_pairs() -> set[tuple[str, str]]:
    if not SUMMARY_PATH.exists():
        return set()
    df = pd.read_csv(SUMMARY_PATH, usecols=["case", "scenario"])
    return set(zip(df["case"], df["scenario"]))


def main() -> None:
    completed = completed_pairs()
    print(
        f"Starting fresh -- no prior progress found."
        if not completed
        else f"Resuming -- {len(completed)} (case, scenario) pairs already done."
    )

    for stem, label in CASES:
        suffixes = discover_scenarios(stem)
        print(f"\n=== {label}: {len(suffixes)} reference scenario(s) available ===")
        for i, suffix in enumerate(suffixes, start=1):
            scenario_label = KNOWN_SUFFIXES.get(suffix, suffix)
            if (label, scenario_label) in completed:
                print(f"  [{i}/{len(suffixes)}] {scenario_label} -- already done, skipping")
                continue
            print(f"  [{i}/{len(suffixes)}] {scenario_label} ... ", end="")
            start = time.time()
            try:
                set_b.process_scenario(stem, label, scenario_label, suffix)
            except Exception as exc:  # noqa: BLE001 -- report and continue across scenarios
                print(f"ERROR: {exc}")
            print(f"done ({time.time() - start:.1f}s)")
            completed.add((label, scenario_label))

    print("\nFull sweep complete (up to whatever references currently exist -- rerun later for more).")


if __name__ == "__main__":
    main()
