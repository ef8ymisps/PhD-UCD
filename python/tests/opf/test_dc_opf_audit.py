from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PYTHON_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PYTHON_DIR))

from own_models.src.opf.dc_opf import DCOPFResult, solve_dc_opf, solve_dc_opf_scipy  # noqa: E402
from own_models.src.opf.dc_opf_audit import (  # noqa: E402
    audit_dc_opf_equations,
    compare_scipy_pyomo_dc_opf,
    describe_dc_opf_model,
    make_three_bus_dc_case,
    make_two_bus_dc_case,
    make_two_bus_lossy_dc_case,
)
from own_models.src.opf.dc_opf_pyomo import (  # noqa: E402
    build_dc_opf_pyomo_model,
    extract_dc_opf_pyomo_result,
    solve_dc_opf_pyomo,
)
from own_models.src.data.case import Branch  # noqa: E402
from own_models.src.network.dc import dc_branch_conductance, dc_branch_flow_coefficient  # noqa: E402
from own_models.src.opf.solver_utils import find_available_pyomo_solver  # noqa: E402


PYOMO_AVAILABLE = importlib.util.find_spec("pyomo") is not None


class DCOPFAuditTests(unittest.TestCase):
    def test_default_solve_dc_opf_calls_scipy_wrapper(self):
        case = make_two_bus_dc_case()
        expected = DCOPFResult(
            success=True,
            status="mock",
            message="mock",
            objective=0.0,
            pg={},
            theta={},
            branch_flows={},
            max_power_balance_violation=0.0,
        )
        with patch("own_models.src.opf.dc_opf.solve_dc_opf_scipy", return_value=expected) as mocked:
            result = solve_dc_opf(case)
        mocked.assert_called_once_with(
            case,
            method="auto",
            loss_model="lossless",
            max_iter=1000,
            ftol=1e-9,
            feasibility_tol=1e-6,
        )
        self.assertIs(result, expected)

    def test_two_bus_case_has_manual_solution_and_mw_scaled_objective(self):
        result = solve_dc_opf(make_two_bus_dc_case())
        self.assertTrue(result.success)
        self.assertAlmostEqual(result.pg["G1"], 1.0, places=10)
        self.assertAlmostEqual(result.theta["1"], 0.0, places=10)
        self.assertAlmostEqual(result.theta["2"], -0.1, places=10)
        self.assertAlmostEqual(result.branch_flows["L1"], 1.0, places=10)
        self.assertAlmostEqual(result.objective, 2003.0, places=10)
        self.assertLess(result.max_power_balance_violation, 1e-10)

    def test_branch_flow_coefficient_is_resistance_adjusted_not_classical(self):
        """The DC baseline uses resistance-adjusted lossless beta, not classical 1/x."""
        branch = Branch(
            "R1",
            1,
            "1",
            "2",
            0.1,
            0.2,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            10.0,
            1.0,
            0.0,
            -3.14,
            3.14,
            1,
        )
        beta = dc_branch_flow_coefficient(branch)
        conductance = dc_branch_conductance(branch)
        self.assertAlmostEqual(beta, 4.0)
        self.assertAlmostEqual(conductance, 2.0)
        self.assertNotAlmostEqual(beta, 5.0)

    def test_two_bus_castillo_lossy_case_matches_manual_solution(self):
        result = solve_dc_opf_scipy(make_two_bus_lossy_dc_case(), loss_model="castillo_quadratic")
        delta = 2.0 - 3.0**0.5
        expected_loss = 2.0 * delta**2
        expected_p_from = 4.0 * delta + delta**2
        self.assertTrue(result.success, result.message)
        self.assertAlmostEqual(result.pg["G1"], expected_p_from, places=6)
        self.assertAlmostEqual(result.theta["1"], 0.0, places=6)
        self.assertAlmostEqual(result.theta["2"], -delta, places=6)
        self.assertAlmostEqual(result.branch_flows_from["L1"], expected_p_from, places=6)
        self.assertAlmostEqual(result.branch_flows_to["L1"], -1.0, places=6)
        self.assertAlmostEqual(result.branch_losses["L1"], expected_loss, places=6)
        self.assertAlmostEqual(
            result.branch_flows_from["L1"] + result.branch_flows_to["L1"],
            result.branch_losses["L1"],
            places=8,
        )

    def test_describe_dc_opf_model_reports_ownership_diagnostics(self):
        summary = describe_dc_opf_model(make_three_bus_dc_case())
        self.assertEqual(summary["model"], "lossless DC OPF with resistance-adjusted branch-flow coefficients")
        self.assertEqual(summary["num_buses"], 3)
        self.assertEqual(summary["num_active_generators"], 2)
        self.assertEqual(summary["slack_bus_id"], "1")
        self.assertEqual(summary["coefficient_mode"], "resistance_adjusted")
        self.assertAlmostEqual(summary["total_active_demand_pu"], 1.25)
        self.assertAlmostEqual(summary["total_active_demand_mw"], 125.0)
        self.assertAlmostEqual(summary["total_active_shunt_conductance_included_pu"], 0.05)
        self.assertIn("2", summary["buses_with_no_generator_but_nonzero_demand"])
        self.assertIn("L2", summary["active_branch_beta"])
        self.assertTrue(summary["branches_where_resistance_materially_changes_beta"])

    def test_audit_dc_opf_equations_returns_human_readable_equations(self):
        audit = audit_dc_opf_equations(make_two_bus_dc_case())
        text = audit["text"]
        self.assertIn("Pg[G1] in [0, 2] pu", text)
        self.assertIn("P_from[L1] = 10 * DeltaTheta[L1]", text)
        self.assertIn("Bus 1: Pg[G1] - 0 == P_from[L1]", text)
        self.assertIn("Bus 2: 0 - 1 == P_to[L1]", text)
        self.assertIn("theta[1] == 0", text)
        self.assertIn("lossy DC OPF", text)
        self.assertIn("AC OPF", text)

    def test_audit_dc_opf_equations_supports_castillo_lossy_equations(self):
        text = audit_dc_opf_equations(
            make_two_bus_lossy_dc_case(),
            loss_model="castillo_quadratic",
        )["text"]
        self.assertIn("Castillo-style lossy DC OPF", text)
        self.assertIn("P_loss[L1] = 2 * DeltaTheta[L1]^2", text)
        self.assertIn("P_from[L1] = 4 * DeltaTheta[L1] + 0.5 * P_loss[L1]", text)
        self.assertIn("P_to[L1] = -4 * DeltaTheta[L1] + 0.5 * P_loss[L1]", text)
        self.assertNotIn("lossy DC OPF, AC OPF", text)

    def test_unsupported_coefficient_mode_raises_value_error(self):
        case = make_two_bus_dc_case()
        with self.assertRaises(ValueError):
            describe_dc_opf_model(case, coefficient_mode="classical")
        with self.assertRaises(ValueError):
            audit_dc_opf_equations(case, coefficient_mode="classical")
        with self.assertRaises(ValueError):
            build_dc_opf_pyomo_model(case, coefficient_mode="classical")
        with self.assertRaises(ValueError):
            solve_dc_opf_scipy(case, loss_model="unsupported")
        with self.assertRaises(ValueError):
            build_dc_opf_pyomo_model(case, loss_model="unsupported")

    def test_pyomo_castillo_rejects_linear_solvers(self):
        with self.assertRaisesRegex(RuntimeError, "requires a nonlinear solver"):
            solve_dc_opf_pyomo(make_two_bus_lossy_dc_case(), loss_model="castillo_quadratic", tee=False)

    @unittest.skipUnless(PYOMO_AVAILABLE, "Pyomo is not installed")
    def test_pyomo_model_construction_and_sign_convention(self):
        model = build_dc_opf_pyomo_model(make_two_bus_dc_case())
        self.assertEqual(sorted(str(bus_id) for bus_id in model.BUS), ["1", "2"])
        self.assertEqual(sorted(str(gen_id) for gen_id in model.GEN), ["G1"])
        self.assertEqual(sorted(str(branch_id) for branch_id in model.BRANCH), ["L1"])
        self.assertAlmostEqual(model._beta["L1"], 10.0)

        model.Pg["G1"].set_value(1.0)
        model.theta["1"].set_value(0.0)
        model.theta["2"].set_value(-0.1)
        pyomo_result = extract_dc_opf_pyomo_result(model)
        self.assertAlmostEqual(pyomo_result.branch_flows["L1"], 1.0)
        self.assertAlmostEqual(pyomo_result.branch_flows_from["L1"], 1.0)
        self.assertAlmostEqual(pyomo_result.branch_flows_to["L1"], -1.0)
        self.assertAlmostEqual(pyomo_result.branch_losses["L1"], 0.0)
        self.assertLess(pyomo_result.max_power_balance_violation, 1e-12)

    @unittest.skipUnless(PYOMO_AVAILABLE, "Pyomo is not installed")
    def test_pyomo_castillo_model_extraction_matches_manual_values(self):
        case = make_two_bus_lossy_dc_case()
        model = build_dc_opf_pyomo_model(case, loss_model="castillo_quadratic")
        delta = 2.0 - 3.0**0.5
        model.Pg["G1"].set_value(4.0 * delta + delta**2)
        model.theta["1"].set_value(0.0)
        model.theta["2"].set_value(-delta)
        pyomo_result = extract_dc_opf_pyomo_result(model)
        self.assertAlmostEqual(pyomo_result.branch_flows_from["L1"], 4.0 * delta + delta**2)
        self.assertAlmostEqual(pyomo_result.branch_flows_to["L1"], -1.0)
        self.assertAlmostEqual(pyomo_result.branch_losses["L1"], 2.0 * delta**2)
        self.assertLess(pyomo_result.max_power_balance_violation, 1e-10)

    @unittest.skipUnless(PYOMO_AVAILABLE, "Pyomo is not installed")
    def test_scipy_and_pyomo_extraction_match_on_deterministic_case(self):
        case = make_two_bus_dc_case()
        scipy_result = solve_dc_opf_scipy(case)
        model = build_dc_opf_pyomo_model(case)
        for gen_id, value in scipy_result.pg.items():
            model.Pg[gen_id].set_value(value)
        for bus_id, value in scipy_result.theta.items():
            model.theta[bus_id].set_value(value)
        pyomo_result = extract_dc_opf_pyomo_result(model)

        report = compare_scipy_pyomo_dc_opf(scipy_result, pyomo_result, tolerance=1e-10)
        self.assertTrue(report["passed"], report["mismatches"])
        self.assertAlmostEqual(report["objective_difference"], 0.0)
        self.assertAlmostEqual(report["max_branch_flow_difference"], 0.0)

    def test_compare_scipy_pyomo_dc_opf_reports_mismatches(self):
        left = solve_dc_opf(make_two_bus_dc_case())
        right = DCOPFResult(
            success=True,
            status="mock",
            message="mock",
            objective=left.objective + 1.0,
            pg={"G1": left.pg["G1"] + 0.1},
            theta=left.theta,
            branch_flows=left.branch_flows,
            max_power_balance_violation=left.max_power_balance_violation,
        )
        report = compare_scipy_pyomo_dc_opf(left, right, tolerance=1e-6)
        self.assertFalse(report["passed"])
        self.assertGreater(report["objective_difference"], 0.0)
        self.assertGreater(report["max_pg_difference"], 0.0)

    def test_find_available_pyomo_solver_returns_none_when_no_preferred_solver_exists(self):
        with patch("own_models.src.opf.solver_utils.is_pyomo_solver_available", return_value=False):
            self.assertIsNone(find_available_pyomo_solver(preferred=("appsi_highs",)))

    @unittest.skipUnless(PYOMO_AVAILABLE, "Pyomo is not installed")
    def test_find_available_pyomo_solver_prefers_appsi_highs_when_available(self):
        with patch("own_models.src.opf.solver_utils.is_pyomo_solver_available") as available:
            available.side_effect = lambda name: name == "appsi_highs"
            self.assertEqual(find_available_pyomo_solver(), "appsi_highs")


if __name__ == "__main__":
    unittest.main()
