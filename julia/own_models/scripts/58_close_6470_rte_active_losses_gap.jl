# Closes a known, narrow gap in the active-losses full sweep
# (48_run_full_sweep_active_losses_table.jl): case6470_rte's branches 7043,
# 7119, 7261 were solved under the generation-cost objective (they appear in
# julia_pm_like/summary.csv, from an older compact_scenarios run whose
# top-k branch-outage ranking selected a 9-branch set) but never under
# active-losses, because a *fresh* compact_scenarios call today only selects
# the current top-6 branches by loading/flow -- a different set that doesn't
# include these 3. Reproducing the exact same 3 scenarios requires applying
# the outage directly to these specific branch IDs rather than re-running
# compact_scenarios' own selection logic.
#
# Appends 3 rows to results/own_vs_powermodels/three_way_active_losses/julia_full_sweep.csv,
# matching that file's existing schema and scenario_id convention exactly
# (e.g. "6470_rte_branch_7043_out__branch_outage__branch__7043__status__0p0"),
# so Python's own script 55 can pick them up by the same lookup key.

const FORTYEIGHT_SCRIPT_PATH_58 = joinpath(@__DIR__, "48_run_full_sweep_active_losses_table.jl")
let
    src = read(FORTYEIGHT_SCRIPT_PATH_58, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(FORTYEIGHT_SCRIPT_PATH_58).")
    Base.include_string(Main, stripped, FORTYEIGHT_SCRIPT_PATH_58)
end

const TARGET_BRANCH_IDS_58 = ["7043", "7119", "7261"]

function main()
    timed_optimizer = timed_ac_optimizer_48()
    completed = completed_pairs_48(LOSSES_FULL_SWEEP_PATH)

    label = "6470_rte"
    case_file = "pglib_opf_case6470_rte"
    base_data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
    base_data["name"] = label

    for branch_id in TARGET_BRANCH_IDS_58
        scenario_id = "$(label)_branch_$(branch_id)_out__branch_outage__branch__$(branch_id)__status__0p0"
        if (label, scenario_id) in completed
            println("$(scenario_id) -- already done, skipping")
            continue
        end
        print("$(scenario_id) (active losses) ... ")
        t = @elapsed begin
            try
                data = deepcopy(base_data)
                apply_branch_outage!(data, branch_id)
                case = OwnPowerModels.powermodels_to_powercase(deepcopy(data))
                pm = solve_pm_active_losses_48(data, timed_optimizer)
                own = solve_own_active_losses_48(case, timed_optimizer)
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
                CSV.write(LOSSES_FULL_SWEEP_PATH, DataFrame([row]); append=isfile(LOSSES_FULL_SWEEP_PATH))
                push!(completed, (label, scenario_id))
            catch err
                println("ERROR: ", sprint(showerror, err))
            end
        end
        println("done ($(round(t, digits=1))s)")
        GC.gc()
    end

    println("\n6470_rte active-losses gap-closing run complete.")
end

main()
