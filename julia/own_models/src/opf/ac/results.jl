# Owns AC OPF result containers and extraction from a solved JuMP model.

using JuMP

struct ACOPFResult
    objective::Float64
    termination_status
    vm::Dict{String, Float64}
    va::Dict{String, Float64}
    pg::Dict{String, Float64}
    qg::Dict{String, Float64}
    branch_flows::Dict{String, Dict{String, Float64}}
    solve_iterations::Float64
end

"""Best-effort Ipopt barrier iteration count; NaN if unavailable for the solver in use."""
function _safe_barrier_iterations(model::Model)
    try
        return Float64(barrier_iterations(model))
    catch
        return NaN
    end
end

"""Return NaN-filled dictionaries when the optimizer has no primal solution."""
function empty_ac_opf_result(case::PowerCase, status)
    return ACOPFResult(
        NaN,
        status,
        Dict(bus.id => NaN for bus in case.buses),
        Dict(bus.id => NaN for bus in case.buses),
        Dict(gen.id => NaN for gen in case.generators),
        Dict(gen.id => NaN for gen in case.generators),
        Dict{String, Dict{String, Float64}}(),
        NaN
    )
end

"""Extract voltages, dispatch, objective, and branch apparent-flow components."""
function collect_ac_opf_result(model::Model, case::PowerCase, data::ACOPFData, pg, qg, vm, va, branch_flows)
    status = termination_status(model)
    if !has_values(model)
        return empty_ac_opf_result(case, status)
    end

    pg_result = Dict(gen.id => value(pg[gen.id]) for gen in case.generators)
    qg_result = Dict(gen.id => value(qg[gen.id]) for gen in case.generators)
    vm_result = Dict(bus.id => value(vm[bus.id]) for bus in case.buses)
    va_result = Dict(bus.id => value(va[bus.id]) for bus in case.buses)

    pf_by_branch, qf_by_branch, pt_by_branch, qt_by_branch = branch_flows.pf, branch_flows.qf, branch_flows.pt, branch_flows.qt
    flow_result = Dict{String, Dict{String, Float64}}()
    for branch in data.active_branches
        flow_result[branch.id] = Dict(
            "pf" => value(pf_by_branch[branch.id]),
            "qf" => value(qf_by_branch[branch.id]),
            "pt" => value(pt_by_branch[branch.id]),
            "qt" => value(qt_by_branch[branch.id])
        )
    end

    return ACOPFResult(
        objective_value(model),
        status,
        vm_result,
        va_result,
        pg_result,
        qg_result,
        flow_result,
        _safe_barrier_iterations(model)
    )
end
