"""Run PyPowSyBl N-1 branch-outage AC load-flow analysis."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
PYPOWSYBL_SRC = SCRIPT_DIR.parent / "src"
REPO_ROOT = SCRIPT_DIR.parents[3]
sys.path.insert(0, str(PYPOWSYBL_SRC))

from network_utils import load_network
from security_utils import run_n_minus_one_branch_study


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="ieee14")
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "results" / "pypowsybl"),
    )
    parser.add_argument("--prefix", default="pypowsybl_n_minus_one")
    parser.add_argument("--max-loading-pct", type=float, default=100.0)
    parser.add_argument("--min-vm-kv", type=float, default=None)
    parser.add_argument("--max-vm-kv", type=float, default=None)
    parser.add_argument("--lines-only", action="store_true")
    args = parser.parse_args()

    network = load_network(args.source)
    result = run_n_minus_one_branch_study(
        network,
        include_transformers=not args.lines_only,
        max_loading_pct=args.max_loading_pct,
        min_vm_kv=args.min_vm_kv,
        max_vm_kv=args.max_vm_kv,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result.base_summary.to_csv(
        output_dir / f"{args.prefix}_base_summary.csv",
        index=False,
    )
    result.contingency_summary.to_csv(
        output_dir / f"{args.prefix}_summary.csv",
        index=False,
    )
    result.contingency_branches.to_csv(
        output_dir / f"{args.prefix}_branches.csv",
        index=False,
    )
    result.contingency_buses.to_csv(
        output_dir / f"{args.prefix}_buses.csv",
        index=False,
    )

    print(result.contingency_summary.to_string(index=False))
    print(f"Saved PyPowSyBl N-1 outputs to: {output_dir}")


if __name__ == "__main__":
    main()
