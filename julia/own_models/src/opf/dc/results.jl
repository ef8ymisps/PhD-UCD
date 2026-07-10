# Owns DC OPF result containers and extraction from a solved JuMP model.

using JuMP

struct DCOPFResult
    objective::Float64
    termination_status
    theta::Dict{String, Float64}
    pg::Dict{String, Float64}
    branch_flows::Dict{String, Float64}
    solve_iterations::Float64
end

"""Best-effort HiGHS simplex iteration count; NaN if unavailable for the solver in use."""
function _safe_simplex_iterations(model::Model)
    try
        return Float64(simplex_iterations(model))
    catch
        return NaN
    end
end

"""Return NaN-filled dictionaries when the optimizer has no primal solution."""
function empty_dc_opf_result(case::PowerCase, status)
    return DCOPFResult(
        NaN,
        status,
        Dict(bus.id => NaN for bus in case.buses),
        Dict(gen.id => NaN for gen in case.generators),
        Dict{String, Float64}(),
        NaN
    )
end

"""Extract theta, Pg, objective, and Bf * theta branch flows."""
function collect_dc_opf_result(model::Model, case::PowerCase, data::DCOPFData, theta, pg)
    status = termination_status(model)
    if !has_values(model)
        return empty_dc_opf_result(case, status)
    end

    theta_result = Dict(bus.id => value(theta[bus.id]) for bus in case.buses)
    pg_result = Dict(gen.id => value(pg[gen.id]) for gen in case.generators)

    branch_flow_result = Dict{String, Float64}()
    for (row, branch) in enumerate(data.active_branches)
        branch_flow_result[branch.id] = sum(
            data.Bf[row, j] * theta_result[data.bus_ids[j]]
            for j in 1:length(data.bus_ids)
        )
    end

    return DCOPFResult(
        objective_value(model),
        status,
        theta_result,
        pg_result,
        branch_flow_result,
        _safe_simplex_iterations(model)
    )
end
