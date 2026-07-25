# N-1 severity-metric research: aggregate D2's per-element PowerModels-PF
# deltas to per-contingency level, join with the existing N-1 screening
# summary, and examine how Pg/Qg/Vm/Va/branch-flow changes relate to each
# other and to severity -- before proposing a new composite severity
# metric grounded in what's actually found.
#
# Both inputs already exist and use PowerModels.jl's compute_ac_pf (fixed
# 2026-07-12 for island handling -- see docs/model_development_and_validation_status.md
# section 7 item 9), so this script does no new solves, purely analysis:
#   - results/own_julia/security/n1_julia_compute_pf_case118_ieee_contingencies.csv
#     (10_run_n1_case118.jl): one row per contingency, with the existing
#     max_loading_pct/voltage-violation/severity_score fields.
#   - results/own_julia/security/n1_case118_variable_impact.csv
#     (14_run_n1_variable_impact_analysis.jl / D2): one row per
#     (contingency, affected element, variable), the Pg/Qg/Vm/Va/loading_pct
#     deltas vs the pre-contingency AC-OPF solution.
#
# D2's own report only computed max/mean/median per (contingency_type,
# variable) *across the whole screen* -- this aggregates D2's rows to one
# row *per contingency* (max/sum |delta| per variable), which is the
# actual level severity metrics operate at, then correlates those against
# each other and against the existing severity_score to see which
# variables move together and which best predict/explain severity.

include(joinpath(@__DIR__, "..", "src", "OwnPowerModels.jl"))
using .OwnPowerModels
using CSV
using DataFrames
using Statistics: mean, median, cor

const SECURITY_DIR = joinpath(@__DIR__, "..", "..", "..", "results", "own_julia", "security")

function load_screening_summary()
    path = joinpath(SECURITY_DIR, "n1_julia_compute_pf_case118_ieee_contingencies.csv")
    return CSV.read(path, DataFrame)
end

function load_variable_impact()
    path = joinpath(SECURITY_DIR, "n1_case118_variable_impact.csv")
    return CSV.read(path, DataFrame)
end

"""One row per contingency: max/sum |delta| for each of pg/qg/vm/va, from
D2's per-element rows. loading_pct is excluded here -- the screening
summary already carries max_loading_pct/n_overloaded_branches, computed
directly by n1_screening.jl from the same post-contingency solve, so
re-deriving it from D2's rows would be redundant, not a cross-check."""
function aggregate_per_contingency(impact::DataFrame)
    rows = NamedTuple[]
    for sub in groupby(impact, :contingency_id)
        cid = sub.contingency_id[1]
        agg = Dict{String, NamedTuple}()
        for var in ("pg", "qg", "vm", "va")
            vals = sub[sub.variable .== var, :abs_delta]
            agg[var] = isempty(vals) ? (max=0.0, sum=0.0, n=0) : (max=maximum(vals), sum=sum(vals), n=length(vals))
        end
        push!(rows, (
            contingency_id=cid,
            max_abs_dpg=agg["pg"].max, sum_abs_dpg=agg["pg"].sum, n_pg=agg["pg"].n,
            max_abs_dqg=agg["qg"].max, sum_abs_dqg=agg["qg"].sum, n_qg=agg["qg"].n,
            max_abs_dvm=agg["vm"].max, sum_abs_dvm=agg["vm"].sum, n_vm=agg["vm"].n,
            max_abs_dva=agg["va"].max, sum_abs_dva=agg["va"].sum, n_va=agg["va"].n,
        ))
    end
    return DataFrame(rows)
end

"""Pearson correlation over rows where both columns are finite and the
screen converged -- NaN placeholders (non-convergent contingencies) would
otherwise poison the whole column."""
function safe_cor(df::DataFrame, a::Symbol, b::Symbol)
    mask = df.converged .& isfinite.(df[!, a]) .& isfinite.(df[!, b])
    n = count(mask)
    n < 3 && return (r=NaN, n=n)
    return (r=cor(df[mask, a], df[mask, b]), n=n)
