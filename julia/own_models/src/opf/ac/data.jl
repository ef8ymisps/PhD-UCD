# Owns the AC OPF data view built from a PowerCase.
#
# AC equations use Ybus in rectangular coefficients G and B, but the decision
# variables are polar voltage magnitude and angle.

using LinearAlgebra
using SparseArrays

struct ACOPFData
    bus_ids::Vector{String}
    branch_ids::Vector{String}
    gen_ids::Vector{String}
    Ybus::SparseMatrixCSC{ComplexF64, Int}
    pd_by_bus::Dict{String, Float64}
    qd_by_bus::Dict{String, Float64}
    gs_by_bus::Dict{String, Float64}
    bs_by_bus::Dict{String, Float64}
    gens_by_bus::Dict{String, Vector{String}}
    active_branches::Vector{Branch}
    active_generators::Vector{Generator}
end

"""Return Ybus, demand maps, and active equipment lists for AC OPF."""
function build_ac_opf_data(case::PowerCase)
    bus_ids = [bus.id for bus in case.buses]
    active_branches = [branch for branch in case.branches if branch.status == 1]
    active_generators = [gen for gen in case.generators if gen.status == 1]
    gen_ids = [gen.id for gen in case.generators]
    branch_ids = [branch.id for branch in active_branches]

    Ybus = build_ybus(case)
    pd_by_bus = Dict(bus.id => 0.0 for bus in case.buses)
    qd_by_bus = Dict(bus.id => 0.0 for bus in case.buses)

    for load in case.loads
        if load.status == 1
            pd_by_bus[load.bus] += load.pd
            qd_by_bus[load.bus] += load.qd
        end
    end

    # Bus-shunt-only aggregates (branch charging is NOT included here -- it's
    # already folded into each branch's own Yff/Ytt in
    # ac_branch_admittance_terms, so it belongs to the arc-based
    # formulation's per-branch equations, not the bus balance). Needed by
    # the nodal power balance (add_ac_power_balance! in model.jl), which
    # sums directed arc flows rather than a full nodal Ybus row.
    gs_by_bus = Dict(bus.id => 0.0 for bus in case.buses)
    bs_by_bus = Dict(bus.id => 0.0 for bus in case.buses)
    for shunt in case.shunts
        if shunt.status == 1
            gs_by_bus[shunt.bus] += shunt.gs
            bs_by_bus[shunt.bus] += shunt.bs
        end
    end

    gens_by_bus = Dict(bus.id => String[] for bus in case.buses)
    for gen in active_generators
        push!(gens_by_bus[gen.bus], gen.id)
    end

    return ACOPFData(
        bus_ids,
        branch_ids,
        gen_ids,
        Ybus,
        pd_by_bus,
        qd_by_bus,
        gs_by_bus,
        bs_by_bus,
        gens_by_bus,
        active_branches,
        active_generators
    )
end
