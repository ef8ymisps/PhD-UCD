using PowerModels
using CSV
using DataFrames

function project_root()
    return normpath(joinpath(@__DIR__, "..", "..", "..", ".."))
end

function arg_value(name::String, default::String)
    prefix = name * "="
    for arg in ARGS
        if startswith(arg, prefix)
            return arg[length(prefix)+1:end]
        end
    end
    return default
end

function arg_bool(name::String, default::Bool=false)
    for arg in ARGS
        if arg == name
            return true
        end
    end
    return default
end

function arg_int(name::String, default::Int)
    return parse(Int, arg_value(name, string(default)))
end

function arg_float(name::String, default::Float64)
    return parse(Float64, arg_value(name, string(default)))
end

function wildcard_regex(pattern::String)
    escaped = replace(pattern, "." => "\\.", "*" => ".*", "?" => ".")
    return Regex("^" * escaped * "\$")
end

function matching_cases(case_dir::String, case_glob::String)
    pattern = wildcard_regex(case_glob)
    return sort([
        joinpath(case_dir, name)
        for name in readdir(case_dir)
        if isfile(joinpath(case_dir, name)) && occursin(pattern, name)
    ])
end

function parse_voltage_replacement_notes(case_path::String)
    notes = Dict{Int, NamedTuple{(:v_from, :theta_from_deg, :v_to, :theta_to_deg), NTuple{4, Float64}}}()
    in_voltage_section = false
    pattern = r"Bus\s+(\d+)\s*:\s*V=([-+.\deE]+),\s*theta=([-+.\deE]+)\s*->\s*V=([-+.\deE]+),\s*theta=([-+.\deE]+)"

    for line in eachline(case_path)
        section_match = match(r"^%\s+INFO\s+:\s+===\s+(.+?)\s+===", line)
        if section_match !== nothing
            in_voltage_section = section_match.captures[1] == "Voltage Setpoint Replacement Notes"
            continue
        end

        if in_voltage_section
            note_match = match(pattern, line)
            if note_match !== nothing
                bus = parse(Int, note_match.captures[1])
                notes[bus] = (
                    v_from = parse(Float64, note_match.captures[2]),
                    theta_from_deg = parse(Float64, note_match.captures[3]),
                    v_to = parse(Float64, note_match.captures[4]),
                    theta_to_deg = parse(Float64, note_match.captures[5]),
                )
            end
        end
    end

    return notes
end

function bus_number(bus_key::String, bus::Dict)
    for field in ("bus_i", "index")
        value = get(bus, field, nothing)
        if value !== nothing
            return Int(round(value))
        end
    end
    return parse(Int, bus_key)
end

function build_bus_key_by_number(data::Dict)
    mapping = Dict{Int, String}()
    for (bus_key, bus) in data["bus"]
        mapping[bus_number(bus_key, bus)] = bus_key
    end
    return mapping
end

function note_values(note, direction::String)
    if direction == "from"
        return note.v_from, deg2rad(note.theta_from_deg)
    elseif direction == "to"
        return note.v_to, deg2rad(note.theta_to_deg)
    else
        error("direction must be 'from' or 'to', got $(direction)")
    end
end

function apply_voltage_note_values!(data::Dict, notes::Dict; direction::String="from")
    bus_key_by_number = build_bus_key_by_number(data)
    applied = DataFrame(
        matpower_bus = Int[],
        bus_key = String[],
        old_vm = Float64[],
        old_va = Float64[],
        new_vm = Float64[],
        new_va = Float64[],
        direction = String[],
    )
    unmatched = Int[]

    for bus_number in sort(collect(keys(notes)))
        if !haskey(bus_key_by_number, bus_number)
            push!(unmatched, bus_number)
            continue
        end

        bus_key = bus_key_by_number[bus_number]
        bus = data["bus"][bus_key]
        new_vm, new_va = note_values(notes[bus_number], direction)
        old_vm = Float64(get(bus, "vm", NaN))
        old_va = Float64(get(bus, "va", NaN))

        bus["vm"] = new_vm
        bus["va"] = new_va

        push!(applied, (bus_number, bus_key, old_vm, old_va, new_vm, new_va, direction))
    end

    return applied, unmatched
end

