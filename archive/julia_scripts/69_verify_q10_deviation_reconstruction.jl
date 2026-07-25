# Q10: formal proof that an own-vs-PowerModels objective gap is explained
# entirely by landing on a different (but equally valid) operating point,
# rather than by some other unexplained effect.
#
# For each decision variable entering the objective, x^e* and x^m (own
# model's and PowerModels' already-solved values respectively) are both
# FIXED data -- not being jointly optimized here. The linking equation
#   x^e* + Delta^+ - Delta^- = x^m,  Delta^+, Delta^- >= 0
# therefore has a UNIQUE solution (Delta^+ = max(0, x^m-x^e*), Delta^- =
# max(0, x^e*-x^m)) with no other degree of freedom -- min sum(Delta^+ +
# Delta^-) is solved by a real (trivial) tiny LP here anyway, matching this
# project's established deviation-minimization idiom
# (add_ac_target_deviation_objective!-style signed slacks, not squared
# penalties -- see feedback_deviation_slack_formulation memory), rather than
# hand-computing the closed form, so the check is reproducible the same way
# as any other own-vs-PowerModels diagnostic in this repo.
#
# Reconstructing the OBJECTIVE gap (not just confirming Delta exists) from
# the deviation needs care for generation-cost, since cost is QUADRATIC in
# Pg, not linear:
#   cost(Pg^m) - cost(Pg^e) = c2*((Pg^m)^2-(Pg^e)^2) + c1*(Pg^m-Pg^e)
#                            = (Pg^m-Pg^e) * (c2*(Pg^m+Pg^e) + c1)
# i.e. the exact per-generator "price" to weight the deviation by is the
# SECANT slope c2*(Pg^m+Pg^e)+c1 (equivalently the derivative at the
# midpoint), not the bare linear cost coefficient c1 alone -- using c1 alone
# is only a first-order approximation and leaves a residual for any
# generator with a nonzero c2 and a non-tiny deviation. Both are reported
# below so the difference is visible, not asserted. Active-losses is
# already exactly linear in the arc-flow variables (coefficient 1 for every
# arc), so no secant correction applies there -- the naive and exact
# reconstructions coincide by construction.

