using PowerModels
using Ipopt
using JuMP
using DataFrames
using CSV
using Statistics

include(joinpath(@__DIR__, "10_run_presentable_lf_replacement_sweep.jl"))

const FOCUS_CASES = [
    "case300_ieee",
    "case1888_rte",
    "case6470_rte",
    "case13659_pegase",
    "case78484_epigrids",
]

const CONSISTENCY_OUTPUT_DIR = joinpath(OUTPUT_ROOT, "opf_to_pf_consistency")
const DIRECT_PF_ROWS = joinpath(OUTPUT_ROOT, "base_full", "powermodels_ac_pf_sweep_rows.csv")

function comma_values(value::String)
    stripped = strip(value)
    if stripped == "" || stripped == "*"
        return String[]
    end
    return [strip(part) for part in split(stripped, ",") if strip(part) != ""]
end

function optional_float_arg(name::String)
    value = strip(arg_value(name, ""))
    return value == "" ? missing : parse(Float64, value)
end

function optional_int_arg(name::String)
    value = strip(arg_value(name, ""))
    return value == "" ? missing : parse(Int, value)
end

function sorted_pm_ids(d)
    return sort(collect(keys(d)); by=x -> parse(Int, string(x)))
end

function successful_opf_status(status)
    text = uppercase(string(status))
    return text in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")
end

function solution_get(result, table::String, id::AbstractString, field::String; default=missing)
    return get_nested(result, "solution", table, string(id), field; default=default)
end

function data_get(data, table::String, id::AbstractString, field::String; default=missing)
    return get_nested(data, table, string(id), field; default=default)
end

function solution_or_data_get(result, data, table::String, id::AbstractString, field::String; default=missing)
    value = solution_get(result, table, id, field; default=missing)
    return ismissing(value) ? data_get(data, table, id, field; default=default) : value
end

function numeric_or_missing(value)
    if value === nothing || ismissing(value)
        return missing
    end
    try
        number = Float64(value)
        return isfinite(number) ? number : missing
    catch
        return missing
    end
end

function scaled_mva(value, base)
    number = numeric_or_missing(value)
    return ismissing(number) ? missing : number * base
end

function absdiff(a, b)
    left = numeric_or_missing(a)
    right = numeric_or_missing(b)
    return (ismissing(left) || ismissing(right)) ? missing : abs(left - right)
end

function absdiff_mva(a, b, base)
    diff = absdiff(a, b)
    return ismissing(diff) ? missing : diff * base
end

function maximum_nonmissing(values)
    clean = collect(skipmissing(values))
    return isempty(clean) ? missing : maximum(clean)
end

function read_csv_or_empty(path::AbstractString)
    return isfile(path) ? CSV.read(path, DataFrame) : DataFrame()
end

function drop_case_variant(df::DataFrame, case_name::AbstractString, variant_name::AbstractString)
    if nrow(df) == 0 || !("case_name" in names(df)) || !("variant_name" in names(df))
        return df
    end
    keep = [
        !(string(row.case_name) == string(case_name) && string(row.variant_name) == string(variant_name))
        for row in eachrow(df)
    ]
    return df[keep, :]
end

function append_result_rows(existing::DataFrame, rows)
    incoming = DataFrame(rows)
    if nrow(existing) == 0
        return incoming
    elseif nrow(incoming) == 0
        return existing
    end
    return vcat(existing, incoming; cols=:union)
end

function truthy(value)
    if value isa Bool
        return value
    end
    return lowercase(strip(string(value))) in ("true", "1", "yes", "y")
end

function direct_failed_variants(direct_pf::DataFrame, case_name::AbstractString)
    if nrow(direct_pf) == 0 || !("case_name" in names(direct_pf)) || !("variant_name" in names(direct_pf)) || !("converged" in names(direct_pf))
        return Set{String}()
    end
    same_case = string.(direct_pf.case_name) .== string(case_name)
    converged = [truthy(value) for value in direct_pf.converged]
    failures = direct_pf[same_case .& .!converged, :]
    return Set(string.(failures.variant_name))
end

function has_case_variant(df::DataFrame, case_name::AbstractString, variant_name::AbstractString)
    if nrow(df) == 0 || !("case_name" in names(df)) || !("variant_name" in names(df))
        return false
    end
    return any(
        (string.(df.case_name) .== string(case_name)) .& (string.(df.variant_name) .== string(variant_name))
    )
