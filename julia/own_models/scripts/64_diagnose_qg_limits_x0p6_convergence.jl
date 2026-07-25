# Diagnoses and tries to fix the isolated 1888_rte_qg_limits_x0.6
# active-losses convergence failure found while auditing the final tables
# (63_retry_1888_rte_qg_limits_x0p6_active_losses.jl already ruled out "just
# needed more time": 4x the time cap bought only 20 more iterations).
#
# Hypothesis being tested: the active-losses objective (sum(p_a), linear in
# the arc-flow variables, no direct curvature from V/theta) is much flatter
# than the generation-cost objective, so Ipopt's default gradient-based
# scaling + a zero warm start can send it down a slow/degenerate path on
# this specific tightened-qg-limit scenario, even though the same own model
# converges cleanly on the identical data under generation-cost. Tests, in
# order:
#   A. Reproduce the baseline failure (zero start, default Ipopt options --
#      same as the sweep) for a sanity-checked reference point.
#   B. Warm-start the active-losses solve from the (quickly-converging)
#      generation-cost solution's own primal point, default Ipopt options.
#   C. Zero start, nlp_scaling_method="none" (bypasses Ipopt's gradient-based
#      auto-scaling entirely).
#   D. Zero start, mu_strategy="adaptive" (Ipopt's alternative barrier-
#      parameter update strategy, often more robust on flat objectives).
#   E. Combines C+D.

const TWENTYFIVE_SCRIPT_PATH_64 = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH_64, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH_64).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH_64)
end

using JuMP

function scenario_data_64()
    label = "1888_rte"
    ac_optimizer = build_ac_optimizer()
    data = PowerModels.parse_file(joinpath(CASE_DIR, "pglib_opf_case1888_rte.m"))
    entries = compact_scenarios(data, label, ac_optimizer; include_no_reference=true)
    target = only(filter(e -> occursin("qg_limits_x0p6", String(e.scenario_id)), entries))
    return OwnPowerModels.powermodels_to_powercase(deepcopy(target.data))
end

function report(label, result, t)
    println("  $(label): status=$(result.termination_status) objective=$(result.objective) iterations=$(result.solve_iterations) time=$(round(t, digits=1))s")
end

function main()
    case = scenario_data_64()

    println("=== A. Baseline repro: zero start, default options (300s cap) ===")
    default_optimizer = optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => 3000, "max_cpu_time" => 300.0)
    t_a = @elapsed result_a = solve_ac_opf_own(case; optimizer=default_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:active_losses)
    report("A", result_a, t_a)

    println("\n=== Solving generation-cost first (warm-start source for B) ===")
    t_cost = @elapsed cost_result = solve_ac_opf_own(case; optimizer=default_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:generation_cost)
    report("cost", cost_result, t_cost)

    println("\n=== B. Warm-started from cost solution, default options ===")
    model_b, data_b, pg_b, qg_b, vm_b, va_b, branch_flows_b, _ = build_ac_opf_model(
        case; optimizer=default_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:active_losses,
    )
    for gen in case.generators
        if gen.status == 1
            set_start_value(pg_b[gen.id], cost_result.pg[gen.id])
            set_start_value(qg_b[gen.id], cost_result.qg[gen.id])
        end
    end
    for bus in case.buses
        set_start_value(vm_b[bus.id], cost_result.vm[bus.id])
        set_start_value(va_b[bus.id], cost_result.va[bus.id])
    end
    for branch in data_b.active_branches
        set_start_value(branch_flows_b.pf[branch.id], cost_result.branch_flows[branch.id]["pf"])
        set_start_value(branch_flows_b.qf[branch.id], cost_result.branch_flows[branch.id]["qf"])
        set_start_value(branch_flows_b.pt[branch.id], cost_result.branch_flows[branch.id]["pt"])
        set_start_value(branch_flows_b.qt[branch.id], cost_result.branch_flows[branch.id]["qt"])
    end
    t_b = @elapsed begin
        optimize!(model_b)
        result_b = OwnPowerModels.collect_ac_opf_result(model_b, case, data_b, pg_b, qg_b, vm_b, va_b, branch_flows_b)
    end
    report("B", result_b, t_b)

    println("\n=== C. Zero start, nlp_scaling_method=none ===")
    optimizer_c = optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => 3000, "max_cpu_time" => 300.0, "nlp_scaling_method" => "none")
    t_c = @elapsed result_c = solve_ac_opf_own(case; optimizer=optimizer_c, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:active_losses)
    report("C", result_c, t_c)

    println("\n=== D. Zero start, mu_strategy=adaptive ===")
    optimizer_d = optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => 3000, "max_cpu_time" => 300.0, "mu_strategy" => "adaptive")
    t_d = @elapsed result_d = solve_ac_opf_own(case; optimizer=optimizer_d, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:active_losses)
    report("D", result_d, t_d)

    println("\n=== E. Zero start, nlp_scaling_method=none + mu_strategy=adaptive ===")
    optimizer_e = optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => 3000, "max_cpu_time" => 300.0, "nlp_scaling_method" => "none", "mu_strategy" => "adaptive")
    t_e = @elapsed result_e = solve_ac_opf_own(case; optimizer=optimizer_e, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:active_losses)
    report("E", result_e, t_e)

    println("\nDiagnostic run complete. Target value (PM/Python converged): ~9.7365 / ~9.7331")
end

main()
