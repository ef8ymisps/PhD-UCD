from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd


PYTHON_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_DIR.parent
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.powermodels_csv_adapter import load_powercase_from_powermodels_json  # noqa: E402
from own_models.src.analysis.pypowsybl_security_validation import (  # noqa: E402
    _base_gate,
    _branch_equipment_type,
    _pp_bus_id,
    build_pypowsybl_network_from_powercase,
)


def _case5():
    return load_powercase_from_powermodels_json(
        REPO_ROOT
        / "results"
        / "reference"
        / "powermodels"
        / "ac_opf"
        / "pglib_opf_case5_pjm_base_case.json"
    )


class PyPowSyBlSecurityValidationTests(unittest.TestCase):
    def test_branch_equipment_ids_are_preserved(self):
        case = _case5()
        build = build_pypowsybl_network_from_powercase(case)
        active_branches = {branch.id for branch in case.branches if branch.status == 1}
        mapped = set(build.branch_map["pypowsybl_equipment_id"].astype(str))
        self.assertTrue(active_branches.issubset(mapped))
        self.assertTrue((build.branch_map["own_branch_id"] == build.branch_map["pypowsybl_equipment_id"]).all())

    def test_non_branch_bus_ids_are_namespaced(self):
        case = _case5()
        build = build_pypowsybl_network_from_powercase(case)
        self.assertIn(_pp_bus_id(case.buses[0].id), set(build.branch_map["pypowsybl_from_bus_id"].astype(str)))

    def test_voltage_limits_round_trip(self):
        case = _case5()
        build = build_pypowsybl_network_from_powercase(case)
        rows = build.voltage_limit_validation
        self.assertLess((rows["own_vmin_pu"] - rows["converted_low_limit_pu"]).abs().max(), 1e-12)
        self.assertLess((rows["own_vmax_pu"] - rows["converted_high_limit_pu"]).abs().max(), 1e-12)

    def test_base_gate_blocks_failed_base_state(self):
        case = _case5()
        comparison = pd.DataFrame(
            [
                {
                    "component_type": "bus",
                    "component_id": "1",
                    "metric": "vm_pu",
                    "diff": 1.0,
                    "base_lf_converged": False,
                    "slack_bus_match": False,
                }
            ]
        )
        gate = _base_gate(comparison, case)
        self.assertFalse(gate["base_state_valid"])

    def test_case5_has_only_line_classifications(self):
        case = _case5()
        classifications = {_branch_equipment_type(case, branch) for branch in case.branches}
        self.assertEqual(classifications, {"line"})


if __name__ == "__main__":
    unittest.main()