end

function mean_nonmissing(values)
    clean = collect(skipmissing(values))
    return isempty(clean) ? missing : mean(clean)
end

function apply_opf_solution_to_pf_data!(data::Dict, opf_result::Dict)
    solution = get(opf_result, "solution", Dict{String,Any}())
    bus_solution = get(solution, "bus", Dict{String,Any}())
    gen_solution = get(solution, "gen", Dict{String,Any}())

    for (bus_id, bus_sol) in bus_solution
        key = string(bus_id)
        if !haskey(data["bus"], key)
            continue
        end
        vm = get(bus_sol, "vm", nothing)
        va = get(bus_sol, "va", nothing)
        if vm !== nothing
            data["bus"][key]["vm"] = vm
            data["bus"][key]["vm_start"] = vm
        end
        if va !== nothing
            data["bus"][key]["va"] = va
            data["bus"][key]["va_start"] = va
        end
    end

    for (gen_id, gen_sol) in gen_solution
        key = string(gen_id)
        if !haskey(data["gen"], key)
            continue
        end
        pg = get(gen_sol, "pg", nothing)
        qg = get(gen_sol, "qg", nothing)
        if pg !== nothing
            data["gen"][key]["pg"] = pg
            data["gen"][key]["pg_start"] = pg
        end
        if qg !== nothing
            data["gen"][key]["qg"] = qg
            data["gen"][key]["qg_start"] = qg
        end
        bus_id = string(data["gen"][key]["gen_bus"])
        if haskey(data["bus"], bus_id) && haskey(data["bus"][bus_id], "vm")
            data["gen"][key]["vg"] = data["bus"][bus_id]["vm"]
        end
    end
    return data
end

function bus_difference_rows(data, opf_result, pf_result; case_name, variant_name)
    rows = NamedTuple[]
    for bus_id in sorted_pm_ids(data["bus"])
        opf_vm = solution_get(opf_result, "bus", bus_id, "vm")
        pf_vm = solution_get(pf_result, "bus", bus_id, "vm")
        opf_va = solution_get(opf_result, "bus", bus_id, "va")
        pf_va = solution_get(pf_result, "bus", bus_id, "va")
        push!(rows, (
            case_name=case_name,
            variant_name=variant_name,
            bus_id=bus_id,
            opf_vm=opf_vm,
            pf_vm=pf_vm,
            abs_vm_diff=absdiff(opf_vm, pf_vm),
            opf_va_rad=opf_va,
            pf_va_rad=pf_va,
            abs_va_rad_diff=absdiff(opf_va, pf_va),
        ))
    end
    return rows
end

function generator_difference_rows(data, opf_result, pf_result; case_name, variant_name)
    base = base_mva(data)
    rows = NamedTuple[]
    for gen_id in sorted_pm_ids(data["gen"])
        gen = data["gen"][gen_id]
        opf_pg = solution_get(opf_result, "gen", gen_id, "pg")
        pf_pg = solution_get(pf_result, "gen", gen_id, "pg")
        opf_qg = solution_get(opf_result, "gen", gen_id, "qg")
        pf_qg = solution_get(pf_result, "gen", gen_id, "qg")
        push!(rows, (
            case_name=case_name,
            variant_name=variant_name,
            gen_id=gen_id,
            bus=gen["gen_bus"],
            opf_pg_mw=scaled_mva(opf_pg, base),
            pf_pg_mw=scaled_mva(pf_pg, base),
            abs_pg_diff_mw=absdiff_mva(opf_pg, pf_pg, base),
            opf_qg_mvar=scaled_mva(opf_qg, base),
            pf_qg_mvar=scaled_mva(pf_qg, base),
            abs_qg_diff_mvar=absdiff_mva(opf_qg, pf_qg, base),
            qmin_mvar=Float64(gen["qmin"]) * base,
            qmax_mvar=Float64(gen["qmax"]) * base,
            pf_qg_below_qmin=!ismissing(numeric_or_missing(pf_qg)) && Float64(pf_qg) < Float64(gen["qmin"]),
            pf_qg_above_qmax=!ismissing(numeric_or_missing(pf_qg)) && Float64(pf_qg) > Float64(gen["qmax"]),
        ))
    end
    return rows
end

