using PowerModels

case_path = "data/raw/pglib-opf/pglib_opf_case5_pjm.m"

network_data = PowerModels.parse_file(case_path)

println("Keys in network data:")
println(keys(network_data))

println("Base MVA: ", network_data["baseMVA"])
println("Number of buses: ", length(network_data["bus"]))
println("Number of branches: ", length(network_data["branch"]))
println("Number of generators: ", length(network_data["gen"]))
println("Number of loads: ", length(network_data["load"]))

println("\nBUSES")
for bus_id in sort(collect(keys(network_data["bus"])); by = x -> parse(Int, x))
    bus = network_data["bus"][bus_id]
    println(
        "Bus ", bus_id,
        " | type = ", bus["bus_type"],
        " | vm = ", bus["vm"],
        " | va = ", bus["va"],
        " | limits = [", bus["vmin"], ", ", bus["vmax"], "]",
        " | base_kv = ", bus["base_kv"]
    )
end
println("\nBRANCHES")
for branch_id in sort(collect(keys(network_data["branch"])); by = x -> parse(Int, x))
    branch = network_data["branch"][branch_id]
    println(
        "Branch ", branch_id,
        " | ", branch["f_bus"], " -> ", branch["t_bus"],
        " | r = ", branch["br_r"],
        " | x = ", branch["br_x"],
        " | rate_a = ", branch["rate_a"],
        " | status = ", branch["br_status"]
    )
end
println("\nGENERATORS")
for gen_id in sort(collect(keys(network_data["gen"])); by = x -> parse(Int, x))
    gen = network_data["gen"][gen_id]
    println(
        "Generator ", gen_id,
        " | ", gen["gen_bus"],
        " | p_max = ", gen["pmax"],
        " | p_min = ", gen["pmin"],
        " | cost = ", gen["cost"]
    )
end
println("\nLOADS")
for load_id in sort(collect(keys(network_data["load"])); by = x -> parse(Int, x))
    load = network_data["load"][load_id]
    println(
        "Load ", load_id,
        " | ", load["load_bus"],
        " | pd = ", load["pd"],
        " | qd = ", load["qd"]
    )
end 