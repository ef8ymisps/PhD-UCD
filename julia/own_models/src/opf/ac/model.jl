# Owns assembly of the AC OPF JuMP model.
#
# Arc-based (PowerModels-style) formulation: directed branch arcs carry real
# p/q flow variables, each pinned to its Ohm's-law formula via a named
# equality constraint; nodal balance sums arc flows incident at each bus.
# This is the sole AC-OPF formulation in this codebase -- the previous
# bus-injection (implicit Ybus-substitution) formulation was deleted after
# this arc-based model reached validated parity with PowerModels.jl's own
# ACPPowerModel on the full own-vs-PowerModels validation sweep.

using JuMP
using Ipopt

const ACArc = Tuple{String, String, String}

struct ACArcData
    base::ACOPFData
    arcs::Vector{ACArc}
    arcs_from::Dict{String, ACArc}
    arcs_to::Dict{String, ACArc}
    bus_arcs::Dict{String, Vector{ACArc}}
    representative_angle_branch::Dict{Tuple{String, String}, String}
end

"""Build directed branch-arc sets mirroring PowerModels' ACP reference layer."""
function build_ac_arc_data(case::PowerCase)
    base = build_ac_opf_data(case)
    arcs = ACArc[]
    arcs_from = Dict{String, ACArc}()
    arcs_to = Dict{String, ACArc}()
    bus_arcs = Dict(bus.id => ACArc[] for bus in case.buses)
    representative_angle_branch = Dict{Tuple{String, String}, String}()

    for branch in base.active_branches
        f = branch.from_bus
        t = branch.to_bus
        from_arc = (branch.id, f, t)
        to_arc = (branch.id, t, f)

        push!(arcs, from_arc)
        push!(arcs, to_arc)
        arcs_from[branch.id] = from_arc
        arcs_to[branch.id] = to_arc
        push!(bus_arcs[f], from_arc)
        push!(bus_arcs[t], to_arc)

        pair = (f, t)
        current = get(representative_angle_branch, pair, nothing)
        if current === nothing || branch.index < parse(Int, current)
            representative_angle_branch[pair] = branch.id
        end
    end

    return ACArcData(base, arcs, arcs_from, arcs_to, bus_arcs, representative_angle_branch)
end

"""Create just the arc-flow variables (p/q) -- factored out of
`add_ac_variables!` so the target-deviation path (which builds pg/qg/vm/va
via `add_ac_target_deviation_variables!` instead) can still reuse this piece
unchanged. `enforce_arc_bounds=false` drops the simple per-arc rate_a bound
entirely (arc flows are never substitution targets themselves, but the
equality-only diagnostic needs these bounds gone too, per its
"removes...simple p/q arc-flow bounds" spec)."""
function add_ac_arc_variables!(model::Model, data::ACArcData; enforce_arc_bounds::Bool=true)
    @variable(model, p[data.arcs])
    @variable(model, q[data.arcs])

    for branch in data.base.active_branches
        if enforce_arc_bounds && branch.rate_a > 0.0
            for arc in (data.arcs_from[branch.id], data.arcs_to[branch.id])
                set_lower_bound(p[arc], -branch.rate_a)
                set_upper_bound(p[arc], branch.rate_a)
                set_lower_bound(q[arc], -branch.rate_a)
                set_upper_bound(q[arc], branch.rate_a)
            end
        end
        set_start_value(p[data.arcs_from[branch.id]], 0.0)
        set_start_value(q[data.arcs_from[branch.id]], 0.0)
        set_start_value(p[data.arcs_to[branch.id]], 0.0)
        set_start_value(q[data.arcs_to[branch.id]], 0.0)
    end

    return p, q
end

