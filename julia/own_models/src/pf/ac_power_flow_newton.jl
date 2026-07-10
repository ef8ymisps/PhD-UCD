# ============================================================
# Classical Newton-Raphson AC power flow
# Fixed bus-type core plus optional one-way PV-PQ switching
# ============================================================

using LinearAlgebra

const PQ_BUS = 1
const PV_BUS = 2
const SLACK_BUS = 3

struct PVtoPQSwitchingEvent
    outer_iteration::Int
    bus_id::String
    from_type::Int
    to_type::Int
    required_q::Float64
    qmin_bus::Float64
    qmax_bus::Float64
    fixed_q::Float64
    violation_direction::String
    violation_magnitude::Float64
end

struct QAllocationResult
    aggregate_feasible::Bool
    individual_allocation::Dict{String, Float64}
end

struct ACPowerFlowResult
    converged::Bool
    iterations::Int
    vm::Vector{Float64}
    va::Vector{Float64}
    pcalc::Vector{Float64}
    qcalc::Vector{Float64}
    qg_by_bus::Dict{String, Float64}
    branch_flows
    mismatch_norm::Float64
    final_bus_types::Dict{String, Int}
    switched_buses::Vector{String}
    switching_events::Vector{PVtoPQSwitchingEvent}
    outer_iterations::Int
    qg_by_generator::Dict{String, Float64}
    slack_q_violation::Bool
    q_limit_converged::Bool
    termination_reason::String
    power_flow_converged::Bool
end

function ACPowerFlowResult(
    converged,
    iterations,
    vm,
    va,
    pcalc,
    qcalc,
    qg_by_bus,
    branch_flows,
    mismatch_norm,
)
    return ACPowerFlowResult(
        converged,
        iterations,
        vm,
        va,
        pcalc,
        qcalc,
        qg_by_bus,
        branch_flows,
        mismatch_norm,
        Dict{String, Int}(),
        String[],
        PVtoPQSwitchingEvent[],
        0,
        Dict{String, Float64}(),
        false,
        true,
        converged ? "fixed_type_converged" : "newton_failed",
        converged,
    )
end

function _newton_power_injections(G, B, vm, va)
    nb = length(vm)
    P = zeros(nb)
    Q = zeros(nb)
    for i in 1:nb
        for j in 1:nb
            angle = va[i] - va[j]
            P[i] += vm[i] * vm[j] * (G[i, j] * cos(angle) + B[i, j] * sin(angle))
            Q[i] += vm[i] * vm[j] * (G[i, j] * sin(angle) - B[i, j] * cos(angle))
        end
    end
    return P, Q
end

function _newton_jacobian(G, B, vm, va, Pcalc, Qcalc, angle_buses, pq_buses)
    n_angle = length(angle_buses)
    n_pq = length(pq_buses)
    J11 = zeros(n_angle, n_angle)
    J12 = zeros(n_angle, n_pq)
    J21 = zeros(n_pq, n_angle)
    J22 = zeros(n_pq, n_pq)

    for (row, i) in enumerate(angle_buses)
        for (col, j) in enumerate(angle_buses)
            if i == j
                J11[row, col] = -Qcalc[i] - B[i, i] * vm[i]^2
            else
                angle = va[i] - va[j]
                J11[row, col] = vm[i] * vm[j] * (G[i, j] * sin(angle) - B[i, j] * cos(angle))
            end
        end
        for (col, j) in enumerate(pq_buses)
            if i == j
                J12[row, col] = Pcalc[i] / vm[i] + G[i, i] * vm[i]
            else
                angle = va[i] - va[j]
                J12[row, col] = vm[i] * (G[i, j] * cos(angle) + B[i, j] * sin(angle))
            end
        end
    end

    for (row, i) in enumerate(pq_buses)
        for (col, j) in enumerate(angle_buses)
            if i == j
                J21[row, col] = Pcalc[i] - G[i, i] * vm[i]^2
            else
                angle = va[i] - va[j]
                J21[row, col] = -vm[i] * vm[j] * (G[i, j] * cos(angle) + B[i, j] * sin(angle))
            end
        end
        for (col, j) in enumerate(pq_buses)
            if i == j
                J22[row, col] = Qcalc[i] / vm[i] - B[i, i] * vm[i]
            else
                angle = va[i] - va[j]
                J22[row, col] = vm[i] * (G[i, j] * sin(angle) - B[i, j] * cos(angle))
            end
        end
    end
    return [J11 J12; J21 J22]
