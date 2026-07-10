# ============================================================
# AC PF validation against PowerModels.compute_ac_pf only
# ============================================================

using PowerModels
using DataFrames

const COMPUTE_AC_PF_SUPPORTED_KWARGS = Set([
    :method,
    :flat_start,
    :show_trace,
    :store_trace,
    :iterations,
    :ftol,
    :xtol,
])

function _compute_pf_case_name(data::Dict)
    return string(get(data, "name", "unknown"))
end

function _compute_pf_converged(result::Dict)
    status = get(result, "termination_status", false)
    return status == true || lowercase(string(status)) in ("true", "converged", "locally_solved")
end

function _validate_compute_ac_pf_kwargs(nlsolve_kwargs::Dict{Symbol,Any})
    unsupported = sort([key for key in keys(nlsolve_kwargs) if !(key in COMPUTE_AC_PF_SUPPORTED_KWARGS)])
    if !isempty(unsupported)
        error(
            "Unsupported PowerModels.compute_ac_pf keyword(s): " *
            join(string.(unsupported), ", ") *
            ". Supported keywords in this project are: " *
            join(string.(sort(collect(COMPUTE_AC_PF_SUPPORTED_KWARGS))), ", ")
        )
    end
end

function _compute_pf_start_kwargs(start_mode::Symbol)
    if start_mode == :powermodels_default
        return Dict{Symbol,Any}()
    elseif start_mode == :case_values_if_supported
        return Dict{Symbol,Any}(:flat_start => false)
    elseif start_mode == :flat_if_supported
        return Dict{Symbol,Any}(:flat_start => true)
    else
        error("Unsupported start_mode=$(start_mode). Use :powermodels_default, :case_values_if_supported, or :flat_if_supported.")
    end
end

function _merge_compute_pf_kwargs(start_mode::Symbol, nlsolve_kwargs::Dict{Symbol,Any})
    kwargs = _compute_pf_start_kwargs(start_mode)
    for (key, value) in nlsolve_kwargs
        kwargs[key] = value
    end
    _validate_compute_ac_pf_kwargs(kwargs)
    return kwargs
end

function _try_calculate_branch_flows!(data_copy::Dict, result::Dict)
    try
        if !haskey(result, "solution")
            error("PowerModels result has no solution field")
        end
        PowerModels.update_data!(data_copy, result["solution"])
        flows = PowerModels.calc_branch_flow_ac(data_copy)
        PowerModels.update_data!(data_copy, flows)
        if haskey(flows, "branch")
            result["solution"]["branch"] = flows["branch"]
        end
        return true, missing
    catch err
        return false, sprint(showerror, err)
    end
end

function _extract_pm_bus_voltages(result::Dict)
    rows = NamedTuple[]
    buses = get(get(result, "solution", Dict()), "bus", Dict())
    for bus_id in sort(collect(keys(buses)); by=x -> parse(Int, x))
        bus = buses[bus_id]
        push!(rows, (
            bus_id = string(bus_id),
            vm = get(bus, "vm", missing),
            va = get(bus, "va", missing),
        ))
    end
    return DataFrame(rows)
end

function _extract_pm_generators(result::Dict)
    rows = NamedTuple[]
    gens = get(get(result, "solution", Dict()), "gen", Dict())
    for gen_id in sort(collect(keys(gens)); by=x -> parse(Int, x))
        gen = gens[gen_id]
        push!(rows, (
            gen_id = string(gen_id),
            pg = get(gen, "pg", missing),
            qg = get(gen, "qg", missing),
        ))
    end
    return DataFrame(rows)
end

function _extract_pm_branches(result::Dict)
    rows = NamedTuple[]
    branches = get(get(result, "solution", Dict()), "branch", Dict())
    for branch_id in sort(collect(keys(branches)); by=x -> parse(Int, x))
        branch = branches[branch_id]
        push!(rows, (
            branch_id = string(branch_id),
            pf = get(branch, "pf", missing),
            qf = get(branch, "qf", missing),
            pt = get(branch, "pt", missing),
            qt = get(branch, "qt", missing),
        ))
    end
    return DataFrame(rows)
end

