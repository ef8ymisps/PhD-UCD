from __future__ import annotations

import math
import warnings
from typing import Any

import numpy as np
import pandas as pd

from own_models.src.data.case import (
    PQ,
    PV,
    SLACK,
    Branch,
    Bus,
    Generator,
    Load,
    PowerCase,
    Shunt,
)
from own_models.src.data.per_unit import (
    admittance_siemens_to_pu,
    impedance_ohm_to_pu,
    mva_to_pu,
    mvar_to_pu,
    mw_to_pu,
)


class PyPowSyBlAdapterError(ValueError):
    pass

def _is_extreme_bound(value: float, threshold: float = 1e20) -> bool:
    """Return True for placeholder/infinite bounds exposed by external tools."""
    return (not math.isfinite(value)) or abs(value) >= threshold


def _sanitize_power_bound(
    value: float,
    *,
    default: float,
    threshold: float = 1e20,
) -> float:
    """Replace non-finite or huge placeholder MW/MVAr bounds with a safe default."""
    if _is_extreme_bound(value, threshold=threshold):
        return float(default)
    return float(value)

def _table(network: Any, getter_name: str) -> pd.DataFrame:
    getter = getattr(network, getter_name, None)
    if getter is None:
        return pd.DataFrame()
    data = getter()
    if data is None:
        return pd.DataFrame()
    result = data.copy() if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    if result.index.name is None:
        result.index.name = "id"
    return result.reset_index()


def _value(row: pd.Series, name: str, default=np.nan):
    if name not in row:
        return default
    value = row[name]
    if value is None:
        return default
    return value


def _float(row: pd.Series, name: str, default=0.0) -> float:
    value = _value(row, name, default)
    if pd.isna(value):
        return float(default)
    return float(value)


def _bool(row: pd.Series, name: str, default=True) -> bool:
    value = _value(row, name, default)
    if pd.isna(value):
        return bool(default)
    return bool(value)


def _connected(row: pd.Series) -> bool:
    if "connected" in row:
        return _bool(row, "connected", True)
    return _bool(row, "connected1", True) and _bool(row, "connected2", True)


def _nominal_voltage_by_bus(buses: pd.DataFrame, voltage_levels: pd.DataFrame) -> dict[str, float]:
    if voltage_levels.empty or "nominal_v" not in voltage_levels:
        raise PyPowSyBlAdapterError("Voltage-level nominal_v data is required for per-unit conversion.")

    vl_nominal = {
        str(row["id"]): float(row["nominal_v"])
        for _, row in voltage_levels.iterrows()
        if not pd.isna(row["nominal_v"])
    }

    result: dict[str, float] = {}
    for _, bus in buses.iterrows():
        bus_id = str(bus["id"])
        vl_id = str(bus["voltage_level_id"])
        if vl_id not in vl_nominal:
            raise PyPowSyBlAdapterError(f"Missing nominal voltage for bus {bus_id} voltage level {vl_id}.")
        result[bus_id] = vl_nominal[vl_id]
    return result


def _voltage_limits_pu(voltage_level_id: str, voltage_levels: pd.DataFrame) -> tuple[float, float]:
    row = voltage_levels.loc[voltage_levels["id"].astype(str) == str(voltage_level_id)]
    if row.empty:
        return 0.9, 1.1
    vl = row.iloc[0]
    nominal = _float(vl, "nominal_v", 0.0)
    if nominal <= 0.0:
        return 0.9, 1.1
    low = _float(vl, "low_voltage_limit", np.nan)
    high = _float(vl, "high_voltage_limit", np.nan)
    vmin = low / nominal if not pd.isna(low) and low > 0.0 else 0.9
    vmax = high / nominal if not pd.isna(high) and high > 0.0 else 1.1
    return float(vmin), float(vmax)


