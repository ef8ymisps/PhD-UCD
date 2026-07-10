# Owns assembly of the DC OPF JuMP model.

using JuMP
using HiGHS

"""Build the DC OPF model without solving it."""
function build_dc_opf_model(
    case::PowerCase;
    optimizer=HiGHS.Optimizer,
    silent::Bool=true,
    fix_reference_angle::Bool=true,
    include_branch_flows::Bool=true,
    include_branch_angles::Bool=true
)
    data = build_dc_opf_data(case)
    model = Model(optimizer)
    if silent
        set_silent(model)
    end

    theta, pg = add_dc_variables!(model, case, data)
    add_dc_reference_angle!(model, case, theta; fix_reference_angle=fix_reference_angle)
    add_dc_power_balance!(model, case, data, theta, pg)
    add_dc_branch_constraints!(
        model,
        data,
        theta;
        include_branch_flows=include_branch_flows,
        include_branch_angles=include_branch_angles
    )
    add_dc_generation_cost_objective!(model, case, pg)

    return model, data, theta, pg
end
