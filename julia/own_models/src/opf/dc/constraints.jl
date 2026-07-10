# Owns DC OPF constraints: reference angle, nodal balance, branch flow limits,
# and branch angle-difference limits.

using JuMP

"""Fix the reference bus angle to remove the DC angle nullspace."""
function add_dc_reference_angle!(model::Model, case::PowerCase, theta; fix_reference_angle::Bool=true)
    if fix_reference_angle
        @constraint(model, theta[case.slack_bus_id] == 0.0)
    end
end

"""Add Pgen - Pd = Bbus * theta at every bus."""
function add_dc_power_balance!(model::Model, case::PowerCase, data::DCOPFData, theta, pg)
    for bus in case.buses
        i = case.bus_id_to_index[bus.id]
        gen_sum = sum((pg[g] for g in data.gens_by_bus[bus.id]); init=0.0)

        # Positive RHS is net active power injected from the bus into the DC network.
        @constraint(
            model,
            gen_sum - data.pd_by_bus[bus.id] ==
                sum(data.Bbus[i, j] * theta[data.bus_ids[j]] for j in 1:length(data.bus_ids))
        )
    end
end

"""Add branch flow and optional angle-difference limits."""
function add_dc_branch_constraints!(
    model::Model,
    data::DCOPFData,
    theta;
    include_branch_flows::Bool=true,
    include_branch_angles::Bool=true
)
    for (row, branch) in enumerate(data.active_branches)
        flow = @expression(
            model,
            sum(data.Bf[row, j] * theta[data.bus_ids[j]] for j in 1:length(data.bus_ids))
        )

        if include_branch_flows && branch.rate_a > 0.0
            @constraint(model, -branch.rate_a <= flow <= branch.rate_a)
        end

        if include_branch_angles
            @constraint(model, branch.angmin <= theta[branch.from_bus] - theta[branch.to_bus] <= branch.angmax)
        end
    end
end