def _infer_slack_bus(generators: pd.DataFrame, explicit_slack_bus_id: str | None) -> str:
    if explicit_slack_bus_id:
        return str(explicit_slack_bus_id)

    connected_gens = generators[generators.apply(_connected, axis=1)] if not generators.empty else generators
    regulating = connected_gens[
        connected_gens.get("voltage_regulator_on", pd.Series(False, index=connected_gens.index)).fillna(False)
    ]
    if not regulating.empty:
        return str(regulating.iloc[0]["bus_id"])
    if not connected_gens.empty:
        return str(connected_gens.iloc[0]["bus_id"])
    raise PyPowSyBlAdapterError("Cannot infer a slack bus because no connected generator exists.")


def _bus_types(buses: pd.DataFrame, generators: pd.DataFrame, slack_bus_id: str) -> dict[str, int]:
    regulating_buses = {
        str(row["bus_id"])
        for _, row in generators.iterrows()
        if _connected(row) and _bool(row, "voltage_regulator_on", False)
    }
    types = {str(row["id"]): PQ for _, row in buses.iterrows()}
    for bus_id in regulating_buses:
        if bus_id in types:
            types[bus_id] = PV
    if slack_bus_id not in types:
        raise PyPowSyBlAdapterError(f"Inferred slack bus {slack_bus_id!r} is not present in the bus table.")
    types[slack_bus_id] = SLACK
    return types


def _rate_pu(row: pd.Series, base_mva: float, element_id: str = "") -> float:
    for name in ("rated_s", "permanent_limit", "rate_a"):
        if name in row and not pd.isna(row[name]) and float(row[name]) > 0.0:
            return mva_to_pu(float(row[name]), base_mva)

    warnings.warn(
        f"Branch {element_id or '<unknown>'} has no imported rating "
        "(rated_s/permanent_limit/rate_a missing). "
        "Branch flow limits will be inactive unless scenario limits are assigned later.",
        UserWarning,
    )
    return 0.0


def _require_branch_buses(row: pd.Series, branch_id: str, bus_nominal_v: dict[str, float]) -> tuple[str, str]:
    f = str(row["bus1_id"])
    t = str(row["bus2_id"])
    if f not in bus_nominal_v or t not in bus_nominal_v:
        raise PyPowSyBlAdapterError(f"Connected branch {branch_id} references unsupported buses {f!r}, {t!r}.")
    return f, t


def _line_to_branch(
    row: pd.Series,
    index: int,
    base_mva: float,
    bus_nominal_v: dict[str, float],
) -> Branch:
    branch_id = str(row["id"])
    f, t = _require_branch_buses(row, branch_id, bus_nominal_v)
    v_fr = bus_nominal_v[f]
    v_to = bus_nominal_v[t]
    status = 1 if _connected(row) else 0

    if status == 1 and (_float(row, "x", np.nan) == 0.0 and _float(row, "r", np.nan) == 0.0):
        raise PyPowSyBlAdapterError(f"Connected line {branch_id} has zero impedance.")

    if abs(v_fr - v_to) <= 1e-9:
        r = impedance_ohm_to_pu(_float(row, "r"), v_fr, base_mva)
        x = impedance_ohm_to_pu(_float(row, "x"), v_fr, base_mva)
        g_fr = admittance_siemens_to_pu(_float(row, "g1"), v_fr, base_mva)
        b_fr = admittance_siemens_to_pu(_float(row, "b1"), v_fr, base_mva)
        g_to = admittance_siemens_to_pu(_float(row, "g2"), v_to, base_mva)
        b_to = admittance_siemens_to_pu(_float(row, "b2"), v_to, base_mva)
    else:
        # PyPowSyBl's IEEE 14 cross-voltage lines expose large b1/b2 values
        # associated with voltage-level conversion. Treating them as ordinary
        # pi-model line charging creates hundreds of Mvar of false reactive flow.
        # The series impedance matches OpenLoadFlow on sqrt(V_from * V_to) base.
        v_series = math.sqrt(v_fr * v_to)
        r = impedance_ohm_to_pu(_float(row, "r"), v_series, base_mva)
        x = impedance_ohm_to_pu(_float(row, "x"), v_series, base_mva)
        g_fr = b_fr = g_to = b_to = 0.0

    return Branch(
        id=branch_id,
        index=index,
        from_bus=f,
        to_bus=t,
        r=r,
        x=x,
        b=b_fr + b_to,
        b_fr=b_fr,
        b_to=b_to,
        g_fr=g_fr,
        g_to=g_to,
        rate_a=_rate_pu(row, base_mva, branch_id),
        tap=1.0,
        shift=0.0,
        angmin=-math.pi / 2,
        angmax=math.pi / 2,
        status=status,
        branch_type="line",
    )


