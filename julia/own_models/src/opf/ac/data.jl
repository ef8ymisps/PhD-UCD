# Owns the AC OPF data view built from a PowerCase.
#
# AC equations use Ybus in rectangular coefficients G and B, but the decision
# variables are polar voltage magnitude and angle.

using LinearAlgebra

struct ACOPFData
    bus_ids::Vector{String}
    branch_ids::Vector{String}
    gen_ids::Vector{String}
    Ybus::Matrix{ComplexF64}
    G::Matrix{Float64}
    B::Matrix{Float64}
    ybus_row_terms::Dict{String, Vector{Tuple{String, Float64, Float64}}}
    pd_by_bus::Dict{String, Float64}
    qd_by_bus::Dict{String, Float64}
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
    ybus_row_terms = nonzero_ybus_row_terms(Ybus, bus_ids)
    pd_by_bus = Dict(bus.id => 0.0 for bus in case.buses)
    qd_by_bus = Dict(bus.id => 0.0 for bus in case.buses)

    for load in case.loads
        if load.status == 1
            pd_by_bus[load.bus] += load.pd
            qd_by_bus[load.bus] += load.qd
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
        real.(Ybus),
        imag.(Ybus),
        ybus_row_terms,
        pd_by_bus,
        qd_by_bus,
        gens_by_bus,
        active_branches,
        active_generators
    )
end

"""Return nonzero Ybus row terms as bus => [(other_bus, G, B), ...]."""
function nonzero_ybus_row_terms(Ybus, bus_ids)
    row_terms = Dict{String, Vector{Tuple{String, Float64, Float64}}}()
    for (i, bus_id) in enumerate(bus_ids)
        row_terms[bus_id] = [
            (bus_ids[j], real(Ybus[i, j]), imag(Ybus[i, j]))
            for j in findall(!iszero, @view Ybus[i, :])
        ]
    end
    return row_terms
end
