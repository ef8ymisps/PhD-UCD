# Investigates a single anomaly found while auditing the final
# active_losses_validation_scenarios.csv table: 1888_rte's qg_limits_x0.6
# scenario shows julia_status=TIME_LIMIT (1294 iterations, cut off at the
# 300s max_cpu_time cap) while PowerModels (9.7365...) and Python
# (9.7331...) both converge cleanly under the SAME active-losses objective,
# and Julia's own model ALSO converges cleanly for this exact scenario
# under the generation-cost objective (LOCALLY_SOLVED, 263 iterations,
# ~18s -- see julia_pm_like/summary.csv). That combination rules out a
# genuinely infeasible/ill-posed scenario -- it points at a slow/stuck
# Ipopt trajectory specific to the active-losses objective's zero warm
# start on this one perturbed case. Re-solving with a much longer time cap
# (1200s vs the sweep's usual 300s) to see whether it eventually converges
# to the same ~9.73 value PM/Python found, or whether it's genuinely stuck
# (e.g. cycling) regardless of budget.
#
# Appends one more row to julia_full_sweep.csv (load_rows_generic keeps the
# LAST matching row per (case, scenario), so this corrected result
# supersedes the old TIME_LIMIT row without needing to edit the file).

const FORTYEIGHT_SCRIPT_PATH_63 = joinpath(@__DIR__, "48_run_full_sweep_active_losses_table.jl")
let
    src = read(FORTYEIGHT_SCRIPT_PATH_63, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(FORTYEIGHT_SCRIPT_PATH_63).")
    Base.include_string(Main, stripped, FORTYEIGHT_SCRIPT_PATH_63)
end

function long_cap_ac_optimizer_63()
    return optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => 5000, "max_cpu_time" => 1200.0)
end

function main()
    label = "1888_rte"
    case_file = "pglib_opf_case1888_rte"
    ac_optimizer = build_ac_optimizer()
    long_optimizer = long_cap_ac_optimizer_63()

    data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
    entries = compact_scenarios(data, label, ac_optimizer; include_no_reference=true)
    target = only(filter(e -> occursin("qg_limits_x0p6", String(e.scenario_id)), entries))
    scenario_id = String(target.scenario_id)

    println("Retrying $(scenario_id) with max_cpu_time=1200s, max_iter=5000 ...")
    t = @elapsed begin
        case = OwnPowerModels.powermodels_to_powercase(deepcopy(target.data))
        pm = solve_pm_active_losses_48(target.data, long_optimizer)
        own = solve_own_active_losses_48(case, long_optimizer)
        row = (
            case=label,
            scenario=scenario_id,
            pm_status=pm.status,
            pm_loss_objective=pm.objective,
            pm_solve_iterations=pm.solve_iterations,
            julia_status=own.status,
            julia_loss_objective=own.objective,
            julia_solve_iterations=own.solve_iterations,
            julia_vs_pm_gap_abs=own.objective - pm.objective,
            julia_vs_pm_gap_pct=100.0 * abs(own.objective - pm.objective) / (1.0 + abs(pm.objective)),
        )
        CSV.write(LOSSES_FULL_SWEEP_PATH, DataFrame([row]); append=true)
    end
    println("done ($(round(t, digits=1))s) julia_status=$(own_status_of(row)) julia_objective=$(row.julia_loss_objective) julia_iterations=$(row.julia_solve_iterations)")
end

own_status_of(row) = row.julia_status

main()
