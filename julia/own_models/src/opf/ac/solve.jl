# Owns the public AC OPF solve entry point.

using JuMP
using Ipopt

"""Solve AC OPF and return objective, dispatch, voltages, and branch flows."""
function solve_ac_opf_own(case::PowerCase; kwargs...)
    model, data, pg, qg, vm, va, branch_flows = build_ac_opf_model(case; kwargs...)
    optimize!(model)
    return collect_ac_opf_result(model, case, data, pg, qg, vm, va, branch_flows)
end
