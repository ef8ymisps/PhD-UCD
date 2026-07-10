# Base OPF cross-model validation.
#
# This layer compares the translated Julia OPF models against PowerModels.jl on
# the unmodified base case. It is the baseline validation before stress sweeps.

using CSV
using DataFrames
using HiGHS
using Ipopt
using JuMP
using PowerModels

struct BaseOPFCrossModelResult
    summary_rows::Vector{NamedTuple}
    generator_rows::Vector{NamedTuple}
    bus_rows::Vector{NamedTuple}
    branch_rows::Vector{NamedTuple}
end

"""Return true for solver statuses that represent an OPF solution."""
function _base_opf_solved(status)
    return string(status) in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")
end

"""Resolve either `case5.m` or `pglib_opf_case5` style names."""
function _base_opf_case_path(case_dir::String, case_name::String)
    if isfile(case_name)
        return normpath(case_name)
    end

    candidate = normpath(joinpath(case_dir, case_name))
    if isfile(candidate)
        return candidate
    end

    if !endswith(candidate, ".m")
        candidate_m = candidate * ".m"
        if isfile(candidate_m)
            return candidate_m
        end
    end

    error("Case file not found: $(case_name)")
end

"""Short case name used in CSV outputs."""
function _base_opf_clean_case_name(case_name::String; strip_case_prefix::Bool=true)
    stem = splitext(basename(case_name))[1]
    if strip_case_prefix
        stem = replace(stem, "pglib_opf_" => "")
    end
    return stem
end

function _base_opf_add_context(row; case_name::String, model::String)
    return (; case_name=case_name, model=model, row...)
end

function _base_opf_error_row(; case_name, model, own_status, pm_status, own_error, pm_error)
    return (
        case_name=case_name,
        model=model,
        scenario="base",
        own_status=own_status,
        pm_status=pm_status,
        own_solved=false,
        pm_solved=false,
        outcome_match=false,
        own_objective=NaN,
        pm_objective=NaN,
        objective_diff=NaN,
        objective_rel_diff=NaN,
        max_pg_diff=NaN,
        max_qg_diff=NaN,
        max_vm_diff=NaN,
        max_va_diff=NaN,
        max_pf_diff=NaN,
        max_qf_diff=NaN,
        own_error=own_error,
        pm_error=pm_error
    )
end

function _base_opf_field(row, name::Symbol, default)
    return hasproperty(row, name) ? getproperty(row, name) : default
end

function _base_opf_finalize_summary(summary; model::String, own_error::String="", pm_error::String="")
    own_solved = _base_opf_solved(summary.own_status)
    pm_solved = _base_opf_solved(summary.pm_status)
    rel = abs(summary.objective_diff) / (1.0 + abs(summary.pm_objective))

    return (
        case_name=_base_opf_field(summary, :case_name, ""),
        model=model,
        scenario=_base_opf_field(summary, :scenario, "base"),
        own_status=summary.own_status,
        pm_status=summary.pm_status,
        own_solved=own_solved,
        pm_solved=pm_solved,
        outcome_match=own_solved == pm_solved,
        own_objective=summary.own_objective,
        pm_objective=summary.pm_objective,
        objective_diff=summary.objective_diff,
        objective_rel_diff=rel,
        max_pg_diff=_base_opf_field(summary, :max_pg_diff, NaN),
        max_qg_diff=_base_opf_field(summary, :max_qg_diff, NaN),
        max_vm_diff=_base_opf_field(summary, :max_vm_diff, NaN),
        max_va_diff=_base_opf_field(summary, :max_va_diff, NaN),
        max_pf_diff=_base_opf_field(summary, :max_pf_diff, NaN),
        max_qf_diff=_base_opf_field(summary, :max_qf_diff, NaN),
        own_error=own_error,
        pm_error=pm_error
    )
end

"""Run own DC OPF and PowerModels DCPPowerModel on one base case."""
function run_base_dc_opf_cross_model_case(case_path::String; case_name=nothing, highs_optimizer=HiGHS.Optimizer)
    data = PowerModels.parse_file(case_path)
    case = powermodels_to_powercase(deepcopy(data))
    label = case_name === nothing ? _base_opf_clean_case_name(case_path) : case_name
    optimizer = optimizer_with_attributes(highs_optimizer, "output_flag" => false)

    own_result = nothing
    pm_result = nothing
    own_error = ""
    pm_error = ""

    try
        own_result = solve_dc_opf_own(case; optimizer=optimizer, silent=true)
    catch err
        own_error = sprint(showerror, err)
    end

    try
        pm_result = PowerModels.solve_opf(deepcopy(data), DCPPowerModel, optimizer)
    catch err
        pm_error = sprint(showerror, err)
    end

    if own_result === nothing || pm_result === nothing
        own_status = own_result === nothing ? "ERROR" : string(own_result.termination_status)
        pm_status = pm_result === nothing ? "ERROR" : string(pm_result["termination_status"])
        return (
            summary_row=_base_opf_error_row(
                case_name=label,
                model="dc",
                own_status=own_status,
                pm_status=pm_status,
                own_error=own_error,
                pm_error=pm_error
            ),
            generator_rows=NamedTuple[],
            bus_rows=NamedTuple[],
            branch_rows=NamedTuple[]
        )
    end

    summary = dc_opf_summary_row(case, own_result, pm_result; scenario="base")
    summary = _base_opf_finalize_summary(summary; model="dc", own_error=own_error, pm_error=pm_error)
    summary = merge(summary, (case_name=label,))

    generator_rows = [
        _base_opf_add_context(row; case_name=label, model="dc")
        for row in compare_dc_opf_generator_rows(case, own_result, pm_result; scenario="base")
    ]
    bus_rows = NamedTuple[]
    branch_rows = [
        _base_opf_add_context(row; case_name=label, model="dc")
        for row in compare_dc_opf_branch_rows(case, own_result, pm_result; scenario="base")
    ]

    return (summary_row=summary, generator_rows=generator_rows, bus_rows=bus_rows, branch_rows=branch_rows)
