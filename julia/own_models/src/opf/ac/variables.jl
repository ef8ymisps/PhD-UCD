# Owns AC OPF JuMP decision variables and initial values.

using JuMP

"""Return valid voltage bounds for one bus under an optional scenario override."""
function voltage_bounds_for_bus(bus::Bus, voltage_bounds)
    case_lower = Float64(bus.vmin)
    case_upper = Float64(bus.vmax)
    validate_voltage_bounds(bus.id, case_lower, case_upper)

    if voltage_bounds === nothing
        return case_lower, case_upper
    end

    if voltage_bounds isa AbstractDict
        lower, upper = get(voltage_bounds, bus.id, (case_lower, case_upper))
        lower = Float64(lower)
        upper = Float64(upper)
        validate_voltage_bounds(bus.id, lower, upper)
        return lower, upper
    end

    lower = Float64(voltage_bounds[1])
    upper = Float64(voltage_bounds[2])
    validate_voltage_bounds("all buses", lower, upper)

    target_width = upper - lower
    case_width = case_upper - case_lower
    if target_width >= case_width
        return min(case_lower, lower), max(case_upper, upper)
    end

    half_width = target_width / 2.0
    center = min(max(Float64(bus.vm0), case_lower), case_upper)
    return max(case_lower, center - half_width), min(case_upper, center + half_width)
end

"""Fail before JuMP receives an invalid voltage-magnitude interval."""
function validate_voltage_bounds(bus_id, lower::Float64, upper::Float64)
    if lower > upper
        error("Invalid voltage bounds for $(bus_id): $(lower) > $(upper).")
    end
end