def _transformer_tap(row: pd.Series, f: str, t: str, bus_nominal_v: dict[str, float]) -> float:
    rated_u1 = _float(row, "rated_u1", bus_nominal_v[f])
    rated_u2 = _float(row, "rated_u2", bus_nominal_v[t])
    if rated_u1 <= 0.0 or rated_u2 <= 0.0:
        return 1.0
    return (rated_u1 / bus_nominal_v[f]) / (rated_u2 / bus_nominal_v[t])


def _transformer_to_branch(
    row: pd.Series,
    index: int,
    base_mva: float,
    bus_nominal_v: dict[str, float],
) -> Branch:
    branch_id = str(row["id"])
    f, t = _require_branch_buses(row, branch_id, bus_nominal_v)
    v_to = bus_nominal_v[t]
    status = 1 if _connected(row) else 0

    if status == 1 and (_float(row, "x", np.nan) == 0.0 and _float(row, "r", np.nan) == 0.0):
        raise PyPowSyBlAdapterError(f"Connected transformer {branch_id} has zero impedance.")

    # PyPowSyBl's IEEE 14 transformer impedances are exposed on the lower-voltage
    # side. Converting on the high-voltage side makes x unrealistically small.
    r = impedance_ohm_to_pu(_float(row, "r"), v_to, base_mva)
    x = impedance_ohm_to_pu(_float(row, "x"), v_to, base_mva)
    g_fr = admittance_siemens_to_pu(_float(row, "g"), v_to, base_mva)
    b_fr = admittance_siemens_to_pu(_float(row, "b"), v_to, base_mva)

    return Branch(
        id=branch_id,
        index=index,
        from_bus=f,
        to_bus=t,
        r=r,
        x=x,
        b=b_fr,
        b_fr=b_fr,
        b_to=0.0,
        g_fr=g_fr,
        g_to=0.0,
        rate_a=_rate_pu(row, base_mva, branch_id),
        tap=_transformer_tap(row, f, t, bus_nominal_v),
        shift=0.0,
        angmin=-math.pi / 2,
        angmax=math.pi / 2,
        status=status,
        branch_type="two_windings_transformer",
    )


