# ============================================================
# Benchmarking utilities
# OwnPowerModels vs PowerModels.jl
# ============================================================

using Statistics
using JuMP
using HiGHS
using Ipopt
using PowerModels

struct BenchmarkResult
    label::String
    times::Vector{Float64}
    min_time::Float64
    median_time::Float64
    mean_time::Float64
end

struct OPFSpeedComparisonResult
    case_name::String
    own_dc_solve_only::BenchmarkResult
    pm_dc_end_to_end::BenchmarkResult
    own_dc_end_to_end::BenchmarkResult
    own_ac_solve_only::BenchmarkResult
    pm_ac_end_to_end::BenchmarkResult
    own_ac_end_to_end::BenchmarkResult
end

function benchmark_call(f, label::String; runs::Int=5, warmup::Bool=true, verbose::Bool=true)
    if verbose
        println("\n", label)
    end

    if warmup
        if verbose
            println("warming up...")
        end
        f()
    end

    times = Float64[]

    for k in 1:runs
        GC.gc()
        t = @elapsed f()
        push!(times, t)

        if verbose
            println("run ", k, ": ", round(t, digits=4), " s")
        end
    end

    result = BenchmarkResult(
        label,
        times,
        minimum(times),
        median(times),
        mean(times)
    )

    if verbose
        println("min:    ", round(result.min_time, digits=4), " s")
        println("median: ", round(result.median_time, digits=4), " s")
        println("mean:   ", round(result.mean_time, digits=4), " s")
    end

    return result
end

function benchmark_call(label::String, f; runs::Int=5, warmup::Bool=true, verbose::Bool=true)
    return benchmark_call(
        f,
        label;
        runs=runs,
        warmup=warmup,
        verbose=verbose
    )
end

function print_benchmark_result(result::BenchmarkResult)
    println("\n", result.label)
    println("min:    ", round(result.min_time, digits=6), " s")
    println("median: ", round(result.median_time, digits=6), " s")
    println("mean:   ", round(result.mean_time, digits=6), " s")
end

function benchmark_opf_speed_comparison(
    case_name::String,
    case_path::String;
    dc_runs::Int=10,
    ac_runs::Int=5,
    highs_optimizer=HiGHS.Optimizer,
    ipopt_optimizer=Ipopt.Optimizer,
    verbose::Bool=true
)
    if verbose
        println("\n==============================")
        println("LOADING CASE")
        println("==============================")
        println("Case name: ", case_name)
        println("Case path: ", case_path)
    end

    case = load_powercase_from_powermodels(case_path)

    highs_silent = optimizer_with_attributes(
        highs_optimizer,
        "output_flag" => false
    )

    ipopt_silent = optimizer_with_attributes(
        ipopt_optimizer,
        "print_level" => 0
    )

    if verbose
        println("\n==============================")
        println("DC OPF SPEED COMPARISON")
        println("==============================")
    end

    own_dc_solve_only = benchmark_call(
        "Own DC OPF solve only";
        runs=dc_runs,
        verbose=verbose
    ) do
        solve_dc_opf_own(
            case;
            optimizer=highs_silent,
            silent=true
        )
    end

    pm_dc_end_to_end = benchmark_call(
        "PowerModels DC OPF end-to-end";
        runs=dc_runs,
        verbose=verbose
    ) do
        PowerModels.solve_dc_opf(case_path, highs_silent)
    end

    own_dc_end_to_end = benchmark_call(
        "Own DC OPF end-to-end: parse + convert + solve";
        runs=dc_runs,
        verbose=verbose
    ) do
        local c = load_powercase_from_powermodels(case_path)

        solve_dc_opf_own(
            c;
            optimizer=highs_silent,
            silent=true
        )
    end

    if verbose
        println("\n==============================")
        println("AC OPF SPEED COMPARISON")
        println("==============================")
    end

    own_ac_solve_only = benchmark_call(
        "Own AC OPF solve only";
        runs=ac_runs,
        verbose=verbose
    ) do
        solve_ac_opf_own(
            case;
            optimizer=ipopt_silent,
            silent=true
        )
    end

    pm_ac_end_to_end = benchmark_call(
        "PowerModels AC OPF end-to-end";
        runs=ac_runs,
        verbose=verbose
    ) do
        PowerModels.solve_ac_opf(case_path, ipopt_silent)
    end

    own_ac_end_to_end = benchmark_call(
        "Own AC OPF end-to-end: parse + convert + solve";
        runs=ac_runs,
        verbose=verbose
    ) do
        local c = load_powercase_from_powermodels(case_path)

        solve_ac_opf_own(
            c;
            optimizer=ipopt_silent,
            silent=true
        )
    end

    return OPFSpeedComparisonResult(
        case_name,
        own_dc_solve_only,
        pm_dc_end_to_end,
        own_dc_end_to_end,
        own_ac_solve_only,
        pm_ac_end_to_end,
        own_ac_end_to_end
    )
end

function opf_speed_comparison_as_named_tuple(result::OPFSpeedComparisonResult)
    return (
        case_name = result.case_name,

        own_dc_solve_only_min = result.own_dc_solve_only.min_time,
        own_dc_solve_only_median = result.own_dc_solve_only.median_time,
        own_dc_solve_only_mean = result.own_dc_solve_only.mean_time,

        pm_dc_end_to_end_min = result.pm_dc_end_to_end.min_time,
        pm_dc_end_to_end_median = result.pm_dc_end_to_end.median_time,
        pm_dc_end_to_end_mean = result.pm_dc_end_to_end.mean_time,

        own_dc_end_to_end_min = result.own_dc_end_to_end.min_time,
        own_dc_end_to_end_median = result.own_dc_end_to_end.median_time,
        own_dc_end_to_end_mean = result.own_dc_end_to_end.mean_time,

        own_ac_solve_only_min = result.own_ac_solve_only.min_time,
        own_ac_solve_only_median = result.own_ac_solve_only.median_time,
        own_ac_solve_only_mean = result.own_ac_solve_only.mean_time,

        pm_ac_end_to_end_min = result.pm_ac_end_to_end.min_time,
        pm_ac_end_to_end_median = result.pm_ac_end_to_end.median_time,
        pm_ac_end_to_end_mean = result.pm_ac_end_to_end.mean_time,

        own_ac_end_to_end_min = result.own_ac_end_to_end.min_time,
        own_ac_end_to_end_median = result.own_ac_end_to_end.median_time,
        own_ac_end_to_end_mean = result.own_ac_end_to_end.mean_time
    )
end

function print_opf_speed_summary(result::OPFSpeedComparisonResult)
    println("\n==============================")
    println("OPF SPEED SUMMARY")
    println("==============================")
    println("Case: ", result.case_name)

    print_benchmark_result(result.own_dc_solve_only)
    print_benchmark_result(result.pm_dc_end_to_end)
    print_benchmark_result(result.own_dc_end_to_end)

    print_benchmark_result(result.own_ac_solve_only)
    print_benchmark_result(result.pm_ac_end_to_end)
    print_benchmark_result(result.own_ac_end_to_end)
end
