using PowerModels
using Ipopt
using HiGHS
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

function termination_text(result)
    return string(get(result, "termination_status", "missing"))
end

function objective_value(result)
    return get(result, "objective", missing)
end

function solve_case(case_path::String)
    ac_result = solve_ac_pf(case_path, Ipopt.Optimizer)
    dc_result = solve_dc_pf(case_path, HiGHS.Optimizer)
    return ac_result, dc_result
end

function wildcard_regex(pattern::String)
    escaped = replace(pattern, "." => "\\.", "*" => ".*", "?" => ".")
    return Regex("^" * escaped * "\$")
end

function matching_cases(case_dir::String, case_glob::String)
    pattern = wildcard_regex(case_glob)
    return [
        joinpath(case_dir, name)
        for name in readdir(case_dir)
        if isfile(joinpath(case_dir, name)) && occursin(pattern, name)
    ]
end

root = project_root()
case_dir = normpath(joinpath(root, arg_value("--case-dir", "data/matpower_m")))
case_glob = arg_value("--case-glob", "*__replacement_experiment_*.m")
output_path = normpath(joinpath(root, arg_value("--output", "results/replacement_note_pf_experiments.csv")))

case_paths = sort(matching_cases(case_dir, case_glob))

if isempty(case_paths)
    error("No case files matched $(joinpath(case_dir, case_glob))")
end

rows = DataFrame(
    case_file = String[],
    ac_status = String[],
    ac_objective = Any[],
    ac_solve_time = Any[],
    dc_status = String[],
    dc_objective = Any[],
    dc_solve_time = Any[],
)

for case_path in case_paths
    println("\n==============================")
    println("Running PF experiment: ", case_path)
    println("==============================")

    ac_result, dc_result = solve_case(case_path)

    push!(
        rows,
        (
            relpath(case_path, root),
            termination_text(ac_result),
            objective_value(ac_result),
            get(ac_result, "solve_time", missing),
            termination_text(dc_result),
            objective_value(dc_result),
            get(dc_result, "solve_time", missing),
        ),
    )
end

mkpath(dirname(output_path))
CSV.write(output_path, rows)
println("\nWrote summary: ", output_path)
