using LinearAlgebra

@isdefined(PowerCase) || include(joinpath(@__DIR__, "..", "data", "case_structs.jl"))

# Return the DC branch-flow coefficient used with angle differences.
function dc_branch_flow_coefficient(branch::Branch)
    # Imaginary part of the series admittance. This is x / (r^2 + x^2),
    # not the simpler lossless 1 / x approximation.
    denom = branch.r^2 + branch.x^2

    if denom < 1e-18
        error("Branch $(branch.id) has near-zero impedance. DC model cannot use r^2 + x^2 near zero.")
    end

    return branch.x / denom
end

# Build the DC bus matrix from active branch connections.
function build_bbus(case::PowerCase)
    # Build the DC network Laplacian from active branches.
    nb = length(case.buses)
    B = zeros(nb, nb)

    for branch in case.branches
        if branch.status == 0
            continue
        end

        if abs(branch.x) < 1e-9
            error("Branch $(branch.id) has near-zero reactance. DC PF cannot use x ≈ 0.")
        end

        i = case.bus_id_to_index[branch.from_bus]
        j = case.bus_id_to_index[branch.to_bus]

        b = dc_branch_flow_coefficient(branch)

        B[i, i] += b
        B[j, j] += b
        B[i, j] -= b
        B[j, i] -= b
    end

    return B
end

# Build the fixed active-power injection vector for DC PF.
function build_net_active_injection(case::PowerCase)
    # Positive sign means net injection into the network: generation adds,
    # loads and shunt conductance subtract.
    nb = length(case.buses)
    P = zeros(nb)

    for gen in case.generators
        if gen.status == 0
            continue
        end

        i = case.bus_id_to_index[gen.bus]
        P[i] += gen.pg0
    end

    for load in case.loads
        if load.status == 0
            continue
        end

        i = case.bus_id_to_index[load.bus]
        P[i] -= load.pd
    end

    for shunt in case.shunts
        if shunt.status == 0
            continue
        end

        i = case.bus_id_to_index[shunt.bus]
        P[i] -= shunt.gs
    end

    return P
end

# Return the array index of the slack/reference bus.
function get_slack_index(case::PowerCase)
    return case.bus_id_to_index[case.slack_bus_id]
end
