using PowerModels
using Ipopt
using JuMP
using JSON

const DEFAULT_CASES = [
    "pglib_opf_case5_pjm.m",
    "pglib_opf_case14_ieee.m",
    "pglib_opf_case30_ieee.m",
]

function project_root()
    return normpath(joinpath(@__DIR__, "..", "..", "..", ".."))
end

function sorted_ids(d)
    return sort(collect(keys(d)); by = x -> parse(Int, string(x)))
end

function load_scenarios(path)
    open(path, "r") do io
        return JSON.parse(io)
    end
end

function apply_scenario(data, scenario)
    modified = deepcopy(data)
    scenario_type = get(scenario, "type", "base")

    if scenario_type == "base"
        return modified
    elseif scenario_type == "relaxed"
        branch_rate_multiplier = Float64(get(scenario, "branch_rate_multiplier", 10.0))
        generator_limit_multiplier = Float64(get(scenario, "generator_limit_multiplier", 10.0))
        voltage_lower = Float64(get(scenario, "voltage_lower", 0.5))
        voltage_upper = Float64(get(scenario, "voltage_upper", 1.5))

        for branch in values(modified["branch"])
            if branch["rate_a"] > 0.0
                branch["rate_a"] *= branch_rate_multiplier
            end
        end

        for gen in values(modified["gen"])
            if get(gen, "gen_status", 1) != 0
                gen["pmax"] *= generator_limit_multiplier
                gen["qmin"] *= generator_limit_multiplier
                gen["qmax"] *= generator_limit_multiplier
            end
        end

        for bus in values(modified["bus"])
            bus["vmin"] = voltage_lower
            bus["vmax"] = voltage_upper
        end
    elseif scenario_type == "branch_congested"
        branch_id = string(get(scenario, "branch_id", first(sorted_ids(modified["branch"]))))
        rate_multiplier = Float64(get(scenario, "rate_multiplier", 0.8))
        if !haskey(modified["branch"], branch_id)
            error("Scenario branch_id $(branch_id) does not exist.")
        end
        modified["branch"][branch_id]["rate_a"] *= rate_multiplier
    elseif scenario_type == "generator_limited"
        gen_id = string(get(scenario, "gen_id", first(sorted_ids(modified["gen"]))))
        pmax_multiplier = Float64(get(scenario, "pmax_multiplier", 0.5))
        if !haskey(modified["gen"], gen_id)
            error("Scenario gen_id $(gen_id) does not exist.")
        end
        gen = modified["gen"][gen_id]
        gen["pmax"] = max(gen["pmin"], gen["pmax"] * pmax_multiplier)
    elseif scenario_type == "load_scaled"
        load_multiplier = Float64(get(scenario, "load_multiplier", 1.2))
        scale_reactive = Bool(get(scenario, "scale_reactive", true))
        for load in values(modified["load"])
            if get(load, "status", 1) == 0
                continue
            end
            load["pd"] *= load_multiplier
            if scale_reactive
                load["qd"] *= load_multiplier
            end
        end
    else
        error("Unsupported scenario type: $(scenario_type)")
    end

    return modified
end

function solved_status(status)
    status_text = uppercase(string(status))
    return status_text in ["OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED"]
end

"""Best-effort Ipopt barrier iteration count; NaN if unavailable. Local copy
of the same helper used in julia/own_models/src/opf/ac/results.jl -- this
script is a standalone tool outside the OwnPowerModels module, so it doesn't
pull in that dependency just for this."""
function safe_barrier_iterations(model::JuMP.Model)
    try
        return Float64(JuMP.barrier_iterations(model))
    catch
        return NaN
    end
end

function safe_get(d, keys...; default = nothing)
    current = d
    for key in keys
        if current isa AbstractDict && haskey(current, key)
            current = current[key]
        else
            return default
        end
    end
    return current
end