"""Create Pg, Qg, Vm, Va, and arc-flow p/q variables in a PowerModels-like order.

`warm_start` controls the initial point handed to the solver (mirrors
Python's `own_models/src/opf/ac/initialization.py`):
- `:zero` (default): fully flat, matching PowerModels.jl's actual default
  exactly -- Pg=Qg=0, Vm=1.0, Va=0.0. See `docs/notes/solver_tolerances.md`.
- `:case`: the case file's own Pg/Qg/Vm/Va (`gen.pg0`/`gen.qg0`,
  `bus.vm0`/`bus.va0`).
- `:flat`: real Pg from the case, Qg=0, Vm=1.0, Va=0.0 (hybrid).
"""
function add_ac_variables!(
    model::Model,
    case::PowerCase,
    data::ACArcData;
    voltage_bounds=nothing,
    warm_start::Symbol=:zero,
)
    if !(warm_start in (:case, :flat, :zero))
        error("Unsupported AC warm-start mode: $(warm_start). Expected :case, :flat, or :zero.")
    end

    @variable(model, va[data.base.bus_ids])
    @variable(model, vm[data.base.bus_ids])
    @variable(model, pg[data.base.gen_ids])
    @variable(model, qg[data.base.gen_ids])

    for bus in case.buses
        vmin, vmax = voltage_bounds_for_bus(bus, voltage_bounds)
        set_lower_bound(vm[bus.id], vmin)
        set_upper_bound(vm[bus.id], vmax)

        vm_start = warm_start == :case ? bus.vm0 : 1.0
        va_start = warm_start == :case ? bus.va0 : 0.0
        set_start_value(vm[bus.id], min(max(vm_start, vmin), vmax))
        set_start_value(va[bus.id], va_start)
    end

    for gen in case.generators
        if gen.status == 1
            set_lower_bound(pg[gen.id], gen.pmin)
            set_upper_bound(pg[gen.id], gen.pmax)
            set_lower_bound(qg[gen.id], gen.qmin)
            set_upper_bound(qg[gen.id], gen.qmax)

            pg_start = warm_start == :zero ? 0.0 : gen.pg0
            qg_start = warm_start == :case ? gen.qg0 : 0.0
            set_start_value(pg[gen.id], min(max(pg_start, gen.pmin), gen.pmax))
            set_start_value(qg[gen.id], min(max(qg_start, gen.qmin), gen.qmax))
        else
            fix(pg[gen.id], 0.0; force=true)
            fix(qg[gen.id], 0.0; force=true)
        end
    end

    p, q = add_ac_arc_variables!(model, data; enforce_arc_bounds=true)

    return pg, qg, vm, va, p, q
end

"""Add AC nodal balances by summing directed arc flows incident at each bus.

Returns `(power_balance_p, power_balance_q)`, each a `Dict{String,
ConstraintRef}` keyed by bus id. The nonlinear voltage-dependence lives
entirely in the separate Ohm's-law constraints that define the arc flow
variables (`_add_ac_ohms_for_branch!`); this constraint itself is purely
linear. Mirrors PowerModels.jl's `constraint_power_balance` (form/acp.jl)
exactly, including keeping bus shunts (`data.base.gs_by_bus`/`bs_by_bus`) as
a separate `-gs*vm^2`/`+bs*vm^2` term here rather than folding them into a
nodal Ybus diagonal.
"""
function add_ac_power_balance!(model::Model, case::PowerCase, data::ACArcData, pg, qg, vm, p, q)
    power_balance_p = Dict{String, ConstraintRef}()
    power_balance_q = Dict{String, ConstraintRef}()

    for bus in case.buses
        bus_id = bus.id
        pgen = sum((pg[g] for g in data.base.gens_by_bus[bus_id]); init=0.0)
        qgen = sum((qg[g] for g in data.base.gens_by_bus[bus_id]); init=0.0)

        power_balance_p[bus_id] = @constraint(
            model,
            sum(p[arc] for arc in data.bus_arcs[bus_id]; init=0.0) ==
                pgen - data.base.pd_by_bus[bus_id] - data.base.gs_by_bus[bus_id] * vm[bus_id]^2,
            base_name = "power_balance_p[$(bus_id)]"
        )
        power_balance_q[bus_id] = @constraint(
            model,
            sum(q[arc] for arc in data.bus_arcs[bus_id]; init=0.0) ==
                qgen - data.base.qd_by_bus[bus_id] + data.base.bs_by_bus[bus_id] * vm[bus_id]^2,
            base_name = "power_balance_q[$(bus_id)]"
        )
    end

    return power_balance_p, power_balance_q
end