function branch_difference_rows(opf_data, pf_data, opf_result, pf_result; case_name, variant_name)
    base = base_mva(pf_data)
    rows = NamedTuple[]
    for branch_id in sorted_pm_ids(pf_data["branch"])
        opf_pf = solution_or_data_get(opf_result, opf_data, "branch", branch_id, "pf")
        pf_pf = solution_or_data_get(pf_result, pf_data, "branch", branch_id, "pf")
        opf_qf = solution_or_data_get(opf_result, opf_data, "branch", branch_id, "qf")
        pf_qf = solution_or_data_get(pf_result, pf_data, "branch", branch_id, "qf")
        opf_pt = solution_or_data_get(opf_result, opf_data, "branch", branch_id, "pt")
        pf_pt = solution_or_data_get(pf_result, pf_data, "branch", branch_id, "pt")
        opf_qt = solution_or_data_get(opf_result, opf_data, "branch", branch_id, "qt")
        pf_qt = solution_or_data_get(pf_result, pf_data, "branch", branch_id, "qt")
        push!(rows, (
            case_name=case_name,
            variant_name=variant_name,
            branch_id=branch_id,
            opf_pf_mw=scaled_mva(opf_pf, base),
            pf_pf_mw=scaled_mva(pf_pf, base),
            abs_pf_diff_mw=absdiff_mva(opf_pf, pf_pf, base),
            opf_qf_mvar=scaled_mva(opf_qf, base),
            pf_qf_mvar=scaled_mva(pf_qf, base),
            abs_qf_diff_mvar=absdiff_mva(opf_qf, pf_qf, base),
            opf_pt_mw=scaled_mva(opf_pt, base),
            pf_pt_mw=scaled_mva(pf_pt, base),
            abs_pt_diff_mw=absdiff_mva(opf_pt, pf_pt, base),
            opf_qt_mvar=scaled_mva(opf_qt, base),
            pf_qt_mvar=scaled_mva(pf_qt, base),
            abs_qt_diff_mvar=absdiff_mva(opf_qt, pf_qt, base),
        ))
    end
    return rows
end

function metric_or(metrics, key::String, fallback)
    return metrics === nothing ? fallback : get(metrics, key, missing)
end

function aggregate_difference_metrics(opf_data, pf_data, opf_result, pf_result)
    base = base_mva(pf_data)
    vm_diffs = Float64[]
    va_diffs = Float64[]
    pg_diffs = Float64[]
    qg_diffs = Float64[]
    branch_pf_diffs = Float64[]
    branch_qf_diffs = Float64[]
    q_violations = 0

    for bus_id in sorted_pm_ids(pf_data["bus"])
        vm_diff = absdiff(solution_get(opf_result, "bus", bus_id, "vm"), solution_get(pf_result, "bus", bus_id, "vm"))
        va_diff = absdiff(solution_get(opf_result, "bus", bus_id, "va"), solution_get(pf_result, "bus", bus_id, "va"))
        !ismissing(vm_diff) && push!(vm_diffs, vm_diff)
        !ismissing(va_diff) && push!(va_diffs, va_diff)
    end

    for gen_id in sorted_pm_ids(pf_data["gen"])
        gen = pf_data["gen"][gen_id]
        opf_pg = solution_get(opf_result, "gen", gen_id, "pg")
        pf_pg = solution_get(pf_result, "gen", gen_id, "pg")
        opf_qg = solution_get(opf_result, "gen", gen_id, "qg")
        pf_qg = solution_get(pf_result, "gen", gen_id, "qg")
        pg_diff = absdiff_mva(opf_pg, pf_pg, base)
        qg_diff = absdiff_mva(opf_qg, pf_qg, base)
        !ismissing(pg_diff) && push!(pg_diffs, pg_diff)
        !ismissing(qg_diff) && push!(qg_diffs, qg_diff)
        if !ismissing(numeric_or_missing(pf_qg)) && (Float64(pf_qg) < Float64(gen["qmin"]) || Float64(pf_qg) > Float64(gen["qmax"]))
            q_violations += 1
        end
    end

    for branch_id in sorted_pm_ids(pf_data["branch"])
        pf_diff = absdiff_mva(
            solution_or_data_get(opf_result, opf_data, "branch", branch_id, "pf"),
            solution_or_data_get(pf_result, pf_data, "branch", branch_id, "pf"),
            base,
        )
        qf_diff = absdiff_mva(
            solution_or_data_get(opf_result, opf_data, "branch", branch_id, "qf"),
            solution_or_data_get(pf_result, pf_data, "branch", branch_id, "qf"),
            base,
        )
        !ismissing(pf_diff) && push!(branch_pf_diffs, pf_diff)
        !ismissing(qf_diff) && push!(branch_qf_diffs, qf_diff)
    end

    return Dict(
        "max_abs_vm_diff" => isempty(vm_diffs) ? missing : maximum(vm_diffs),
        "mean_abs_vm_diff" => isempty(vm_diffs) ? missing : mean(vm_diffs),
        "max_abs_va_rad_diff" => isempty(va_diffs) ? missing : maximum(va_diffs),
        "mean_abs_va_rad_diff" => isempty(va_diffs) ? missing : mean(va_diffs),
        "max_abs_pg_diff_mw" => isempty(pg_diffs) ? missing : maximum(pg_diffs),
        "mean_abs_pg_diff_mw" => isempty(pg_diffs) ? missing : mean(pg_diffs),
        "max_abs_qg_diff_mvar" => isempty(qg_diffs) ? missing : maximum(qg_diffs),
        "mean_abs_qg_diff_mvar" => isempty(qg_diffs) ? missing : mean(qg_diffs),
        "max_abs_branch_pf_diff_mw" => isempty(branch_pf_diffs) ? missing : maximum(branch_pf_diffs),
        "max_abs_branch_qf_diff_mvar" => isempty(branch_qf_diffs) ? missing : maximum(branch_qf_diffs),
        "n_pf_q_limit_violations" => q_violations,
    )
