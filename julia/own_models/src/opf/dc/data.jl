# Owns the DC OPF data view built from a PowerCase.
#
# The model convention is fully per-unit. Bbus maps bus angles to active
# network injections, and Bf maps bus angles to oriented branch active flows.

using LinearAlgebra

struct DCOPFData
    bus_ids::Vector{String}
    branch_ids::Vector{String}
    gen_ids::Vector{String}
    Bbus::Matrix{Float64}
    Bf::Matrix{Float64}
    pd_by_bus::Dict{String, Float64}
    gens_by_bus::Dict{String, Vector{String}}
    active_branches::Vector{Branch}
    active_generators::Vector{Generator}
end

"""Return online generators, active branches, and DC matrices for OPF."""
function build_dc_opf_data(case::PowerCase)
    bus_ids = [bus.id for bus in case.buses]
    active_branches = [branch for branch in case.branches if branch.status == 1]
    active_generators = [gen for gen in case.generators if gen.status == 1]
    branch_ids = [branch.id for branch in active_branches]
    gen_ids = [gen.id for gen in case.generators]

    Bbus = build_bbus(case)
    Bf = zeros(Float64, length(active_branches), length(bus_ids))

    for (row, branch) in enumerate(active_branches)
        f = case.bus_id_to_index[branch.from_bus]
        t = case.bus_id_to_index[branch.to_bus]
        b = dc_branch_flow_coefficient(branch)
        Bf[row, f] = b
        Bf[row, t] = -b
    end

    pd_by_bus = Dict(bus.id => 0.0 for bus in case.buses)
    for load in case.loads
        if load.status == 1
            pd_by_bus[load.bus] += load.pd
        end
    end
    for shunt in case.shunts
        if shunt.status == 1
            # Constant conductance consumes active power at nominal voltage.
            pd_by_bus[shunt.bus] += shunt.gs
        end
    end

    gens_by_bus = Dict(bus.id => String[] for bus in case.buses)
    for gen in active_generators
        push!(gens_by_bus[gen.bus], gen.id)
    end

    return DCOPFData(
        bus_ids,
        branch_ids,
        gen_ids,
        Bbus,
        Bf,
        pd_by_bus,
        gens_by_bus,
        active_branches,
        active_generators
    )
end
