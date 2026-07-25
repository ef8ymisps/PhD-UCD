# Targeted, single-scenario fix for the 1888_rte_qg_limits_x0.6
# active-losses TIME_LIMIT anomaly diagnosed in
# 64_diagnose_qg_limits_x0p6_convergence.jl: Julia's own model, under the
# active-losses objective, zero warm start, and Ipopt's DEFAULT
# mu_strategy="monotone", gets stuck in a slow/degenerate trajectory on
# this one perturbed case (1291+ iterations without converging inside the
# usual 300s cap) even though the identical constraint set converges
# cleanly under generation-cost (263 iterations). Setting
# mu_strategy="adaptive" (Ipopt's alternative barrier-parameter update
# rule) converges in 280 iterations / ~24s, landing at 9.733125761622192 --
# matching Python's independently-found optimum (9.73312699450552, via a
# completely different Ipopt/Pyomo build) to 6 significant figures, and
# close to PowerModels' own optimum (9.7365...) within the same small
# cross-solver local-optimum gap already normal for this project's
# nonconvex AC-OPF work. See that diagnostic script for the other options
# tried (warm-starting from the cost solution, nlp_scaling_method=none) --
# all of them either failed to converge or landed on a DIFFERENT local
# optimum; only mu_strategy=adaptive reproduced Python's own point.
#
# Scope, per explicit choice: this is a targeted, single-row fix -- only
# this one scenario's Julia optimizer uses mu_strategy=adaptive. The
# general sweep (scripts 25/27/48/58/60/61/62) is left untouched, since
# applying this option change project-wide would itself need a full
# re-sweep + regression check (this project's acceptance-gate practice)
# and was explicitly out of scope here.
#
# Appends one corrected row to julia_full_sweep.csv (load_rows_generic
# keeps the LAST matching row per (case, scenario), so this supersedes the
# old TIME_LIMIT row without needing to edit the file). PM's side was
# never the problem (already LOCALLY_SOLVED in the existing row) -- it is
# re-solved here too, with the sweep's normal default optimizer, purely so
# this one row is internally self-consistent (both sides solved in the
# same run) rather than splicing values from two different runs.

const FORTYEIGHT_SCRIPT_PATH_65 = joinpath(@__DIR__, "48_run_full_sweep_active_losses_table.jl")
let
    src = read(FORTYEIGHT_SCRIPT_PATH_65, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(FORTYEIGHT_SCRIPT_PATH_65).")
    Base.include_string(Main, stripped, FORTYEIGHT_SCRIPT_PATH_65)
end

function adaptive_mu_ac_optimizer_65()
    return optimizer_with_attributes(
        Ipopt.Optimizer, "print_level" => 0, "max_iter" => 3000, "max_cpu_time" => 300.0, "mu_strategy" => "adaptive",
    )
end

function main()
    label = "1888_rte"
    ac_optimizer = build_ac_optimizer()
    default_timed_optimizer = timed_ac_optimizer_48()
    adaptive_optimizer = adaptive_mu_ac_optimizer_65()

    data = PowerModels.parse_file(joinpath(CASE_DIR, "pglib_opf_case1888_rte.m"))
    entries = compact_scenarios(data, label, ac_optimizer; include_no_reference=true)
    target = only(filter(e -> occursin("qg_limits_x0p6", String(e.scenario_id)), entries))
    scenario_id = String(target.scenario_id)

    println("Fixing $(scenario_id): own model solved with mu_strategy=adaptive, PM with the sweep's default optimizer ...")
    t = @elapsed begin
        case = OwnPowerModels.powermodels_to_powercase(deepcopy(target.data))
        pm = solve_pm_active_losses_48(target.data, default_timed_optimizer)
        own = solve_own_active_losses_48(case, adaptive_optimizer)
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
    println("done ($(round(t, digits=1))s) julia_status=$(own.status) julia_objective=$(own.objective) julia_iterations=$(own.solve_iterations)")
    println("pm_status=$(pm.status) pm_objective=$(pm.objective)")
end

main()