end

function calc_ac_branch_flows(case::PowerCase, vm, va)
    V = vm .* exp.(im .* va)
    flows = Dict{String, Dict{String, Float64}}()
    for branch in case.branches
        if branch.status == 0
            continue
        end
        i = case.bus_id_to_index[branch.from_bus]
        j = case.bus_id_to_index[branch.to_bus]
        y = 1.0 / (branch.r + im * branch.x)
        y_fr = branch.g_fr + im * branch.b_fr
        y_to = branch.g_to + im * branch.b_to
        tap_mag = branch.tap == 0.0 ? 1.0 : branch.tap
        tap = tap_mag * exp(im * branch.shift)
        yff = (y + y_fr) / abs2(tap)
        yft = -y / conj(tap)
        ytf = -y / tap
        ytt = y + y_to
        vf = V[i]
        vt = V[j]
        sf = vf * conj(yff * vf + yft * vt)
        st = vt * conj(ytf * vf + ytt * vt)
        flows[branch.id] = Dict(
            "pf" => real(sf),
            "qf" => imag(sf),
            "pt" => real(st),
            "qt" => imag(st),
        )
    end
    return flows
end

function build_specified_power(case::PowerCase)
    P = zeros(length(case.buses))
    Q = zeros(length(case.buses))
    for gen in case.generators
        if gen.status == 1
            i = case.bus_id_to_index[gen.bus]
            P[i] += gen.pg0
            Q[i] += gen.qg0
        end
    end
    for load in case.loads
        if load.status == 1
            i = case.bus_id_to_index[load.bus]
            P[i] -= load.pd
            Q[i] -= load.qd
        end
    end
    return P, Q
end

function get_bus_type_indices(case::PowerCase)
    slack = case.bus_id_to_index[case.slack_bus_id]
    pv = [bus.index for bus in case.buses if bus.bus_type == PV_BUS]
    pq = [bus.index for bus in case.buses if bus.bus_type == PQ_BUS]
    return slack, pv, pq
end

function _bus_type_indices_from_working_types(case::PowerCase, bus_types::Dict{String, Int})
    slack = case.bus_id_to_index[case.slack_bus_id]
    pv = [case.bus_id_to_index[bus.id] for bus in case.buses if bus_types[bus.id] == PV_BUS]
    pq = [case.bus_id_to_index[bus.id] for bus in case.buses if bus_types[bus.id] == PQ_BUS]
    return slack, pv, pq
end

function _load_q_by_bus(case::PowerCase)
    qd = Dict(bus.id => 0.0 for bus in case.buses)
    for load in case.loads
        if load.status == 1
            qd[load.bus] += load.qd
        end
    end
    return qd
end

function estimate_qg_by_bus(case::PowerCase, Qcalc)
    qd = _load_q_by_bus(case)
    qg = Dict{String, Float64}()
    for gen in case.generators
        if gen.status == 1
            idx = case.bus_id_to_index[gen.bus]
            qg[gen.bus] = Qcalc[idx] + get(qd, gen.bus, 0.0)
        end
    end
    return qg
end

function _active_generators_by_bus(case::PowerCase)
    grouped = Dict{String, Vector{Generator}}()
    for gen in case.generators
        if gen.status == 1
            push!(get!(grouped, gen.bus, Generator[]), gen)
        end
    end
    for gens in values(grouped)
        sort!(gens, by=g -> (g.index, g.id))
    end
    return grouped
end

function _aggregate_q_limits_by_bus(case::PowerCase)
    limits = Dict{String, Tuple{Float64, Float64}}()
    for (bus_id, gens) in _active_generators_by_bus(case)
        limits[bus_id] = (sum(g.qmin for g in gens), sum(g.qmax for g in gens))
    end
    return limits
end

