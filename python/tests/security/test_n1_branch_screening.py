from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

import numpy as np


PYTHON_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_DIR.parent
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.powermodels_csv_adapter import load_powercase_from_powermodels_json  # noqa: E402
from own_models.src.analysis.contingencies import apply_contingency, generate_branch_contingencies  # noqa: E402
from own_models.src.analysis.security_checks import evaluate_security_checks  # noqa: E402


def _case5():
    return load_powercase_from_powermodels_json(
        REPO_ROOT
        / "results"
        / "reference"
        / "powermodels"
        / "ac_opf"
        / "pglib_opf_case5_pjm_base_case.json"
    )


def _fake_pf_result(case, *, vm=None, branch_rate_multiplier=0.0):
    vm_values = np.array(vm if vm is not None else [bus.vm0 for bus in case.buses], dtype=float)
    va_values = np.array([bus.va0 for bus in case.buses], dtype=float)
    pcalc = np.zeros(len(case.buses), dtype=float)
    qcalc = np.zeros(len(case.buses), dtype=float)
    branch_flows = {}
    for branch in case.branches:
        if branch.status != 1:
            continue
        flow = branch.rate_a * branch_rate_multiplier if branch.rate_a > 0 else 0.0
        branch_flows[branch.id] = {"pf": flow, "qf": 0.0, "pt": -flow, "qt": 0.0}
    return SimpleNamespace(
        converged=True,
        iterations=1,
        vm=vm_values,
        va=va_values,
        pcalc=pcalc,
        qcalc=qcalc,
        branch_flows=branch_flows,
        mismatch_norm=0.0,
    )


class N1BranchScreeningTests(unittest.TestCase):
    def test_generate_active_branch_contingencies(self):
        case = _case5()
        contingencies = generate_branch_contingencies(case)
        active_branch_count = sum(branch.status == 1 for branch in case.branches)
        self.assertEqual(len(contingencies), active_branch_count)
        self.assertEqual(len({contingency.id for contingency in contingencies}), len(contingencies))

    def test_apply_contingency_is_immutable(self):
        case = _case5()
        contingency = generate_branch_contingencies(case)[0]
        modified = apply_contingency(case, contingency)
        outaged_id = contingency.outaged_branch_ids[0]
        self.assertEqual(next(branch.status for branch in case.branches if branch.id == outaged_id), 1)
        self.assertEqual(next(branch.status for branch in modified.branches if branch.id == outaged_id), 0)

    def test_voltage_violation_detector(self):
        case = _case5()
        low_voltage = [bus.vmin - 0.05 for bus in case.buses]
        result = evaluate_security_checks(case, _fake_pf_result(case, vm=low_voltage), {})
        self.assertTrue(result.has_voltage_violation)

    def test_thermal_violation_detector(self):
        case = _case5()
        result = evaluate_security_checks(case, _fake_pf_result(case, branch_rate_multiplier=1.1), {})
        self.assertTrue(result.has_thermal_violation)

    def test_reactive_violation_detector(self):
        case = _case5()
        qcalc = np.zeros(len(case.buses), dtype=float)
        for bus_id, idx in case.bus_id_to_index.items():
            qcalc[idx] = 100.0
        pf_result = _fake_pf_result(case)
        pf_result.qcalc[:] = qcalc
        result = evaluate_security_checks(case, pf_result, {})
        self.assertTrue(result.has_reactive_violation)

    def test_active_generation_violation_detector(self):
        case = _case5()
        pcalc = np.zeros(len(case.buses), dtype=float)
        pcalc[case.bus_id_to_index[case.slack_bus_id]] = 100.0
        pf_result = _fake_pf_result(case)
        pf_result.pcalc[:] = pcalc
        result = evaluate_security_checks(case, pf_result, {})
        self.assertTrue(result.has_active_generation_violation)

    def test_angle_violation_detector(self):
        case = _case5()
        tight_branch = replace(case.branches[0], angmin=-1e-4, angmax=1e-4)
        modified = replace(case, branches=[tight_branch] + case.branches[1:])
        pf_result = _fake_pf_result(modified)
        pf_result.va[modified.bus_id_to_index[tight_branch.from_bus]] = 0.1
        pf_result.va[modified.bus_id_to_index[tight_branch.to_bus]] = 0.0
        result = evaluate_security_checks(modified, pf_result, {})
        self.assertTrue(result.has_angle_violation)


if __name__ == "__main__":
    unittest.main()
