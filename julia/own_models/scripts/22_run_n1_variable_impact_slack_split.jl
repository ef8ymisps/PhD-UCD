# D2 follow-up: separate out the slack/reference-bus generator's Pg/Qg
# deltas from every other generator's, for both the raw and the
# band-normalized (script 21) datasets.
#
# case118's reference bus is bus 69 (bus_type==3), and its only attached
# generator is gen id "30" (pmax=11.82, pmin=0, qmax=3.0, qmin=-3.0 -- a
# small unit, so its %-of-band deltas normalize to especially large numbers
# even for modest MW/MVAr swings). In a power flow (not OPF), only the slack
# bus's Pg (and, since it's the reference, effectively its Qg too) is solved
# for to close the system's power balance -- every other PV bus's Pg is a
# fixed input. So generator 30's Pg/Qg deltas aren't "redispatch" in the same
# sense as every other generator's -- they're the slack absorbing whatever
# the contingency changed about system losses/flows. Mixing it into the
# generator/pg and generator/qg summary rows (both the original report and
# the normalized follow-up) risks one small, structurally-different unit
# dominating -- or at least muddying -- statistics meant to describe
# "how do generators redispatch under N-1".
#
# This script quantifies exactly how much of the existing summary numbers
# come from gen 30 alone vs everything else, so the report can show both
# "all generators" and "excluding the slack" side by side rather than
# silently picking one.

using CSV
using DataFrames
using Statistics: mean, median

const ROOT = normpath(joinpath(@__DIR__, "..", "..", ".."))
const RAW_PATH = joinpath(ROOT, "results", "own_julia", "security", "n1_case118_variable_impact.csv")
const NORM_PATH = joinpath(ROOT, "results", "own_julia", "security", "n1_case118_variable_impact_normalized.csv")
const SLACK_GEN_ID = 30

function summarize(df, value_col::Symbol, label::String)
    println("\n--- $(label) ---")
    gens = filter(r -> r.affected_type == "generator" && r.variable in ("pg", "qg"), df)
    for split_name in ("all generators", "excluding slack (gen $(SLACK_GEN_ID))")
        subset = split_name == "all generators" ? gens : filter(r -> r.affected_id != SLACK_GEN_ID, gens)
        grouped = combine(
            groupby(dropmissing(subset, value_col), [:contingency_type, :variable]),
            value_col => maximum => :max_v,
            value_col => mean => :mean_v,
            value_col => median => :median_v,
            nrow => :n,
        )
        sort!(grouped, [:contingency_type, :variable])
        println("  [$split_name]")
        for row in eachrow(grouped)
            println("    $(row.contingency_type) / $(row.variable): max=$(round(row.max_v, sigdigits=4))  mean=$(round(row.mean_v, sigdigits=4))  median=$(round(row.median_v, sigdigits=4))  n=$(row.n)")
        end
    end

    println("  [slack generator $(SLACK_GEN_ID) alone]")
    slack_only = filter(r -> r.affected_id == SLACK_GEN_ID, gens)
    grouped_slack = combine(
        groupby(dropmissing(slack_only, value_col), [:contingency_type, :variable]),
        value_col => maximum => :max_v,
        value_col => mean => :mean_v,
        value_col => median => :median_v,
        nrow => :n,
    )
    sort!(grouped_slack, [:contingency_type, :variable])
    for row in eachrow(grouped_slack)
        println("    $(row.contingency_type) / $(row.variable): max=$(round(row.max_v, sigdigits=4))  mean=$(round(row.mean_v, sigdigits=4))  median=$(round(row.median_v, sigdigits=4))  n=$(row.n)")
    end
end

function main()
    raw = DataFrame(CSV.File(RAW_PATH))
    norm = DataFrame(CSV.File(NORM_PATH))
    summarize(raw, :abs_delta, "RAW |delta| (pu)")
    summarize(norm, :pct_of_own_band, "NORMALIZED (% of own band)")
end

main()
