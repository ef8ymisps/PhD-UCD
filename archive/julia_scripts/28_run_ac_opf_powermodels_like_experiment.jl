# Compare the current Julia AC OPF model against the experimental
# PowerModels-like variant.
#
# This script is intentionally opt-in and is not used by the validation sweep.
# By default it runs only case5_pjm/base as a smoke test.  Use --profile=bigger
# for larger base + stressed cases, or pass PGLib case stems explicitly, for
# example:
#
#   julia --project=julia julia/own_models/scripts/28_run_ac_opf_powermodels_like_experiment.jl pglib_opf_case1888_rte
#   julia --project=julia julia/own_models/scripts/28_run_ac_opf_powermodels_like_experiment.jl --profile=bigger
#   julia --project=julia julia/own_models/scripts/28_run_ac_opf_powermodels_like_experiment.jl --output-name=rte_stress.csv pglib_opf_case1888_rte --scenarios=base,qstress_x1.2

include(joinpath(@__DIR__, "..", "src", "OwnPowerModels.jl"))
using .OwnPowerModels
using CSV
using DataFrames
using Ipopt
using JuMP
using PowerModels

PowerModels.silence()

const ROOT = normpath(joinpath(@__DIR__, "..", "..", ".."))
const CASE_DIR = joinpath(ROOT, "data", "raw", "pglib", "matpower_m", "base_case")
const DEFAULT_CASE_FILES = ["pglib_opf_case5_pjm"]
const BIGGER_CASE_FILES = [
    "pglib_opf_case118_ieee",
    "pglib_opf_case300_ieee",
    "pglib_opf_case1354_pegase",
    "pglib_opf_case1888_rte",
]
const DEFAULT_SCENARIOS = ["base"]
const STRESS_SCENARIOS = ["base", "load_x1.2", "qstress_x1.2", "qg_limits_x0.8", "angle_20deg"]

function arg_value(name::String, default::String)
    prefix = name * "="
    for arg in ARGS
        if startswith(arg, prefix)
            return arg[length(prefix) + 1:end]
        end
    end
    return default
end

function split_csv_arg(text::String)
    return [String(strip(item)) for item in split(text, ",") if !isempty(strip(item))]
end

function positional_case_args()
    return [arg for arg in ARGS if !startswith(arg, "--")]
end

function short_case_name(case_file::String)
    stem = replace(case_file, ".m" => "")
    return replace(stem, "pglib_opf_case" => "")
end

function build_ac_optimizer()
    return optimizer_with_attributes(
        Ipopt.Optimizer,
        "print_level" => 0,
        "max_iter" => 3000,
        "tol" => 1e-8,
        "acceptable_tol" => 1e-6,
        "constr_viol_tol" => 1e-4,
        "compl_inf_tol" => 1e-4,
        "dual_inf_tol" => 1.0,
    )
end

function solved_status(status)
    return string(status) in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")
end

function scale_loads!(data, multiplier::Float64; scale_p::Bool, scale_q::Bool)
    for load in values(data["load"])
        if scale_p
            load["pd"] = Float64(load["pd"]) * multiplier
        end
        if scale_q
            load["qd"] = Float64(load["qd"]) * multiplier
        end
    end
end

function tighten_qg_limits!(data, multiplier::Float64)
    for gen in values(data["gen"])
        if Int(get(gen, "gen_status", 1)) != 1
            continue
        end
        qmin = Float64(gen["qmin"])
        qmax = Float64(gen["qmax"])
        center = clamp(Float64(gen["qg"]), qmin, qmax)
        lower_width = max(0.0, center - qmin) * multiplier
        upper_width = max(0.0, qmax - center) * multiplier
        gen["qmin"] = center - lower_width
        gen["qmax"] = center + upper_width
        gen["qg"] = center
    end
end

function set_system_angle_limit!(data, angle_limit_deg::Float64)
    limit = deg2rad(angle_limit_deg)
    for branch in values(data["branch"])
        branch["angmin"] = -limit
        branch["angmax"] = limit
    end
end

function scenario_data(base_data, scenario::String)
    data = deepcopy(base_data)
    if scenario == "base"
        return data
    elseif scenario == "load_x1.2"
        scale_loads!(data, 1.2; scale_p=true, scale_q=true)
    elseif scenario == "load_x0.8"
        scale_loads!(data, 0.8; scale_p=true, scale_q=true)
    elseif scenario == "qstress_x1.2"
        scale_loads!(data, 1.2; scale_p=false, scale_q=true)
    elseif scenario == "qstress_x1.4"
        scale_loads!(data, 1.4; scale_p=false, scale_q=true)
    elseif scenario == "qg_limits_x0.8"
        tighten_qg_limits!(data, 0.8)
    elseif scenario == "qg_limits_x0.6"
        tighten_qg_limits!(data, 0.6)
    elseif scenario == "angle_20deg"
        set_system_angle_limit!(data, 20.0)
    elseif scenario == "angle_10deg"
        set_system_angle_limit!(data, 10.0)
    else
        error("Unsupported scenario $(scenario).")
    end
    return data
end

function solve_one(label::String, solver_fn, case::PowerCase, optimizer; kwargs...)
    elapsed = @elapsed result = solver_fn(case; optimizer=optimizer, silent=true, fix_reference_angle=true, kwargs...)
    return (
        variant=label,
        result=result,
        success=solved_status(result.termination_status),
        status=string(result.termination_status),
        objective=result.objective,
        iterations=result.solve_iterations,
        solve_time_sec=elapsed,
    )
end