function _add_ac_ohms_for_branch!(model::Model, branch::Branch, vm, va, p, q, from_arc::ACArc, to_arc::ACArc)
    f = branch.from_bus
    t = branch.to_bus

    z = branch.r + im * branch.x
    y = 1.0 / z
    g = real(y)
    b = imag(y)
    g_fr = branch.g_fr
    b_fr = branch.b_fr
    g_to = branch.g_to
    b_to = branch.b_to
    tm = branch.tap == 0.0 ? 1.0 : branch.tap
    tr = tm * cos(branch.shift)
    ti = tm * sin(branch.shift)

    ohms_from_p = @constraint(
        model,
        p[from_arc] ==
            (g + g_fr) / tm^2 * vm[f]^2 +
            (-g * tr + b * ti) / tm^2 * (vm[f] * vm[t] * cos(va[f] - va[t])) +
            (-b * tr - g * ti) / tm^2 * (vm[f] * vm[t] * sin(va[f] - va[t])),
        base_name = "ohms_from_p[$(branch.id)]"
    )
    ohms_from_q = @constraint(
        model,
        q[from_arc] ==
            -(b + b_fr) / tm^2 * vm[f]^2 -
            (-b * tr - g * ti) / tm^2 * (vm[f] * vm[t] * cos(va[f] - va[t])) +
            (-g * tr + b * ti) / tm^2 * (vm[f] * vm[t] * sin(va[f] - va[t])),
        base_name = "ohms_from_q[$(branch.id)]"
    )
    ohms_to_p = @constraint(
        model,
        p[to_arc] ==
            (g + g_to) * vm[t]^2 +
            (-g * tr - b * ti) / tm^2 * (vm[t] * vm[f] * cos(va[t] - va[f])) +
            (-b * tr + g * ti) / tm^2 * (vm[t] * vm[f] * sin(va[t] - va[f])),
        base_name = "ohms_to_p[$(branch.id)]"
    )
    ohms_to_q = @constraint(
        model,
        q[to_arc] ==
            -(b + b_to) * vm[t]^2 -
            (-b * tr + g * ti) / tm^2 * (vm[t] * vm[f] * cos(va[t] - va[f])) +
            (-g * tr - b * ti) / tm^2 * (vm[t] * vm[f] * sin(va[t] - va[f])),
        base_name = "ohms_to_q[$(branch.id)]"
    )

    return ohms_from_p, ohms_from_q, ohms_to_p, ohms_to_q
end

function add_ac_branch_constraints!(
    model::Model,
    data::ACArcData,
    vm,
    va,
    p,
    q;
    include_branch_thermal::Bool=true,
    include_branch_angles::Bool=true,
    deduplicate_angle_constraints::Bool=false,
)
    angle_diff = Dict{String, ConstraintRef}()
    thermal_from = Dict{String, ConstraintRef}()
    thermal_to = Dict{String, ConstraintRef}()
    ohms_from = Dict{String, Tuple{ConstraintRef, ConstraintRef}}()
    ohms_to = Dict{String, Tuple{ConstraintRef, ConstraintRef}}()

    for branch in data.base.active_branches
        f = branch.from_bus
        t = branch.to_bus
        from_arc = data.arcs_from[branch.id]
        to_arc = data.arcs_to[branch.id]

        if include_branch_angles
            should_add_angle = !deduplicate_angle_constraints ||
                get(data.representative_angle_branch, (f, t), branch.id) == branch.id
            if should_add_angle
                angle_diff[branch.id] = @constraint(
                    model,
                    branch.angmin <= va[f] - va[t] <= branch.angmax,
                    base_name = "branch_angle_diff[$(branch.id)]"
                )
            end
        end

        ofp, ofq, otp, otq = _add_ac_ohms_for_branch!(model, branch, vm, va, p, q, from_arc, to_arc)
        ohms_from[branch.id] = (ofp, ofq)
        ohms_to[branch.id] = (otp, otq)

        if include_branch_thermal && branch.rate_a > 0.0
            thermal_from[branch.id] = @constraint(
                model,
                p[from_arc]^2 + q[from_arc]^2 <= branch.rate_a^2,
                base_name = "branch_thermal_from[$(branch.id)]"
            )
            thermal_to[branch.id] = @constraint(
                model,
                p[to_arc]^2 + q[to_arc]^2 <= branch.rate_a^2,
                base_name = "branch_thermal_to[$(branch.id)]"
            )
        end
    end

    pf = Dict(branch.id => p[data.arcs_from[branch.id]] for branch in data.base.active_branches)
    qf = Dict(branch.id => q[data.arcs_from[branch.id]] for branch in data.base.active_branches)
    pt = Dict(branch.id => p[data.arcs_to[branch.id]] for branch in data.base.active_branches)
    qt = Dict(branch.id => q[data.arcs_to[branch.id]] for branch in data.base.active_branches)

    return (
        pf=pf, qf=qf, pt=pt, qt=qt,
        angle_diff=angle_diff,
        thermal_from=thermal_from,
        thermal_to=thermal_to,
        ohms_from=ohms_from,
        ohms_to=ohms_to,
        p_arc=p,
        q_arc=q,
    )
end

