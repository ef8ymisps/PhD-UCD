using CSV
using DataFrames
using Ipopt
using JuMP
using PowerModels

function project_root()
    return normpath(joinpath(@__DIR__, "..", "..", ".."))
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

function has_flag(name::String)
    return name in ARGS
end

function completed_scenarios(summary_path::String)
    if !isfile(summary_path)
        return Set{String}()
    end
    frame = DataFrame(CSV.File(summary_path; select=[:scenario_id]))
    return Set(String(row.scenario_id) for row in eachrow(frame))
end

function solved_status(status::String)
    return status in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")
end

function append_rows!(path::String, rows, columns::Vector{Symbol})
    write_header = !isfile(path)
    open(path, write_header ? "w" : "a") do io
        if write_header
            write(io, join(String.(columns), ","), "\n")
        end
        for row in rows
            write_csv_row!(io, row, columns)
        end
    end
end

function write_csv_row!(io, row, columns::Vector{Symbol})
    keys = propertynames(row)
    for (idx, column) in enumerate(columns)
        idx > 1 && write(io, ",")
        value = column in keys ? getproperty(row, column) : missing
        write(io, csv_cell(value))
    end
    write(io, "\n")
end

function csv_cell(value)
    if value === missing || value === nothing
        return ""
    end
    text = string(value)
    if occursin(",", text) || occursin("\"", text) || occursin("\n", text) || occursin("\r", text)
        return "\"" * replace(text, "\"" => "\"\"") * "\""
    end
    return text
end

function key_context(row)
    return (
        case_name=cell_string(row.case_name),
        scenario=cell_string(row.scenario),
        target_type=cell_string(row.target_type),
        target_id=cell_string(row.target_id),
        parameter_name=cell_string(row.parameter_name),
        parameter_value=cell_string(row.parameter_value),
        scenario_id=cell_string(row.scenario_id),
    )
end

function cell_string(value)
    if value === missing || value === nothing
        return ""
    end
    return String(value)
end

function solution_dict(result, field::String)
    if !haskey(result, "solution") || !haskey(result["solution"], field)
        return Dict{String,Any}()
    end
    return result["solution"][field]
end

function pm_summary_row(context, result, solve_time_sec::Float64; error::String="")
    status = haskey(result, "termination_status") ? string(result["termination_status"]) : "ERROR"
    objective = haskey(result, "objective") ? result["objective"] : NaN
    return (;
        context...,
        pm_status=status,
        pm_solved=solved_status(status),
        pm_objective=objective,
        pm_solve_time_sec=solve_time_sec,
        pm_error=error,
    )
end

function pm_error_summary_row(context, solve_time_sec::Float64, error::String)
    return (;
        context...,
        pm_status="ERROR",
        pm_solved=false,
        pm_objective=NaN,
        pm_solve_time_sec=solve_time_sec,
        pm_error=error,
    )
end

function pm_generator_rows(context, result)
    rows = NamedTuple[]
    for gen_id in sort(collect(keys(solution_dict(result, "gen"))); by=x -> parse(Int, string(x)))
        gen = result["solution"]["gen"][gen_id]
        push!(rows, (;
            context...,
            gen_id=string(gen_id),
            pm_pg=get(gen, "pg", NaN),
            pm_qg=get(gen, "qg", NaN),
        ))
    end
    return rows
end

function pm_bus_rows(context, result)
    rows = NamedTuple[]
    for bus_id in sort(collect(keys(solution_dict(result, "bus"))); by=x -> parse(Int, string(x)))
        bus = result["solution"]["bus"][bus_id]
        push!(rows, (;
            context...,
            bus_id=string(bus_id),
            pm_vm=get(bus, "vm", NaN),
            pm_va=get(bus, "va", NaN),
        ))
    end
    return rows
end

function pm_branch_rows(context, result)
    rows = NamedTuple[]
    for branch_id in sort(collect(keys(solution_dict(result, "branch"))); by=x -> parse(Int, string(x)))
        branch = result["solution"]["branch"][branch_id]
        push!(rows, (;
            context...,
            branch_id=string(branch_id),
            pm_pf=get(branch, "pf", NaN),
            pm_qf=get(branch, "qf", NaN),
            pm_pt=get(branch, "pt", NaN),
            pm_qt=get(branch, "qt", NaN),
        ))
    end
    return rows
