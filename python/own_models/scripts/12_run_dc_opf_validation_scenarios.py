"""Run DC OPF validation scenarios on a PGLib/MATPOWER .mat case."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd


PYTHON_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from own_models.src.analysis.dc_opf_validation import (  # noqa: E402
    run_dc_opf_validation_suite_from_matpower_mat,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "matpower_path",
        type=Path,
        help="Path to PGLib/MATPOWER .mat file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "results" / "own_python" / "dc_opf_validation",
        help="Directory for validation CSV outputs.",
    )
    args = parser.parse_args()

    outputs = run_dc_opf_validation_suite_from_matpower_mat(args.matpower_path)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    summary_path = args.output_dir / f"{args.matpower_path.stem}_summary.csv"
    generators_path = args.output_dir / f"{args.matpower_path.stem}_generators.csv"
    branches_path = args.output_dir / f"{args.matpower_path.stem}_branches.csv"

    outputs["summary"].to_csv(summary_path, index=False)
    outputs["generators"].to_csv(generators_path, index=False)
    outputs["branches"].to_csv(branches_path, index=False)

    with pd.option_context("display.max_columns", None, "display.width", 180):
        print(outputs["summary"].to_string(index=False))

    print(f"\nWrote validation outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
