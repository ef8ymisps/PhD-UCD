using LinearAlgebra

@isdefined(build_bbus) || include(joinpath(@__DIR__, "..", "network", "dc_matrices.jl"))

# Result container for DC power-flow angles, branch flows, and slack injection.
struct DCPowerFlowResult
    theta::Vector{Float64}
    branch_flows::Dict{String, Float64}
    slack_injection::Float64
end

# Solve DC power flow by removing the slack row/column and solving angles.
function solve_dc_power_flow(case::PowerCase)
    B = build_bbus(case)
    P = build_net_active_injection(case)

    slack_idx = get_slack_index(case)

    # Remove the reference bus equation so the linear angle system is
    # nonsingular; the slack angle is inserted as zero after the solve.
    non_slack = [i for i in 1:length(case.buses) if i != slack_idx]

    Bred = B[non_slack, non_slack]
    Pred = P[non_slack]

    theta = zeros(length(case.buses))
    theta[non_slack] = Bred \ Pred
    theta[slack_idx] = 0.0

    branch_flows = Dict{String, Float64}()

    for branch in case.branches
        if branch.status == 0
            continue
        end

        i = case.bus_id_to_index[branch.from_bus]
        j = case.bus_id_to_index[branch.to_bus]

        pf = dc_branch_flow_coefficient(branch) * (theta[i] - theta[j])

        branch_flows[branch.id] = pf
    end

    # Slack injection needed to balance the solved network.
    slack_injection = (B[slack_idx, :] ⋅ theta)

    return DCPowerFlowResult(theta, branch_flows, slack_injection)
end