def pypowsybl_to_powercase(
    network: Any,
    *,
    name: str = "pypowsybl_network",
    base_mva: float = 100.0,
    slack_bus_id: str | None = None,
    assign_synthetic_costs: bool = True,
) -> PowerCase:
    buses_df = _table(network, "get_buses")
    lines_df = _table(network, "get_lines")
    transformers_df = _table(network, "get_2_windings_transformers")
    generators_df = _table(network, "get_generators")
    loads_df = _table(network, "get_loads")
    shunts_df = _table(network, "get_shunt_compensators")
    voltage_levels_df = _table(network, "get_voltage_levels")

    if buses_df.empty:
        raise PyPowSyBlAdapterError("PyPowSyBl network has no buses.")
    if generators_df.empty:
        raise PyPowSyBlAdapterError("PyPowSyBl network has no generators; slack/PV bus inference is impossible.")
    bus_nominal_v = _nominal_voltage_by_bus(buses_df, voltage_levels_df)
    slack_id = _infer_slack_bus(generators_df, slack_bus_id)
    types = _bus_types(buses_df, generators_df, slack_id)

    buses: list[Bus] = []
    bus_id_to_index: dict[str, int] = {}
    for idx, row in enumerate(buses_df.sort_values("id").itertuples(index=False), start=1):
        bus = pd.Series(row._asdict())
        bus_id = str(bus["id"])
        nominal = bus_nominal_v[bus_id]
        vmin, vmax = _voltage_limits_pu(str(bus["voltage_level_id"]), voltage_levels_df)
        vm_kv = _float(bus, "v_mag", nominal)
        va_deg = _float(bus, "v_angle", 0.0)
        buses.append(
            Bus(
                id=bus_id,
                index=idx,
                bus_type=types[bus_id],
                vm0=vm_kv / nominal,
                va0=math.radians(va_deg),
                vmin=vmin,
                vmax=vmax,
                nominal_v_kv=nominal,
            )
        )
        bus_id_to_index[bus_id] = idx - 1

    branches: list[Branch] = []
    branch_index = 1
    for _, line in lines_df.sort_values("id").iterrows():
        branches.append(_line_to_branch(line, branch_index, base_mva, bus_nominal_v))
        branch_index += 1
    for _, transformer in transformers_df.sort_values("id").iterrows():
        branches.append(_transformer_to_branch(transformer, branch_index, base_mva, bus_nominal_v))
        branch_index += 1

    generators: list[Generator] = []
    total_load_mw = sum(
        _float(load, "p0", _float(load, "p", 0.0))
        for _, load in loads_df.iterrows()
        if _connected(load)
    )
    for idx, gen in enumerate(generators_df.sort_values("id").itertuples(index=False), start=1):
        row = pd.Series(gen._asdict())
        bus_id = str(row["bus_id"])
        if _connected(row) and bus_id not in bus_id_to_index:
            raise PyPowSyBlAdapterError(f"Connected generator {row['id']} references unsupported bus {bus_id}.")
        gen_id = str(row["id"])
        target_p = _float(row, "target_p", _float(row, "p", 0.0))

        raw_pmin = _float(row, "min_p", 0.0)
        raw_pmax = _float(row, "max_p", max(target_p, total_load_mw))

        if assign_synthetic_costs:
            # Linear synthetic costs keep the DC OPF as an LP solvable by HiGHS.
            # These costs are only for testing our formulation on PyPowSyBl networks;
            # they are not comparable with PGLib/PowerModels OPF costs.
            cost_c2 = 0.0
            cost_c1 = 10.0 + idx
            cost_c0 = 0.0
        else:
            cost_c2 = 0.0
            cost_c1 = 0.0
            cost_c0 = 0.0

        # PyPowSyBl imports some PGLib/MATPOWER cases with placeholder active-power
        # bounds such as -9999 / 9999. These are not meaningful OPF limits.
        # Repair them only so experiments can run, but warn clearly because this
        # makes the case unsuitable for strict comparison with PGLib/PowerModels OPF.
        pmin_mw = raw_pmin
        pmax_mw = raw_pmax

        invalid_pmin = (not math.isfinite(raw_pmin)) or raw_pmin <= -9999.0
        invalid_pmax = (not math.isfinite(raw_pmax)) or abs(raw_pmax) >= 9999.0

        if invalid_pmin:
            warnings.warn(
                f"Generator {gen_id} has invalid/unbounded PyPowSyBl Pmin={raw_pmin}. "
                "Repaired Pmin to 0.0 MW.",
                UserWarning,
            )
            pmin_mw = 0.0

        if invalid_pmax or pmax_mw < pmin_mw:
            repaired_pmax = max(target_p, total_load_mw, pmin_mw + 1.0)
            warnings.warn(
                f"Generator {gen_id} has invalid/unbounded PyPowSyBl active-power "
                f"limits min_p={raw_pmin}, max_p={raw_pmax}. "
                f"Repaired Pmax to {repaired_pmax} MW. "
                "This is not suitable for strict OPF comparison with PGLib/PowerModels.",
                UserWarning,
            )
            pmax_mw = repaired_pmax
        generators.append(
            Generator(
                id=gen_id,
                index=idx,
                bus=bus_id,
                pg0=mw_to_pu(target_p, base_mva),
                qg0=mvar_to_pu(_float(row, "target_q", _float(row, "q", 0.0)), base_mva),
                pmin=mw_to_pu(pmin_mw, base_mva),
                pmax=mw_to_pu(pmax_mw, base_mva),
                qmin=mvar_to_pu(
                    _sanitize_power_bound(
                        _float(row, "min_q", -1e6),
                        default=-1e6,
                    ),
                    base_mva,
                ),
                qmax=mvar_to_pu(
                    _sanitize_power_bound(
                        _float(row, "max_q", 1e6),
                        default=1e6,
                    ),
                    base_mva,
                ),
                status=1 if _connected(row) else 0,
                voltage_regulator_on=_bool(row, "voltage_regulator_on", False),
                cost_c2=cost_c2,
                cost_c1=cost_c1,
                cost_c0=cost_c0,
            )
        )

    loads: list[Load] = []
    for idx, load in enumerate(loads_df.sort_values("id").itertuples(index=False), start=1):
        row = pd.Series(load._asdict())
        bus_id = str(row["bus_id"])
        if _connected(row) and bus_id not in bus_id_to_index:
            raise PyPowSyBlAdapterError(f"Connected load {row['id']} references unsupported bus {bus_id}.")
        loads.append(
            Load(
                id=str(row["id"]),
                index=idx,
                bus=bus_id,
                pd=mw_to_pu(_float(row, "p0", _float(row, "p", 0.0)), base_mva),
                qd=mvar_to_pu(_float(row, "q0", _float(row, "q", 0.0)), base_mva),
                status=1 if _connected(row) else 0,
            )
        )

    shunts: list[Shunt] = []
    for idx, shunt in enumerate(shunts_df.sort_values("id").itertuples(index=False), start=1):
        row = pd.Series(shunt._asdict())
        bus_id = str(row["bus_id"])
        if _connected(row) and bus_id not in bus_id_to_index:
            raise PyPowSyBlAdapterError(f"Connected shunt {row['id']} references unsupported bus {bus_id}.")
        nominal = bus_nominal_v.get(bus_id, 1.0)
        shunts.append(
            Shunt(
                id=str(row["id"]),
                index=idx,
                bus=bus_id,
                gs=admittance_siemens_to_pu(_float(row, "g"), nominal, base_mva),
                bs=admittance_siemens_to_pu(_float(row, "b"), nominal, base_mva),
                status=1 if _connected(row) else 0,
            )
        )

    case = PowerCase(
        name=name,
        base_mva=base_mva,
        buses=buses,
        branches=branches,
        generators=generators,
        loads=loads,
        shunts=shunts,
        slack_bus_id=slack_id,
        bus_id_to_index=bus_id_to_index,
    )
    validate_powercase(case)
    return case


