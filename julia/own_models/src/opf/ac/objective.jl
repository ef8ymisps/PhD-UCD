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

"""Attach the L1 deviation objective from an already-built `deviation_slacks`
NamedTuple (see `add_ac_target_deviation_variables!`, `variables.jl`) --
`sum(plus[id] + minus[id])` over whatever `d*_plus`/`d*_minus` Dict pairs
exist there (one pair per INCLUDED group; excluded groups have no slacks at
all, since they're ordinary free variables, not a deviation from anything).

No constraint-building happens here -- `add_ac_target_deviation_variables!`
already built the `target + Δ+ - Δ-` substitution expressions and any bound
constraints. This function existed previously with a linking-equality-
constraint implementation (kept Pg/Qg/Vm/Va as free variables, added
`var == target + plus - minus` as a separate constraint per element); that
formulation and the current one have the same feasible set but are not the
same problem to a nonlinear solver -- see
`.claude/skills/opf-discrepancy-inspection/SKILL.md`'s substitution section.

At the optimum, minimizing the sum drives at most one of
`plus[id]`/`minus[id]` nonzero per element (standard property of this
decomposition), so `plus[id] - minus[id]` is exactly the signed deviation
and `plus[id] + minus[id]` its magnitude."""
function add_ac_target_deviation_objective!(model::Model, deviation_slacks::NamedTuple)
    terms = []
    for key in keys(deviation_slacks)
        endswith(String(key), "_plus") || continue
        label = String(key)[1:end-5]
        plus = deviation_slacks[key]
        minus = deviation_slacks[Symbol("$(label)_minus")]
        push!(terms, sum(plus[id] + minus[id] for id in keys(plus); init = 0.0))
    end
    isempty(terms) && error("add_ac_target_deviation_objective! needs at least one included group (deviation_slacks was empty).")

    @objective(model, Min, sum(terms))
end

"""Minimize total active branch losses from both branch ends."""
function add_ac_active_losses_objective!(model::Model, pf_by_branch, pt_by_branch)
    @objective(
        model,
        Min,
        sum((pf_by_branch[id] + pt_by_branch[id] for id in keys(pf_by_branch)); init=0.0)
    )
end
