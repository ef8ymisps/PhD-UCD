from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


PYTHON_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_DIR.parent
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.adapters.powermodels_csv_adapter import load_powercase_from_powermodels_json  # noqa: E402
from own_models.src.data.case import PQ, PV, Shunt  # noqa: E402
from own_models.src.pf.ac_power_flow import (  # noqa: E402
    allocate_qg_to_generators,
    solve_ac_power_flow,
    solve_ac_power_flow_newton,
    solve_ac_power_flow_with_q_limits,
)


def _case14():
    return load_powercase_from_powermodels_json(
        REPO_ROOT
        / "results"
        / "reference"
        / "powermodels"
        / "ac_opf"
        / "pglib_opf_case14_ieee_base_case.json"
    )


def _pv_generator(case):
    pv_buses = {bus.id for bus in case.buses if bus.bus_type == PV}
    return next(gen for gen in case.generators if gen.status == 1 and gen.bus in pv_buses)


def _replace_generator(case, target_id, replacement):
    return replace(
        case,
        generators=[replacement if gen.id == target_id else gen for gen in case.generators],
    )


class ACQSwitchingTests(unittest.TestCase):
    def test_default_fixed_type_behavior_unchanged(self):
        case = _case14()
        old = solve_ac_power_flow_newton(case)
        public = solve_ac_power_flow(case)
        self.assertTrue(old.converged)
        self.assertTrue(public.converged)
        self.assertEqual(public.switched_buses, ())
        self.assertEqual(public.termination_reason, "fixed_type_converged")
        self.assertLess(abs(old.mismatch_norm - public.mismatch_norm), 1e-12)

    def test_no_q_violation_has_no_switching(self):
        case = _case14()
        wide = replace(
            case,
            generators=[
                replace(gen, qmin=-10.0, qmax=10.0) if gen.status == 1 else gen
                for gen in case.generators
            ],
        )
        result = solve_ac_power_flow(wide, enforce_q_limits=True)
        self.assertTrue(result.converged)
        self.assertTrue(result.q_limit_converged)
        self.assertEqual(result.switched_buses, ())
        self.assertEqual(result.termination_reason, "q_limits_satisfied")

    def test_upper_q_violation_switches_pv_to_pq(self):
        case = _case14()
        base = solve_ac_power_flow_newton(case)
        gen = _pv_generator(case)
        required = base.qg_by_bus[gen.bus]
        modified = _replace_generator(case, gen.id, replace(gen, qmin=required - 1.0, qmax=required - 1e-3))
        result = solve_ac_power_flow_with_q_limits(modified)
        self.assertTrue(result.converged)
        self.assertIn(gen.bus, result.switched_buses)
        self.assertEqual(result.final_bus_types[gen.bus], PQ)
        event = next(event for event in result.switching_events if event.bus_id == gen.bus)
        self.assertEqual(event.violation_direction, "upper")
        self.assertAlmostEqual(event.fixed_q, required - 1e-3, places=8)

    def test_lower_q_violation_switches_pv_to_pq(self):
        case = _case14()
        base = solve_ac_power_flow_newton(case)
        gen = _pv_generator(case)
        required = base.qg_by_bus[gen.bus]
        modified = _replace_generator(case, gen.id, replace(gen, qmin=required + 1e-3, qmax=required + 1.0))
        result = solve_ac_power_flow_with_q_limits(modified)
        self.assertTrue(result.converged)
        self.assertIn(gen.bus, result.switched_buses)
        self.assertEqual(result.final_bus_types[gen.bus], PQ)
        event = next(event for event in result.switching_events if event.bus_id == gen.bus)
        self.assertEqual(event.violation_direction, "lower")

    def test_multiple_generator_allocation_respects_limits(self):
        gens = [
            SimpleNamespace(id="g1", index=1, qmin=0.0, qmax=1.0),
            SimpleNamespace(id="g2", index=2, qmin=-1.0, qmax=3.0),
        ]
        result = allocate_qg_to_generators(gens, 2.0)
        self.assertTrue(result.aggregate_feasible)
        self.assertAlmostEqual(sum(result.individual_allocation.values()), 2.0)
        self.assertGreaterEqual(result.individual_allocation["g1"], 0.0)
        self.assertLessEqual(result.individual_allocation["g1"], 1.0)
        self.assertGreaterEqual(result.individual_allocation["g2"], -1.0)
        self.assertLessEqual(result.individual_allocation["g2"], 3.0)

    def test_aggregate_q_infeasibility_is_reported_by_allocator(self):
        gens = [SimpleNamespace(id="g1", index=1, qmin=0.0, qmax=1.0)]
        result = allocate_qg_to_generators(gens, 2.0)
        self.assertFalse(result.aggregate_feasible)
        self.assertAlmostEqual(result.individual_allocation["g1"], 1.0)

    def test_slack_q_violation_is_reported_without_switching_slack(self):
        case = _case14()
        base = solve_ac_power_flow_newton(case)
        slack_gen = next(gen for gen in case.generators if gen.bus == case.slack_bus_id and gen.status == 1)
        required = base.qg_by_bus[case.slack_bus_id]
        modified = _replace_generator(case, slack_gen.id, replace(slack_gen, qmax=required - 1e-3))
        result = solve_ac_power_flow_with_q_limits(modified)
        self.assertTrue(result.converged)
        self.assertTrue(result.slack_q_violation)
        self.assertNotIn(case.slack_bus_id, result.switched_buses)
        self.assertEqual(result.final_bus_types[case.slack_bus_id], 3)

    def test_bus_shunt_is_not_double_counted_after_switching(self):
        case = _case14()
        gen = _pv_generator(case)
        base = solve_ac_power_flow_newton(case)
        required = base.qg_by_bus[gen.bus]
        modified_gen = replace(gen, qmin=required - 1.0, qmax=required - 0.2)
        shunt = Shunt("test_shunt", 999, gen.bus, 0.0, 0.25, 1)
        modified = replace(
            _replace_generator(case, gen.id, modified_gen),
            shunts=case.shunts + [shunt],
        )
        result = solve_ac_power_flow_with_q_limits(modified)
        self.assertTrue(result.converged)
        event = next(event for event in result.switching_events if event.bus_id == gen.bus)
        self.assertAlmostEqual(event.fixed_q, modified_gen.qmax, places=8)

    def test_original_case_bus_type_is_unchanged(self):
        case = _case14()
        gen = _pv_generator(case)
        base = solve_ac_power_flow_newton(case)
        modified = _replace_generator(case, gen.id, replace(gen, qmax=base.qg_by_bus[gen.bus] - 1e-3))
        original_type = next(bus.bus_type for bus in modified.buses if bus.id == gen.bus)
        result = solve_ac_power_flow_with_q_limits(modified)
        after_type = next(bus.bus_type for bus in modified.buses if bus.id == gen.bus)
        self.assertIn(gen.bus, result.switched_buses)
        self.assertEqual(original_type, PV)
        self.assertEqual(after_type, PV)


if __name__ == "__main__":
    unittest.main()
