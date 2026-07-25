# ============================================================
# N-1 contingency screening via PowerModels.compute_ac_pf
#
# Per the tool decision (docs/formulations/security_constraints.md): Julia
# post-contingency PF uses PowerModels.jl's native compute_ac_pf
# (Newton-Raphson), via the existing run_powermodels_ac_pf_compute wrapper
# (ac_pf_compute_validation.jl) -- not the own model's PF solver, not Ipopt.
#
# Mirrors the Python pypowsybl_security_validation.jl N-1 pipeline
# structurally and uses the SAME output column schema, so results from both
# languages/tools are directly comparable/joinable for case118.
# ============================================================

using DataFrames

# ------------------------------------------------------------
# Contingency generation (own-language port of Python's contingencies.py)
# ------------------------------------------------------------

struct N1Contingency
    id::String
    element_type::String  # "branch" or "generator"
    element_id::String
end

function generate_branch_n1_contingencies(case::PowerCase)
    return [
        N1Contingency("outage::$(branch.id)", "branch", branch.id)
        for branch in case.branches
        if branch.status == 1
    ]
end

function generate_generator_n1_contingencies(case::PowerCase)
    return [
        N1Contingency("outage::gen::$(gen.id)", "generator", gen.id)
        for gen in case.generators
        if gen.status == 1
    ]
end

# ------------------------------------------------------------
# Static proportional-reserve redispatch (port of Python's balancing_policy.py)
# ------------------------------------------------------------

struct BalancingPolicyResult
    success::Bool
    lost_generation_p::Float64
    available_reserve_p::Float64
    scheduled_pg_by_generator::Dict{String, Float64}
    new_slack_generator_id::String
end

function _select_new_slack_generator(case::PowerCase, base_pg::Dict{String, Float64})
    candidates = [gen for gen in case.generators if gen.status == 1]
    isempty(candidates) && error("Cannot select a new slack generator: no active generators remain.")
    return sort(candidates; by = gen -> (-(gen.pmax - get(base_pg, gen.id, gen.pg0)), gen.index, gen.id))[1]
end

function _update_slack_if_needed(case::PowerCase, base_pg::Dict{String, Float64})
    active_slack_generators = [gen for gen in case.generators if gen.status == 1 && gen.bus == case.slack_bus_id]
    if !isempty(active_slack_generators)
        selected = sort(active_slack_generators; by = gen -> (gen.index, gen.id))[1]
        return case, selected.id
    end
    new_slack = _select_new_slack_generator(case, base_pg)
    new_buses = [
        Bus(bus.id, bus.index, bus.id == new_slack.bus ? 3 : (any(g.status == 1 && g.bus == bus.id for g in case.generators) ? 2 : 1), bus.vm0, bus.va0, bus.vmin, bus.vmax)
        for bus in case.buses
    ]
    new_case = PowerCase(case.name, case.baseMVA, new_buses, case.branches, case.generators, case.loads, case.shunts, new_slack.bus, case.bus_id_to_index)
    return new_case, new_slack.id
end

function apply_proportional_reserve_balancing(
    case::PowerCase,
    base_pg::Dict{String, Float64},
    outaged_gen_id::String;
    tol::Float64 = 1e-8,
)
    lost_generation = get(base_pg, outaged_gen_id, 0.0)
    balanced_case, slack_generator_id = _update_slack_if_needed(case, base_pg)

    scheduled = Dict(gen.id => get(base_pg, gen.id, gen.pg0) for gen in balanced_case.generators if gen.status == 1)
    reserves = Dict(
        gen.id => max(0.0, gen.pmax - get(base_pg, gen.id, gen.pg0))
        for gen in balanced_case.generators if gen.status == 1 && gen.id != outaged_gen_id
    )
    eligible = [gen_id for (gen_id, reserve) in reserves if reserve > tol]
    total_reserve = sum(reserves[gen_id] for gen_id in eligible; init = 0.0)

    if total_reserve + tol < lost_generation
        return balanced_case, BalancingPolicyResult(false, lost_generation, total_reserve, scheduled, slack_generator_id)
    end

    for gen_id in eligible
        scheduled[gen_id] = scheduled[gen_id] + lost_generation * reserves[gen_id] / total_reserve
    end

    new_generators = [
        gen.status == 1 && haskey(scheduled, gen.id) ?
            Generator(gen.id, gen.index, gen.bus, scheduled[gen.id], gen.qg0, gen.pmin, gen.pmax, gen.qmin, gen.qmax, gen.status, gen.cost_c2, gen.cost_c1, gen.cost_c0) :
            gen
        for gen in balanced_case.generators
    ]
    redispatched_case = PowerCase(
        balanced_case.name, balanced_case.baseMVA, balanced_case.buses, balanced_case.branches,
        new_generators, balanced_case.loads, balanced_case.shunts, balanced_case.slack_bus_id, balanced_case.bus_id_to_index,
    )
    return redispatched_case, BalancingPolicyResult(true, lost_generation, total_reserve, scheduled, slack_generator_id)
