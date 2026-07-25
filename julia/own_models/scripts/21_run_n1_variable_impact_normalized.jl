# D2 follow-up: normalize each variable's delta by its own physical operating
# band, not just raw units.
#
# 14_run_n1_variable_impact_analysis.jl's summary table compares Δpg/Δqg (pu,
# scaled by system MVA base, not by that generator's own capacity), Δvm (raw
# pu deviation, not scaled by that bus's own voltage band), Δva (radians, no
# per-element bound at all) directly against Δloading_pct -- which, unlike
# the others, IS already normalized (100*|S|/rate_a, i.e. already a % of that
# branch's own thermal limit; see branch_loading_pct in script 14). Comparing
# a normalized quantity against three raw ones and concluding "loading is
# where severity lives" is an apples-to-oranges comparison -- flagged by the
# user after inspecting n1_variable_impact_report.html.
#
# This script re-expresses every row of the existing 126,868-row dataset
# (results/own_julia/security/n1_case118_variable_impact.csv, no new power
# flows run) as "% of this element's own operating band consumed":
#   - generator pg -> abs_delta / (pmax - pmin)
#   - generator qg -> abs_delta / (qmax - qmin)
#   - bus vm       -> abs_delta / (vmax - vmin)
#   - bus va       -> abs_delta / (case-wide branch angmax-angmin band, in
#                      rad). NOTE this is an approximation, not a true
#                      per-element bound: bus voltage angle itself isn't
#                      directly limited in the AC-OPF/PF formulation used
#                      here -- only branch angle *differences* are
#                      (angmin/angmax), and that's a branch property, not a
#                      bus property. Confirmed case118_ieee uses a uniform
#                      +-30 deg (0.5236 rad) band on every one of its 186
#                      branches, so using that single constant as a network-
#                      wide reference is reasonable here, but would need
#                      re-deriving (e.g. per-branch, or dropped) on a case
#                      with non-uniform angle limits.
#   - branch loading_pct -> already a %, used as-is (no rescale needed).
# All four are then on a common "% of own allowed range" scale.

include(joinpath(@__DIR__, "..", "src", "OwnPowerModels.jl"))

using .OwnPowerModels
using CSV
using DataFrames
using PowerModels
using Statistics: mean, median

const CASE_FILE = "pglib_opf_case118_ieee"
const ROOT = normpath(joinpath(@__DIR__, "..", "..", ".."))
const CASE_PATH = joinpath(ROOT, "data", "raw", "pglib", "matpower_m", "base_case", "$(CASE_FILE).m")
const INPUT_PATH = joinpath(ROOT, "results", "own_julia", "security", "n1_case118_variable_impact.csv")
const OUTPUT_PATH = joinpath(ROOT, "results", "own_julia", "security", "n1_case118_variable_impact_normalized.csv")

function main()
    data = PowerModels.parse_file(CASE_PATH)
    case = OwnPowerModels.powermodels_to_powercase(data)

    gen_band_pg = Dict{String,Float64}()
    gen_band_qg = Dict{String,Float64}()
    for g in case.generators
        gen_band_pg[g.id] = g.pmax - g.pmin
        gen_band_qg[g.id] = g.qmax - g.qmin
    end

    bus_band_vm = Dict{String,Float64}()
    for b in case.buses
        bus_band_vm[b.id] = b.vmax - b.vmin
    end

    angle_bands = unique(round(b.angmax - b.angmin; digits=6) for b in case.branches)
    length(angle_bands) == 1 || @warn "Branch angle bands are not uniform across case118 -- va normalization below uses the single value $(angle_bands[1]) rad anyway; treat as an approximation" angle_bands
    va_band = angle_bands[1]
    println("Using bus-Va normalization reference: $(va_band) rad (case-wide branch angmax-angmin; see script docstring for why this is an approximation, not a strict per-bus bound)")

    df = DataFrame(CSV.File(INPUT_PATH))

    own_band = Vector{Union{Missing,Float64}}(undef, nrow(df))
    pct_of_own_band = Vector{Union{Missing,Float64}}(undef, nrow(df))

    for (i, row) in enumerate(eachrow(df))
        band = missing
        if row.affected_type == "generator" && row.variable == "pg"
            band = get(gen_band_pg, string(row.affected_id), missing)
        elseif row.affected_type == "generator" && row.variable == "qg"
            band = get(gen_band_qg, string(row.affected_id), missing)
        elseif row.affected_type == "bus" && row.variable == "vm"
            band = get(bus_band_vm, string(row.affected_id), missing)
        elseif row.affected_type == "bus" && row.variable == "va"
            band = va_band
        elseif row.affected_type == "branch" && row.variable == "loading_pct"
            band = missing  # already a %, handled separately below
        end
        own_band[i] = band

        if row.affected_type == "branch" && row.variable == "loading_pct"
            pct_of_own_band[i] = row.abs_delta  # already % of rate_a
        elseif !ismissing(band) && band > 0
            pct_of_own_band[i] = 100.0 * row.abs_delta / band
        else
            pct_of_own_band[i] = missing
        end
    end

    df.own_band = own_band
    df.pct_of_own_band = pct_of_own_band

    dropped = count(ismissing, pct_of_own_band)
    println("Rows with no valid normalization band (e.g. pmax==pmin fixed units): $(dropped) / $(nrow(df))")

    CSV.write(OUTPUT_PATH, df)
    println("Saved: $(OUTPUT_PATH)")

    println("\n=== Max / mean / median (% of own operating band) by contingency type and variable ===")
    grouped = combine(
        groupby(dropmissing(df, :pct_of_own_band), [:contingency_type, :variable]),
        :pct_of_own_band => maximum => :max_pct,
        :pct_of_own_band => mean => :mean_pct,
        :pct_of_own_band => median => :median_pct,
        nrow => :n,
    )
    sort!(grouped, [:contingency_type, :variable])
    for row in eachrow(grouped)
        println("  $(row.contingency_type) / $(row.variable): max=$(round(row.max_pct, digits=2))%  mean=$(round(row.mean_pct, digits=4))%  median=$(round(row.median_pct, digits=4))%  n=$(row.n)")
    end
end

main()
