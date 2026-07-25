# Owns DC OPF constraints: reference angle, nodal balance, branch flow limits,
# and branch angle-difference limits.

using JuMP

"""Fix the reference bus angle to remove the DC angle nullspace.

Returns the `ConstraintRef`, or `nothing` if `fix_reference_angle` is false.
"""
function add_dc_reference_angle!(model::Model, case::PowerCase, theta; fix_reference_angle::Bool=true)
    if fix_reference_angle
        return @constraint(model, theta[case.slack_bus_id] == 0.0, base_name = "reference_angle")
    end
    return nothing
end

"""Add Pgen - Pd = Bbus * theta at every bus.

Returns a `Dict{String, ConstraintRef}` keyed by bus id, so each nodal
balance constraint (and its dual / shadow price once solved) is reachable
by name rather than only existing anonymously inside the model.
"""
function add_dc_power_balance!(model::Model, case::PowerCase, data::DCOPFData, theta, pg)
    power_balance = Dict{String, ConstraintRef}()

    for bus in case.buses
        i = case.bus_id_to_index[bus.id]
        gen_sum = sum((pg[g] for g in data.gens_by_bus[bus.id]); init=0.0)

        # Positive RHS is net active power injected from the bus into the DC network.
        power_balance[bus.id] = @constraint(
            model,
            gen_sum - data.pd_by_bus[bus.id] ==
                sum(data.Bbus[i, j] * theta[data.bus_ids[j]] for j in 1:length(data.bus_ids)),
            base_name = "power_balance[$(bus.id)]"
        )
    end

    return power_balance
end

"""Add branch flow and optional angle-difference limits.

Returns `(flow_limits, angle_limits)`, each a `Dict{String, ConstraintRef}`
keyed by branch id. A branch is present in `flow_limits` only if
`include_branch_flows` is true and it has a finite thermal rating
(`rate_a > 0`); present in `angle_limits` only if `include_branch_angles`
is true.
"""
function add_dc_branch_constraints!(
    model::Model,
    data::DCOPFData,
    theta;
    include_branch_flows::Bool=true,
    include_branch_angles::Bool=true
)
    flow_limits = Dict{String, ConstraintRef}()
    angle_limits = Dict{String, ConstraintRef}()

    for (row, branch) in enumerate(data.active_branches)
        flow = @expression(
            model,
            sum(data.Bf[row, j] * theta[data.bus_ids[j]] for j in 1:length(data.bus_ids))
        )

        if include_branch_flows && branch.rate_a > 0.0
            flow_limits[branch.id] = @constraint(
                model, -branch.rate_a <= flow <= branch.rate_a, base_name = "branch_flow_limit[$(branch.id)]"
            )
        end

        if include_branch_angles
            angle_limits[branch.id] = @constraint(
                model,
                branch.angmin <= theta[branch.from_bus] - theta[branch.to_bus] <= branch.angmax,
                base_name = "branch_angle_limit[$(branch.id)]"
            )
        end
    end

    return flow_limits, angle_limits
end
