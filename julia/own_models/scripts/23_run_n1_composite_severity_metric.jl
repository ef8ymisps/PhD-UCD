# N-1 composite severity metric (4-term, single-slack).
#
# Design decisions from this session's discussion, recorded here so the
# formula isn't re-derived from scratch later:
#
# - Kept a fourth "generator Pg violation" term, reversing an earlier draft
#   that dropped it. Under this pipeline's single-slack Newton-Raphson PF,
#   only the slack bus's Pg is solved for (every other generator's Pg is a
#   fixed, feasibility-checked input from apply_proportional_reserve_balancing,
#   so it structurally can't violate its own limit) -- so S_Pg^c is, in
#   practice, almost entirely a "does this contingency overload the slack"
#   check. That's not a reason to drop it: outage::7/outage::9 push case118's
#   slack (gen 30, pmax=11.82) to ~14.32 pu, a real ~2.5 pu violation -- AND
#   verified to be the *same physical event* as a concrete thermal-congestion
#   signal: 4 of the 6 branches directly touching bus 69 (105, 106, 108, 116)
#   go from under 100% to 126-158% loaded under those same two contingencies.
#   Switching to a distributed-slack PF would smear this out and hide it, but
#   the user chose to keep single slack (avoids re-deriving the base AC-OPF
#   under a distributed convention just to serve this metric) -- so this is
#   a real, single-slack-consistent finding, not an artifact to explain away.
# - Every term is a VIOLATION measure (how far past the element's own
#   physical limit, as a fraction of its own band), not a deviation measure
#   (how far it moved) -- those are different questions. loading_pct already
#   has an implicit "own band" of 100; Vm/Pg/Qg use their own max-min range.
# - Qg (and now Pg) checked across ALL generators, including the slack --
#   Qg is a free/solved variable at every PV bus (not just non-slack), and
#   the worst Qg violation found all session (outage::gen::40, sync
#   condenser gen 43, 631% of its own Q-band) is at a non-slack unit anyway.
# - No re-solving anything: this reuses post_value directly from the
#   existing 126,868-row D2 dataset (results/own_julia/security/
#   n1_case118_variable_impact.csv), joined against the case's own
#   vmin/vmax (bus), pmin/pmax and qmin/qmax (generator) limits.
#
# Formula (per contingency c):
#   S_line^c = max_b   max(0, loading_pct_b - 100) / 100
#   S_V^c    = max_bus max(0, vmin-vm, vm-vmax) / (vmax-vmin)
#   S_Pg^c   = max_g   max(0, pmin-pg, pg-pmax) / (pmax-pmin)
#   S_Qg^c   = max_g   max(0, qmin-qg, qg-qmax) / (qmax-qmin)
#   phi_c    = [S_line^c, S_V^c, S_Pg^c, S_Qg^c]
#   S_c      = w_L*S_line^c + w_V*S_V^c + w_P*S_Pg^c + w_Q*S_Qg^c  (default equal weights)
#
# This is deliberately the cheap, power-flow-only "SI4/S_c-style" candidate
# (raw post-contingency constraint violations), as distinct from SI1-3-style
# indices which need a relaxed-PSCOPF solve's dual/objective information.
# Next step (per user): compare this against SI1-4 and NDC-style dominance
# filtering on phi_c, not just use it standalone.

include(joinpath(@__DIR__, "..", "src", "OwnPowerModels.jl"))

using .OwnPowerModels
using CSV
using DataFrames
using PowerModels
using Statistics: mean

const CASE_FILE = "pglib_opf_case118_ieee"
const ROOT = normpath(joinpath(@__DIR__, "..", "..", ".."))
const CASE_PATH = joinpath(ROOT, "data", "raw", "pglib", "matpower_m", "base_case", "$(CASE_FILE).m")
const INPUT_PATH = joinpath(ROOT, "results", "own_julia", "security", "n1_case118_variable_impact.csv")
const OUTPUT_PATH = joinpath(ROOT, "results", "own_julia", "security", "n1_case118_composite_severity.csv")

const WEIGHTS = (L=1/4, V=1/4, P=1/4, Q=1/4)  # default: equal, assumption-free; tune later against known cases / SI comparison