end

# ------------------------------------------------------------
# Base-case dispatch -> PowerModels data dict transfer
# ------------------------------------------------------------

"""Write a case's own pg0/qg0 (generators) and vm0/va0 (buses) into a deep
copy of a PowerModels data dict, so compute_ac_pf solves from that dispatch
(PV buses use gen "pg"/"vg" as fixed inputs; slack is bus_type==3)."""
function _write_case_dispatch_into_data(data::Dict, case::PowerCase)
    data_copy = deepcopy(data)
    bus_by_id = Dict(bus.id => bus for bus in case.buses)
    for gen in case.generators
        gen.status != 1 && continue
        if haskey(data_copy["gen"], gen.id)
            data_copy["gen"][gen.id]["pg"] = gen.pg0
            data_copy["gen"][gen.id]["qg"] = gen.qg0
            data_copy["gen"][gen.id]["gen_status"] = 1
            bus = bus_by_id[gen.bus]
            data_copy["gen"][gen.id]["vg"] = bus.vm0
        end
    end
    for bus in case.buses
        haskey(data_copy["bus"], bus.id) || continue
        data_copy["bus"][bus.id]["vm"] = bus.vm0
        data_copy["bus"][bus.id]["va"] = bus.va0
        data_copy["bus"][bus.id]["bus_type"] = bus.id == case.slack_bus_id ? 3 :
            (any(g.status == 1 && g.bus == bus.id for g in case.generators) ? 2 : 1)
    end
    for branch in case.branches
        haskey(data_copy["branch"], branch.id) || continue
        data_copy["branch"][branch.id]["br_status"] = branch.status
    end
    for gen in case.generators
        haskey(data_copy["gen"], gen.id) || continue
        data_copy["gen"][gen.id]["gen_status"] = gen.status
    end
    return data_copy
end

# ------------------------------------------------------------
# Per-contingency metrics (same schema as Python's n1_screening_table)
# ------------------------------------------------------------

function _empty_n1_row(case_name, contingency_id, element_type, element_id, status)
    return (
        case_name = case_name,
        contingency_id = contingency_id,
        element_type = element_type,
        outaged_element_id = element_id,
        status = status,
        converged = false,
        max_loading_pct = NaN,
        n_overloaded_branches = 0,
        total_overload_pct = 0.0,
        total_overload_pct_sq = 0.0,
        min_vm_pu = NaN,
        max_vm_pu = NaN,
        n_voltage_violations = 0,
        total_voltage_violation_pu = 0.0,
        total_voltage_violation_pu_sq = 0.0,
        thermal_violation = false,
        voltage_violation = false,
        secure = false,
        severity_score = NaN,
    )
end