"""Build the AC OPF model without solving it."""
function build_ac_opf_model(
    case::PowerCase;
    optimizer=Ipopt.Optimizer,
    silent::Bool=true,
    voltage_bounds=nothing,
    fix_reference_angle::Bool=true,
    include_branch_angles::Bool=true,
    include_branch_thermal::Bool=true,
    objective::Symbol=:generation_cost,
    warm_start::Symbol=:zero,
    deduplicate_angle_constraints::Bool=false,
    target_pg::Union{Dict{String, Float64}, Nothing}=nothing,
    target_qg::Union{Dict{String, Float64}, Nothing}=nothing,
    target_vm::Union{Dict{String, Float64}, Nothing}=nothing,
    target_va::Union{Dict{String, Float64}, Nothing}=nothing,
    own_pg::Union{Dict{String, Float64}, Nothing}=nothing,
    own_qg::Union{Dict{String, Float64}, Nothing}=nothing,
    own_vm::Union{Dict{String, Float64}, Nothing}=nothing,
    own_va::Union{Dict{String, Float64}, Nothing}=nothing,
    include_pg::Bool=true,
    include_qg::Bool=true,
    include_vm::Bool=true,
    include_va::Bool=true,
    enforce_deviation_bounds::Bool=true,
    enforce_excluded_bounds::Bool=false,
    warm_start_source::Symbol=:target,
)
    data = build_ac_arc_data(case)
    model = Model(optimizer)
    if silent
        set_silent(model)
    end

    deviation_slacks = nothing
    if objective == :target_deviation
        pg, qg, vm, va, deviation_slacks = add_ac_target_deviation_variables!(
            model, case, data.base;
            target_pg=target_pg, target_qg=target_qg, target_vm=target_vm, target_va=target_va,
            own_pg=own_pg, own_qg=own_qg, own_vm=own_vm, own_va=own_va,
            include_pg=include_pg, include_qg=include_qg, include_vm=include_vm, include_va=include_va,
            enforce_bounds=enforce_deviation_bounds, enforce_excluded_bounds=enforce_excluded_bounds,
            voltage_bounds=voltage_bounds, warm_start_source=warm_start_source,
        )
        p, q = add_ac_arc_variables!(model, data; enforce_arc_bounds=enforce_deviation_bounds)
    else
        pg, qg, vm, va, p, q = add_ac_variables!(
            model, case, data; voltage_bounds=voltage_bounds, warm_start=warm_start
        )
    end
    reference_angle = add_ac_reference_angle!(model, case, va; fix_reference_angle=fix_reference_angle)
    branch_flows = add_ac_branch_constraints!(
        model, data, vm, va, p, q;
        include_branch_thermal=include_branch_thermal,
        include_branch_angles=include_branch_angles,
        deduplicate_angle_constraints=deduplicate_angle_constraints,
    )
    power_balance_p, power_balance_q = add_ac_power_balance!(model, case, data, pg, qg, vm, p, q)

    if objective == :generation_cost
        add_ac_generation_cost_objective!(model, case, pg)
    elseif objective == :pg_deviation
        add_ac_pg_deviation_objective!(model, case, pg)
    elseif objective == :active_losses
        add_ac_active_losses_objective!(model, branch_flows.pf, branch_flows.pt)
    elseif objective == :target_deviation
        add_ac_target_deviation_objective!(model, deviation_slacks)
    else
        error("Unsupported AC OPF objective: $(objective)")
    end

    # Named/indexed constraint references (A5) -- not consumed by
    # solve_ac_opf_own's result struct, but available to any caller that
    # wants duals/shadow prices via build_ac_opf_model directly. See
    # docs/notation.md for the full symbol-to-identifier mapping.
    # deviation_slacks is `nothing` unless objective == :target_deviation,
    # in which case it's a NamedTuple of the dPg_plus/dPg_minus-style slack
    # Dicts (see add_ac_target_deviation_objective!'s docstring) -- C2's
    # per-element signed-deviation reporting reads from here.
    constraints = (
        reference_angle=reference_angle,
        power_balance_p=power_balance_p,
        power_balance_q=power_balance_q,
        branch_angle_diff=branch_flows.angle_diff,
        branch_thermal_from=branch_flows.thermal_from,
        branch_thermal_to=branch_flows.thermal_to,
        branch_ohms_from=branch_flows.ohms_from,
        branch_ohms_to=branch_flows.ohms_to,
        deviation_slacks=deviation_slacks,
    )

    return model, data.base, pg, qg, vm, va, branch_flows, constraints
end

"""Solve AC OPF and return objective, dispatch, voltages, and branch flows."""
function solve_ac_opf_own(case::PowerCase; kwargs...)
    model, data, pg, qg, vm, va, branch_flows, _constraints = build_ac_opf_model(case; kwargs...)
    optimize!(model)
    return collect_ac_opf_result(model, case, data, pg, qg, vm, va, branch_flows)
end
