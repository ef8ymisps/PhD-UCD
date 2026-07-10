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
from own_models.src.analysis.balancing_policy import apply_balancing_policy  # noqa: E402
from own_models.src.analysis.contingencies import (  # noqa: E402
    Contingency,
    ContingencyElement,
    apply_contingency,
    generate_generator_contingencies,
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


def _opf_result(case, pg_by_generator):
    return SimpleNamespace(
        pg={gen.id: pg_by_generator.get(gen.id, gen.pg0) for gen in case.generators},
        qg={gen.id: gen.qg0 for gen in case.generators},
        vm={bus.id: bus.vm0 for bus in case.buses},
        va={bus.id: bus.va0 for bus in case.buses},
    )


def _generator_contingency(gen_id: str) -> Contingency:
    return Contingency(
        id=f"generator_{gen_id}",
        k=1,
        elements=(ContingencyElement("generator", gen_id),),
    )


class N1GeneratorScreeningTests(unittest.TestCase):
    def test_generate_active_generator_contingencies(self):
        case = _case5()
        contingencies = generate_generator_contingencies(case)
        active_generator_count = sum(gen.status == 1 for gen in case.generators)
        self.assertEqual(len(contingencies), active_generator_count)
        self.assertEqual(len({contingency.id for contingency in contingencies}), len(contingencies))

    def test_apply_generator_contingency_is_immutable(self):
        case = _case5()
        contingency = generate_generator_contingencies(case)[0]
        modified = apply_contingency(case, contingency)
        outaged_id = contingency.outaged_generator_ids[0]
        self.assertEqual(next(gen.status for gen in case.generators if gen.id == outaged_id), 1)
        self.assertEqual(next(gen.status for gen in modified.generators if gen.id == outaged_id), 0)
        self.assertEqual(next(gen.pg0 for gen in modified.generators if gen.id == outaged_id), 0.0)

    def test_balancing_policy_uses_surviving_base_reserves(self):
        case = _case5()
        active_generators = [gen for gen in case.generators if gen.status == 1]
        outaged = active_generators[0]
        pg = {gen.id: 0.10 for gen in active_generators}
        generous_generators = [
            replace(gen, pmax=2.0, pg0=pg[gen.id]) if gen.status == 1 else gen
            for gen in case.generators
        ]
        generous_case = replace(case, generators=generous_generators)
        contingency = _generator_contingency(outaged.id)
        contingency_case = apply_contingency(generous_case, contingency)

        _, balancing = apply_balancing_policy(contingency_case, _opf_result(generous_case, pg), contingency)

        self.assertTrue(balancing.success)
        self.assertNotIn(outaged.id, balancing.responding_generator_ids)
        self.assertAlmostEqual(balancing.scheduled_replacement_error, 0.0, places=9)
        self.assertAlmostEqual(
            sum(balancing.scheduled_pg_by_generator.values()),
            sum(pg.values()),
            places=9,
        )

    def test_balancing_policy_reports_insufficient_reserve(self):
        case = _case5()
        active_generators = [gen for gen in case.generators if gen.status == 1]
        outaged = active_generators[0]
        pg = {gen.id: 0.10 for gen in active_generators}
        no_reserve_generators = [
            replace(gen, pmax=pg[gen.id], pg0=pg[gen.id]) if gen.status == 1 else gen
            for gen in case.generators
        ]
        no_reserve_case = replace(case, generators=no_reserve_generators)
        contingency = _generator_contingency(outaged.id)
        contingency_case = apply_contingency(no_reserve_case, contingency)

        _, balancing = apply_balancing_policy(contingency_case, _opf_result(no_reserve_case, pg), contingency)

        self.assertFalse(balancing.success)
        self.assertTrue(balancing.has_generation_imbalance)
        self.assertFalse(balancing.pf_attempted)
        self.assertAlmostEqual(balancing.uncovered_power_deficit_p, pg[outaged.id], places=9)

    def test_slack_outage_reassigns_to_surviving_generator(self):
        case = _case5()
        slack_generators = [
            gen for gen in case.generators if gen.status == 1 and gen.bus == case.slack_bus_id
        ]
        self.assertTrue(slack_generators)
        outaged = slack_generators[0]
        pg = {gen.id: 0.10 for gen in case.generators if gen.status == 1}
        generators = [
            replace(gen, pmax=2.0, pg0=pg[gen.id]) if gen.status == 1 else gen
            for gen in case.generators
        ]
        modified = replace(case, generators=generators)
        contingency = _generator_contingency(outaged.id)
        contingency_case = apply_contingency(modified, contingency)

        balanced_case, balancing = apply_balancing_policy(contingency_case, _opf_result(modified, pg), contingency)
        new_slack = next(gen for gen in balanced_case.generators if gen.id == balancing.new_slack_generator_id)

        self.assertNotEqual(balancing.new_slack_generator_id, outaged.id)
        self.assertEqual(balanced_case.slack_bus_id, new_slack.bus)


if __name__ == "__main__":
    unittest.main()
