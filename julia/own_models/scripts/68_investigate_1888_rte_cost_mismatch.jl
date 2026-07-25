# Applies the same Q1-Q9 diagnostic ladder as
# 66_investigate_1888_rte_active_losses_improvement.jl /
# 67_rerun_q9_apples_to_apples.jl, this time to the three generation-cost
# scenarios where PowerModels converges to a BETTER (lower) objective than
# the own model: 1888_rte_load_x0.8 (PM better by 7.22%),
# 1888_rte_qload_x1.2 [reactive_power_stress] (PM better by 0.24%), and
# 1888_rte_qg_limits_x0.6 (PM better by 0.027%, Julia only -- Python's own
# generation-cost solve already matches PM closely for this scenario).
#
# Reuses scenario_data_66/q1_q2_compare/q3_equality_at_pm/
# q4_q5_q6_inequalities/q7_q8_loadflow verbatim (all objective-agnostic --
# they only compare two already-solved points) from script 66; only the
# solve calls themselves change (generation-cost objective, PowerModels'
# own default build_opf objective instead of the custom loss-objective
# override).

const SIXTYSIX_SCRIPT_PATH_68 = joinpath(@__DIR__, "66_investigate_1888_rte_active_losses_improvement.jl")
let
    src = read(SIXTYSIX_SCRIPT_PATH_68, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(SIXTYSIX_SCRIPT_PATH_68).")
    Base.include_string(Main, stripped, SIXTYSIX_SCRIPT_PATH_68)
end

function solve_pm_cost_full_68(raw_data, optimizer)
    pm = PowerModels.instantiate_model(deepcopy(raw_data), PowerModels.ACPPowerModel, PowerModels.build_opf)
    result = PowerModels.optimize_model!(pm, optimizer=optimizer)
    sol = result["solution"]
    return (
        status=string(get(result, "termination_status", "UNKNOWN")),
        objective=Float64(get(result, "objective", NaN)),
        pg=Dict(string(k) => Float64(v["pg"]) for (k, v) in sol["gen"]),
        qg=Dict(string(k) => Float64(v["qg"]) for (k, v) in sol["gen"]),
        vm=Dict(string(k) => Float64(v["vm"]) for (k, v) in sol["bus"]),
        va=Dict(string(k) => Float64(v["va"]) for (k, v) in sol["bus"]),
        pf=Dict(string(k) => Float64(v["pf"]) for (k, v) in sol["branch"]),
        qf=Dict(string(k) => Float64(v["qf"]) for (k, v) in sol["branch"]),
        pt=Dict(string(k) => Float64(v["pt"]) for (k, v) in sol["branch"]),
        qt=Dict(string(k) => Float64(v["qt"]) for (k, v) in sol["branch"]),
    )
end

function q9_no_thermal_cost_68(raw_data, default_optimizer)
    relaxed_data = deepcopy(raw_data)
    for (_, branch) in relaxed_data["branch"]
        branch["rate_a"] = HUGE_RATE_A_66
    end
    relaxed_case = OwnPowerModels.powermodels_to_powercase(deepcopy(relaxed_data))
    pm_relaxed = solve_pm_cost_full_68(relaxed_data, default_optimizer)

    println("\n--- Q9: thermal relaxed identically on both sides (rate_a=1e6), generation-cost objective ---")
    @printf("  PM (thermal relaxed rate_a=1e6): status=%s objective=%.6f\n", pm_relaxed.status, pm_relaxed.objective)
    own_relaxed = solve_ac_opf_own(relaxed_case; optimizer=default_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:generation_cost, include_branch_thermal=true)
    @printf("  own (thermal relaxed rate_a=1e6): status=%s objective=%.6f iterations=%.0f\n", own_relaxed.termination_status, own_relaxed.objective, own_relaxed.solve_iterations)
    gap_relaxed_pct = 100.0 * abs(own_relaxed.objective - pm_relaxed.objective) / (1.0 + abs(pm_relaxed.objective))
    @printf("  objective gap = %.4f%%\n", gap_relaxed_pct)
end

function investigate_cost(label::String, match_substr::String)
    println("\n" * "="^90)
    println("SCENARIO (generation-cost): $(label)")
    println("="^90)

    raw_data, case = scenario_data_66(match_substr)
    data = OwnPowerModels.build_ac_opf_data(case)
    default_optimizer = build_ac_optimizer()

    pm = solve_pm_cost_full_68(raw_data, default_optimizer)
    own = solve_ac_opf_own(case; optimizer=default_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:generation_cost)
    @printf("PM:  status=%s objective=%.6f\n", pm.status, pm.objective)
    @printf("Own: status=%s objective=%.6f iterations=%.0f\n", own.termination_status, own.objective, own.solve_iterations)

    q1_q2_compare(case, pm, own)
    calc_flows_at_pm = q3_equality_at_pm(case, data, pm)
    q4_q5_q6_inequalities(case, pm, own, calc_flows_at_pm)
    q7_q8_loadflow(case, "PM's solution x^p*", pm)
    q7_q8_loadflow(case, "own model's solution x^e*", own)
    q9_no_thermal_cost_68(raw_data, default_optimizer)
end

function main()
    investigate_cost("1888_rte_load_x0.8", "load_x0p8__load_scaling")
    investigate_cost("1888_rte_qload_x1.2 (reactive_power_stress)", "load_x1p2__reactive_power_stress")
    investigate_cost("1888_rte_qg_limits_x0.6", "qg_limits_x0p6__reactive_generation_limits")
end

main()
