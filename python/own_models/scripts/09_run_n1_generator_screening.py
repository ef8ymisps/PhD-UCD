"""Run N-1 active-generator post-contingency AC PF screening."""

from __future__ import annotations

from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_DIR = SCRIPT_DIR.parents[1]
REPO_ROOT = PYTHON_DIR.parent
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.powermodels_csv_adapter import load_powercase_from_powermodels_json  # noqa: E402
from own_models.src.analysis.contingency_outputs import write_contingency_outputs  # noqa: E402
from own_models.src.analysis.post_contingency import run_generator_contingency_screening  # noqa: E402
from own_models.src.opf.ac import solve_ac_opf_pyomo  # noqa: E402


def main() -> None:
    reference_dir = REPO_ROOT / "results" / "reference" / "powermodels" / "ac_opf"
    output_dir = REPO_ROOT / "results" / "own_python" / "security"
    case_paths = sorted(reference_dir.glob("*_base_case.json"))

    all_results = []
    for case_path in case_paths:
        case = load_powercase_from_powermodels_json(case_path)
        operating_point_id = f"{case_path.stem.removesuffix('_case')}_own_ac_opf"
        print(f"Solving base AC OPF for {case.name} ({case_path.name})")
        ac_opf_result = solve_ac_opf_pyomo(case)
        print(
            f"  AC OPF success={ac_opf_result.success} "
            f"objective={ac_opf_result.objective:.6f}"
        )

        results = run_generator_contingency_screening(
            case,
            ac_opf_result,
            operating_point_id=operating_point_id,
        )
        all_results.extend(results)
        n_secure = sum(result.secure for result in results)
        print(
            f"  Screened {len(results)} generator outages; "
            f"secure={n_secure}; insecure={len(results) - n_secure}"
        )

    paths = write_contingency_outputs(all_results, output_dir, stem="n1_generator")
    print(f"Saved detailed results to: {paths['detail']}")
    print(f"Saved summary results to: {paths['summary']}")


if __name__ == "__main__":
    main()