function allocate_qg_to_generators(generators, q_required; tol=1e-8)
    if isempty(generators)
        return QAllocationResult(abs(q_required) <= tol, Dict{String, Float64}())
    end
    gens = sort(collect(generators), by=g -> (g.index, g.id))
    qmin_total = sum(g.qmin for g in gens)
    qmax_total = sum(g.qmax for g in gens)
    feasible = qmin_total - tol <= q_required <= qmax_total + tol
    target = min(max(q_required, qmin_total), qmax_total)
    allocation = Dict(g.id => g.qmin for g in gens)
    remaining = target - qmin_total
    flexible = [g for g in gens if g.qmax - g.qmin > tol]
    while remaining > tol && !isempty(flexible)
        total_range = sum(g.qmax - allocation[g.id] for g in flexible)
        total_range <= tol && break
        used = 0.0
        next_flexible = Generator[]
        for g in flexible
            headroom = g.qmax - allocation[g.id]
            add = min(headroom, remaining * headroom / total_range)
            allocation[g.id] += add
            used += add
            if g.qmax - allocation[g.id] > tol
                push!(next_flexible, g)
            end
        end
        remaining -= used
        flexible = next_flexible
    end
    return QAllocationResult(feasible, Dict(k => Float64(v) for (k, v) in allocation))
end

function _allocate_all_qg_by_generator(case::PowerCase, qg_by_bus)
    allocation = Dict{String, Float64}()
    feasible = true
    for (bus_id, gens) in _active_generators_by_bus(case)
        result = allocate_qg_to_generators(gens, get(qg_by_bus, bus_id, 0.0))
        merge!(allocation, result.individual_allocation)
        feasible = feasible && result.aggregate_feasible
    end
    return allocation, feasible
end

function solve_ac_power_flow_newton_fixed_types(
    case::PowerCase;
    pv_buses,
    pq_buses,
    qspec_override=Dict{Int, Float64}(),
    vm_start=nothing,
    va_start=nothing,
    tol=1e-8,
    max_iter=20,
)
    Ybus = build_ybus(case)
    G = real.(Ybus)
    B = imag.(Ybus)
    Pspec, Qspec = build_specified_power(case)
    for (idx, value) in qspec_override
        Qspec[idx] = value
    end
    angle_buses = sort(vcat(pv_buses, pq_buses))
    vm = vm_start === nothing ? [bus.vm0 for bus in case.buses] : copy(vm_start)
    va = va_start === nothing ? [bus.va0 for bus in case.buses] : copy(va_start)

    converged = false
    iterations = 0
    mismatch_norm = Inf
    for iter in 1:max_iter
        Pcalc, Qcalc = _newton_power_injections(G, B, vm, va)
        mismatch = vcat(Pspec[angle_buses] - Pcalc[angle_buses], Qspec[pq_buses] - Qcalc[pq_buses])
        mismatch_norm = norm(mismatch, Inf)
        iterations = iter - 1
        if mismatch_norm <= tol
            converged = true
            break
        end
        J = _newton_jacobian(G, B, vm, va, Pcalc, Qcalc, angle_buses, pq_buses)
        dx = try
            J \ mismatch
        catch
            break
        end
        n_angle = length(angle_buses)
        va[angle_buses] .+= dx[1:n_angle]
        vm[pq_buses] .+= dx[n_angle+1:end]
        iterations = iter
    end

    Pcalc, Qcalc = _newton_power_injections(G, B, vm, va)
    mismatch = vcat(Pspec[angle_buses] - Pcalc[angle_buses], Qspec[pq_buses] - Qcalc[pq_buses])
    mismatch_norm = norm(mismatch, Inf)
    converged = converged || mismatch_norm <= tol
    qg_by_bus = estimate_qg_by_bus(case, Qcalc)
    qg_by_generator, allocation_feasible = _allocate_all_qg_by_generator(case, qg_by_bus)
    return ACPowerFlowResult(
        converged,
        iterations,
        vm,
        va,
        Pcalc,
        Qcalc,
        qg_by_bus,
        calc_ac_branch_flows(case, vm, va),
        mismatch_norm,
        Dict(bus.id => bus.bus_type for bus in case.buses),
        String[],
        PVtoPQSwitchingEvent[],
        0,
        qg_by_generator,
        false,
        allocation_feasible,
        converged ? "fixed_type_converged" : "newton_failed",
        converged,
    )
end

function solve_ac_power_flow_newton(case::PowerCase; tol=1e-8, max_iter=20)
    _, pv_buses, pq_buses = get_bus_type_indices(case)
    return solve_ac_power_flow_newton_fixed_types(case; pv_buses=pv_buses, pq_buses=pq_buses, tol=tol, max_iter=max_iter)
end

