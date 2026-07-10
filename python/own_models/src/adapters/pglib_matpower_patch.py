from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import numpy as np
from scipy.io import loadmat

from own_models.src.data.case import PowerCase


class PGLibPatchError(RuntimeError):
    """Raised when PGLib MATPOWER metadata cannot be patched safely."""


def _load_mpc(path: Path):
    mat = loadmat(path, squeeze_me=False, struct_as_record=False)

    if "mpc" not in mat:
        raise PGLibPatchError(f"{path} does not contain variable 'mpc'.")

    return mat["mpc"][0, 0]


def _bus_number_from_powercase_bus_id(bus_id: str) -> int:
    """
    Extract the MATPOWER bus number from common PyPowSyBl/PGLib bus ids.

    Examples:
        BUS-14 -> 14
        VL14_0 -> 14
        14 -> 14
    """
    digits = "".join(ch if ch.isdigit() else " " for ch in bus_id).split()

    if not digits:
        raise PGLibPatchError(f"Cannot extract bus number from bus id {bus_id!r}.")

    return int(digits[0])


def patch_branch_rate_a_from_pglib_matpower(
    case: PowerCase,
    matpower_path: str | Path,
) -> PowerCase:
    """
    Patch branch rate_a values from a PGLib/MATPOWER .mat file.

    The PyPowSyBl network loader preserves topology but may not expose MATPOWER
    branch rateA for lines. This function recovers those ratings from the raw
    MATPOWER branch table and writes them into PowerCase.branch.rate_a in p.u.

    MATPOWER branch column 5 is rateA in MVA/MW-like units on baseMVA.
    Our internal PowerCase stores rate_a in p.u.
    """
    path = Path(matpower_path)
    mpc = _load_mpc(path)

    base_mva = float(mpc.baseMVA[0, 0])
    branch_table = np.asarray(mpc.branch, dtype=float)

    # Map unordered endpoint pair -> queue of rateA values.
    # A queue handles possible parallel branches.
    rates_by_pair: dict[tuple[int, int], deque[float]] = defaultdict(deque)

    for row in branch_table:
        fbus = int(row[0])
        tbus = int(row[1])
        rate_a_mva = float(row[5])
        status = int(row[10])

        if status == 0:
            continue

        key = tuple(sorted((fbus, tbus)))
        rates_by_pair[key].append(rate_a_mva / base_mva)

    patched = 0
    missing: list[str] = []

    for branch in case.branches:
        if branch.status == 0:
            continue

        fbus = _bus_number_from_powercase_bus_id(branch.from_bus)
        tbus = _bus_number_from_powercase_bus_id(branch.to_bus)
        key = tuple(sorted((fbus, tbus)))

        if not rates_by_pair[key]:
            missing.append(branch.id)
            continue

        branch.rate_a = rates_by_pair[key].popleft()
        patched += 1

    if missing:
        raise PGLibPatchError(
            "Could not patch rate_a for these branches: "
            + ", ".join(missing[:20])
            + (" ..." if len(missing) > 20 else "")
        )

    return case