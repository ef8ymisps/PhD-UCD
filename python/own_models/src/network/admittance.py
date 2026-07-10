from __future__ import annotations

import cmath

import numpy as np

from own_models.src.data.case import PowerCase


def build_admittance_matrix(case: PowerCase) -> np.ndarray:
    """Build the full complex AC bus-admittance matrix Ybus."""
    # Full AC Ybus, including branch-side shunts and off-nominal complex taps.
    # The tap is applied on the from side as tap_mag * exp(j * shift).
    nb = len(case.buses)
    ybus = np.zeros((nb, nb), dtype=complex)

    for branch in case.branches:
        if branch.status == 0:
            continue
        if abs(branch.r) < 1e-14 and abs(branch.x) < 1e-14:
            raise ValueError(f"Branch {branch.id} has near-zero impedance.")

        i = case.bus_id_to_index[branch.from_bus]
        j = case.bus_id_to_index[branch.to_bus]

        y = 1.0 / complex(branch.r, branch.x)
        y_fr = complex(branch.g_fr, branch.b_fr)
        y_to = complex(branch.g_to, branch.b_to)
        tap_mag = branch.tap if branch.tap != 0.0 else 1.0
        tap = tap_mag * cmath.exp(1j * branch.shift)

        ybus[i, i] += (y + y_fr) / abs(tap) ** 2
        ybus[i, j] += -y / tap.conjugate()
        ybus[j, i] += -y / tap
        ybus[j, j] += y + y_to

    for shunt in case.shunts:
        if shunt.status == 1:
            i = case.bus_id_to_index[shunt.bus]
            ybus[i, i] += complex(shunt.gs, shunt.bs)

    return ybus


def calc_bus_power_injections(ybus: np.ndarray, vm: np.ndarray, va: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Calculate bus active/reactive injections from voltage and Ybus."""
    # Positive P/Q are injections into the network: S = V * conj(YV).
    voltage = vm * np.exp(1j * va)
    s = voltage * np.conjugate(ybus @ voltage)
    return s.real, s.imag


def calc_ac_branch_flows(case: PowerCase, vm: np.ndarray, va: np.ndarray) -> dict[str, dict[str, float]]:
    """Calculate from-side and to-side AC branch P/Q flows."""
    # Return both terminal flows; taps and charging can make from/to apparent
    # powers differ, so thermal checks need both ends.
    voltage = vm * np.exp(1j * va)
    flows: dict[str, dict[str, float]] = {}

    for branch in case.branches:
        if branch.status == 0:
            continue

        i = case.bus_id_to_index[branch.from_bus]
        j = case.bus_id_to_index[branch.to_bus]
        y = 1.0 / complex(branch.r, branch.x)
        y_fr = complex(branch.g_fr, branch.b_fr)
        y_to = complex(branch.g_to, branch.b_to)
        tap_mag = branch.tap if branch.tap != 0.0 else 1.0
        tap = tap_mag * cmath.exp(1j * branch.shift)

        yff = (y + y_fr) / abs(tap) ** 2
        yft = -y / tap.conjugate()
        ytf = -y / tap
        ytt = y + y_to

        vf = voltage[i]
        vt = voltage[j]
        sf = vf * np.conjugate(yff * vf + yft * vt)
        st = vt * np.conjugate(ytf * vf + ytt * vt)

        flows[branch.id] = {
            "pf": float(sf.real),
            "qf": float(sf.imag),
            "pt": float(st.real),
            "qt": float(st.imag),
        }

    return flows
