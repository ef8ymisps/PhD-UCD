# Owns assembly of the AC OPF JuMP model.

using JuMP
using Ipopt

"""Build the AC OPF model without solving it."""
function build_ac_opf_model(
    case::PowerCase;
    optimizer=Ipopt.Optimizer,
    silent::Bool=true,
    voltage_bounds=nothing,
    fix_reference_angle::Bool=true,
    include_branch_angles::Bool=true,
    include_branch_thermal::Bool=true,
    objective::Symbol=:generation_cost
)
    data = build_ac_opf_data(case)
    model = Model(optimizer)
    if silent
        set_silent(model)
    end

    pg, qg, vm, va = add_ac_variables!(model, case, data; voltage_bounds=voltage_bounds)
    add_ac_reference_angle!(model, case, va; fix_reference_angle=fix_reference_angle)
    add_ac_power_balance!(model, case, data, pg, qg, vm, va)
    branch_flows = add_ac_branch_constraints!(
        model,
        data,
        vm,
        va;
        include_branch_thermal=include_branch_thermal,
        include_branch_angles=include_branch_angles
    )

    if objective == :generation_cost
        add_ac_generation_cost_objective!(model, case, pg)
    elseif objective == :pg_deviation
        add_ac_pg_deviation_objective!(model, case, pg)
    elseif objective == :active_losses
        add_ac_active_losses_objective!(model, branch_flows[1], branch_flows[3])
    else
        error("Unsupported AC OPF objective: $(objective)")
    end

    return model, data, pg, qg, vm, va, branch_flows
end