end

function summary_row(data, opf_result, pf_result, bus_rows, gen_rows, branch_rows; case_name, variant_name, counts, metrics=nothing, settings=Dict{String,Any}())
    opf_status = get(opf_result, "termination_status", "NOT_RUN")
    pf_status = get(pf_result, "termination_status", "NOT_RUN")
    q_violations = isempty(gen_rows) ? missing : sum(row.pf_qg_below_qmin || row.pf_qg_above_qmax for row in gen_rows)
    return (
        case_name=case_name,
        variant_name=variant_name,
        opf_solver=get(settings, "opf_solver", "Ipopt"),
        opf_print_level=get(settings, "opf_print_level", 0),
        opf_max_cpu_time_sec=get(settings, "opf_max_cpu_time_sec", missing),
        opf_max_wall_time_sec=get(settings, "opf_max_wall_time_sec", missing),
        opf_max_iter=get(settings, "opf_max_iter", missing),
        detail_output_mode=get(settings, "detail_output_mode", "full"),
        opf_status=string(opf_status),
        opf_solved=successful_opf_status(opf_status),
        opf_objective=get(opf_result, "objective", missing),
        opf_solve_time_sec=get(opf_result, "solve_time", missing),
        pf_status=string(pf_status),
        pf_converged=status_converged(pf_status),
        pf_solve_time_sec=get(pf_result, "solve_time", missing),
        n_applied_bus_voltage=counts["n_applied_bus_voltage"],
        n_applied_gen_pg=counts["n_applied_gen_pg"],
        n_applied_gen_qg=counts["n_applied_gen_qg"],
        n_applied_gen_vg=counts["n_applied_gen_vg"],
        max_abs_vm_diff=metric_or(metrics, "max_abs_vm_diff", maximum_nonmissing([row.abs_vm_diff for row in bus_rows])),
        mean_abs_vm_diff=metric_or(metrics, "mean_abs_vm_diff", mean_nonmissing([row.abs_vm_diff for row in bus_rows])),
        max_abs_va_rad_diff=metric_or(metrics, "max_abs_va_rad_diff", maximum_nonmissing([row.abs_va_rad_diff for row in bus_rows])),
        mean_abs_va_rad_diff=metric_or(metrics, "mean_abs_va_rad_diff", mean_nonmissing([row.abs_va_rad_diff for row in bus_rows])),
        max_abs_pg_diff_mw=metric_or(metrics, "max_abs_pg_diff_mw", maximum_nonmissing([row.abs_pg_diff_mw for row in gen_rows])),
        mean_abs_pg_diff_mw=metric_or(metrics, "mean_abs_pg_diff_mw", mean_nonmissing([row.abs_pg_diff_mw for row in gen_rows])),
        max_abs_qg_diff_mvar=metric_or(metrics, "max_abs_qg_diff_mvar", maximum_nonmissing([row.abs_qg_diff_mvar for row in gen_rows])),
        mean_abs_qg_diff_mvar=metric_or(metrics, "mean_abs_qg_diff_mvar", mean_nonmissing([row.abs_qg_diff_mvar for row in gen_rows])),
        max_abs_branch_pf_diff_mw=metric_or(metrics, "max_abs_branch_pf_diff_mw", maximum_nonmissing([row.abs_pf_diff_mw for row in branch_rows])),
        max_abs_branch_qf_diff_mvar=metric_or(metrics, "max_abs_branch_qf_diff_mvar", maximum_nonmissing([row.abs_qf_diff_mvar for row in branch_rows])),
        n_pf_q_limit_violations=metric_or(metrics, "n_pf_q_limit_violations", q_violations),
    )
