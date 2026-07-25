# Summarize PM-like AC OPF experiment outputs into comparison tables.
#
# Reads all powermodels_like_experiment*.csv files under the experiment
# results directory and writes:
#   - powermodels_like_experiment_all_rows.csv
#   - powermodels_like_experiment_approach_summary.csv
#   - powermodels_like_experiment_case_scenario_summary.csv

using CSV
using DataFrames
using Statistics

const ROOT = normpath(joinpath(@__DIR__, "..", "..", ".."))
const INPUT_DIR = joinpath(ROOT, "results", "own_julia", "ac_opf_powermodels_like_experiment")

function finite_values(values)
    return [Float64(v) for v in values if !ismissing(v) && isfinite(Float64(v))]
end

function mean_or_nan(values)
    vals = finite_values(values)
    return isempty(vals) ? NaN : mean(vals)
end

function median_or_nan(values)
    vals = finite_values(values)
    return isempty(vals) ? NaN : median(vals)
end

function max_or_nan(values)
    vals = finite_values(values)
    return isempty(vals) ? NaN : maximum(vals)
end

function read_experiment_files()
    files = sort([
        joinpath(INPUT_DIR, name)
        for name in readdir(INPUT_DIR)
        if startswith(name, "powermodels_like_experiment") && endswith(name, ".csv") &&
           !(occursin("_all_rows", name) || occursin("_approach_summary", name) || occursin("_case_scenario_summary", name))
    ])
    isempty(files) && error("No experiment CSV files found in $(INPUT_DIR).")

    frames = DataFrame[]
    for file in files
        df = DataFrame(CSV.File(file))
        df.source_file .= basename(file)
        push!(frames, df)
    end
    return vcat(frames...; cols=:union)
end

function best_variant_per_case_scenario(df::DataFrame)
    rows = NamedTuple[]
    for group in groupby(df, [:case_label, :scenario])
        pm = filter(row -> row.variant == "powermodels", group)
        solved = filter(row -> row.variant != "powermodels" && row.success == true, group)
        if isempty(pm) || isempty(solved)
            continue
        end

        by_iter = sort(solved, :iterations)
        by_resemblance = sort(solved, [:objective_rel_diff_vs_pm, :max_pg_diff_vs_pm, :max_pf_diff_vs_pm])
        push!(rows, (
            case_label=group.case_label[1],
            scenario=group.scenario[1],
            pm_iterations=pm.iterations[1],
            pm_time_sec=pm.solve_time_sec[1],
            fastest_variant=by_iter.variant[1],
            fastest_iterations=by_iter.iterations[1],
            fastest_time_sec=by_iter.solve_time_sec[1],
            fastest_iteration_ratio_vs_pm=by_iter.iteration_ratio_vs_pm[1],
            closest_variant=by_resemblance.variant[1],
            closest_objective_rel_diff_vs_pm=by_resemblance.objective_rel_diff_vs_pm[1],
            closest_max_pg_diff_vs_pm=by_resemblance.max_pg_diff_vs_pm[1],
            closest_max_pf_diff_vs_pm=by_resemblance.max_pf_diff_vs_pm[1],
        ))
    end
    return DataFrame(rows)
end

function summarize_by_variant(df::DataFrame)
    rows = NamedTuple[]
    for group in groupby(df, :variant)
        push!(rows, (
            variant=group.variant[1],
            n_rows=nrow(group),
            n_success=count(group.success .== true),
            success_rate=count(group.success .== true) / nrow(group),
            mean_iterations=mean_or_nan(group.iterations),
            median_iterations=median_or_nan(group.iterations),
            mean_time_sec=mean_or_nan(group.solve_time_sec),
            median_time_sec=median_or_nan(group.solve_time_sec),
            mean_iteration_ratio_vs_pm=mean_or_nan(group.iteration_ratio_vs_pm),
            median_iteration_ratio_vs_pm=median_or_nan(group.iteration_ratio_vs_pm),
            mean_time_ratio_vs_pm=mean_or_nan(group.time_ratio_vs_pm),
            median_time_ratio_vs_pm=median_or_nan(group.time_ratio_vs_pm),
            max_objective_rel_diff_vs_pm=max_or_nan(group.objective_rel_diff_vs_pm),
            max_pg_diff_vs_pm=max_or_nan(group.max_pg_diff_vs_pm),
            max_pf_diff_vs_pm=max_or_nan(group.max_pf_diff_vs_pm),
        ))
    end
    return sort(DataFrame(rows), :variant)
end

function main()
    df = read_experiment_files()
    all_rows_path = joinpath(INPUT_DIR, "powermodels_like_experiment_all_rows.csv")
    approach_summary_path = joinpath(INPUT_DIR, "powermodels_like_experiment_approach_summary.csv")
    case_summary_path = joinpath(INPUT_DIR, "powermodels_like_experiment_case_scenario_summary.csv")

    CSV.write(all_rows_path, df)
    CSV.write(approach_summary_path, summarize_by_variant(df))
    CSV.write(case_summary_path, best_variant_per_case_scenario(df))

    println("Saved: $(all_rows_path)")
    println("Saved: $(approach_summary_path)")
    println("Saved: $(case_summary_path)")
end

main()
