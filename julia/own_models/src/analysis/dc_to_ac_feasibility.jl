# ============================================================
# DC OPF dispatch -> AC PF feasibility check
# ============================================================

struct VoltageViolation
    bus_id::String
    vm::Float64
    vmin::Float64
    vmax::Float64
    violation_type::String
    magnitude::Float64
end

struct ReactiveViolation
    bus_id::String
    qg_required::Float64
    qmin_total::Float64
    qmax_total::Float64
    violation_type::String
    magnitude::Float64
end

struct BranchThermalViolation
    branch_id::String
    from_bus::String
    to_bus::String
    sf::Float64
    st::Float64
    rate_a::Float64
    violation_type::String
    magnitude::Float64
end

struct DCToACFeasibilityResult
    ac_pf_result
    voltage_violations::Vector{VoltageViolation}
    reactive_violations::Vector{ReactiveViolation}
    branch_violations::Vector{BranchThermalViolation}
    feasible::Bool
end

function case_with_generator_pg_dispatch(case::PowerCase, pg_dispatch::Dict{String, Float64})
    updated_generators = Generator[]

    for gen in case.generators
        new_pg = haskey(pg_dispatch, gen.id) ? pg_dispatch[gen.id] : gen.pg0

        push!(updated_generators, Generator(
            gen.id,
            gen.index,
            gen.bus,
            new_pg,
            gen.qg0,
            gen.pmin,
            gen.pmax,
            gen.qmin,
            gen.qmax,
            gen.status,
            gen.cost_c2,
            gen.cost_c1,
            gen.cost_c0
        ))
    end

    return PowerCase(
        case.name,
        case.baseMVA,
        case.buses,
        case.branches,
        updated_generators,
        case.loads,
        case.shunts,
        case.slack_bus_id,
        case.bus_id_to_index
    )
end

function check_voltage_violations(case::PowerCase, ac_pf_result; tol=1e-6)
    violations = VoltageViolation[]

    for bus in case.buses
        vm = ac_pf_result.vm[bus.index]

        if vm < bus.vmin - tol
            push!(violations, VoltageViolation(
                bus.id,
                vm,
                bus.vmin,
                bus.vmax,
                "LOW_VOLTAGE",
                bus.vmin - vm
            ))
        elseif vm > bus.vmax + tol
            push!(violations, VoltageViolation(
                bus.id,
                vm,
                bus.vmin,
                bus.vmax,
                "HIGH_VOLTAGE",
                vm - bus.vmax
            ))
        end
    end

    return violations
end

function check_reactive_violations_by_bus(case::PowerCase, ac_pf_result; tol=1e-6)
    violations = ReactiveViolation[]

    for bus in case.buses
        gens_at_bus = [
            gen for gen in case.generators
            if gen.bus == bus.id && gen.status == 1
        ]

        if isempty(gens_at_bus)
            continue
        end

        qmin_total = sum(gen.qmin for gen in gens_at_bus)
        qmax_total = sum(gen.qmax for gen in gens_at_bus)

        qg_required = get(ac_pf_result.qg_by_bus, bus.id, 0.0)

        if qg_required < qmin_total - tol
            push!(violations, ReactiveViolation(
                bus.id,
                qg_required,
                qmin_total,
                qmax_total,
                "BELOW_QMIN",
                qmin_total - qg_required
            ))
        elseif qg_required > qmax_total + tol
            push!(violations, ReactiveViolation(
                bus.id,
                qg_required,
                qmin_total,
                qmax_total,
                "ABOVE_QMAX",
                qg_required - qmax_total
            ))
        end
    end

    return violations
end

function check_branch_thermal_violations(case::PowerCase, ac_pf_result; tol=1e-6)
    violations = BranchThermalViolation[]

    for branch in case.branches
        if branch.status == 0 || branch.rate_a <= 0
            continue
        end

        if !haskey(ac_pf_result.branch_flows, branch.id)
            continue
        end

        flow = ac_pf_result.branch_flows[branch.id]

        pf = flow["pf"]
        qf = flow["qf"]
        pt = flow["pt"]
        qt = flow["qt"]

        sf = sqrt(pf^2 + qf^2)
        st = sqrt(pt^2 + qt^2)

        max_loading = max(sf, st)

        if max_loading > branch.rate_a + tol
            push!(violations, BranchThermalViolation(
                branch.id,
                branch.from_bus,
                branch.to_bus,
                sf,
                st,
                branch.rate_a,
                "THERMAL_LIMIT",
                max_loading - branch.rate_a
            ))
        end
    end

    return violations
end

function check_dc_opf_dispatch_with_ac_pf(case::PowerCase, dc_opf_result; verbose=true)
    ac_case = case_with_generator_pg_dispatch(case, dc_opf_result.pg)

    ac_pf_result = solve_ac_power_flow_own(ac_case)

    voltage_violations = check_voltage_violations(ac_case, ac_pf_result)
    reactive_violations = check_reactive_violations_by_bus(ac_case, ac_pf_result)
    branch_violations = check_branch_thermal_violations(ac_case, ac_pf_result)

    feasible =
        ac_pf_result.converged &&
        isempty(voltage_violations) &&
        isempty(reactive_violations) &&
        isempty(branch_violations)

    if verbose
        println("\n==============================")
        println("DC OPF DISPATCH -> AC PF FEASIBILITY")
        println("==============================")

        println("AC PF converged: ", ac_pf_result.converged)
        println("AC PF iterations: ", ac_pf_result.iterations)
        println("AC PF mismatch norm: ", ac_pf_result.mismatch_norm)

        println("\nVoltage violations: ", length(voltage_violations))
        for v in voltage_violations
            println(
                "Bus ", v.bus_id,
                " | vm = ", round(v.vm, digits=6),
                " | limits = [", v.vmin, ", ", v.vmax, "]",
                " | ", v.violation_type,
                " | magnitude = ", round(v.magnitude, digits=6)
            )
        end

        println("\nReactive capability violations: ", length(reactive_violations))
        for q in reactive_violations
            println(
                "Bus ", q.bus_id,
                " | qg required = ", round(q.qg_required, digits=6),
                " | q limits = [", q.qmin_total, ", ", q.qmax_total, "]",
                " | ", q.violation_type,
                " | magnitude = ", round(q.magnitude, digits=6)
            )
        end

        println("\nBranch thermal violations: ", length(branch_violations))
        for br in branch_violations
            println(
                "Branch ", br.branch_id,
                " | ", br.from_bus, " -> ", br.to_bus,
                " | Sf = ", round(br.sf, digits=6),
                " | St = ", round(br.st, digits=6),
                " | rate_a = ", br.rate_a,
                " | magnitude = ", round(br.magnitude, digits=6)
            )
        end

        println("\nFinal feasibility result: ", feasible)
    end

    return DCToACFeasibilityResult(
        ac_pf_result,
        voltage_violations,
        reactive_violations,
        branch_violations,
        feasible
    )
end