end

function summary_columns()
    return [
        :case_name, :scenario, :target_type, :target_id, :parameter_name, :parameter_value,
        :scenario_id, :pm_status, :pm_solved, :pm_objective, :pm_solve_time_sec, :pm_error,
    ]
end

function generator_columns()
    return [
        :case_name, :scenario, :target_type, :target_id, :parameter_name, :parameter_value,
        :scenario_id, :gen_id, :pm_pg, :pm_qg,
    ]
end

function bus_columns()
    return [
        :case_name, :scenario, :target_type, :target_id, :parameter_name, :parameter_value,
        :scenario_id, :bus_id, :pm_vm, :pm_va,
    ]
end

function branch_columns()
    return [
        :case_name, :scenario, :target_type, :target_id, :parameter_name, :parameter_value,
        :scenario_id, :branch_id, :pm_pf, :pm_qf, :pm_pt, :pm_qt,
    ]
end

root = project_root()
manifest_path = normpath(joinpath(root, arg_value(
    "--manifest",
    "results/reference/powermodels/ac_opf_compact_sweeps/compact_ac_opf_powermodels_manifest.csv",
)))
output_dir = normpath(joinpath(root, arg_value(
    "--output-dir",
    "results/reference/powermodels/ac_opf_compact_sweeps",
)))
verbose = !has_flag("--quiet")

summary_path = joinpath(output_dir, "powermodels_ac_opf_summary.csv")
generator_path = joinpath(output_dir, "powermodels_ac_opf_generators.csv")
bus_path = joinpath(output_dir, "powermodels_ac_opf_buses.csv")
branch_path = joinpath(output_dir, "powermodels_ac_opf_branches.csv")

if has_flag("--overwrite") && isdir(output_dir)
    for path in (summary_path, generator_path, bus_path, branch_path)
        isfile(path) && rm(path)
    end
end

mkpath(output_dir)
manifest = DataFrame(CSV.File(manifest_path))
completed = completed_scenarios(summary_path)
optimizer = optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => 3000)

for row in eachrow(manifest)
    scenario_id = String(row.scenario_id)
    if scenario_id in completed
        verbose && println("skipped existing PowerModels reference: ", scenario_id)
        continue
    end

    context = key_context(row)
    scenario_file = String(row.scenario_file)
    verbose && println("PowerModels AC OPF: ", scenario_id)

    start = time()
    try
        data = PowerModels.parse_file(scenario_file)
        result = PowerModels.solve_ac_opf(data, optimizer)
        solve_time_sec = time() - start

        if solved_status(string(result["termination_status"]))
            append_rows!(generator_path, pm_generator_rows(context, result), generator_columns())
            append_rows!(bus_path, pm_bus_rows(context, result), bus_columns())
            append_rows!(branch_path, pm_branch_rows(context, result), branch_columns())
        else
            append_rows!(generator_path, NamedTuple[], generator_columns())
            append_rows!(bus_path, NamedTuple[], bus_columns())
            append_rows!(branch_path, NamedTuple[], branch_columns())
        end
        append_rows!(summary_path, [pm_summary_row(context, result, solve_time_sec)], summary_columns())
        push!(completed, scenario_id)
        verbose && println("  status=", result["termination_status"], " objective=", get(result, "objective", NaN))
    catch err
        solve_time_sec = time() - start
        append_rows!(generator_path, NamedTuple[], generator_columns())
        append_rows!(bus_path, NamedTuple[], bus_columns())
        append_rows!(branch_path, NamedTuple[], branch_columns())
        append_rows!(summary_path, [pm_error_summary_row(context, solve_time_sec, sprint(showerror, err))], summary_columns())
        push!(completed, scenario_id)
        verbose && println("  ERROR: ", sprint(showerror, err))
    end
end

println("\nWrote:")
println("  summary:    ", summary_path)
println("  generators: ", generator_path)
println("  buses:      ", bus_path)
println("  branches:   ", branch_path)
