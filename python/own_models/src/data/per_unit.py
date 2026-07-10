from __future__ import annotations


def to_per_unit(value: float, base: float) -> float:
    """Scale a physical value by its base value to get a per-unit value."""
    return value / base


def from_per_unit(value_pu: float, base: float) -> float:
    """Scale a per-unit value back to the physical unit defined by base."""
    return value_pu * base


def z_base_ohm(nominal_v_kv: float, base_mva: float) -> float:
    """Compute impedance base in ohms for a voltage level and system MVA base."""
    return nominal_v_kv**2 / base_mva


def impedance_ohm_to_pu(value_ohm: float, nominal_v_kv: float, base_mva: float) -> float:
    """Convert an impedance from ohms to per-unit on the given voltage/MVA base."""
    return value_ohm / z_base_ohm(nominal_v_kv, base_mva)


def admittance_siemens_to_pu(value_siemens: float, nominal_v_kv: float, base_mva: float) -> float:
    """Convert an admittance from siemens to per-unit on the reciprocal impedance base."""
    return value_siemens * z_base_ohm(nominal_v_kv, base_mva)


def mw_to_pu(value_mw: float, base_mva: float) -> float:
    """Convert active power in MW to per-unit on the system MVA base."""
    return value_mw / base_mva


def mvar_to_pu(value_mvar: float, base_mva: float) -> float:
    """Convert reactive power in Mvar to per-unit on the system MVA base."""
    return value_mvar / base_mva


def mva_to_pu(value_mva: float, base_mva: float) -> float:
    """Convert apparent power in MVA to per-unit on the system MVA base."""
    return value_mva / base_mva