function reference_payload(case_name, scenario_name, data, result, solve_iterations::Float64, solve_time_sec::Float64)
    solution = get(result, "solution", Dict{String, Any}())
    status = string(get(result, "termination_status", "UNKNOWN"))
    solved = solved_status(status)

    generators = Dict{String, Any}()
    for gen_id in sorted_ids(data["gen"])
        gen = data["gen"][gen_id]
        generators[string(gen_id)] = Dict(
            "pg" => safe_get(solution, "gen", gen_id, "pg"; default = nothing),
            "qg" => safe_get(solution, "gen", gen_id, "qg"; default = nothing),
            "pmin" => gen["pmin"],
            "pmax" => gen["pmax"],
            "qmin" => gen["qmin"],
            "qmax" => gen["qmax"],
            "cost" => get(gen, "cost", []),
        )
    end

    buses = Dict{String, Any}()
    for bus_id in sorted_ids(data["bus"])
        bus = data["bus"][bus_id]
        buses[string(bus_id)] = Dict(
            "va" => safe_get(solution, "bus", bus_id, "va"; default = nothing),
            "vm" => safe_get(solution, "bus", bus_id, "vm"; default = nothing),
            "vmin" => bus["vmin"],
            "vmax" => bus["vmax"],
        )
    end

    branches = Dict{String, Any}()
    for branch_id in sorted_ids(data["branch"])
        branch = data["branch"][branch_id]
        branches[string(branch_id)] = Dict(
            "pf" => safe_get(solution, "branch", branch_id, "pf"; default = nothing),
            "qf" => safe_get(solution, "branch", branch_id, "qf"; default = nothing),
            "pt" => safe_get(solution, "branch", branch_id, "pt"; default = nothing),
            "qt" => safe_get(solution, "branch", branch_id, "qt"; default = nothing),
            "rate_a" => branch["rate_a"],
            "angmin" => get(branch, "angmin", -pi / 2),
            "angmax" => get(branch, "angmax", pi / 2),
        )
    end

    return Dict(
        "case_name" => case_name,
        "scenario" => scenario_name,
        "termination_status" => status,
        "solved" => solved,
        "objective" => get(result, "objective", nothing),
        "solve_iterations" => isfinite(solve_iterations) ? solve_iterations : nothing,
        "solve_time_sec" => isfinite(solve_time_sec) ? solve_time_sec : nothing,
        "generators" => generators,
        "buses" => buses,
        "branches" => branches,
    )
end

function json_ready(value)
    if value isa AbstractDict
        return Dict(string(k) => json_ready(v) for (k, v) in value)
    elseif value isa AbstractVector
        return [json_ready(v) for v in value]
    elseif value isa Tuple
        return [json_ready(v) for v in value]
    elseif value isa Missing
        return nothing
    elseif value isa Symbol
        return string(value)
    elseif value isa AbstractFloat
        return isfinite(value) ? value : nothing
    elseif value isa Number || value isa AbstractString || value isa Bool || value === nothing
        return value
    else
        return string(value)
    end
end

function write_json(path, value)
    mkpath(dirname(path))
    open(path, "w") do io
        JSON.print(io, json_ready(value), 4)
    end
end

function export_reference(case_name, scenario, case_dir, output_dir)
    scenario_name = string(scenario["name"])
    case_path = joinpath(case_dir, case_name)
    data = PowerModels.parse_file(case_path)
    modified = apply_scenario(data, scenario)

    optimizer = optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0)
    # instantiate_model + optimize_model! (rather than the solve_ac_opf
    # convenience wrapper) so the underlying JuMP model stays reachable for
    # barrier_iterations -- same pattern used in
    # julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl to
    # backfill PowerModels' own iteration count on the Julia AC OPF sweep.
    solve_start = time()
    pm = PowerModels.instantiate_model(modified, ACPPowerModel, PowerModels.build_opf)
    result = PowerModels.optimize_model!(pm, optimizer=optimizer)
    solve_time_sec = time() - solve_start
    solve_iterations = safe_barrier_iterations(pm.model)

    stem = replace(case_name, ".m" => "")
    case_json_path = joinpath(output_dir, stem * "_" * scenario_name * "_case.json")
    reference_json_path = joinpath(output_dir, stem * "_" * scenario_name * "_reference.json")

    write_json(case_json_path, modified)
    write_json(
        reference_json_path,
        reference_payload(case_name, scenario_name, modified, result, solve_iterations, solve_time_sec),
    )

    println("Exported ", stem, " / ", scenario_name, " -> ", reference_json_path)
end

function main()
    root = project_root()
    case_dir = length(ARGS) >= 1 ? ARGS[1] : joinpath(root, "data", "raw", "pglib-opf")
    scenario_path = length(ARGS) >= 2 ? ARGS[2] : joinpath(root, "config", "validation", "dc_opf_scenarios.json")
    output_dir = length(ARGS) >= 3 ? ARGS[3] : joinpath(root, "results", "reference", "powermodels", "ac_opf")

    scenarios = load_scenarios(scenario_path)
    for case_name in DEFAULT_CASES
        for scenario in scenarios
            export_reference(case_name, scenario, case_dir, output_dir)
        end
    end
end

main()
