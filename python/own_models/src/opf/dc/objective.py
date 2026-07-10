"""Generation-cost objective for the DC OPF model."""

from __future__ import annotations

import pyomo.environ as pyo

from own_models.src.opf.dc.data import DCOPFError


def cost_power_expression(pg_pu, base_mva: float, cost_power_units: str):
    """Convert ``Pg`` into the units expected by the cost function.

    Python/PyPowSyBl convention:
        Pg is p.u.
        synthetic/current costs are MW-based
        therefore use Pg * base_mva

    Future p.u.-scaled convention:
        use Pg directly
    """
    if cost_power_units == "mw":
        return pg_pu * base_mva
    if cost_power_units == "pu":
        return pg_pu

    raise DCOPFError(f"Unsupported cost_power_units={cost_power_units!r}.")


def add_generation_cost_objective(
    model,
    case,
    generators,
    *,
    cost_power_units: str = "mw",
) -> None:
    """Add the generator production-cost objective.

    Mathematical role:
        ``min sum_g c2_g Pg_cost_g^2 + c1_g Pg_cost_g + c0_g``
    """

    def objective_rule(_model):
        total_cost = 0.0

        for gen in generators:
            pg_cost = cost_power_expression(
                _model.Pg[gen.id],
                case.base_mva,
                cost_power_units,
            )

            if abs(float(gen.cost_c2)) > 1e-12:
                total_cost += float(gen.cost_c2) * pg_cost**2

            total_cost += (
                float(gen.cost_c1) * pg_cost
                + float(gen.cost_c0)
            )

        return total_cost

    model.objective = pyo.Objective(rule=objective_rule, sense=pyo.minimize)
