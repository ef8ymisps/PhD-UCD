# Shared lower-bound invariant and data-quality flag helpers for AC-OPF
# optimality-gap outputs. This file is both:
#
# - reusable from scripts 15/19 via `include(...)`;
# - executable as a refresh utility for already-saved CSVs.

module OptimalityGapQualityFlags

using CSV
using DataFrames
using PowerModels

export BOUND_VIOLATION_REL_TOL,
    is_solved,
    bound_violation_rel_margin,
    lower_bound_violated,
    negative_branch_flags,
    case_flags,
    annotate_soc_gap!,
    annotate_obbt_gap!,
    refresh_existing_results!

const ROOT = normpath(joinpath(@__DIR__, "..", "..", ".."))
const CASE_DIR = joinpath(ROOT, "data", "raw", "pglib", "matpower_m", "base_case")
const GAP_PATH = joinpath(ROOT, "results", "own_julia", "ac_opf_optimality_gap", "optimality_gap_results.csv")
const OBBT_PATH = joinpath(ROOT, "results", "own_julia", "ac_opf_optimality_gap", "optimality_gap_obbt_results.csv")
const NEGATIVE_BRANCH_SUMMARY_PATH = joinpath(ROOT, "results", "own_julia", "data_quality", "negative_branch_impedance_summary.csv")
const BOUND_VIOLATION_REL_TOL = 1e-6

short_case_name(path::String) = replace(replace(basename(path), ".m" => ""), "pglib_opf_case" => "")

function is_solved(status)
    return string(status) in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")
end

function bound_violation_rel_margin(objective, lower_bound, lb_success::Bool=true)
    objective = Float64(objective)
    lower_bound = Float64(lower_bound)
    (!lb_success || isnan(objective) || isnan(lower_bound)) && return NaN
    return (objective - lower_bound) / (1.0 + abs(lower_bound))
end

function lower_bound_violated(objective, lower_bound, lb_success::Bool=true)
    margin = bound_violation_rel_margin(objective, lower_bound, lb_success)
    return !isnan(margin) && margin < -BOUND_VIOLATION_REL_TOL
end

function negative_branch_flags(data)
    count_r = 0
    count_x = 0
    count_any = 0
    for branch in values(data["branch"])
        r = Float64(get(branch, "br_r", NaN))
        x = Float64(get(branch, "br_x", NaN))
        neg_r = !isnan(r) && r < 0.0
        neg_x = !isnan(x) && x < 0.0
        count_r += neg_r ? 1 : 0
        count_x += neg_x ? 1 : 0
        count_any += (neg_r || neg_x) ? 1 : 0
    end
    return (
        has_negative_branch_r=count_r > 0,
        has_negative_branch_x=count_x > 0,
        negative_branch_count=count_any,
    )
end

function case_flags()
    flags = Dict{String, NamedTuple}()
    if isfile(NEGATIVE_BRANCH_SUMMARY_PATH)
        for row in eachrow(DataFrame(CSV.File(NEGATIVE_BRANCH_SUMMARY_PATH)))
            flags[String(row.case_label)] = (
                has_negative_branch_r=Bool(row.has_negative_branch_r),
                has_negative_branch_x=Bool(row.has_negative_branch_x),
                negative_branch_count=Int(row.negative_branch_count),
            )
        end
        return flags
    end

    for path in sort(filter(path -> endswith(path, ".m"), joinpath.(CASE_DIR, readdir(CASE_DIR))))
        flags[short_case_name(path)] = negative_branch_flags(PowerModels.parse_file(path))
    end
    return flags
end

function case_key(label::String, flags)
    for key in sort(collect(keys(flags)); by=length, rev=true)
        startswith(label, key) && return key
    end
    return replace(label, r" \(base\)$" => "")
end

function add_branch_flag_columns!(df::DataFrame, label_col::Symbol, flags)
    has_r = Bool[]
    has_x = Bool[]
    counts = Int[]
    for row in eachrow(df)
        key = case_key(String(row[label_col]), flags)
        flag = get(flags, key, (has_negative_branch_r=false, has_negative_branch_x=false, negative_branch_count=0))
        push!(has_r, flag.has_negative_branch_r)
        push!(has_x, flag.has_negative_branch_x)
        push!(counts, flag.negative_branch_count)
    end
    df.has_negative_branch_r = has_r
    df.has_negative_branch_x = has_x
    df.negative_branch_count = counts
    return df
end

function annotate_soc_gap!(df::DataFrame; flags=case_flags())
    margins = Float64[]
    violations = Bool[]
    for row in eachrow(df)
        lb_success = is_solved(row.soc_status)
        margin = bound_violation_rel_margin(row.objective, row.soc_lower_bound, lb_success)
        push!(margins, margin)
        push!(violations, !isnan(margin) && margin < -BOUND_VIOLATION_REL_TOL)
    end
    df.bound_violated = violations
    df.bound_violation_rel_margin = margins
    add_branch_flag_columns!(df, :case_label, flags)
    return df
end

function annotate_obbt_gap!(df::DataFrame; flags=case_flags())
    margin_initial = Float64[]
    margin_final = Float64[]
    violated_initial = Bool[]
    violated_final = Bool[]
    for row in eachrow(df)
        mi = bound_violation_rel_margin(row.ac_objective, row.obbt_initial_lb)
        mf = bound_violation_rel_margin(row.ac_objective, row.obbt_final_lb)
        push!(margin_initial, mi)
        push!(margin_final, mf)
        push!(violated_initial, !isnan(mi) && mi < -BOUND_VIOLATION_REL_TOL)
        push!(violated_final, !isnan(mf) && mf < -BOUND_VIOLATION_REL_TOL)
    end
    df.bound_violated_initial = violated_initial
    df.bound_violated_final = violated_final
    df.bound_violation_rel_margin_initial = margin_initial
    df.bound_violation_rel_margin_final = margin_final
    add_branch_flag_columns!(df, :case_label, flags)
    return df
end

function refresh_gap!(flags)
    if !isfile(GAP_PATH)
        println("Skipping missing $(GAP_PATH)")
        return
    end
    df = DataFrame(CSV.File(GAP_PATH))
    annotate_soc_gap!(df; flags=flags)
    CSV.write(GAP_PATH, df)
    println("Refreshed $(GAP_PATH)")
end

function refresh_obbt!(flags)
    if !isfile(OBBT_PATH)
        println("Skipping missing $(OBBT_PATH)")
        return
    end
    df = DataFrame(CSV.File(OBBT_PATH))
    annotate_obbt_gap!(df; flags=flags)
    CSV.write(OBBT_PATH, df)
    println("Refreshed $(OBBT_PATH)")
end

function refresh_existing_results!()
    flags = case_flags()
    refresh_gap!(flags)
    refresh_obbt!(flags)
end

function main()
    refresh_existing_results!()
end

end # module

if abspath(PROGRAM_FILE) == @__FILE__
    using .OptimalityGapQualityFlags
    OptimalityGapQualityFlags.main()
end