def validate_powercase(case: PowerCase) -> None:
    if not case.buses:
        raise PyPowSyBlAdapterError("PowerCase has no buses.")
    if not case.branches:
        raise PyPowSyBlAdapterError("PowerCase has no branches.")
    if not case.generators:
        raise PyPowSyBlAdapterError("PowerCase has no generators.")
    if case.slack_bus_id not in case.bus_id_to_index:
        raise PyPowSyBlAdapterError(f"Slack bus {case.slack_bus_id!r} is not in the bus map.")

    bus_ids = set(case.bus_id_to_index)
    for branch in case.branches:
        if branch.status == 1:
            if branch.from_bus not in bus_ids or branch.to_bus not in bus_ids:
                raise PyPowSyBlAdapterError(f"Connected branch {branch.id} references missing buses.")
            if not np.isfinite(branch.r) or not np.isfinite(branch.x):
                raise PyPowSyBlAdapterError(f"Connected branch {branch.id} has non-finite impedance.")
            if abs(branch.r) < 1e-14 and abs(branch.x) < 1e-14:
                raise PyPowSyBlAdapterError(f"Connected branch {branch.id} has zero impedance.")

    for load in case.loads:
        if load.status == 1 and load.bus not in bus_ids:
            raise PyPowSyBlAdapterError(f"Connected load {load.id} references missing bus {load.bus}.")
    for gen in case.generators:
        if gen.status == 1 and gen.bus not in bus_ids:
            raise PyPowSyBlAdapterError(f"Connected generator {gen.id} references missing bus {gen.bus}.")
