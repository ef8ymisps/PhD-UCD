using PowerModels
using JSON

function project_root()
    return normpath(joinpath(@__DIR__, ".."))
end

function data_dir(parts...)
    return joinpath(project_root(), "data", parts...)
end

function processed_dir(parts...)
    return data_dir("processed", parts...)
end

function data_results_dir(parts...)
    return data_dir("results", parts...)
end

function results_dir(parts...)
    return joinpath(project_root(), "results", parts...)
end

function summary_dir(parts...)
    return results_dir("summaries", parts...)
end

function figure_dir(parts...)
    return results_dir("figures", parts...)
end

function raw_output_dir(parts...)
    return results_dir("raw_outputs", parts...)
end

function ensure_project_data_layout()
    dirs = [
        processed_dir("case5"),
        processed_dir("case14"),
        processed_dir("case24_ieee_rts"),
        data_results_dir(),
        summary_dir(),
        figure_dir("voltage_profiles"),
        figure_dir("branch_flows"),
        figure_dir("generator_dispatch"),
        raw_output_dir("case5"),
        raw_output_dir("case14"),
        raw_output_dir("case24"),
    ]

    for dir in dirs
        mkpath(dir)
    end

    return dirs
end

function load_scale_tag(load_scale)
    return lpad(string(round(Int, 100 * load_scale)), 3, '0')
end

function processed_case_path(case_label, file_stem)
    return processed_dir(case_label, file_stem * ".json")
end

function save_network_json(network_data, output_path)
    output_dir = dirname(output_path)
    if !isempty(output_dir)
        mkpath(output_dir)
    end

    open(output_path, "w") do io
        JSON.print(io, network_data, 4)
    end

    return output_path
end

function save_processed_case(network_data, case_label, file_stem)
    return save_network_json(network_data, processed_case_path(case_label, file_stem))
end

function save_clean_processed_case(network_data, case_label)
    return save_processed_case(network_data, case_label, case_label * "_base_clean")
end

function save_load_scaled_processed_case(network_data, case_label, load_scale; scale_reactive = true)
    scaled_network_data = deepcopy(network_data)

    for load in values(scaled_network_data["load"])
        if get(load, "status", 1) == 0
            continue
        end

        load["pd"] *= load_scale
        if scale_reactive
            load["qd"] *= load_scale
        end
    end

    file_stem = case_label * "_load_scale_" * load_scale_tag(load_scale)
    return save_processed_case(scaled_network_data, case_label, file_stem)
end

function save_branch_outage_processed_case(network_data, case_label, branch_id)
    outage_network_data = deepcopy(network_data)
    branch_key = string(branch_id)

    if !haskey(outage_network_data["branch"], branch_key)
        error("Branch $(branch_key) does not exist.")
    end

    outage_network_data["branch"][branch_key]["br_status"] = 0
    return save_processed_case(outage_network_data, case_label, case_label * "_branch_" * branch_key * "_out")
end

function save_generator_outage_processed_case(network_data, case_label, gen_id)
    outage_network_data = deepcopy(network_data)
    gen_key = string(gen_id)

    if !haskey(outage_network_data["gen"], gen_key)
        error("Generator $(gen_key) does not exist.")
    end

    outage_network_data["gen"][gen_key]["gen_status"] = 0
    outage_network_data["gen"][gen_key]["pg"] = 0.0
    outage_network_data["gen"][gen_key]["qg"] = 0.0

    return save_processed_case(outage_network_data, case_label, case_label * "_gen_" * gen_key * "_out")
end

function load_case(case_path)
    return PowerModels.parse_file(case_path)
end

function print_case_summary(network_data)
    println("\n==============================")
    println("CASE SUMMARY")
    println("==============================")

    println("Case name: ", get(network_data, "name", "unknown"))
    println("Base MVA: ", network_data["baseMVA"])
    println("Number of buses: ", length(network_data["bus"]))
    println("Number of branches: ", length(network_data["branch"]))
    println("Number of generators: ", length(network_data["gen"]))
    println("Number of loads: ", length(network_data["load"]))
end

function print_buses(network_data)
    println("\n==============================")
    println("BUSES")
    println("==============================")

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
end

function print_branches(network_data)
    println("\n==============================")
    println("BRANCHES")
    println("==============================")

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
end

function print_generators(network_data)
    println("\n==============================")
    println("GENERATORS")
    println("==============================")

    for gen_id in sort(collect(keys(network_data["gen"])); by = x -> parse(Int, x))
        gen = network_data["gen"][gen_id]

        println(
            "Gen ", gen_id,
            " | bus = ", gen["gen_bus"],
            " | pg = ", gen["pg"],
            " | qg = ", gen["qg"],
            " | pmin = ", gen["pmin"],
            " | pmax = ", gen["pmax"],
            " | qmin = ", gen["qmin"],
            " | qmax = ", gen["qmax"],
            " | status = ", gen["gen_status"]
        )
    end
end

function print_loads(network_data)
    println("\n==============================")
    println("LOADS")
    println("==============================")

    for load_id in sort(collect(keys(network_data["load"])); by = x -> parse(Int, x))
        load = network_data["load"][load_id]

        println(
            "Load ", load_id,
            " | bus = ", load["load_bus"],
            " | pd = ", load["pd"],
            " | qd = ", load["qd"],
            " | status = ", load["status"]
        )
    end
end
