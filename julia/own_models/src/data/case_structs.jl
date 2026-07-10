# Bus data used for indexing, voltage starts/limits, and bus type.
struct Bus
    id::String
    index::Int
    bus_type::Int
    vm0::Float64
    va0::Float64
    vmin::Float64
    vmax::Float64
end

# Branch or transformer data used by DC and AC network models.
struct Branch
    id::String
    index::Int
    from_bus::String
    to_bus::String
    r::Float64
    x::Float64
    b::Float64
    b_fr::Float64
    b_to::Float64
    g_fr::Float64
    g_to::Float64
    rate_a::Float64
    tap::Float64
    shift::Float64
    angmin::Float64
    angmax::Float64
    status::Int
end

# Generator data with dispatch starts, limits, status, and cost coefficients.
struct Generator
    id::String
    index::Int
    bus::String
    pg0::Float64
    qg0::Float64
    pmin::Float64
    pmax::Float64
    qmin::Float64
    qmax::Float64
    status::Int
    cost_c2::Float64
    cost_c1::Float64
    cost_c0::Float64
end

# Fixed active/reactive load attached to a bus.
struct Load
    id::String
    index::Int
    bus::String
    pd::Float64
    qd::Float64
    status::Int
end

# Fixed shunt conductance/susceptance attached to a bus.
struct Shunt
    id::String
    index::Int
    bus::String
    gs::Float64
    bs::Float64
    status::Int
end

# Complete normalized power-system case consumed by own-model solvers.
struct PowerCase
    name::String
    baseMVA::Float64
    buses::Vector{Bus}
    branches::Vector{Branch}
    generators::Vector{Generator}
    loads::Vector{Load}
    shunts::Vector{Shunt}
    slack_bus_id::String
    bus_id_to_index::Dict{String, Int}
end
