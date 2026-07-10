"""Compare own N-1 branch screening with PyPowSyBl built-in security analysis."""

from __future__ import annotations

from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
REPO_ROOT = PYTHON_DIR.parent
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.analysis.pypowsybl_security_validation import (  # noqa: E402
    run_pypowsybl_branch_security_validation,
)


def main() -> None:
    paths = run_pypowsybl_branch_security_validation(
        reference_dir=REPO_ROOT / "results" / "reference" / "powermodels" / "ac_opf",
        own_branch_results_path=REPO_ROOT
        / "results"
        / "own_python"
        / "security"
        / "n1_branch_contingencies.csv",
        pypowsybl_output_dir=REPO_ROOT / "results" / "pypowsybl" / "security",
        comparison_output_path=REPO_ROOT
        / "results"
        / "own_python"
        / "security"
        / "n1_branch_pypowsybl_comparison.csv",
    )
    for name, path in paths.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
