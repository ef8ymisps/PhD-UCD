# Owns DC OPF objective definitions.

using JuMP

"""Minimize PGLib/MATPOWER generator cost using p.u. Pg coefficients."""
function add_dc_generation_cost_objective!(model::Model, case::PowerCase, pg)
    @objective(
        model,
        Min,
        sum((
            gen.cost_c2 * pg[gen.id]^2 +
            gen.cost_c1 * pg[gen.id] +
            gen.cost_c0
            for gen in case.generators
            if gen.status == 1
        ); init=0.0)
    )
end