end

function main()
    screening = load_screening_summary()
    impact = load_variable_impact()

    per_contingency = aggregate_per_contingency(impact)
    joined = leftjoin(screening, per_contingency; on=:contingency_id)
    for col in (:max_abs_dpg, :sum_abs_dpg, :max_abs_dqg, :sum_abs_dqg, :max_abs_dvm, :sum_abs_dvm, :max_abs_dva, :sum_abs_dva)
        joined[!, col] = coalesce.(joined[!, col], 0.0)
    end

    out_path = joinpath(SECURITY_DIR, "n1_case118_severity_research_dataset.csv")
    CSV.write(out_path, joined)
    println("Saved: $(out_path) ($(nrow(joined)) rows)")

    converged = joined[joined.converged .& .!ismissing.(joined.severity_score) .& isfinite.(joined.severity_score), :]
    println("\n$(nrow(converged))/$(nrow(joined)) contingencies converged with a usable severity_score.")

    println("\n--- Correlations against the existing severity_score ---")
    pairs = [
        (:max_loading_pct, "max branch loading %"),
        (:total_voltage_violation_pu, "total voltage violation (pu)"),
        (:max_abs_dpg, "max |ΔPg| (pu)"),
        (:sum_abs_dpg, "sum |ΔPg| (pu)"),
        (:max_abs_dqg, "max |ΔQg| (pu)"),
        (:sum_abs_dqg, "sum |ΔQg| (pu)"),
        (:max_abs_dvm, "max |ΔVm| (pu)"),
        (:sum_abs_dvm, "sum |ΔVm| (pu)"),
        (:max_abs_dva, "max |ΔVa| (rad)"),
        (:sum_abs_dva, "sum |ΔVa| (rad)"),
    ]
    for (col, label) in pairs
        r = safe_cor(converged, :severity_score, col)
        println("  severity_score vs $(rpad(label, 32)) r=$(round(r.r, digits=3))  (n=$(r.n))")
    end

    println("\n--- Correlations among the variable-change aggregates themselves ---")
    cross_pairs = [
        (:max_loading_pct, :max_abs_dvm, "max loading % vs max |ΔVm|"),
        (:max_loading_pct, :max_abs_dqg, "max loading % vs max |ΔQg|"),
        (:max_abs_dqg, :max_abs_dvm, "max |ΔQg| vs max |ΔVm|"),
        (:max_abs_dpg, :max_loading_pct, "max |ΔPg| vs max loading %"),
        (:max_abs_dva, :max_loading_pct, "max |ΔVa| vs max loading %"),
        (:n_voltage_violations, :max_abs_dqg, "n_voltage_violations vs max |ΔQg|"),
    ]
    for (a, b, label) in cross_pairs
        r = safe_cor(converged, a, b)
        println("  $(rpad(label, 42)) r=$(round(r.r, digits=3))  (n=$(r.n))")
    end

    println("\n--- By contingency type (branch vs generator) ---")
    for ctype in unique(converged.element_type)
        sub = converged[converged.element_type .== ctype, :]
        println("  $(ctype) (n=$(nrow(sub))):")
        println("    mean severity_score=$(round(mean(sub.severity_score), digits=5))  median=$(round(median(sub.severity_score), digits=5))")
        println("    mean max|ΔPg|=$(round(mean(sub.max_abs_dpg), digits=5))  mean max|ΔQg|=$(round(mean(sub.max_abs_dqg), digits=5))")
        println("    mean max|ΔVm|=$(round(mean(sub.max_abs_dvm), digits=5))  mean max|ΔVa|=$(round(mean(sub.max_abs_dva), digits=5))")
    end

    println("\n--- Top 10 by existing severity_score ---")
    top = sort(converged, :severity_score; rev=true)[1:10, [:contingency_id, :element_type, :max_loading_pct, :total_voltage_violation_pu, :max_abs_dqg, :max_abs_dvm, :severity_score]]
    show(stdout, top; allrows=true, allcols=true)
    println()
end

main()
