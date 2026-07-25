# Owns the public DC OPF solve entry point.

using JuMP
using HiGHS

"""Solve DC OPF and return objective, dispatch, angles, and branch flows."""
function solve_dc_opf_own(case::PowerCase; kwargs...)
    model, data, theta, pg, _constraints = build_dc_opf_model(case; kwargs...)
    optimize!(model)
    return collect_dc_opf_result(model, case, data, theta, pg)
end