function solve_ac_power_flow_with_q_limits(
    case::PowerCase;
    mode::Symbol=:one_way,
    switch_policy::Symbol=:all,
    q_tolerance=1e-6,
    max_switch_iterations=nothing,
    newton_tol=1e-8,
    newton_max_iter=20,
)
    mode == :one_way || error("Only one-way PV-PQ switching is implemented in this phase.")
    switch_policy in (:all, :largest_violation) || error("switch_policy must be :all or :largest_violation.")
    bus_types = Dict(bus.id => bus.bus_type for bus in case.buses)
    _, original_pv, _ = get_bus_type_indices(case)
    original_non_slack_pv = [idx for idx in original_pv if case.buses[idx].id != case.slack_bus_id]
    max_iter_outer = max_switch_iterations === nothing ? length(original_non_slack_pv) + 1 : max_switch_iterations
    qd_by_bus = _load_q_by_bus(case)
    q_limits = _aggregate_q_limits_by_bus(case)
    qspec_override = Dict{Int, Float64}()
    switched = Set{String}()
    events = PVtoPQSwitchingEvent[]
    vm_start = nothing
    va_start = nothing
    last_result = nothing

    for outer in 1:max_iter_outer
        _, pv_buses, pq_buses = _bus_type_indices_from_working_types(case, bus_types)
        result = solve_ac_power_flow_newton_fixed_types(
            case;
            pv_buses=pv_buses,
            pq_buses=pq_buses,
            qspec_override=qspec_override,
            vm_start=vm_start,
            va_start=va_start,
            tol=newton_tol,
            max_iter=newton_max_iter,
        )
        last_result = result
        if !result.converged
            return ACPowerFlowResult(
                result.converged, result.iterations, result.vm, result.va, result.pcalc, result.qcalc,
                result.qg_by_bus, result.branch_flows, result.mismatch_norm, copy(bus_types), sort(collect(switched)),
                events, outer, result.qg_by_generator, false, false, "newton_failed", false,
            )
        end
        violations = []
        for idx in pv_buses
            bus = case.buses[idx]
            if bus.id == case.slack_bus_id || bus.id in switched || !haskey(q_limits, bus.id)
                continue
            end
            q_required = get(result.qg_by_bus, bus.id, 0.0)
            qmin, qmax = q_limits[bus.id]
            if q_required > qmax + q_tolerance
                push!(violations, (q_required - qmax, bus.id, q_required, qmin, qmax, qmax, "upper"))
            elseif q_required < qmin - q_tolerance
                push!(violations, (qmin - q_required, bus.id, q_required, qmin, qmax, qmin, "lower"))
            end
        end
        if isempty(violations)
            qg_by_generator, allocation_feasible = _allocate_all_qg_by_generator(case, result.qg_by_bus)
            slack_violation = false
            if haskey(q_limits, case.slack_bus_id)
                q_slack = get(result.qg_by_bus, case.slack_bus_id, 0.0)
                qmin_slack, qmax_slack = q_limits[case.slack_bus_id]
                slack_violation = q_slack < qmin_slack - q_tolerance || q_slack > qmax_slack + q_tolerance
            end
            q_ok = allocation_feasible && !slack_violation
            reason = q_ok ? "q_limits_satisfied" : (slack_violation ? "slack_q_limit_violated" : "individual_q_allocation_infeasible")
            return ACPowerFlowResult(
                result.converged, result.iterations, result.vm, result.va, result.pcalc, result.qcalc,
                result.qg_by_bus, result.branch_flows, result.mismatch_norm, copy(bus_types), sort(collect(switched)),
                events, outer, qg_by_generator, slack_violation, q_ok, reason, result.converged,
            )
        end
        if switch_policy == :largest_violation
            violations = [maximum(violations, by=x -> x[1])]
        end
        for (magnitude, bus_id, q_required, qmin, qmax, fixed_q, direction) in violations
            idx = case.bus_id_to_index[bus_id]
            bus_types[bus_id] = PQ_BUS
            qspec_override[idx] = fixed_q - get(qd_by_bus, bus_id, 0.0)
            push!(switched, bus_id)
            push!(events, PVtoPQSwitchingEvent(outer, bus_id, PV_BUS, PQ_BUS, q_required, qmin, qmax, fixed_q, direction, magnitude))
        end
        vm_start = copy(result.vm)
        va_start = copy(result.va)
    end

    result = last_result
    return ACPowerFlowResult(
        result.converged, result.iterations, result.vm, result.va, result.pcalc, result.qcalc,
        result.qg_by_bus, result.branch_flows, result.mismatch_norm, copy(bus_types), sort(collect(switched)),
        events, max_iter_outer, result.qg_by_generator, false, false, "maximum_switch_iterations", result.converged,
    )
end
