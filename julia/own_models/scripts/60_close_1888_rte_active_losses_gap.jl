# Closes another instance of the same gap pattern found while closing the
# 6470_rte gap (58_close_6470_rte_active_losses_gap.jl): case1888_rte's
# branches 2252, 2328, 2350 were solved under the generation-cost objective
# (present -- both own and PM LOCALLY_INFEASIBLE -- in
# julia_pm_like/summary.csv, from an older compact_scenarios run's top-k
# branch-outage selection) but never under active-losses, since a fresh
# compact_scenarios call today selects a different branch set. Reproducing
# the exact same 3 scenarios requires applying the outage directly to these
# specific branch IDs.
#
# Both sides are already known LOCALLY_INFEASIBLE under generation-cost;
# infeasibility is a property of the constraint set (AC balance + limits +
# the outage), not the objective, so the same status is expected here too --
# still solved and recorded explicitly rather than assumed, exactly as
# 6470_rte's branch_7119 (also LOCALLY_INFEASIBLE) was.
#
# Appends 3 rows to results/own_vs_powermodels/three_way_active_losses/julia_full_sweep.csv.

const FORTYEIGHT_SCRIPT_PATH_60 = joinpath(@__DIR__, "48_run_full_sweep_active_losses_table.jl")
let
    src = read(FORTYEIGHT_SCRIPT_PATH_60, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(FORTYEIGHT_SCRIPT_PATH_60).")
    Base.include_string(Main, stripped, FORTYEIGHT_SCRIPT_PATH_60)
end

const TARGET_BRANCH_IDS_60 = ["2252", "2328", "2350"]

function main()
    timed_optimizer = timed_ac_optimizer_48()
    completed = completed_pairs_48(LOSSES_FULL_SWEEP_PATH)

    label = "1888_rte"
    case_file = "pglib_opf_case1888_rte"
    base_data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
    base_data["name"] = label

    for branch_id in TARGET_BRANCH_IDS_60
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

    println("\n1888_rte active-losses gap-closing run complete.")
end

main()
