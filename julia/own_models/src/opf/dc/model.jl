# Owns assembly of the DC OPF JuMP model.

using JuMP
using HiGHS

"""Build the DC OPF model without solving it."""
function build_dc_opf_model(
    case::PowerCase;
    optimizer=default_optimizer(),
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

    theta, pg, Pf = add_dc_variables!(model, case, data)
    reference_angle = add_dc_reference_angle!(model, case, theta; fix_reference_angle=fix_reference_angle)
    power_balance = add_dc_power_balance!(model, case, data, theta, pg)
    branch_flow_eq, branch_angle_limits = add_dc_branch_constraints!(
        model,
        data,
        theta,
        Pf;
        include_branch_flows=include_branch_flows,
        include_branch_angles=include_branch_angles
    )
    add_dc_generation_cost_objective!(model, case, pg)

    # Named/indexed constraint references (A5) -- not consumed by
    # solve_dc_opf_own's result struct, but available to any caller that
    # wants duals/shadow prices via build_dc_opf_model directly. See
    # docs/notation.md for the full symbol-to-identifier mapping.
    constraints = (
        reference_angle=reference_angle,
        power_balance=power_balance,
        branch_flow_eq=branch_flow_eq,
        branch_angle_limits=branch_angle_limits,
    )

    return model, data, theta, pg, Pf, constraints
end