end

function run_case_variant(
    case_path::AbstractString,
    case_name::AbstractString,
    variant_name::AbstractString,
    variant,
    output_dir::AbstractString;
    opf_time_limit_sec=missing,
    opf_wall_time_limit_sec=missing,
    opf_max_iter=missing,
    summary_only::Bool=false,
)
    println("\n=== ", case_name, " | ", variant_name, " ===")
    data, _ = parse_case_for_experiment(case_path; quiet=true, normalize_bus_types=true)
    notes = parse_replacement_notes(case_path)
    _, counts = apply_presentable_variant!(data, notes, variant)

    optimizer_attributes = Pair{String, Any}["print_level" => 0]
    if !ismissing(opf_time_limit_sec)
        push!(optimizer_attributes, "max_cpu_time" => opf_time_limit_sec)
    end
    if !ismissing(opf_wall_time_limit_sec)
        push!(optimizer_attributes, "max_wall_time" => opf_wall_time_limit_sec)
    end
    if !ismissing(opf_max_iter)
        push!(optimizer_attributes, "max_iter" => opf_max_iter)
    end
    settings = Dict{String,Any}(
        "opf_solver" => "Ipopt",
        "opf_print_level" => 0,
        "opf_max_cpu_time_sec" => opf_time_limit_sec,
        "opf_max_wall_time_sec" => opf_wall_time_limit_sec,
        "opf_max_iter" => opf_max_iter,
        "detail_output_mode" => summary_only ? "summary_only" : "full",
    )
    optimizer = optimizer_with_attributes(Ipopt.Optimizer, optimizer_attributes...)
    opf_result = PowerModels.solve_ac_opf(deepcopy(data), optimizer)

    if !successful_opf_status(get(opf_result, "termination_status", "UNKNOWN"))
        empty_rows = NamedTuple[]
        pf_result = Dict("termination_status" => "SKIPPED_OPF_NOT_SOLVED")
        return (
            summary=summary_row(data, opf_result, pf_result, empty_rows, empty_rows, empty_rows; case_name=case_name, variant_name=variant_name, counts=counts, settings=settings),
            bus_rows=empty_rows,
            gen_rows=empty_rows,
            branch_rows=empty_rows,
        )
    end

    opf_data = deepcopy(data)
    update_solution_and_flows!(opf_data, opf_result, "AC")

    pf_data = deepcopy(data)
    apply_opf_solution_to_pf_data!(pf_data, opf_result)
    pf_result = ac_compute_call(pf_data)
    update_solution_and_flows!(pf_data, pf_result, "AC")

    if summary_only
        empty_rows = NamedTuple[]
        metrics = aggregate_difference_metrics(opf_data, pf_data, opf_result, pf_result)
        return (
            summary=summary_row(pf_data, opf_result, pf_result, empty_rows, empty_rows, empty_rows; case_name=case_name, variant_name=variant_name, counts=counts, metrics=metrics, settings=settings),
            bus_rows=empty_rows,
            gen_rows=empty_rows,
            branch_rows=empty_rows,
        )
    end

    bus_rows = bus_difference_rows(pf_data, opf_result, pf_result; case_name=case_name, variant_name=variant_name)
    gen_rows = generator_difference_rows(pf_data, opf_result, pf_result; case_name=case_name, variant_name=variant_name)
    branch_rows = branch_difference_rows(opf_data, pf_data, opf_result, pf_result; case_name=case_name, variant_name=variant_name)
    return (
        summary=summary_row(pf_data, opf_result, pf_result, bus_rows, gen_rows, branch_rows; case_name=case_name, variant_name=variant_name, counts=counts, settings=settings),
        bus_rows=bus_rows,
        gen_rows=gen_rows,
        branch_rows=branch_rows,
    )