function run_powermodels_ac_pf_compute(
    data;
    nlsolve_kwargs = Dict{Symbol,Any}(),
    calc_branch_flows::Bool = true,
    start_mode::Symbol = :powermodels_default,
)
    data_copy = deepcopy(data)
    kwargs = _merge_compute_pf_kwargs(start_mode, Dict{Symbol,Any}(nlsolve_kwargs))

    result = try
        PowerModels.compute_ac_pf(data_copy; kwargs...)
    catch err
        summary = (
            case_name = _compute_pf_case_name(data_copy),
            reference_method = "PowerModels.compute_ac_pf",
            solver_family = "NLsolve",
            formulation = "native polar AC PF",
            start_mode = string(start_mode),
            nlsolve_kwargs = repr(kwargs),
            termination_status = "EXCEPTION",
            converged = false,
            solve_time = missing,
            branch_flows_calculated = false,
            branch_flow_error = missing,
            exception = sprint(showerror, err),
        )
        return (
            raw_result = Dict{String,Any}(),
            summary = summary,
            buses = DataFrame(),
            generators = DataFrame(),
            branches = DataFrame(),
            data = data_copy,
        )
    end

    branch_flows_calculated = false
    branch_flow_error = missing
    if calc_branch_flows
        branch_flows_calculated, branch_flow_error = _try_calculate_branch_flows!(data_copy, result)
    end

    summary = (
        case_name = _compute_pf_case_name(data_copy),
        reference_method = "PowerModels.compute_ac_pf",
        solver_family = "NLsolve",
        formulation = "native polar AC PF",
        start_mode = string(start_mode),
        nlsolve_kwargs = repr(kwargs),
        termination_status = string(get(result, "termination_status", "missing")),
        converged = _compute_pf_converged(result),
        solve_time = get(result, "solve_time", missing),
        branch_flows_calculated = branch_flows_calculated,
        branch_flow_error = branch_flow_error,
        exception = missing,
    )

    return (
        raw_result = result,
        summary = summary,
        buses = _extract_pm_bus_voltages(result),
        generators = _extract_pm_generators(result),
        branches = _extract_pm_branches(result),
        data = data_copy,
    )
end

function _own_ac_pf_bus_table(case::PowerCase, own)
    return DataFrame([
        (
            bus_id = bus.id,
            vm = own.vm[bus.index],
            va = own.va[bus.index],
        )
        for bus in case.buses
    ])
end

function _own_ac_pf_gen_table(case::PowerCase, own)
    return DataFrame([
        (
            gen_id = gen.id,
            pg = gen.pg0,
            qg = get(own.qg_by_generator, gen.id, missing),
        )
        for gen in case.generators
    ])
end

function _own_ac_pf_branch_table(case::PowerCase, own)
    return DataFrame([
        (
            branch_id = branch_id,
            pf = get(flow, "pf", missing),
            qf = get(flow, "qf", missing),
            pt = get(flow, "pt", missing),
            qt = get(flow, "qt", missing),
        )
        for (branch_id, flow) in own.branch_flows
    ])
end

function _max_abs_join_diff(left::DataFrame, right::DataFrame, id_col::Symbol, value_col::Symbol)
    if nrow(left) == 0 || nrow(right) == 0 || !(value_col in propertynames(left)) || !(value_col in propertynames(right))
        return missing
    end
    joined = innerjoin(left[:, [id_col, value_col]], right[:, [id_col, value_col]], on=id_col, makeunique=true)
    if nrow(joined) == 0
        return missing
    end
    diffs = Float64[]
    for row in eachrow(joined)
        lhs = row[value_col]
        rhs = row[Symbol(string(value_col) * "_1")]
        if !ismissing(lhs) && !ismissing(rhs)
            push!(diffs, abs(Float64(lhs) - Float64(rhs)))
        end
    end
    return isempty(diffs) ? missing : maximum(diffs)
end

function compare_own_fixed_ac_pf_with_powermodels_compute(
    case::PowerCase,
    data;
    own_kwargs = Dict{Symbol,Any}(),
    nlsolve_kwargs = Dict{Symbol,Any}(),
    start_mode::Symbol = :powermodels_default,
    calc_branch_flows::Bool = true,
)
    own = solve_ac_power_flow_own(case; method=:newton, own_kwargs...)
    pm = run_powermodels_ac_pf_compute(
        data;
        nlsolve_kwargs = nlsolve_kwargs,
        calc_branch_flows = calc_branch_flows,
        start_mode = start_mode,
    )

    own_bus = _own_ac_pf_bus_table(case, own)
    own_gen = _own_ac_pf_gen_table(case, own)
    own_branch = _own_ac_pf_branch_table(case, own)

    summary = merge(
        pm.summary,
        (
            own_ac_pf_mode = "fixed_type_newton_no_q_limit_switching",
            powermodels_ac_pf_mode = "compute_ac_pf_native",
            own_converged = own.converged,
            powermodels_converged = pm.summary.converged,
            own_mismatch_norm = own.mismatch_norm,
            max_vm_diff = _max_abs_join_diff(own_bus, pm.buses, :bus_id, :vm),
            max_va_diff = _max_abs_join_diff(own_bus, pm.buses, :bus_id, :va),
            max_pg_diff = _max_abs_join_diff(own_gen, pm.generators, :gen_id, :pg),
            max_qg_diff = _max_abs_join_diff(own_gen, pm.generators, :gen_id, :qg),
            max_branch_pf_diff = _max_abs_join_diff(own_branch, pm.branches, :branch_id, :pf),
            max_branch_qf_diff = _max_abs_join_diff(own_branch, pm.branches, :branch_id, :qf),
            max_branch_pt_diff = _max_abs_join_diff(own_branch, pm.branches, :branch_id, :pt),
            max_branch_qt_diff = _max_abs_join_diff(own_branch, pm.branches, :branch_id, :qt),
        ),
    )

    return (
        summary = summary,
        own_result = own,
        powermodels_reference = pm,
        own_buses = own_bus,
        own_generators = own_gen,
        own_branches = own_branch,
    )
end
