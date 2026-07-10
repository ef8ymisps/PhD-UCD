from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from own_models.src.data.case import Branch, Bus, Generator, Load, PowerCase, Shunt


class PowerModelsAdapterError(ValueError):
    """Raised when exported PowerModels data cannot be mapped into PowerCase."""
    pass


def _sorted_ids(mapping: dict[str, Any]) -> list[str]:
    """Return PowerModels dictionary keys sorted by numeric id."""
    return sorted((str(key) for key in mapping.keys()), key=lambda key: int(key))


def _cost_coefficients(gen_data: dict[str, Any]) -> tuple[float, float, float]:
    """Normalize a PowerModels generator cost vector into c2, c1, c0."""
    cost = gen_data.get("cost", [])
    if len(cost) == 3:
        return float(cost[0]), float(cost[1]), float(cost[2])
    if len(cost) == 2:
        return 0.0, float(cost[0]), float(cost[1])
    if len(cost) == 1:
        return 0.0, float(cost[0]), 0.0
    return 0.0, 0.0, 0.0


def _slack_bus_id(data: dict[str, Any]) -> str:
    """Find the first reference/slack bus declared by bus_type == 3."""
    for bus_id, bus in data["bus"].items():
        if int(bus["bus_type"]) == 3:
            return str(bus_id)
    raise PowerModelsAdapterError("No bus with bus_type == 3 found.")


def powermodels_dict_to_powercase(data: dict[str, Any]) -> PowerCase:
    """Convert a parsed PowerModels dictionary into the repository PowerCase."""
    base_mva = float(data["baseMVA"])
    buses: list[Bus] = []
    bus_id_to_index: dict[str, int] = {}

    for idx, bus_id in enumerate(_sorted_ids(data["bus"]), start=1):
        bus = data["bus"][bus_id]
        buses.append(
            Bus(
                id=bus_id,
                index=idx,
                bus_type=int(bus["bus_type"]),
                vm0=float(bus.get("vm", 1.0)),
                va0=float(bus.get("va", 0.0)),
                vmin=float(bus.get("vmin", 0.0)),
                vmax=float(bus.get("vmax", 2.0)),
                nominal_v_kv=float(bus.get("base_kv", 1.0)),
            )
        )
        bus_id_to_index[bus_id] = idx - 1

    branches: list[Branch] = []
    for idx, branch_id in enumerate(_sorted_ids(data["branch"]), start=1):
        branch = data["branch"][branch_id]
        tap = float(branch.get("tap", 1.0))
        if tap == 0.0:
            tap = 1.0
        b_fr = float(branch.get("b_fr", 0.0))
        b_to = float(branch.get("b_to", 0.0))
        branches.append(
            Branch(
                id=branch_id,
                index=idx,
                from_bus=str(branch["f_bus"]),
                to_bus=str(branch["t_bus"]),
                r=float(branch["br_r"]),
                x=float(branch["br_x"]),
                b=b_fr + b_to,
                b_fr=b_fr,
                b_to=b_to,
                g_fr=float(branch.get("g_fr", 0.0)),
                g_to=float(branch.get("g_to", 0.0)),
                rate_a=float(branch.get("rate_a", 0.0)),
                tap=tap,
                shift=float(branch.get("shift", 0.0)),
                angmin=float(branch.get("angmin", -math.pi / 2)),
                angmax=float(branch.get("angmax", math.pi / 2)),
                status=int(branch.get("br_status", 1)),
                branch_type="branch",
            )
        )

    generators: list[Generator] = []
    for idx, gen_id in enumerate(_sorted_ids(data["gen"]), start=1):
        gen = data["gen"][gen_id]
        c2, c1, c0 = _cost_coefficients(gen)
        generators.append(
            Generator(
                id=gen_id,
                index=idx,
                bus=str(gen["gen_bus"]),
                pg0=float(gen.get("pg", 0.0)),
                qg0=float(gen.get("qg", 0.0)),
                pmin=float(gen.get("pmin", 0.0)),
                pmax=float(gen.get("pmax", 0.0)),
                qmin=float(gen.get("qmin", 0.0)),
                qmax=float(gen.get("qmax", 0.0)),
                status=int(gen.get("gen_status", 1)),
                voltage_regulator_on=False,
                cost_c2=c2,
                cost_c1=c1,
                cost_c0=c0,
            )
        )

    loads: list[Load] = []
    for idx, load_id in enumerate(_sorted_ids(data.get("load", {})), start=1):
        load = data["load"][load_id]
        loads.append(
            Load(
                id=load_id,
                index=idx,
                bus=str(load["load_bus"]),
                pd=float(load.get("pd", 0.0)),
                qd=float(load.get("qd", 0.0)),
                status=int(load.get("status", 1)),
            )
        )

    shunts: list[Shunt] = []
    for idx, shunt_id in enumerate(_sorted_ids(data.get("shunt", {})), start=1):
        shunt = data["shunt"][shunt_id]
        shunts.append(
            Shunt(
                id=shunt_id,
                index=idx,
                bus=str(shunt["shunt_bus"]),
                gs=float(shunt.get("gs", 0.0)),
                bs=float(shunt.get("bs", 0.0)),
                status=int(shunt.get("status", 1)),
            )
        )

    return PowerCase(
        name=str(data.get("name", "powermodels_case")),
        base_mva=base_mva,
        buses=buses,
        branches=branches,
        generators=generators,
        loads=loads,
        shunts=shunts,
        slack_bus_id=_slack_bus_id(data),
        bus_id_to_index=bus_id_to_index,
    )


def load_powermodels_json(path: str | Path) -> dict[str, Any]:
    """Load a PowerModels-style JSON file from disk."""
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_powercase_from_powermodels_json(path: str | Path) -> PowerCase:
    """Load a PowerModels JSON file and convert it into PowerCase."""
    return powermodels_dict_to_powercase(load_powermodels_json(path))


def cost_coefficient_validation_table(data: dict[str, Any], case: PowerCase) -> pd.DataFrame:
    """Build a table comparing raw PowerModels cost data with adapted coefficients."""
    py_gen = {gen.id: gen for gen in case.generators}
    rows = []
    for gen_id in _sorted_ids(data["gen"]):
        pm_c2, pm_c1, pm_c0 = _cost_coefficients(data["gen"][gen_id])
        gen = py_gen[gen_id]
        rows.append(
            {
                "gen_id": gen_id,
                "pm_c2": pm_c2,
                "py_c2": gen.cost_c2,
                "c2_diff": gen.cost_c2 - pm_c2,
                "pm_c1": pm_c1,
                "py_c1": gen.cost_c1,
                "c1_diff": gen.cost_c1 - pm_c1,
                "pm_c0": pm_c0,
                "py_c0": gen.cost_c0,
                "c0_diff": gen.cost_c0 - pm_c0,
            }
        )
    return pd.DataFrame(rows)
