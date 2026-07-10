"""MATPOWER/PGLib file conversion into the internal PowerCase format."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import loadmat

from own_models.src.data.case import Branch, Bus, Generator, Load, PowerCase, Shunt


class MatpowerAdapterError(ValueError):
    """Raised when MATPOWER data cannot be converted into PowerCase."""


def load_powercase_from_matpower_mat(path: str | Path) -> PowerCase:
    """Load a MATPOWER ``.mat`` file and convert it into ``PowerCase``.

    MATPOWER stores active/reactive powers in MW/MVAr on ``baseMVA``. The
    internal ``PowerCase`` stores powers in p.u., while branch impedances and
    voltage magnitudes are already p.u. in the MATPOWER tables.
    """
    path = Path(path)
    mpc = _load_mpc(path)
    return matpower_mpc_to_powercase(mpc, name=path.stem)


def matpower_mpc_to_powercase(mpc: Any, *, name: str = "matpower_case") -> PowerCase:
    """Convert a loaded MATPOWER ``mpc`` struct into ``PowerCase``."""
    base_mva = float(_array(mpc.baseMVA)[0, 0])
    bus_table = _array(mpc.bus)
    branch_table = _array(mpc.branch)
    gen_table = _array(mpc.gen)
    gencost_table = _array(getattr(mpc, "gencost", np.empty((0, 0))))

    buses, bus_id_to_index = _convert_buses(bus_table)
    branches = _convert_branches(branch_table, base_mva)
    generators = _convert_generators(gen_table, gencost_table, base_mva)
    loads = _convert_bus_loads(bus_table, base_mva)
    shunts = _convert_bus_shunts(bus_table, base_mva)
    slack_bus_id = _find_slack_bus_id(bus_table)

    return PowerCase(
        name=name,
        base_mva=base_mva,
        buses=buses,
        branches=branches,
        generators=generators,
        loads=loads,
        shunts=shunts,
        slack_bus_id=slack_bus_id,
        bus_id_to_index=bus_id_to_index,
    )


def _load_mpc(path: Path):
    mat = loadmat(path, squeeze_me=False, struct_as_record=False)
    if "mpc" not in mat:
        raise MatpowerAdapterError(f"{path} does not contain variable 'mpc'.")
    return mat["mpc"][0, 0]


def _array(value) -> np.ndarray:
    return np.asarray(value, dtype=float)


def _bus_id(value: float) -> str:
    integer = int(value)
    return str(integer) if float(integer) == float(value) else str(value)


def _convert_buses(bus_table: np.ndarray) -> tuple[list[Bus], dict[str, int]]:
    buses: list[Bus] = []
    bus_id_to_index: dict[str, int] = {}

    for row_index, row in enumerate(bus_table, start=1):
        bus_id = _bus_id(row[0])
        buses.append(
            Bus(
                id=bus_id,
                index=row_index,
                bus_type=int(row[1]),
                vm0=float(row[7]),
                va0=math.radians(float(row[8])),
                vmin=float(row[12]),
                vmax=float(row[11]),
                nominal_v_kv=float(row[9]),
            )
        )
        bus_id_to_index[bus_id] = row_index - 1

    return buses, bus_id_to_index


def _convert_branches(branch_table: np.ndarray, base_mva: float) -> list[Branch]:
    branches: list[Branch] = []

    for row_index, row in enumerate(branch_table, start=1):
        tap = float(row[8])
        if tap == 0.0:
            tap = 1.0
        charging = float(row[4])
        branches.append(
            Branch(
                id=str(row_index),
                index=row_index,
                from_bus=_bus_id(row[0]),
                to_bus=_bus_id(row[1]),
                r=float(row[2]),
                x=float(row[3]),
                b=charging,
                b_fr=charging / 2.0,
                b_to=charging / 2.0,
                g_fr=0.0,
                g_to=0.0,
                rate_a=float(row[5]) / base_mva,
                tap=tap,
                shift=math.radians(float(row[9])),
                angmin=math.radians(float(row[11])),
                angmax=math.radians(float(row[12])),
                status=int(row[10]),
                branch_type="branch",
            )
        )

    return branches


def _convert_generators(
    gen_table: np.ndarray,
    gencost_table: np.ndarray,
    base_mva: float,
) -> list[Generator]:
    generators: list[Generator] = []

    for row_index, row in enumerate(gen_table, start=1):
        c2, c1, c0 = _cost_coefficients(gencost_table, row_index)
        generators.append(
            Generator(
                id=str(row_index),
                index=row_index,
                bus=_bus_id(row[0]),
                pg0=float(row[1]) / base_mva,
                qg0=float(row[2]) / base_mva,
                pmin=float(row[9]) / base_mva,
                pmax=float(row[8]) / base_mva,
                qmin=float(row[4]) / base_mva,
                qmax=float(row[3]) / base_mva,
                status=int(row[7]),
                voltage_regulator_on=False,
                cost_c2=c2,
                cost_c1=c1,
                cost_c0=c0,
            )
        )

    return generators


def _cost_coefficients(gencost_table: np.ndarray, generator_index: int) -> tuple[float, float, float]:
    if gencost_table.size == 0 or generator_index > gencost_table.shape[0]:
        return 0.0, 0.0, 0.0

    row = gencost_table[generator_index - 1]
    model = int(row[0])
    if model != 2:
        raise MatpowerAdapterError(
            f"Only polynomial MATPOWER gencost rows are supported; got model={model}."
        )

    n_coefficients = int(row[3])
    coefficients = [float(value) for value in row[4 : 4 + n_coefficients]]

    if n_coefficients == 3:
        return coefficients[0], coefficients[1], coefficients[2]
    if n_coefficients == 2:
        return 0.0, coefficients[0], coefficients[1]
    if n_coefficients == 1:
        return 0.0, coefficients[0], 0.0
    return 0.0, 0.0, 0.0


def _convert_bus_loads(bus_table: np.ndarray, base_mva: float) -> list[Load]:
    loads: list[Load] = []

    for row_index, row in enumerate(bus_table, start=1):
        pd = float(row[2])
        qd = float(row[3])
        if abs(pd) <= 1e-12 and abs(qd) <= 1e-12:
            continue
        loads.append(
            Load(
                id=str(row_index),
                index=len(loads) + 1,
                bus=_bus_id(row[0]),
                pd=pd / base_mva,
                qd=qd / base_mva,
                status=1,
            )
        )

    return loads


def _convert_bus_shunts(bus_table: np.ndarray, base_mva: float) -> list[Shunt]:
    shunts: list[Shunt] = []

    for row_index, row in enumerate(bus_table, start=1):
        gs = float(row[4])
        bs = float(row[5])
        if abs(gs) <= 1e-12 and abs(bs) <= 1e-12:
            continue
        shunts.append(
            Shunt(
                id=str(row_index),
                index=len(shunts) + 1,
                bus=_bus_id(row[0]),
                gs=gs / base_mva,
                bs=bs / base_mva,
                status=1,
            )
        )

    return shunts


def _find_slack_bus_id(bus_table: np.ndarray) -> str:
    slack_rows = [row for row in bus_table if int(row[1]) == 3]
    if not slack_rows:
        raise MatpowerAdapterError("MATPOWER case has no reference bus with type 3.")
    return _bus_id(slack_rows[0][0])
