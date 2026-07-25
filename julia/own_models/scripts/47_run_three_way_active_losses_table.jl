# Three-way active-losses (loss-minimization) objective comparison:
# PowerModels, Julia (own arc-based model), Python (read separately).
#
# The own model's :active_losses objective (add_ac_active_losses_objective!)
# already works for the arc-based model in both languages -- no new own-model
# code needed. PowerModels.jl has no built-in loss-minimization variant of
# build_opf, so this script builds one: instantiate the normal ACPPowerModel
# (all constraints, default cost objective), then overrides the JuMP
# objective with total branch active loss (sum of each branch's own arc-flow
# variables p[(l,f,t)] + p[(l,t,f)], the same quantity add_ac_active_losses_objective!
# sums for the own model), and re-optimizes.
#
# Scope: the 8 default PGLib base cases only (not the case1888_rte spotlight
# scenarios) -- this is a new objective axis, not a re-run of the existing
# cost-objective validation sweep.
#
# Output: results/own_vs_powermodels/three_way_active_losses/julia_and_pm.csv
# (Python script 54 reads this and appends its own own_python_loss_objective
# column to produce the final three-way table.)

const TWENTYFIVE_SCRIPT_PATH_47 = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH_47, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH_47).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH_47)
end

using JuMP

const LOSSES_OUT = joinpath(ROOT, "results", "own_vs_powermodels", "three_way_active_losses")

"""Build a PowerModels ACPPowerModel with the standard build_opf (all
constraints, cost objective), then override the objective with total branch
active loss -- sum of each branch's own from/to arc-flow variables, matching
add_ac_active_losses_objective!'s own summation exactly."""
function solve_pm_active_losses(data, optimizer)
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

function solve_own_active_losses(case, ac_optimizer)
    result = solve_ac_opf_own(case; optimizer=ac_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:active_losses)
    return (
        status=string(result.termination_status),
        objective=result.objective,
        solve_time_sec=NaN,
        solve_iterations=result.solve_iterations,
    )
end

function main()
    ac_optimizer = build_ac_optimizer()
    rows = NamedTuple[]

    for case_file in DEFAULT_CASES
        label = short_case_name(case_file)
        println("=== $(label) (active losses) ===")
        data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
        case = OwnPowerModels.powermodels_to_powercase(deepcopy(data))

        pm = solve_pm_active_losses(data, ac_optimizer)
        own = solve_own_active_losses(case, ac_optimizer)

        push!(rows, (
            case=label,
            scenario="base",
            pm_status=pm.status,
            pm_loss_objective=pm.objective,
            pm_solve_iterations=pm.solve_iterations,
            julia_status=own.status,
            julia_loss_objective=own.objective,
            julia_solve_iterations=own.solve_iterations,
            julia_vs_pm_gap_abs=own.objective - pm.objective,
            julia_vs_pm_gap_pct=100.0 * abs(own.objective - pm.objective) / (1.0 + abs(pm.objective)),
        ))
        println("  pm=$(pm.objective) julia=$(own.objective)")
    end

    mkpath(LOSSES_OUT)
    out_path = joinpath(LOSSES_OUT, "julia_and_pm.csv")
    CSV.write(out_path, DataFrame(rows))
    println("Wrote $(out_path)")
end

main()