function solve_pm(data, optimizer)
    pm = PowerModels.instantiate_model(deepcopy(data), ACPPowerModel, PowerModels.build_opf)
    elapsed = @elapsed pm_result = PowerModels.optimize_model!(pm, optimizer=optimizer)
    status = string(get(pm_result, "termination_status", "UNKNOWN"))
    return (
        variant="powermodels",
        result=pm_result,
        success=solved_status(status),
        status=status,
        objective=Float64(get(pm_result, "objective", NaN)),
        iterations=OwnPowerModels._safe_barrier_iterations(pm.model),
        solve_time_sec=elapsed,
    )
end

function pm_relative_metrics(case::PowerCase, own_row, pm_row)
    obj_diff = own_row.objective - pm_row.objective
    obj_rel = isfinite(obj_diff) && isfinite(pm_row.objective) ? abs(obj_diff) / (1.0 + abs(pm_row.objective)) : NaN
    iter_ratio = isfinite(own_row.iterations) && isfinite(pm_row.iterations) && pm_row.iterations > 0.0 ? own_row.iterations / pm_row.iterations : NaN
    time_ratio = isfinite(own_row.solve_time_sec) && isfinite(pm_row.solve_time_sec) && pm_row.solve_time_sec > 0.0 ? own_row.solve_time_sec / pm_row.solve_time_sec : NaN

    if own_row.success && pm_row.success
        summary = ac_opf_summary_row(case, own_row.result, pm_row.result; scenario="")
        return (
            objective_diff_vs_pm=obj_diff,
            objective_rel_diff_vs_pm=obj_rel,
            iteration_ratio_vs_pm=iter_ratio,
            time_ratio_vs_pm=time_ratio,
            max_pg_diff_vs_pm=Float64(summary.max_pg_diff),
            max_qg_diff_vs_pm=Float64(summary.max_qg_diff),
            max_vm_diff_vs_pm=Float64(summary.max_vm_diff),
            max_va_diff_vs_pm=Float64(summary.max_va_diff),
            max_pf_diff_vs_pm=Float64(summary.max_pf_diff),
            max_qf_diff_vs_pm=Float64(summary.max_qf_diff),
            max_loading_pct_diff_vs_pm=Float64(summary.max_loading_pct_diff),
        )
    end

    return (
        objective_diff_vs_pm=obj_diff,
        objective_rel_diff_vs_pm=obj_rel,
        iteration_ratio_vs_pm=iter_ratio,
        time_ratio_vs_pm=time_ratio,
        max_pg_diff_vs_pm=NaN,
        max_qg_diff_vs_pm=NaN,
        max_vm_diff_vs_pm=NaN,
        max_va_diff_vs_pm=NaN,
        max_pf_diff_vs_pm=NaN,
        max_qf_diff_vs_pm=NaN,
        max_loading_pct_diff_vs_pm=NaN,
    )
end

function run_case_scenario(case_file::String, scenario::String, optimizer)
    base_data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
    data = scenario_data(base_data, scenario)
    case = OwnPowerModels.powermodels_to_powercase(deepcopy(data))

    println("  PowerModels reference ...")
    pm_row = solve_pm(data, optimizer)

    rows = [
        solve_one("own_pm_like_zero_start", solve_ac_opf_own, case, optimizer; warm_start=:zero),
        solve_one(
            "own_pm_like_zero_start_angle_dedup",
            solve_ac_opf_own,
            case,
            optimizer;
            warm_start=:zero,
            deduplicate_angle_constraints=true,
        ),
        pm_row,
    ]

    output_rows = NamedTuple[]
    for row in rows
        metrics = row.variant == "powermodels" ? (
            objective_diff_vs_pm=0.0,
            objective_rel_diff_vs_pm=0.0,
            iteration_ratio_vs_pm=1.0,
            time_ratio_vs_pm=1.0,
            max_pg_diff_vs_pm=0.0,
            max_qg_diff_vs_pm=0.0,
            max_vm_diff_vs_pm=0.0,
            max_va_diff_vs_pm=0.0,
            max_pf_diff_vs_pm=0.0,
            max_qf_diff_vs_pm=0.0,
            max_loading_pct_diff_vs_pm=0.0,
        ) : pm_relative_metrics(case, row, pm_row)

        push!(output_rows, merge((
            case_file=case_file,
            case_label=short_case_name(case_file),
            scenario=scenario,
            variant=row.variant,
            success=row.success,
            status=row.status,
            objective=row.objective,
            iterations=row.iterations,
            solve_time_sec=row.solve_time_sec,
        ), metrics))
    end

    return DataFrame(output_rows)
end

function selected_cases_and_scenarios()
    profile = lowercase(arg_value("--profile", "smoke"))
    case_args = positional_case_args()
    cases = !isempty(case_args) ? case_args :
        profile == "bigger" ? BIGGER_CASE_FILES :
        profile == "stress" ? BIGGER_CASE_FILES :
        DEFAULT_CASE_FILES

    scenario_arg = arg_value("--scenarios", "")
    scenarios = !isempty(scenario_arg) ? split_csv_arg(scenario_arg) :
        profile in ("bigger", "stress") ? STRESS_SCENARIOS :
        DEFAULT_SCENARIOS

    return cases, scenarios
end

function main()
    case_files, scenarios = selected_cases_and_scenarios()
    optimizer = build_ac_optimizer()
    all_rows = DataFrame()

    for case_file in case_files
        for scenario in scenarios
            println("Running $(case_file) | $(scenario) ...")
            append!(all_rows, run_case_scenario(case_file, scenario, optimizer))
            GC.gc()
        end
    end

    output_dir = joinpath(ROOT, "results", "own_julia", "ac_opf_powermodels_like_experiment")
    mkpath(output_dir)
    out_path = joinpath(output_dir, arg_value("--output-name", "powermodels_like_experiment.csv"))
    CSV.write(out_path, all_rows)

    println("Saved: $(out_path)")
    show(stdout, all_rows; allrows=true, allcols=true)
    println()
end

main()
