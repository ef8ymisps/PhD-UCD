"""Convert MATPOWER/PGLib .m cases to MATPOWER .mat files without Octave."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import savemat

try:
    from matpowercaseframes import CaseFrames
except ImportError as exc:  # pragma: no cover - exercised before runtime work.
    raise SystemExit(
        "matpowercaseframes is not installed. Install python/requirements.txt "
        "before running this converter."
    ) from exc


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "raw" / "pglib" / "matpower_mat"


def _frame_to_array(value: Any, field_name: str) -> np.ndarray:
    """Return a numeric 2-D numpy array from a CaseFrames field."""
    if value is None:
        raise ValueError(f"Required MATPOWER field '{field_name}' is missing.")

    if hasattr(value, "to_numpy"):
        array = value.to_numpy(dtype=float)
    else:
        array = np.asarray(value, dtype=float)

    if array.ndim == 1:
        array = array.reshape(1, -1)
    if array.ndim != 2:
        raise ValueError(
            f"MATPOWER field '{field_name}' must be a 2-D matrix; got shape "
            f"{array.shape}."
        )
    if array.size == 0:
        raise ValueError(f"Required MATPOWER field '{field_name}' is empty.")
    return array


def _get_required(case: CaseFrames, field_name: str) -> Any:
    if not hasattr(case, field_name):
        raise ValueError(f"Required MATPOWER field '{field_name}' is missing.")
    return getattr(case, field_name)


def build_mpc(case_path: Path) -> dict[str, Any]:
    """Parse a MATPOWER .m case and build a scipy.savemat-ready mpc dict."""
    case = CaseFrames(str(case_path))

    if not hasattr(case, "baseMVA"):
        raise ValueError("Required MATPOWER field 'baseMVA' is missing.")

    mpc: dict[str, Any] = {
        "version": "2",
        "baseMVA": float(getattr(case, "baseMVA")),
        "bus": _frame_to_array(_get_required(case, "bus"), "bus"),
        "gen": _frame_to_array(_get_required(case, "gen"), "gen"),
        "branch": _frame_to_array(_get_required(case, "branch"), "branch"),
    }

    if hasattr(case, "gencost") and getattr(case, "gencost") is not None:
        gencost = _frame_to_array(getattr(case, "gencost"), "gencost")
        if gencost.size:
            mpc["gencost"] = gencost

    return mpc


def print_summary(input_path: Path, output_path: Path, mpc: dict[str, Any]) -> None:
    print(f"Input:  {input_path}")
    print(f"Output: {output_path}")
    print(f"version: {mpc['version']}")
    print(f"baseMVA: {mpc['baseMVA']}")
    for field in ("bus", "gen", "branch", "gencost"):
        if field in mpc:
            print(f"{field}: {mpc[field].shape}")
        else:
            print(f"{field}: not present")


def default_output_path(input_path: Path, output_root: Path | None = None) -> Path:
    root = output_root or DEFAULT_OUTPUT_DIR
    return root / f"{input_path.stem}.mat"


def convert_file(input_path: Path, output_path: Path) -> None:
    try:
        mpc = build_mpc(input_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to convert {input_path}: {exc}") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    savemat(str(output_path), {"mpc": mpc}, do_compression=True)
    print_summary(input_path, output_path, mpc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a MATPOWER/PGLib .m case to a MATPOWER .mat file."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Input MATPOWER/PGLib .m case path, or a directory of .m cases.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "Output .mat path for one input file, or output directory for an input "
            "directory. Defaults to data/raw/pglib/matpower_mat."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    if not input_path.exists():
        raise SystemExit(f"Input path does not exist: {input_path}")

    if input_path.is_dir():
        output_dir = (args.output or DEFAULT_OUTPUT_DIR).resolve()
        if output_dir.suffix.lower() == ".mat":
            raise SystemExit("Directory conversion requires an output directory.")

        case_paths = sorted(input_path.glob("*.m"))
        if not case_paths:
            raise SystemExit(f"No .m files found in: {input_path}")

        failures: list[str] = []
        for idx, case_path in enumerate(case_paths, start=1):
            print(f"\n[{idx}/{len(case_paths)}] Converting {case_path.name}")
            try:
                convert_file(case_path, default_output_path(case_path, output_dir))
            except Exception as exc:
                failures.append(str(exc))
                print(exc)

        if failures:
            raise SystemExit(
                f"Converted {len(case_paths) - len(failures)} of {len(case_paths)} "
                f"cases; {len(failures)} failed."
            )
        print(f"\nConverted {len(case_paths)} cases to: {output_dir}")
        return

    if input_path.suffix.lower() != ".m":
        raise SystemExit(f"Input case must be a .m file: {input_path}")

    output_path = (args.output or default_output_path(input_path)).resolve()
    if output_path.suffix.lower() != ".mat":
        raise SystemExit(f"Output path must end in .mat: {output_path}")

    try:
        convert_file(input_path, output_path)
    except Exception as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