end

"""Run own AC OPF and PowerModels ACPPowerModel on one base case."""
function run_base_ac_opf_cross_model_case(case_path::String; case_name=nothing, ipopt_optimizer=Ipopt.Optimizer)
    data = PowerModels.parse_file(case_path)
    case = powermodels_to_powercase(deepcopy(data))
    label = case_name === nothing ? _base_opf_clean_case_name(case_path) : case_name
    optimizer = optimizer_with_attributes(ipopt_optimizer, "print_level" => 0)

    own_result = nothing
    pm_result = nothing
    own_error = ""
    pm_error = ""

    try
        own_result = solve_ac_opf_own(case; optimizer=optimizer, silent=true)
    catch err
        own_error = sprint(showerror, err)
    end

    try
        pm_result = PowerModels.solve_opf(deepcopy(data), ACPPowerModel, optimizer)
    catch err
        pm_error = sprint(showerror, err)
    end

    if own_result === nothing || pm_result === nothing
        own_status = own_result === nothing ? "ERROR" : string(own_result.termination_status)
        pm_status = pm_result === nothing ? "ERROR" : string(pm_result["termination_status"])
        return (
            summary_row=_base_opf_error_row(
                case_name=label,
                model="ac",
                own_status=own_status,
                pm_status=pm_status,
                own_error=own_error,
                pm_error=pm_error
            ),
            generator_rows=NamedTuple[],
            bus_rows=NamedTuple[],
            branch_rows=NamedTuple[]
        )
    end

    summary = ac_opf_summary_row(case, own_result, pm_result; scenario="base")
    summary = _base_opf_finalize_summary(summary; model="ac", own_error=own_error, pm_error=pm_error)
    summary = merge(summary, (case_name=label,))

    generator_rows = [
        _base_opf_add_context(row; case_name=label, model="ac")
        for row in compare_ac_opf_generator_rows(case, own_result, pm_result; scenario="base")
    ]
    bus_rows = [
        _base_opf_add_context(row; case_name=label, model="ac")
        for row in compare_ac_opf_bus_rows(case, own_result, pm_result; scenario="base")
    ]
    branch_rows = [
        _base_opf_add_context(row; case_name=label, model="ac")
        for row in compare_ac_opf_branch_rows(case, own_result, pm_result; scenario="base")
    ]

    return (summary_row=summary, generator_rows=generator_rows, bus_rows=bus_rows, branch_rows=branch_rows)
end

"""Run base cross-model validation for a list of cases and model types."""
function run_base_opf_cross_model_suite(
    case_names;
    case_dir::String,
    models=(:dc, :ac),
    strip_case_prefix::Bool=true,
    verbose::Bool=true
)
    summary_rows = NamedTuple[]
    generator_rows = NamedTuple[]
    bus_rows = NamedTuple[]
    branch_rows = NamedTuple[]

    for raw_name in case_names
        case_path = _base_opf_case_path(case_dir, String(raw_name))
        label = _base_opf_clean_case_name(case_path; strip_case_prefix=strip_case_prefix)

        if verbose
            println("\nBASE OPF CROSS-MODEL: ", label)
        end

        if :dc in models
            result = run_base_dc_opf_cross_model_case(case_path; case_name=label)
            push!(summary_rows, result.summary_row)
            append!(generator_rows, result.generator_rows)
            append!(branch_rows, result.branch_rows)
            if verbose
                println("  dc | own=", result.summary_row.own_status, " | pm=", result.summary_row.pm_status,
                    " | obj_diff=", result.summary_row.objective_diff)
            end
        end

        if :ac in models
            result = run_base_ac_opf_cross_model_case(case_path; case_name=label)
            push!(summary_rows, result.summary_row)
            append!(generator_rows, result.generator_rows)
            append!(bus_rows, result.bus_rows)
            append!(branch_rows, result.branch_rows)
            if verbose
                println("  ac | own=", result.summary_row.own_status, " | pm=", result.summary_row.pm_status,
                    " | obj_diff=", result.summary_row.objective_diff)
            end
        end
    end

    return BaseOPFCrossModelResult(summary_rows, generator_rows, bus_rows, branch_rows)
end

"""Write summary and detail CSVs for a base cross-model result."""
function write_base_opf_cross_model_csvs(result::BaseOPFCrossModelResult, output_dir::String)
    mkpath(output_dir)

    summary_path = joinpath(output_dir, "base_opf_cross_model_summary.csv")
    generator_path = joinpath(output_dir, "base_opf_cross_model_generators.csv")
    bus_path = joinpath(output_dir, "base_opf_cross_model_buses.csv")
    branch_path = joinpath(output_dir, "base_opf_cross_model_branches.csv")

    CSV.write(summary_path, DataFrame(result.summary_rows))
    CSV.write(generator_path, DataFrame(result.generator_rows))
    CSV.write(bus_path, DataFrame(result.bus_rows))
    CSV.write(branch_path, DataFrame(result.branch_rows))

    return (
        summary=summary_path,
        generators=generator_path,
        buses=bus_path,
        branches=branch_path
    )
end
