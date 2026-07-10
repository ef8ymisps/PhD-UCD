"""Export compact AC OPF validation scenarios as PowerModels-readable cases."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from own_models.src.adapters.matpower_adapter import load_powercase_from_matpower_mat  # noqa: E402
from own_models.src.analysis.ac_opf_cross_model_export import (  # noqa: E402
    export_powermodels_ac_scenarios,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--compact-sweep",
        type=Path,
        default=REPO_ROOT / "results" / "own_python" / "ac_opf_validation" / "compact_ac_opf_sweeps.csv",
        help="Compact Python AC OPF sweep CSV to export.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "results" / "reference" / "powermodels" / "ac_opf_compact_sweeps",
        help="Directory for the scenario MATPOWER files and manifest.",
    )
    parser.add_argument("--cases", nargs="*", default=None, help="Optional case names to export.")
    parser.add_argument(
        "--include-no-reference-slack",
        action="store_true",
        help="Export no-reference rows as ordinary reference-bus cases. This is not an exact model match.",
    )
    args = parser.parse_args()

    case_dir = REPO_ROOT / "data" / "raw" / "pglib" / "matpower_mat" / "base_case"
    case_labels = {
        path.stem.replace("pglib_opf_case", "")
        for path in case_dir.glob("pglib_opf_case*.mat")
    }

    def load_case(case_name: str):
        stem = case_name if case_name.startswith("pglib_opf_case") else f"pglib_opf_case{case_name}"
        return load_powercase_from_matpower_mat(case_dir / f"{stem}.mat")

    manifest = export_powermodels_ac_scenarios(
        args.compact_sweep,
        output_dir=args.output_dir,
        case_loader=load_case,
        cases=set(args.cases) if args.cases else None,
        case_labels=case_labels,
        include_no_reference_slack=args.include_no_reference_slack,
    )

    print(f"Exported {len(manifest)} PowerModels AC OPF scenario cases.")
    print(f"Manifest: {args.output_dir / 'compact_ac_opf_powermodels_manifest.csv'}")
    print(f"Cases:    {args.output_dir / 'cases'}")


if __name__ == "__main__":
    main()