"""Build Pg/Qg/Vm/Va for the target-deviation objective: for each of the 4
groups, either a true `target + Δ+ - Δ-` substitution expression (included
groups) or an ordinary bounded free variable (excluded groups) -- see
`.claude/skills/opf-discrepancy-inspection/SKILL.md`'s substitution section.
Replaces the previous linking-equality-constraint implementation (kept the
original variable free, added `var == target + plus - minus` as a separate
constraint) -- that formulation and this one have the same feasible set but
are not the same problem to a nonlinear solver.

`target_pg`/`target_qg`/`target_vm`/`target_va` are the PowerModels
reference point x^{p*} (`Dict{String,Float64}` keyed by generator/bus id).
`own_pg`/`own_qg`/`own_vm`/`own_va` are the own model's own previous
solution x^{e*}, needed only when `warm_start_source=:own`.

`include_pg`/`include_qg`/`include_vm`/`include_va` select which groups get
the substitution treatment. Excluded groups are ordinary free variables --
NOT pinned at target with zero degrees of freedom (a design considered and
explicitly rejected: it specifies a different, not-wanted experiment;
leaving excluded groups free and untracked is the correct semantics for
`no_qg`/`no_v`/`pg_only`).

`enforce_bounds`: whether an included group's original bound (pmin/pmax,
qmin/qmax, vmin/vmax -- Va has no elementwise bound in this model) is
re-attached as an explicit constraint on the substituted expression, since
`set_lower_bound`/`set_upper_bound` only work on a real `VariableRef`, not a
general affine expression. `false` for the equality-only diagnostic.

`enforce_excluded_bounds` (default `false`): whether an EXCLUDED group's
own physical bound is kept. Investigated directly on the
load_x0.8/PM-like anomaly: keeping the bound (`true`) let Ipopt's
interior-point barrier for that bound divert the whole Newton trajectory
away from a target point that was already fully feasible (confirmed via a
`dual_inf_tol` sweep from 1.0 to 1e-9 -- identical result throughout, ruling
out premature convergence as the cause) -- dropping it resolved that case
cleanly. It is not a universal win: for `warm_start_source=:own` on the
same case, ~34% of generators are legitimately sitting AT their own Qg
bound already in `x^{e*}` (a real, physically normal operating condition,
not solver noise) -- dropping the bound out from under ~100
simultaneously-active real constraints destabilized that solve instead of
helping it. Kept as the default anyway per direct user decision after
comparing both rules with no single universal fix existing; a `false`
result on an own-seeded reduced variant should be read as a genuine
finding about reachability from a realistic operating point, not a bug.

`warm_start_source`: `:target` seeds every variable/expression's initial
value at `x^{p*}` (Δ+/Δ- start at 0 for included groups, so the expression
starts exactly at target); `:own` seeds at `x^{e*}` instead (one of
Δ+/Δ- starts nonzero so the expression's initial value reproduces
`own[id]`) -- the "does the search still find its way to x^{p*} starting
from a realistic point" reachability test, as opposed to starting basically
at the answer.

Returns `(pg, qg, vm, va, deviation_slacks)`: `pg`/`qg`/`vm`/`va` are
`Dict{String,Any}` (variable OR expression per group, mirroring the
existing `pf_by_branch::Dict{String,Any}` "same container, either kind of
JuMP object" pattern already used in `constraints.jl`); `deviation_slacks`
is a `NamedTuple` of whichever `dPg_plus`/`dPg_minus`-style slack `Dict`s
were actually created (only for included groups)."""
function add_ac_target_deviation_variables!(
    model::Model, case::PowerCase, data::ACOPFData;
    target_pg::Dict{String,Float64}, target_qg::Dict{String,Float64},
    target_vm::Dict{String,Float64}, target_va::Dict{String,Float64},
    own_pg::Union{Dict{String,Float64},Nothing}=nothing,
    own_qg::Union{Dict{String,Float64},Nothing}=nothing,
    own_vm::Union{Dict{String,Float64},Nothing}=nothing,
    own_va::Union{Dict{String,Float64},Nothing}=nothing,
    include_pg::Bool=true, include_qg::Bool=true, include_vm::Bool=true, include_va::Bool=true,
    enforce_bounds::Bool=true,
    enforce_excluded_bounds::Bool=false,
    voltage_bounds=nothing,
    warm_start_source::Symbol=:target,
)
    warm_start_source ∈ (:target, :own) || error("Unsupported warm_start_source: $(warm_start_source). Expected :target or :own.")
    if warm_start_source == :own && (own_pg === nothing || own_qg === nothing || own_vm === nothing || own_va === nothing)
        error("warm_start_source=:own requires own_pg/own_qg/own_vm/own_va.")
    end

    pg = Dict{String,Any}()
    qg = Dict{String,Any}()
    vm = Dict{String,Any}()
    va = Dict{String,Any}()
    slacks = Dict{Symbol,Any}()

    function build_group!(container::Dict{String,Any}, label::String, ids, target::Dict{String,Float64}, own, include::Bool, bound_fn)
        plus = Dict{String,VariableRef}()
        minus = Dict{String,VariableRef}()
        for id in ids
            start_value = warm_start_source == :own ? own[id] : target[id]
            if include
                p = @variable(model, lower_bound = 0.0, base_name = "d$(label)_plus[$(id)]")
                m = @variable(model, lower_bound = 0.0, base_name = "d$(label)_minus[$(id)]")
                gap = start_value - target[id]
                set_start_value(p, max(gap, 0.0))
                set_start_value(m, max(-gap, 0.0))
                expr = @expression(model, target[id] + p - m)
                container[id] = expr
                plus[id] = p
                minus[id] = m
                if enforce_bounds
                    lb, ub = bound_fn(id)
                    lb !== nothing && @constraint(model, expr >= lb, base_name = "$(label)_lb[$(id)]")
                    ub !== nothing && @constraint(model, expr <= ub, base_name = "$(label)_ub[$(id)]")
                end
            else
                v = @variable(model, base_name = "$(label)[$(id)]")
                clamped = start_value
                if enforce_excluded_bounds
                    lb, ub = bound_fn(id)
                    lb !== nothing && set_lower_bound(v, lb)
                    ub !== nothing && set_upper_bound(v, ub)
                    lb !== nothing && (clamped = max(clamped, lb))
                    ub !== nothing && (clamped = min(clamped, ub))
                end
                set_start_value(v, clamped)
                container[id] = v
            end
        end
        if include
            slacks[Symbol("$(label)_plus")] = plus
            slacks[Symbol("$(label)_minus")] = minus
        end
    end

    # Offline generators are fixed at 0.0 regardless of variant, exactly like
    # add_ac_variables! -- they're never referenced by gens_by_bus (built
    # from active_generators only), so this is API-completeness, not a
    # physics requirement, but collect_ac_opf_result iterates ALL
    # case.generators and needs an entry for every id.
    for gen in case.generators
        gen.status == 1 && continue
        pg_var = @variable(model, base_name = "Pg[$(gen.id)]")
        qg_var = @variable(model, base_name = "Qg[$(gen.id)]")
        fix(pg_var, 0.0; force = true)
        fix(qg_var, 0.0; force = true)
        pg[gen.id] = pg_var
        qg[gen.id] = qg_var
    end

    online_gen_ids = [gen.id for gen in case.generators if gen.status == 1]
    gen_by_id = Dict(gen.id => gen for gen in case.generators)
    build_group!(pg, "Pg", online_gen_ids, target_pg, own_pg, include_pg, id -> (gen_by_id[id].pmin, gen_by_id[id].pmax))
    build_group!(qg, "Qg", online_gen_ids, target_qg, own_qg, include_qg, id -> (gen_by_id[id].qmin, gen_by_id[id].qmax))

    bus_ids = [bus.id for bus in case.buses]
    build_group!(vm, "Vm", bus_ids, target_vm, own_vm, include_vm, id -> voltage_bounds_for_bus(case.buses[case.bus_id_to_index[id]], voltage_bounds))
    build_group!(va, "Va", bus_ids, target_va, own_va, include_va, _id -> (nothing, nothing))

    return pg, qg, vm, va, NamedTuple(slacks)
end