function main()
    data = PowerModels.parse_file(CASE_PATH)
    case = OwnPowerModels.powermodels_to_powercase(data)

    bus_vmin = Dict(b.id => b.vmin for b in case.buses)
    bus_vmax = Dict(b.id => b.vmax for b in case.buses)
    gen_pmin = Dict(g.id => g.pmin for g in case.generators)
    gen_pmax = Dict(g.id => g.pmax for g in case.generators)
    gen_qmin = Dict(g.id => g.qmin for g in case.generators)
    gen_qmax = Dict(g.id => g.qmax for g in case.generators)

    df = DataFrame(CSV.File(INPUT_PATH))

    line_term = fill(NaN, nrow(df))
    v_term = fill(NaN, nrow(df))
    pg_term = fill(NaN, nrow(df))
    qg_term = fill(NaN, nrow(df))

    for (i, row) in enumerate(eachrow(df))
        if row.affected_type == "branch" && row.variable == "loading_pct"
            line_term[i] = max(0.0, row.post_value - 100.0) / 100.0
        elseif row.affected_type == "bus" && row.variable == "vm"
            vmin = bus_vmin[string(row.affected_id)]
            vmax = bus_vmax[string(row.affected_id)]
            band = vmax - vmin
            v_term[i] = band > 0 ? max(0.0, vmin - row.post_value, row.post_value - vmax) / band : NaN
        elseif row.affected_type == "generator" && row.variable == "pg"
            pmin = gen_pmin[string(row.affected_id)]
            pmax = gen_pmax[string(row.affected_id)]
            band = pmax - pmin
            pg_term[i] = band > 0 ? max(0.0, pmin - row.post_value, row.post_value - pmax) / band : NaN
        elseif row.affected_type == "generator" && row.variable == "qg"
            qmin = gen_qmin[string(row.affected_id)]
            qmax = gen_qmax[string(row.affected_id)]
            band = qmax - qmin
            qg_term[i] = band > 0 ? max(0.0, qmin - row.post_value, row.post_value - qmax) / band : NaN
        end
    end
    df.line_violation = line_term
    df.v_violation = v_term
    df.pg_violation = pg_term
    df.qg_violation = qg_term

    safe_max(x) = (v = collect(skipmissing(x)); v = filter(!isnan, v); isempty(v) ? 0.0 : maximum(v))

    per_contingency = combine(
        groupby(df, [:contingency_id, :contingency_type, :outaged_id]),
        :line_violation => safe_max => :S_line,
        :v_violation => safe_max => :S_V,
        :pg_violation => safe_max => :S_Pg,
        :qg_violation => safe_max => :S_Qg,
    )
    per_contingency.S_c = WEIGHTS.L .* per_contingency.S_line .+ WEIGHTS.V .* per_contingency.S_V .+
                          WEIGHTS.P .* per_contingency.S_Pg .+ WEIGHTS.Q .* per_contingency.S_Qg
    sort!(per_contingency, :S_c; rev=true)

    CSV.write(OUTPUT_PATH, per_contingency)
    println("Saved: $(OUTPUT_PATH)")

    println("\n=== Top 10 by composite S_c ===")
    for row in eachrow(first(per_contingency, 10))
        println("  $(row.contingency_id) ($(row.contingency_type)): S_c=$(round(row.S_c, digits=3))  [S_line=$(round(row.S_line, digits=3)), S_V=$(round(row.S_V, digits=3)), S_Pg=$(round(row.S_Pg, digits=3)), S_Qg=$(round(row.S_Qg, digits=3))]")
    end

    println("\n=== By contingency type: mean of each component ===")
    by_type = combine(groupby(per_contingency, :contingency_type), :S_line => mean, :S_V => mean, :S_Pg => mean, :S_Qg => mean, :S_c => mean)
    for row in eachrow(by_type)
        println("  $(row.contingency_type): S_line=$(round(row.S_line_mean, digits=4)) S_V=$(round(row.S_V_mean, digits=4)) S_Pg=$(round(row.S_Pg_mean, digits=4)) S_Qg=$(round(row.S_Qg_mean, digits=4)) S_c=$(round(row.S_c_mean, digits=4))")
    end

    n_pg_violations = count(x -> !ismissing(x) && !isnan(x) && x > 0, pg_term)
    n_qg_violations = count(x -> !ismissing(x) && !isnan(x) && x > 0, qg_term)
    println("\nRows with a nonzero Pg violation: $(n_pg_violations); nonzero Qg violation: $(n_qg_violations)")
end

main()
