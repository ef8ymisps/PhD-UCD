include("../src/OwnPowerModels.jl")

using .OwnPowerModels
using CSV
using DataFrames

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

function split_csv_arg(value::String)
    return [strip(x) for x in split(value, ",") if strip(x) != ""]
end

function completed_case_models(summary_path::String)
    if !isfile(summary_path)
        return Set{Tuple{String,String}}()
    end
    frame = DataFrame(CSV.File(summary_path; select=[:case_name, :model]))
    return Set((String(row.case_name), String(row.model)) for row in eachrow(frame))
end

function append_result!(result, model, summary_path, generator_path, bus_path, branch_path)
    # Write details first. The summary row is the resume marker, so it is
    # written last only after the detail files have been checkpointed.
    append_rows!(generator_path, result.generator_rows, generator_columns())
    if model == "ac"
        append_rows!(bus_path, result.bus_rows, bus_columns())
    elseif !isfile(bus_path)
        append_rows!(bus_path, NamedTuple[], bus_columns())
    end
    append_rows!(branch_path, result.branch_rows, branch_columns())
    append_rows!(summary_path, [result.summary_row], summary_columns())
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
    row_keys = propertynames(row)
    for (idx, column) in enumerate(columns)
        if idx > 1
            write(io, ",")
        end
        value = column in row_keys ? getproperty(row, column) : missing
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

function summary_columns()
    return [
        :case_name,
        :model,
        :scenario,
        :own_status,
        :pm_status,
        :own_solved,
        :pm_solved,
        :outcome_match,
        :own_objective,
        :pm_objective,
        :objective_diff,
        :objective_rel_diff,
        :max_pg_diff,
        :max_qg_diff,
        :max_vm_diff,
        :max_va_diff,
        :max_pf_diff,
        :max_qf_diff,
        :own_error,
        :pm_error,
    ]
end

function generator_columns()
    return [
        :case_name,
        :model,
        :scenario,
        :gen_id,
        :bus_id,
        :own_pg,
        :pm_pg,
        :pg_diff,
        :abs_pg_diff,
        :own_qg,
        :pm_qg,
        :qg_diff,
        :abs_qg_diff,
        :pmin,
        :pmax,
        :qmin,
        :qmax,
        :cost_c2,
        :cost_c1,
        :cost_c0,
        :own_at_pmin,
        :own_at_pmax,
        :own_at_qmin,
        :own_at_qmax,
        :pm_at_pmin,
        :pm_at_pmax,
        :pm_at_qmin,
        :pm_at_qmax,
        :own_headroom_to_pmax,
        :pm_headroom_to_pmax,
    ]
end

function bus_columns()
    return [
        :case_name,
        :model,
        :scenario,
        :bus_id,
        :own_vm,
        :pm_vm,
        :vm_diff,
        :abs_vm_diff,
        :own_va,
        :pm_va,
        :va_diff,
        :abs_va_diff,
        :vmin,
        :vmax,
        :own_at_vmin,
        :own_at_vmax,
        :pm_at_vmin,
        :pm_at_vmax,
    ]
end

function branch_columns()
    return [
        :case_name,
        :model,
        :scenario,
        :branch_id,
        :from_bus,
        :to_bus,
        :own_pf,
        :pm_pf,
        :pf_diff,
        :abs_pf_diff,
        :own_qf,
        :pm_qf,
        :qf_diff,
        :abs_qf_diff,
        :own_pt,
        :pm_pt,
        :pt_diff,
        :abs_pt_diff,
        :own_qt,
        :pm_qt,
        :qt_diff,
        :abs_qt_diff,
        :own_sf,
        :pm_sf,
        :own_st,
        :pm_st,
        :own_loading_abs,
        :pm_loading_abs,
        :own_loading_pct,
        :pm_loading_pct,
        :own_binding,
        :pm_binding,
        :own_flow_margin,
        :pm_flow_margin,
        :rate_a,
        :angmin,
        :angmax,
    ]
end

root = project_root()
case_dir = normpath(joinpath(root, arg_value("--case-dir", "data/raw/pglib/matpower_m/base_case")))
output_dir = normpath(joinpath(root, arg_value("--output-dir", "results/own_julia/base_opf_cross_model")))

default_cases = [
    "pglib_opf_case5_pjm",
    "pglib_opf_case14_ieee",
    "pglib_opf_case30_ieee",
    "pglib_opf_case118_ieee",
    "pglib_opf_case300_ieee",
]

cases = split_csv_arg(arg_value("--cases", join(default_cases, ",")))
model_arg = lowercase(arg_value("--models", "dc,ac"))
models = Symbol.(split_csv_arg(model_arg))
strip_case_prefix = !has_flag("--keep-case-prefix")
verbose = !has_flag("--quiet")

summary_path = joinpath(output_dir, "base_opf_cross_model_summary.csv")
generator_path = joinpath(output_dir, "base_opf_cross_model_generators.csv")
bus_path = joinpath(output_dir, "base_opf_cross_model_buses.csv")
branch_path = joinpath(output_dir, "base_opf_cross_model_branches.csv")

if has_flag("--overwrite") && isdir(output_dir)
    for path in (summary_path, generator_path, bus_path, branch_path)
        if isfile(path)
            rm(path)
        end
    end
end

mkpath(output_dir)
completed = completed_case_models(summary_path)

for raw_name in cases
    case_path = OwnPowerModels._base_opf_case_path(case_dir, String(raw_name))
    label = OwnPowerModels._base_opf_clean_case_name(case_path; strip_case_prefix=strip_case_prefix)

    if verbose
        println("\nBASE OPF CROSS-MODEL: ", label)
    end

    if :dc in models
        key = (label, "dc")
        if key in completed
            println("  dc | skipped existing result")
        else
            result = OwnPowerModels.run_base_dc_opf_cross_model_case(case_path; case_name=label)
            append_result!(result, "dc", summary_path, generator_path, bus_path, branch_path)
            push!(completed, key)
            if verbose
                println("  dc | own=", result.summary_row.own_status, " | pm=", result.summary_row.pm_status,
                    " | obj_diff=", result.summary_row.objective_diff)
            end
        end
    end

    if :ac in models
        key = (label, "ac")
        if key in completed
            println("  ac | skipped existing result")
        else
            result = OwnPowerModels.run_base_ac_opf_cross_model_case(case_path; case_name=label)
            append_result!(result, "ac", summary_path, generator_path, bus_path, branch_path)
            push!(completed, key)
            if verbose
                println("  ac | own=", result.summary_row.own_status, " | pm=", result.summary_row.pm_status,
                    " | obj_diff=", result.summary_row.objective_diff)
            end
        end
    end
end

println("\nWrote:")
println("  summary:    ", summary_path)
println("  generators: ", generator_path)
println("  buses:      ", bus_path)
println("  branches:   ", branch_path)
