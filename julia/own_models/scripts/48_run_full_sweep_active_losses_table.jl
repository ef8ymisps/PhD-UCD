# Full-scenario-grid active-losses (loss-minimization) objective comparison:
# PowerModels (custom loss objective), Julia own arc-based model.
#
# Extends 47_run_three_way_active_losses_table.jl (base case only, 8 cases)
# to the full ~313-scenario grid compact_scenarios generates for all 8
# cases -- the same grid 25_run_own_vs_powermodels_tables.jl /
# 27_run_own_vs_powermodels_full_sweep.jl already cover for the
# generation-cost objective. Checkpointed/resumable: skips (case, scenario)
# pairs already present in the output CSV.
#
# Output: results/own_vs_powermodels/three_way_active_losses/julia_full_sweep.csv

const TWENTYFIVE_SCRIPT_PATH_48 = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH_48, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH_48).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH_48)
end

using JuMP

const LOSSES_OUT_48 = joinpath(ROOT, "results", "own_vs_powermodels", "three_way_active_losses")
const LOSSES_FULL_SWEEP_PATH = joinpath(LOSSES_OUT_48, "julia_full_sweep.csv")

# Wall-clock safety cap: a prior run of this script got stuck for 8+ hours
# of active CPU time on a single case1888_rte scenario without hitting
# max_iter -- an Ipopt max_cpu_time cap (per solve) prevents any one
# pathological scenario from blocking the rest of the sweep. A capped-out
# solve is recorded with whatever status Ipopt reports (typically
# "MAXITER_EXCEEDED"-equivalent or a time-limit-specific status) rather than
# silently hanging.
const MAX_CPU_TIME_SEC_48 = 300.0

function timed_ac_optimizer_48()
    return optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => 3000, "max_cpu_time" => MAX_CPU_TIME_SEC_48)
end

function solve_pm_active_losses_48(data, optimizer)
    pm = PowerModels.instantiate_model(deepcopy(data), PowerModels.ACPPowerModel, PowerModels.build_opf)
    p = PowerModels.var(pm, :p)
    loss_expr = @expression(
        pm.model,
        sum(
            p[(l, branch["f_bus"], branch["t_bus"])] + p[(l, branch["t_bus"], branch["f_bus"])]
            for (l, branch) in PowerModels.ref(pm, :branch)
        )
    )
    @objective(pm.model, Min, loss_expr)
    elapsed = @elapsed result = PowerModels.optimize_model!(pm, optimizer=optimizer)
    return (
        status=string(get(result, "termination_status", "UNKNOWN")),
        objective=Float64(get(result, "objective", NaN)),
        solve_time_sec=elapsed,
        solve_iterations=OwnPowerModels._safe_barrier_iterations(pm.model),
    )
end

function solve_own_active_losses_48(case, ac_optimizer)
    result = solve_ac_opf_own(case; optimizer=ac_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:active_losses)
    return (
        status=string(result.termination_status),
        objective=result.objective,
        solve_iterations=result.solve_iterations,
    )
end

function completed_pairs_48(path::String)
    completed = Set{Tuple{String,String}}()
    isfile(path) || return completed
    df = DataFrame(CSV.File(path))
    for row in eachrow(df)
        push!(completed, (String(row.case), String(row.scenario)))
    end
    return completed
end

function main()
    ac_optimizer = build_ac_optimizer()
    timed_optimizer = timed_ac_optimizer_48()
    mkpath(LOSSES_OUT_48)
    completed = completed_pairs_48(LOSSES_FULL_SWEEP_PATH)
    println(isempty(completed) ? "Starting fresh." : "Resuming -- $(length(completed)) pairs already done.")

    for case_file in DEFAULT_CASES
        label = short_case_name(case_file)
        data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
        entries = compact_scenarios(data, label, ac_optimizer; include_no_reference=true)
        println("\n=== $(label): $(length(entries)) scenario(s) ===")

        for (i, entry) in enumerate(entries)
            scenario_id = String(entry.scenario_id)
            if (label, scenario_id) in completed
                println("  [$(i)/$(length(entries))] $(scenario_id) -- already done, skipping")
                continue
            end
            print("  [$(i)/$(length(entries))] $(scenario_id) (active losses) ... ")
            t = @elapsed begin
                try
                    case = OwnPowerModels.powermodels_to_powercase(deepcopy(entry.data))
                    pm = solve_pm_active_losses_48(entry.data, timed_optimizer)
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
    end

    println("\nFull active-losses sweep complete. Wrote $(LOSSES_FULL_SWEEP_PATH)")
end

main()