function _n1_row_from_compute_pf(case::PowerCase, pm_result, contingency_id::String, element_type::String, element_id::String)
    converged = pm_result.summary.converged
    status = string(pm_result.summary.termination_status)
    if !converged
        return _empty_n1_row(case.name, contingency_id, element_type, element_id, status)
    end

    branch_by_id = Dict(b.id => b for b in case.branches)
    bus_by_id = Dict(b.id => b for b in case.buses)

    max_loading = NaN
    total_overload_pct = 0.0
    total_overload_pct_sq = 0.0
    n_overloaded = 0
    thermal_violation = false
    for row in eachrow(pm_result.branches)
        branch = get(branch_by_id, row.branch_id, nothing)
        (branch === nothing || branch.status != 1 || branch.rate_a <= 0.0) && continue
        pf, qf, pt, qt = row.pf, row.qf, row.pt, row.qt
        (ismissing(pf) || ismissing(qf) || ismissing(pt) || ismissing(qt)) && continue
        s1 = hypot(pf, qf)
        s2 = hypot(pt, qt)
        loading = 100.0 * max(s1, s2) / branch.rate_a
        max_loading = isnan(max_loading) ? loading : max(max_loading, loading)
        if loading > 100.0 + 1e-6
            thermal_violation = true
            n_overloaded += 1
            overload = loading - 100.0
            total_overload_pct += overload
            total_overload_pct_sq += overload^2
        end
    end

    min_vm = NaN
    max_vm = NaN
    voltage_violation = false
    total_voltage_violation_pu = 0.0
    total_voltage_violation_pu_sq = 0.0
    n_voltage_violations = 0
    for row in eachrow(pm_result.buses)
        bus = get(bus_by_id, row.bus_id, nothing)
        bus === nothing && continue
        vm = row.vm
        ismissing(vm) && continue
        min_vm = isnan(min_vm) ? vm : min(min_vm, vm)
        max_vm = isnan(max_vm) ? vm : max(max_vm, vm)
        deviation = 0.0
        if vm < bus.vmin - 1e-6
            deviation = bus.vmin - vm
        elseif vm > bus.vmax + 1e-6
            deviation = vm - bus.vmax
        end
        if deviation > 0.0
            voltage_violation = true
            n_voltage_violations += 1
            total_voltage_violation_pu += deviation
            total_voltage_violation_pu_sq += deviation^2
        end
    end

    secure = converged && !thermal_violation && !voltage_violation
    severity_score = max(
        isnan(max_loading) ? 0.0 : max(0.0, max_loading - 100.0) / 100.0,
        total_voltage_violation_pu,
    )
    return (
        case_name = case.name,
        contingency_id = contingency_id,
        element_type = element_type,
        outaged_element_id = element_id,
        status = status,
        converged = converged,
        max_loading_pct = max_loading,
        n_overloaded_branches = n_overloaded,
        total_overload_pct = total_overload_pct,
        total_overload_pct_sq = total_overload_pct_sq,
        min_vm_pu = min_vm,
        max_vm_pu = max_vm,
        n_voltage_violations = n_voltage_violations,
        total_voltage_violation_pu = total_voltage_violation_pu,
        total_voltage_violation_pu_sq = total_voltage_violation_pu_sq,
        thermal_violation = thermal_violation,
        voltage_violation = voltage_violation,
        secure = secure,
        severity_score = severity_score,
    )
end

# ------------------------------------------------------------
# Top-level N-1 screening
# ------------------------------------------------------------

"""`start_mode` is forwarded to `run_powermodels_ac_pf_compute` --
`:powermodels_default` (unchanged prior behavior), `:case_values_if_supported`
(explicit warm start: Newton-Raphson's initial guess uses the Vm/Va baked
into `base_case`), or `:flat_if_supported` (explicit flat start: Vm/Va
initial guess ignored regardless of what's in `base_case`, dispatch/vg stay
as fixed inputs either way -- see docs/notation.md and the D1 warm-start
experiment script for why dispatch isn't itself a "start value" in a power
flow, only the NR voltage guess is)."""
function run_n1_branch_screening(case::PowerCase, data::Dict, base_pg::Dict{String, Float64}, base_case::PowerCase; start_mode::Symbol = :powermodels_default, drop_islands::Bool = true)
    rows = []
    for contingency in generate_branch_n1_contingencies(base_case)
        branches = [
            b.id == contingency.element_id ? Branch(b.id, b.index, b.from_bus, b.to_bus, b.r, b.x, b.b, b.b_fr, b.b_to, b.g_fr, b.g_to, b.rate_a, b.tap, b.shift, b.angmin, b.angmax, 0) : b
            for b in base_case.branches
        ]
        contingency_case = PowerCase(base_case.name, base_case.baseMVA, base_case.buses, branches, base_case.generators, base_case.loads, base_case.shunts, base_case.slack_bus_id, base_case.bus_id_to_index)
        data_copy = _write_case_dispatch_into_data(data, contingency_case)
        pm_result = run_powermodels_ac_pf_compute(data_copy; start_mode=start_mode, drop_islands=drop_islands)
        push!(rows, _n1_row_from_compute_pf(base_case, pm_result, contingency.id, "branch", contingency.element_id))
    end
    return DataFrame(rows)