const SIXTYEIGHT_SCRIPT_PATH_69 = joinpath(@__DIR__, "68_investigate_1888_rte_cost_mismatch.jl")
let
    src = read(SIXTYEIGHT_SCRIPT_PATH_69, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(SIXTYEIGHT_SCRIPT_PATH_69).")
    Base.include_string(Main, stripped, SIXTYEIGHT_SCRIPT_PATH_69)
end

using JuMP
using Printf

"""Tiny linking LP: x^e* and x^m are both fixed parameters here (already
solved elsewhere); the only decision variables are the signed slacks. Not a
re-optimization of the OPF -- a formal, reproducible statement of the
linking equation, matching this project's established deviation-slack
convention."""
function solve_linking_lp_69(values_e::Dict{String,Float64}, values_m::Dict{String,Float64})
    keys_common = collect(keys(values_e))
    model = Model(Ipopt.Optimizer)
    set_silent(model)
    @variable(model, dplus[keys_common] >= 0)
    @variable(model, dminus[keys_common] >= 0)
    @constraint(model, [k in keys_common], values_e[k] + dplus[k] - dminus[k] == values_m[k])
    @objective(model, Min, sum(dplus[k] + dminus[k] for k in keys_common))
    optimize!(model)
    return Dict(k => value(dplus[k]) - value(dminus[k]) for k in keys_common), Dict(k => value(dplus[k]) + value(dminus[k]) for k in keys_common)
end

function q10_cost_scenario(label::String, match_substr::String)
    println("\n" * "="^90)
    println("Q10 (generation-cost): $(label)")
    println("="^90)

    raw_data, case = scenario_data_66(match_substr)
    default_optimizer = build_ac_optimizer()
    pm = solve_pm_cost_full_68(raw_data, default_optimizer)
    own = solve_ac_opf_own(case; optimizer=default_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:generation_cost)

    active_gen_ids = [gen.id for gen in case.generators if gen.status == 1]
    pg_e = Dict(g => own.pg[g] for g in active_gen_ids)
    pg_m = Dict(g => pm.pg[g] for g in active_gen_ids)
    delta, abs_delta = solve_linking_lp_69(pg_e, pg_m)

    total_deviation = sum(values(abs_delta))
    c2 = Dict(gen.id => gen.cost_c2 for gen in case.generators)
    c1 = Dict(gen.id => gen.cost_c1 for gen in case.generators)

    naive_reconstruction = sum(c1[g] * delta[g] for g in active_gen_ids)
    exact_reconstruction = sum((c2[g] * (pg_e[g] + pg_m[g]) + c1[g]) * delta[g] for g in active_gen_ids)
    true_gap = pm.objective - own.objective

    @printf("  total |Pg deviation| (sum Delta+ + Delta-) = %.6f pu\n", total_deviation)
    @printf("  true objective gap (PM - own)                = %.6f\n", true_gap)
    @printf("  naive reconstruction (linear c1 only)         = %.6f  (residual = %.6f)\n", naive_reconstruction, true_gap - naive_reconstruction)
    @printf("  exact reconstruction (secant c2*(Pg^m+Pg^e)+c1) = %.6f  (residual = %.3e)\n", exact_reconstruction, true_gap - exact_reconstruction)
end

function q10_losses_scenario(label::String, match_substr::String, own_optimizer)
    println("\n" * "="^90)
    println("Q10 (active-losses): $(label)")
    println("="^90)

    raw_data, case = scenario_data_66(match_substr)
    default_optimizer = build_ac_optimizer()
    pm = solve_pm_full_66(raw_data, default_optimizer)
    own = solve_ac_opf_own(case; optimizer=own_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:active_losses)

    active_branch_ids = [b.id for b in case.branches if b.status == 1]
    # Two arc-flow decision variables per branch (from-end pf, to-end pt) --
    # the active-losses objective sums both directly, coefficient 1 each,
    # so no secant correction is needed anywhere here.
    flow_e = Dict{String,Float64}()
    flow_m = Dict{String,Float64}()
    for b in active_branch_ids
        flow_e["$(b)_f"] = own.branch_flows[b]["pf"]
        flow_e["$(b)_t"] = own.branch_flows[b]["pt"]
        flow_m["$(b)_f"] = pm.pf[b]
        flow_m["$(b)_t"] = pm.pt[b]
    end
    delta, abs_delta = solve_linking_lp_69(flow_e, flow_m)

    total_deviation = sum(values(abs_delta))
    reconstruction = sum(values(delta))  # coefficient 1 for every arc
    true_gap = pm.objective - own.objective

    @printf("  total |arc-flow deviation| (sum Delta+ + Delta-) = %.6f pu\n", total_deviation)
    @printf("  true objective gap (PM - own)                     = %.6f\n", true_gap)
    @printf("  reconstruction (coefficient 1 per arc, exact)     = %.6f  (residual = %.3e)\n", reconstruction, true_gap - reconstruction)
end

function main()
    adaptive_optimizer = optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => 3000, "max_cpu_time" => 300.0, "mu_strategy" => "adaptive")
    default_optimizer = build_ac_optimizer()

    q10_cost_scenario("1888_rte_load_x0.8", "load_x0p8__load_scaling")
    q10_cost_scenario("1888_rte_qload_x1.2 (reactive_power_stress)", "load_x1p2__reactive_power_stress")
    q10_cost_scenario("1888_rte_qg_limits_x0.6", "qg_limits_x0p6__reactive_generation_limits")

    q10_losses_scenario("1888_rte_qg_limits_x0.6 (needs mu_strategy=adaptive)", "qg_limits_x0p6", adaptive_optimizer)
    q10_losses_scenario("1888_rte_qg_limits_x0.8", "qg_limits_x0p8", default_optimizer)
end

main()