end

function main()
    root = project_root()
    case_dir = arg_value("--case-dir", joinpath(root, "data", "raw", "pglib", "matpower_m", "base_case"))
    output_dir = arg_value("--output-dir", CONSISTENCY_OUTPUT_DIR)
    case_filter = comma_values(arg_value("--case", join(FOCUS_CASES, ",")))
    variant_filter = comma_values(arg_value("--variant", "*"))
    direct_pf_path = arg_value("--direct-pf-rows", DIRECT_PF_ROWS)
    fresh = arg_bool("--fresh")
    only_direct_failures = arg_bool("--only-direct-failures")
    skip_existing = arg_bool("--skip-existing")
    quiet = arg_bool("--quiet")
    summary_only = arg_bool("--summary-only")
    opf_time_limit_sec = optional_float_arg("--opf-time-limit-sec")
    opf_wall_time_limit_sec = optional_float_arg("--opf-wall-time-limit-sec")
    opf_max_iter = optional_int_arg("--opf-max-iter")
    if quiet
        Logging.disable_logging(Logging.Warn)
    end

    variants = canonical_ac_variants()
    if !isempty(variant_filter)
        variants = [entry for entry in variants if entry.name in variant_filter]
    end
    direct_pf = (only_direct_failures && isfile(direct_pf_path)) ? CSV.read(direct_pf_path, DataFrame) : DataFrame()

    mkpath(output_dir)
    summary_path = joinpath(output_dir, "opf_to_pf_consistency_summary.csv")
    bus_path = joinpath(output_dir, "opf_to_pf_bus_state_differences.csv")
    gen_path = joinpath(output_dir, "opf_to_pf_generator_state_differences.csv")
    branch_path = joinpath(output_dir, "opf_to_pf_branch_flow_differences.csv")

    summary_df = fresh ? DataFrame() : read_csv_or_empty(summary_path)
    bus_df = fresh ? DataFrame() : read_csv_or_empty(bus_path)
    gen_df = fresh ? DataFrame() : read_csv_or_empty(gen_path)
    branch_df = fresh ? DataFrame() : read_csv_or_empty(branch_path)

    for case_name in case_filter
        case_path = joinpath(case_dir, "pglib_opf_$(case_name).m")
        if !isfile(case_path)
            error("Missing case file: $(case_path)")
        end
        case_variants = variants
        if only_direct_failures
            failed = direct_failed_variants(direct_pf, case_name)
            case_variants = [entry for entry in variants if entry.name in failed]
            println("Direct-PF failed variants for ", case_name, ": ", isempty(case_variants) ? "(none)" : join([entry.name for entry in case_variants], ", "))
        end
        for entry in case_variants
            if skip_existing && has_case_variant(summary_df, case_name, entry.name)
                println("\n=== ", case_name, " | ", entry.name, " ===")
                println("Skipping existing OPF-to-PF result")
                continue
            end
            result = run_case_variant(
                case_path,
                case_name,
                entry.name,
                entry.variant,
                output_dir;
                opf_time_limit_sec=opf_time_limit_sec,
                opf_wall_time_limit_sec=opf_wall_time_limit_sec,
                opf_max_iter=opf_max_iter,
                summary_only=summary_only,
            )
            summary_df = drop_case_variant(summary_df, case_name, entry.name)
            bus_df = drop_case_variant(bus_df, case_name, entry.name)
            gen_df = drop_case_variant(gen_df, case_name, entry.name)
            branch_df = drop_case_variant(branch_df, case_name, entry.name)

            summary_df = append_result_rows(summary_df, [result.summary])
            bus_df = append_result_rows(bus_df, result.bus_rows)
            gen_df = append_result_rows(gen_df, result.gen_rows)
            branch_df = append_result_rows(branch_df, result.branch_rows)

            CSV.write(summary_path, summary_df)
            CSV.write(bus_path, bus_df)
            CSV.write(gen_path, gen_df)
            CSV.write(branch_path, branch_df)
        end
    end

    println("Wrote OPF-to-PF consistency outputs to ", output_dir)
end

if abspath(PROGRAM_FILE) == @__FILE__
    main()
end
