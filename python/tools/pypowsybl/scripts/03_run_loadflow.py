"""Run a PyPowSyBl AC load-flow study and export inspection tables."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
PYPOWSYBL_SRC = SCRIPT_DIR.parent / "src"
REPO_ROOT = SCRIPT_DIR.parents[3]
sys.path.insert(0, str(PYPOWSYBL_SRC))

from loadflow_utils import run_ac_loadflow_from_source


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="ieee14")
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "results" / "pypowsybl"),
    )
    parser.add_argument("--prefix", default="pypowsybl_loadflow")
    args = parser.parse_args()

    _, result = run_ac_loadflow_from_source(args.source)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result.component_results.to_csv(
        output_dir / f"{args.prefix}_component_results.csv",
        index=False,
    )
    result.buses.to_csv(output_dir / f"{args.prefix}_buses.csv", index=False)
    result.generators.to_csv(
        output_dir / f"{args.prefix}_generators.csv",
        index=False,
    )
    result.loads.to_csv(output_dir / f"{args.prefix}_loads.csv", index=False)
    result.branches.to_csv(output_dir / f"{args.prefix}_branches.csv", index=False)
    result.lines.to_csv(output_dir / f"{args.prefix}_lines.csv", index=False)
    result.transformers.to_csv(
        output_dir / f"{args.prefix}_transformers.csv",
        index=False,
    )
    result.summary.to_csv(output_dir / f"{args.prefix}_summary.csv", index=False)

    print(result.summary.to_string(index=False))
    print(f"Saved PyPowSyBl load-flow outputs to: {output_dir}")


if __name__ == "__main__":
    main()
