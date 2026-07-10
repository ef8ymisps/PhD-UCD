# Owns AC OPF objective definitions.

using JuMP

"""Minimize PGLib/MATPOWER generator cost using p.u. Pg coefficients."""
function add_ac_generation_cost_objective!(model::Model, case::PowerCase, pg)
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

"""Minimize least-squares active dispatch deviation from the case start Pg0."""
function add_ac_pg_deviation_objective!(model::Model, case::PowerCase, pg)
    @objective(
        model,
        Min,
        sum(((pg[gen.id] - gen.pg0)^2 for gen in case.generators if gen.status == 1); init=0.0)
    )
end

"""Minimize total active branch losses from both branch ends."""
function add_ac_active_losses_objective!(model::Model, pf_by_branch, pt_by_branch)
    @objective(
        model,
        Min,
        sum((pf_by_branch[id] + pt_by_branch[id] for id in keys(pf_by_branch)); init=0.0)
    )
end
