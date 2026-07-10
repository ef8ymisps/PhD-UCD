"""Validate Python AC OPF against exported PowerModels/PGLib references."""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
REPO_ROOT = PYTHON_DIR.parent
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.analysis.compare_ac_with_powermodels import (  # noqa: E402
    run_python_ac_opf_against_powermodels,
)


def _scenario_from_reference(path: Path) -> str:
    stem = path.name.removesuffix("_reference.json")
    for scenario in (
        "branch_congested",
        "generator_limited",
        "load_scaled",
        "relaxed",
        "base",
    ):
        if stem.endswith("_" + scenario):
            return scenario
    return "unknown"


def main() -> None:
    reference_dir = REPO_ROOT / "results" / "reference" / "powermodels" / "ac_opf"
    output_dir = REPO_ROOT / "results" / "own_python" / "ac_opf"
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_frames = []
    generator_frames = []
    bus_frames = []
    branch_frames = []
    constraint_frames = []
    cost_frames = []

    for reference_path in sorted(reference_dir.glob("*_reference.json")):
        case_path = reference_path.with_name(reference_path.name.replace("_reference.json", "_case.json"))
        if not case_path.exists():
            print(f"Skipping {reference_path.name}: missing {case_path.name}")
            continue

        scenario = _scenario_from_reference(reference_path)
        result = run_python_ac_opf_against_powermodels(
            case_path,
            reference_path,
            scenario=scenario,
        )

        summary = result["summary"]
        summary.insert(0, "reference_file", reference_path.name)
        summary_frames.append(summary)

        for key, frames in (
            ("generators", generator_frames),
            ("buses", bus_frames),
            ("branches", branch_frames),
            ("constraints", constraint_frames),
            ("cost_validation", cost_frames),
        ):
            frame = result[key]
            if not frame.empty:
                frame = frame.copy()
                frame.insert(0, "reference_file", reference_path.name)
                if "scenario" not in frame.columns:
                    frame.insert(1, "scenario", scenario)
                frames.append(frame)

        print(summary.to_string(index=False))

    if summary_frames:
        pd.concat(summary_frames, ignore_index=True).to_csv(output_dir / "ac_opf_summary.csv", index=False)
    if generator_frames:
        pd.concat(generator_frames, ignore_index=True).to_csv(output_dir / "ac_opf_generators.csv", index=False)
    if bus_frames:
        pd.concat(bus_frames, ignore_index=True).to_csv(output_dir / "ac_opf_buses.csv", index=False)
    if branch_frames:
        pd.concat(branch_frames, ignore_index=True).to_csv(output_dir / "ac_opf_branches.csv", index=False)
    if constraint_frames:
        pd.concat(constraint_frames, ignore_index=True).to_csv(output_dir / "ac_opf_constraints.csv", index=False)
    if cost_frames:
        pd.concat(cost_frames, ignore_index=True).to_csv(output_dir / "ac_opf_cost_validation.csv", index=False)

    print(f"Saved Python AC OPF validation outputs to: {output_dir}")


if __name__ == "__main__":
    main()