function call_set_ac_pf_start_values!(data::Dict)
    bang_name = Symbol("set_ac_pf_start_values!")
    plain_name = Symbol("set_ac_pf_start_values")

    if isdefined(PowerModels, bang_name)
        return getfield(PowerModels, bang_name)(data)
    elseif isdefined(PowerModels, plain_name)
        result = getfield(PowerModels, plain_name)(data)
        return result === nothing ? data : result
    else
        error("PowerModels does not expose set_ac_pf_start_values! or set_ac_pf_start_values")
    end
end

function run_nr_ac_pf(data::Dict; max_iter::Int=100, tolerance::Float64=1e-8)
    if !isdefined(PowerModels, :compute_ac_pf)
        error("PowerModels.compute_ac_pf is not available in this Julia environment")
    end

    compute_ac_pf = getfield(PowerModels, :compute_ac_pf)
    attempts = (
        (max_iter = max_iter, tolerance = tolerance),
        (max_iter = max_iter, tol = tolerance),
        (max_iteration = max_iter, tolerance = tolerance),
        NamedTuple(),
    )

    last_error = nothing
    for kwargs in attempts
        try
            return compute_ac_pf(data; kwargs...)
        catch err
            last_error = err
        end
    end

    error("PowerModels.compute_ac_pf failed for all known keyword signatures. Last error: $(last_error)")
end

function solve_with_voltage_warmstart(case_path::String; direction::String="from", strict::Bool=true, max_iter::Int=100, tolerance::Float64=1e-8)
    data = PowerModels.parse_file(case_path)
    notes = parse_voltage_replacement_notes(case_path)
    applied, unmatched = apply_voltage_note_values!(data, notes; direction=direction)

    if strict && !isempty(unmatched)
        error("Unmatched voltage replacement notes for MATPOWER buses: $(join(unmatched, ", "))")
    end

    call_set_ac_pf_start_values!(data)
    result = run_nr_ac_pf(data; max_iter=max_iter, tolerance=tolerance)
    return data, notes, applied, unmatched, result
end

function result_row(case_path::String, notes, applied, unmatched, result; direction::String)
    return (
        case_file = basename(case_path),
        direction = direction,
        n_voltage_notes = length(notes),
        n_applied_voltage_notes = nrow(applied),
        n_unmatched_voltage_notes = length(unmatched),
        termination_status = string(get(result, "termination_status", "missing")),
        solver = "PowerModels.compute_ac_pf",
        solve_time = get(result, "solve_time", missing),
    )
end

root = project_root()
case_dir = normpath(joinpath(root, arg_value("--case-dir", "data/raw/pglib/matpower_m")))
case_glob = arg_value("--case-glob", "pglib_opf_case300_ieee.m")
direction = arg_value("--direction", "from")
output_path = normpath(joinpath(root, arg_value("--output", "results/powermodels_voltage_note_warmstart_pf.csv")))
applied_output_path = normpath(joinpath(root, arg_value("--applied-output", "results/powermodels_voltage_note_warmstart_applied.csv")))
strict = !arg_bool("--no-strict", false)
max_iter = arg_int("--max-iter", 100)
tolerance = arg_float("--tolerance", 1e-8)

case_paths = matching_cases(case_dir, case_glob)
if isempty(case_paths)
    error("No case files matched $(joinpath(case_dir, case_glob))")
end

summary = DataFrame()
applied_all = DataFrame()

for case_path in case_paths
    println("\n==============================")
    println("AC PF with voltage-note warm start")
    println("Case: ", case_path)
    println("Direction: ", direction)
    println("==============================")

    data, notes, applied, unmatched, result = solve_with_voltage_warmstart(
        case_path;
        direction=direction,
        strict=strict,
        max_iter=max_iter,
        tolerance=tolerance,
    )

    println("Parsed voltage notes: ", length(notes))
    println("Applied voltage notes: ", nrow(applied))
    println("Unmatched voltage notes: ", length(unmatched))
    println("NR AC PF status: ", get(result, "termination_status", "missing"))
    println("NR AC PF solve time: ", get(result, "solve_time", missing))

    append!(summary, DataFrame([result_row(case_path, notes, applied, unmatched, result; direction=direction)]), cols=:union)
    if nrow(applied) > 0
        insertcols!(applied, 1, :case_file => fill(basename(case_path), nrow(applied)))
        append!(applied_all, applied, cols=:union)
    end
end

mkpath(dirname(output_path))
CSV.write(output_path, summary)
CSV.write(applied_output_path, applied_all)
println("\nWrote summary: ", output_path)
println("Wrote applied voltage table: ", applied_output_path)
