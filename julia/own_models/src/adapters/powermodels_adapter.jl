@isdefined(PowerCase) || include(joinpath(@__DIR__, "..", "data", "case_structs.jl"))

@isdefined(PowerCase) || include(joinpath(@__DIR__, "..", "data", "case_structs.jl"))

# Sort PowerModels dictionary keys numerically rather than lexicographically.
function sorted_keys_by_int(d::Dict)
    return sort(collect(keys(d)); by = x -> parse(Int, x))
end

# Find the reference/slack bus declared in PowerModels data.
function get_slack_bus_id_from_powermodels(network_data)
    for bus_id in keys(network_data["bus"])
        bus = network_data["bus"][bus_id]
        if bus["bus_type"] == 3
            return string(bus_id)
        end
    end

    error("No slack/reference bus found. Expected one bus with bus_type == 3.")
end

# Normalize a PowerModels generator cost vector into c2, c1, c0.
#
# IMPORTANT:
# PowerModels.parse_file returns data in PowerModels' internal convention:
# - active/reactive powers are in p.u.
# - branch ratings are in p.u.
# - angle quantities are in radians
# - generator cost coefficients in gen["cost"] are already scaled consistently
#   with p.u. active generation Pg.
#
# Therefore, if a PowerCase is built from PowerModels data, the JuMP objective
# should evaluate:
#
#     c2 * Pg[g]^2 + c1 * Pg[g] + c0
#
# and NOT:
#
#     c2 * (Pg[g] * baseMVA)^2 + c1 * (Pg[g] * baseMVA) + c0
#
function extract_cost_coefficients(gen_data)
    if !haskey(gen_data, "cost")
        return 0.0, 0.0, 0.0
    end

    cost = gen_data["cost"]

    if length(cost) == 3
        return Float64(cost[1]), Float64(cost[2]), Float64(cost[3])
    elseif length(cost) == 2
        return 0.0, Float64(cost[1]), Float64(cost[2])
    elseif length(cost) == 1
        return 0.0, Float64(cost[1]), 0.0
    else
        return 0.0, 0.0, 0.0
    end
end

# Convert parsed PowerModels data into the repository's PowerCase structs.
#
# PowerModels-derived PowerCase convention:
# - active/reactive powers are in p.u.
# - branch ratings are in p.u.
# - impedances/admittances are in p.u.
# - voltage angles and angle limits are in radians
# - generator cost coefficients are scaled for p.u. active generation Pg
#
# Therefore, the JuMP objective for this adapter should use Pg directly,
# not Pg * baseMVA.
function powermodels_to_powercase(network_data)
    name = get(network_data, "name", "unknown")
    baseMVA = Float64(network_data["baseMVA"])

    bus_keys = sorted_keys_by_int(network_data["bus"])
    branch_keys = sorted_keys_by_int(network_data["branch"])
    gen_keys = sorted_keys_by_int(network_data["gen"])
    load_keys = sorted_keys_by_int(network_data["load"])

    # -----------------------------
    # Buses
    # -----------------------------
    buses = Bus[]
    bus_id_to_index = Dict{String, Int}()

    for (idx, bus_id) in enumerate(bus_keys)
        bus = network_data["bus"][bus_id]

        push!(buses, Bus(
            string(bus_id),
            idx,
            Int(bus["bus_type"]),
            Float64(bus["vm"]),
            Float64(bus["va"]),
            Float64(bus["vmin"]),
            Float64(bus["vmax"])
        ))

        bus_id_to_index[string(bus_id)] = idx
    end

    # -----------------------------
    # Branches
    # -----------------------------
    branches = Branch[]

    for (idx, branch_id) in enumerate(branch_keys)
        branch = network_data["branch"][branch_id]

        b_fr = Float64(get(branch, "b_fr", 0.0))
        b_to = Float64(get(branch, "b_to", 0.0))
        g_fr = Float64(get(branch, "g_fr", 0.0))
        g_to = Float64(get(branch, "g_to", 0.0))

        tap = Float64(get(branch, "tap", 1.0))
        if tap == 0.0
            tap = 1.0
        end

        # PowerModels stores angle limits in radians.
        # Do not apply deg2rad here.
        angmin = Float64(get(branch, "angmin", -pi / 2))
        angmax = Float64(get(branch, "angmax", pi / 2))

        push!(branches, Branch(
            string(branch_id),
            idx,
            string(branch["f_bus"]),
            string(branch["t_bus"]),
            Float64(branch["br_r"]),
            Float64(branch["br_x"]),
            b_fr + b_to,
            b_fr,
            b_to,
            g_fr,
            g_to,
            Float64(branch["rate_a"]),
            tap,
            Float64(get(branch, "shift", 0.0)),
            angmin,
            angmax,
            Int(branch["br_status"])
        ))
    end

    # -----------------------------
    # Generators
    # -----------------------------
    generators = Generator[]

    for (idx, gen_id) in enumerate(gen_keys)
        gen = network_data["gen"][gen_id]
        c2, c1, c0 = extract_cost_coefficients(gen)

        push!(generators, Generator(
            string(gen_id),
            idx,
            string(gen["gen_bus"]),
            Float64(gen["pg"]),
            Float64(gen["qg"]),
            Float64(gen["pmin"]),
            Float64(gen["pmax"]),
            Float64(gen["qmin"]),
            Float64(gen["qmax"]),
            Int(gen["gen_status"]),
            c2,
            c1,
            c0
        ))
    end

    # -----------------------------
    # Loads
    # -----------------------------
    # In PowerModels data, bus Pd/Qd are represented as separate load components.
    # Therefore, loads must be read from network_data["load"], not from bus entries.
    loads = Load[]

    for (idx, load_id) in enumerate(load_keys)
        load = network_data["load"][load_id]

        push!(loads, Load(
            string(load_id),
            idx,
            string(load["load_bus"]),
            Float64(load["pd"]),
            Float64(load["qd"]),
            Int(get(load, "status", 1))
        ))
    end

    # -----------------------------
    # Shunts
    # -----------------------------
    shunts = Shunt[]

    if haskey(network_data, "shunt")
        shunt_keys = sorted_keys_by_int(network_data["shunt"])

        for (idx, shunt_id) in enumerate(shunt_keys)
            shunt = network_data["shunt"][shunt_id]

            push!(shunts, Shunt(
                string(shunt_id),
                idx,
                string(shunt["shunt_bus"]),
                Float64(get(shunt, "gs", 0.0)),
                Float64(get(shunt, "bs", 0.0)),
                Int(get(shunt, "status", 1))
            ))
        end
    end

    slack_bus_id = get_slack_bus_id_from_powermodels(network_data)

    return PowerCase(
        name,
        baseMVA,
        buses,
        branches,
        generators,
        loads,
        shunts,
        slack_bus_id,
        bus_id_to_index
    )
end