end

function run_n1_generator_screening(case::PowerCase, data::Dict, base_pg::Dict{String, Float64}, base_case::PowerCase; start_mode::Symbol = :powermodels_default, drop_islands::Bool = true)
    rows = []
    for contingency in generate_generator_n1_contingencies(base_case)
        generators = [
            g.id == contingency.element_id ? Generator(g.id, g.index, g.bus, g.pg0, g.qg0, g.pmin, g.pmax, g.qmin, g.qmax, 0, g.cost_c2, g.cost_c1, g.cost_c0) : g
            for g in base_case.generators
        ]
        contingency_case = PowerCase(base_case.name, base_case.baseMVA, base_case.buses, base_case.branches, generators, base_case.loads, base_case.shunts, base_case.slack_bus_id, base_case.bus_id_to_index)

        # islanding: no online generator left at all
        if !any(g.status == 1 for g in contingency_case.generators)
            push!(rows, _empty_n1_row(base_case.name, contingency.id, "generator", contingency.element_id, "NO_GENERATION_REMAINING"))
            continue
        end

        balanced_case, balancing_result = apply_proportional_reserve_balancing(contingency_case, base_pg, contingency.element_id)
        if !balancing_result.success
            push!(rows, _empty_n1_row(base_case.name, contingency.id, "generator", contingency.element_id, "GENERATION_IMBALANCE"))
            continue
        end

        data_copy = _write_case_dispatch_into_data(data, balanced_case)
        pm_result = run_powermodels_ac_pf_compute(data_copy; start_mode=start_mode, drop_islands=drop_islands)
        push!(rows, _n1_row_from_compute_pf(base_case, pm_result, contingency.id, "generator", contingency.element_id))
    end
    return DataFrame(rows)
end

function run_n1_screening(case::PowerCase, data::Dict, own_ac_opf_result; include_branches::Bool = true, include_generators::Bool = true, start_mode::Symbol = :powermodels_default, drop_islands::Bool = true)
    base_pg = Dict(gen.id => get(own_ac_opf_result.pg, gen.id, gen.pg0) for gen in case.generators if gen.status == 1)
    base_case = case_with_base_dispatch_own_ac(case, own_ac_opf_result)

    frames = DataFrame[]
    include_branches && push!(frames, run_n1_branch_screening(case, data, base_pg, base_case; start_mode=start_mode, drop_islands=drop_islands))
    include_generators && push!(frames, run_n1_generator_screening(case, data, base_pg, base_case; start_mode=start_mode, drop_islands=drop_islands))
    isempty(frames) && return DataFrame()
    return vcat(frames...)
end

"""Bake own AC-OPF result (Pg/Qg/Vm/Va) into a PowerCase's own fields, mirroring
Python's case_with_base_dispatch -- needed so case.buses[i].vm0/va0 and
case.generators[i].pg0/qg0 reflect the solved dispatch, not raw file defaults."""
function case_with_base_dispatch_own_ac(case::PowerCase, ac_opf_result)
    new_generators = [
        gen.status == 1 ?
            Generator(gen.id, gen.index, gen.bus, get(ac_opf_result.pg, gen.id, gen.pg0), get(ac_opf_result.qg, gen.id, gen.qg0), gen.pmin, gen.pmax, gen.qmin, gen.qmax, gen.status, gen.cost_c2, gen.cost_c1, gen.cost_c0) :
            gen
        for gen in case.generators
    ]
    new_buses = [
        Bus(bus.id, bus.index, bus.bus_type, get(ac_opf_result.vm, bus.id, bus.vm0), get(ac_opf_result.va, bus.id, bus.va0), bus.vmin, bus.vmax)
        for bus in case.buses
    ]
    return PowerCase(case.name, case.baseMVA, new_buses, case.branches, new_generators, case.loads, case.shunts, case.slack_bus_id, case.bus_id_to_index)
end
