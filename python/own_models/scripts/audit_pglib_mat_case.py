from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pypowsybl as pp
from scipy.io import loadmat

from own_models.src.adapters.pypowsybl_adapter import pypowsybl_to_powercase


def find_case(pattern: str) -> Path:
    matches = sorted(Path().glob(pattern))

    if not matches:
        raise FileNotFoundError(f"No files matched pattern: {pattern}")

    if len(matches) > 1:
        print("\nMatched files:")
        for path in matches:
            print(" ", path)
        raise RuntimeError(
            "Pattern matched more than one file. Use a more specific path."
        )

    return matches[0]


def _mat_struct_field(struct, field_name: str):
    """
    Read a field from a scipy-loaded MATLAB struct.
    """
    return struct[field_name][0, 0]


def audit_raw_matpower_file(path: Path) -> None:
    """
    Audit the raw MATPOWER .mat file directly.

    MATPOWER branch columns:
        0  fbus
        1  tbus
        2  r
        3  x
        4  b
        5  rateA
        6  rateB
        7  rateC
        8  ratio
        9  angle
        10 status
        11 angmin
        12 angmax
    """
    mat = loadmat(path, squeeze_me=False, struct_as_record=False)

    if "mpc" not in mat:
        raise RuntimeError(f"{path} does not contain variable 'mpc'.")

    mpc = mat["mpc"][0, 0]

    base_mva = float(mpc.baseMVA[0, 0])
    branch = np.asarray(mpc.branch, dtype=float)
    gen = np.asarray(mpc.gen, dtype=float)
    bus = np.asarray(mpc.bus, dtype=float)

    rate_a = branch[:, 5]
    active_branch = branch[:, 10] > 0

    active_rate_a = rate_a[active_branch]
    positive_rate_a = active_rate_a[active_rate_a > 0.0]

    print("\n=== Raw MATPOWER/PGLib audit ===")
    print("path:", path)
    print("baseMVA:", base_mva)
    print("n_bus:", bus.shape[0])
    print("n_gen:", gen.shape[0])
    print("n_branch:", branch.shape[0])
    print("n_active_branch:", int(active_branch.sum()))
    print("branches with positive rateA:", len(positive_rate_a), "/", int(active_branch.sum()))

    if len(positive_rate_a) > 0:
        print("rateA min:", float(np.min(positive_rate_a)))
        print("rateA max:", float(np.max(positive_rate_a)))
        print("rateA first 10:", positive_rate_a[:10].tolist())

    pmin = gen[:, 9]
    pmax = gen[:, 8]

    print("\nGenerator limits in raw file:")
    print("Pmin first 10:", pmin[:10].tolist())
    print("Pmax first 10:", pmax[:10].tolist())
    print("Pmin min/max:", float(np.min(pmin)), float(np.max(pmin)))
    print("Pmax min/max:", float(np.min(pmax)), float(np.max(pmax)))


def audit_pypowsybl_loaded_case(path: Path) -> None:
    network = pp.network.load(str(path))

    lines = network.get_lines()
    transformers = network.get_2_windings_transformers()
    generators = network.get_generators()

    print("\n=== PyPowSyBl loaded-data audit ===")
    print("n_lines:", len(lines))
    print("n_transformers:", len(transformers))
    print("n_generators:", len(generators))

    print("\nLine columns:")
    print(list(lines.columns))

    print("\nTransformer columns:")
    print(list(transformers.columns))

    print("\nGenerator columns:")
    print(list(generators.columns))

    case = pypowsybl_to_powercase(
        network,
        name=path.stem,
        base_mva=100.0,
        assign_synthetic_costs=True,
    )

    active_branches = [b for b in case.branches if b.status == 1]
    rated_branches = [b for b in active_branches if b.rate_a > 0.0]

    active_gens = [g for g in case.generators if g.status == 1]

    print("\n=== Converted PowerCase audit ===")
    print("name:", case.name)
    print("base_mva:", case.base_mva)
    print("n_buses:", len(case.buses))
    print("n_active_generators:", len(active_gens))
    print("n_active_branches:", len(active_branches))
    print("rated_branches:", len(rated_branches), "/", len(active_branches))
    print("total_load_pu:", sum(l.pd for l in case.loads if l.status == 1))
    print("total_pmin_pu:", sum(g.pmin for g in active_gens))
    print("total_pmax_pu:", sum(g.pmax for g in active_gens))

    if rated_branches:
        print("rate_a first 10 [p.u.]:", [b.rate_a for b in rated_branches[:10]])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path_or_pattern",
        help="Path or glob pattern to a PGLib MATPOWER .mat file.",
    )
    args = parser.parse_args()

    path = Path(args.path_or_pattern)

    if "*" in args.path_or_pattern:
        path = find_case(args.path_or_pattern)

    if not path.exists():
        raise FileNotFoundError(path)

    audit_raw_matpower_file(path)
    audit_pypowsybl_loaded_case(path)


if __name__ == "__main__":
    main()