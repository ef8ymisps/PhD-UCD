# Applies this project's established Q1-Q9 diagnostic ladder (previously
# used on case1888_rte's generation-cost objective-gap spotlight scenarios,
# see 41/46_run_opf_constraint_residuals_at_*_point.jl and
# 45_run_no_thermal_equality_comparison.jl) to the two NEW active-losses
# scenarios where the own model converges to a slightly BETTER (lower)
# objective than PowerModels: 1888_rte_qg_limits_x0.6 (0.035% better) and
# 1888_rte_qg_limits_x0.8 (0.027% better).
#
# Fully self-contained: solves both PM and the own model fresh here
# (capturing the FULL per-element solution, not just the aggregate
# objective the existing three_way_active_losses/ sweep records), then
# evaluates residuals directly via calc_bus_power_injections/
# calc_ac_branch_flows -- no dependency on the pre-existing per-scenario
# CSV folders scripts 41/46/45 read (those were built for the
# generation-cost sweep's own output layout, which the active-losses
# sweep never produced).
#
# Q1/Q2: print & diff Pg/Pf (p) and Vm/Va (u) between x^{p*} and x^{e*}.
# Q3:    g^e(x^{p*}) == 0? (own model's power-balance + Ohm's-law formulas
#        evaluated at PM's point)
# Q4:    h^e(x^{p*}) match PM's own reported inequality values?
# Q5:    h^e(x^{e*}) vs h^e(x^{p*}) -- close?
# Q6:    h^e(x^{p*}) <= h-bar (PGLib/PowerModels published limits)?
# Q7/Q8: Newton-Raphson AC PF using x^{p*}/x^{e*} as the operating point
#        (case_with_base_dispatch_own_ac + solve_ac_power_flow_own) --
#        feasible, within limits?
# Q9:    thermal-removed (own) vs thermal-relaxed (PM, rate_a=1e6)
#        equality-only comparison under active-losses.

const FORTYEIGHT_SCRIPT_PATH_66 = joinpath(@__DIR__, "48_run_full_sweep_active_losses_table.jl")
let
    src = read(FORTYEIGHT_SCRIPT_PATH_66, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(FORTYEIGHT_SCRIPT_PATH_66).")
    Base.include_string(Main, stripped, FORTYEIGHT_SCRIPT_PATH_66)
end

using JuMP
using LinearAlgebra
using Printf

const TOL_66 = 1e-4
const HUGE_RATE_A_66 = 1.0e6

function scenario_data_66(match_substr::String)
    label = "1888_rte"
    ac_optimizer = build_ac_optimizer()
    data = PowerModels.parse_file(joinpath(CASE_DIR, "pglib_opf_case1888_rte.m"))
    entries = compact_scenarios(data, label, ac_optimizer; include_no_reference=true)
    target = only(filter(e -> occursin(match_substr, String(e.scenario_id)), entries))
    raw = deepcopy(target.data)
    case = OwnPowerModels.powermodels_to_powercase(deepcopy(raw))
    return raw, case
end

function solve_pm_full_66(raw_data, optimizer)
    pm = PowerModels.instantiate_model(deepcopy(raw_data), PowerModels.ACPPowerModel, PowerModels.build_opf)
    p = PowerModels.var(pm, :p)
    loss_expr = @expression(
        pm.model,
        sum(p[(l, branch["f_bus"], branch["t_bus"])] + p[(l, branch["t_bus"], branch["f_bus"])] for (l, branch) in PowerModels.ref(pm, :branch))
    )
    @objective(pm.model, Min, loss_expr)
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

# --- Q1/Q2: side-by-side comparison of dispatch (p) and voltage state (u) ---
function q1_q2_compare(case, pm, own)
    println("\n--- Q1: Pg (active generation) at x^p* vs x^e* ---")
    max_pg_diff = 0.0
    for gen in case.generators
        gen.status == 1 || continue
        pgp = pm.pg[gen.id]
        pge = own.pg[gen.id]
        d = pge - pgp
        max_pg_diff = max(max_pg_diff, abs(d))
        if abs(d) > 1e-6
            @printf("  gen %-6s pm=%12.6f  own=%12.6f  diff=%12.6f\n", gen.id, pgp, pge, d)
        end
    end
    @printf("  max |Pg diff| = %.6e\n", max_pg_diff)

    println("\n--- Q1 (cont.): Pf (branch active flow, from-end) at x^p* vs x^e* ---")
    max_pf_diff = 0.0
    for branch in case.branches
        branch.status == 0 && continue
        pfp = get(pm.pf, branch.id, NaN)
        pfe = get(own.branch_flows, branch.id, Dict())["pf"]
        d = pfe - pfp
        max_pf_diff = max(max_pf_diff, abs(d))
    end
    @printf("  max |Pf diff| = %.6e\n", max_pf_diff)

    println("\n--- Q2: Vm (voltage magnitude, u) at x^p* vs x^e* ---")
    max_vm_diff = 0.0
    max_va_diff = 0.0
    for bus in case.buses
        vmp = pm.vm[bus.id]
        vme = own.vm[bus.id]
        vap = pm.va[bus.id]
        vae = own.va[bus.id]
        max_vm_diff = max(max_vm_diff, abs(vme - vmp))
        max_va_diff = max(max_va_diff, abs(vae - vap))
        if abs(vme - vmp) > 1e-4
            @printf("  bus %-6s pm_vm=%9.6f  own_vm=%9.6f  diff=%9.6f\n", bus.id, vmp, vme, vme - vmp)
        end
    end
    @printf("  max |Vm diff| = %.6e,  max |Va diff| = %.6e\n", max_vm_diff, max_va_diff)
end

# --- Q3: g^e(x^p*) == 0? power balance + Ohm's-law consistency at PM's point ---
function q3_equality_at_pm(case, data, pm)
    println("\n--- Q3: equality constraints g^e(x^p*) evaluated at PM's point ---")
    arcs_from = Dict(bus.id => String[] for bus in case.buses)
    arcs_to = Dict(bus.id => String[] for bus in case.buses)
    for branch in data.active_branches
        push!(arcs_from[branch.from_bus], branch.id)
        push!(arcs_to[branch.to_bus], branch.id)
    end

    max_p_residual = 0.0
    max_q_residual = 0.0
    for bus in case.buses
        bus_id = bus.id
        pgen = sum((get(pm.pg, g, 0.0) for g in data.gens_by_bus[bus_id]); init=0.0)
        qgen = sum((get(pm.qg, g, 0.0) for g in data.gens_by_bus[bus_id]); init=0.0)

        lhs_p = sum((get(pm.pf, b, 0.0) for b in arcs_from[bus_id]); init=0.0) +
                sum((get(pm.pt, b, 0.0) for b in arcs_to[bus_id]); init=0.0)
        rhs_p = pgen - data.pd_by_bus[bus_id] - data.gs_by_bus[bus_id] * pm.vm[bus_id]^2
        max_p_residual = max(max_p_residual, abs(lhs_p - rhs_p))

        lhs_q = sum((get(pm.qf, b, 0.0) for b in arcs_from[bus_id]); init=0.0) +
                sum((get(pm.qt, b, 0.0) for b in arcs_to[bus_id]); init=0.0)
        rhs_q = qgen - data.qd_by_bus[bus_id] + data.bs_by_bus[bus_id] * pm.vm[bus_id]^2
        max_q_residual = max(max_q_residual, abs(lhs_q - rhs_q))
    end
    @printf("  power balance: max |P residual| = %.3e, max |Q residual| = %.3e  (tol=%.0e)\n", max_p_residual, max_q_residual, TOL_66)
    println("  power balance g(x)=0? ", (max_p_residual <= TOL_66 && max_q_residual <= TOL_66) ? "YES" : "NO")

    calc_flows = OwnPowerModels.calc_ac_branch_flows(case, pm.vm, pm.va)
    max_ohms_residual = 0.0
    for branch in case.branches
        branch.status == 0 && continue
        haskey(calc_flows, branch.id) || continue
        c = calc_flows[branch.id]
        max_ohms_residual = max(max_ohms_residual, abs(c.pf - pm.pf[branch.id]), abs(c.qf - pm.qf[branch.id]), abs(c.pt - pm.pt[branch.id]), abs(c.qt - pm.qt[branch.id]))
    end
    @printf("  Ohm's law consistency: max |residual| = %.3e  (tol=%.0e)\n", max_ohms_residual, TOL_66)
    println("  Ohm's law g(x)=0? ", max_ohms_residual <= TOL_66 ? "YES" : "NO")
    return calc_flows
end

# --- Q4/Q5/Q6: inequality constraints at x^p* vs x^e*, vs published limits ---
function q4_q5_q6_inequalities(case, pm, own, calc_flows_at_pm)
    println("\n--- Q4/Q5/Q6: inequality constraints h^e at x^p* vs x^e*, vs published limits h-bar ---")

    println("  Thermal (S = hypot(p,q) <= rate_a):")
    max_gap_thermal = 0.0
    any_violation_at_pm = false
    for branch in case.branches
        branch.status == 0 && continue
        branch.rate_a > 0.0 || continue
        haskey(calc_flows_at_pm, branch.id) || continue
        cf = calc_flows_at_pm[branch.id]
        s_from_at_pm = hypot(cf.pf, cf.qf)
        s_to_at_pm = hypot(cf.pt, cf.qt)
        own_pf = own.branch_flows[branch.id]["pf"]; own_qf = own.branch_flows[branch.id]["qf"]
        own_pt = own.branch_flows[branch.id]["pt"]; own_qt = own.branch_flows[branch.id]["qt"]
        s_from_at_own = hypot(own_pf, own_qf)
        s_to_at_own = hypot(own_pt, own_qt)
        margin_at_pm = branch.rate_a - max(s_from_at_pm, s_to_at_pm)
        margin_at_own = branch.rate_a - max(s_from_at_own, s_to_at_own)
        max_gap_thermal = max(max_gap_thermal, abs(margin_at_own - margin_at_pm))
        if margin_at_pm < -TOL_66
            any_violation_at_pm = true
            @printf("    VIOLATION branch %-6s margin_at_pm=%.6f\n", branch.id, margin_at_pm)
        end
    end
    @printf("    max |margin_at_own - margin_at_pm| = %.4e\n", max_gap_thermal)
    println("    h^e(x^p*) <= h-bar (thermal)? ", any_violation_at_pm ? "NO -- violation found" : "YES")

    println("  Pg/Qg bounds:")
    max_gap_pg = 0.0
    any_violation_pg = false
    for gen in case.generators
        gen.status == 1 || continue
        pgp = pm.pg[gen.id]
        pge = own.pg[gen.id]
        margin_at_pm = min(pgp - gen.pmin, gen.pmax - pgp)
        margin_at_own = min(pge - gen.pmin, gen.pmax - pge)
        max_gap_pg = max(max_gap_pg, abs(margin_at_own - margin_at_pm))
        if margin_at_pm < -TOL_66
            any_violation_pg = true
        end
    end
    @printf("    max |Pg margin gap| = %.4e; any Pg violation at x^p*? %s\n", max_gap_pg, any_violation_pg ? "YES" : "NO")

    println("  Vm bounds:")
    max_gap_vm = 0.0
    any_violation_vm = false
    for bus in case.buses
        vmp = pm.vm[bus.id]
        vme = own.vm[bus.id]
        margin_at_pm = min(vmp - bus.vmin, bus.vmax - vmp)
        margin_at_own = min(vme - bus.vmin, bus.vmax - vme)
        max_gap_vm = max(max_gap_vm, abs(margin_at_own - margin_at_pm))
        if margin_at_pm < -TOL_66
            any_violation_vm = true
        end
    end
    @printf("    max |Vm margin gap| = %.4e; any Vm violation at x^p*? %s\n", max_gap_vm, any_violation_vm ? "YES" : "NO")

    println("  Angle-difference bounds:")
    max_gap_angle = 0.0
    any_violation_angle = false
    for branch in case.branches
        branch.status == 0 && continue
        f, t = branch.from_bus, branch.to_bus
        diff_at_pm = pm.va[f] - pm.va[t]
        diff_at_own = own.va[f] - own.va[t]
        margin_at_pm = min(diff_at_pm - branch.angmin, branch.angmax - diff_at_pm)
        margin_at_own = min(diff_at_own - branch.angmin, branch.angmax - diff_at_own)
        max_gap_angle = max(max_gap_angle, abs(margin_at_own - margin_at_pm))
        if margin_at_pm < -TOL_66
            any_violation_angle = true
        end
    end
    @printf("    max |angle margin gap| = %.4e; any angle violation at x^p*? %s\n", max_gap_angle, any_violation_angle ? "YES" : "NO")
end

# --- Q7/Q8: Newton-Raphson AC PF using x^p*/x^e* as the operating point ---
function q7_q8_loadflow(case, label, point)
    println("\n--- Q7/Q8: Newton-Raphson AC PF using $(label) as the operating point ---")
    dispatch_case = case_with_base_dispatch_own_ac(case, point)
    pf_result = solve_ac_power_flow_own(dispatch_case; method=:newton)
    println("  converged? ", pf_result.converged, "  mismatch_norm=", pf_result.mismatch_norm, " iterations=", pf_result.iterations)

    if pf_result.converged
        any_thermal_violation = false
        for branch in case.branches
            (branch.status == 0 || branch.rate_a <= 0.0) && continue
            haskey(pf_result.branch_flows, branch.id) || continue
            flow = pf_result.branch_flows[branch.id]
            s = max(hypot(flow["pf"], flow["qf"]), hypot(flow["pt"], flow["qt"]))
            if s > branch.rate_a + TOL_66
                any_thermal_violation = true
                @printf("    thermal violation branch %-6s S=%.4f > rate_a=%.4f\n", branch.id, s, branch.rate_a)
            end
        end
        any_qg_violation = false
        for gen in case.generators
            gen.status == 1 || continue
            qg = get(pf_result.qg_by_generator, gen.id, NaN)
            isnan(qg) && continue
            if qg < gen.qmin - TOL_66 || qg > gen.qmax + TOL_66
                any_qg_violation = true
                @printf("    Qg violation gen %-6s qg=%.4f bounds=[%.4f, %.4f]\n", gen.id, qg, gen.qmin, gen.qmax)
            end
        end
        println("  thermal limits respected? ", any_thermal_violation ? "NO" : "YES")
        println("  Qg limits respected (q_limit_converged=$(pf_result.q_limit_converged))? ", any_qg_violation ? "NO" : "YES")
    end
    return pf_result
end

# --- Q9: thermal-removed (own) vs thermal-relaxed (PM) equality-only comparison ---
function q9_no_thermal(raw_data, case, default_optimizer)
    relaxed_data = deepcopy(raw_data)
    for (_, branch) in relaxed_data["branch"]
        branch["rate_a"] = HUGE_RATE_A_66
    end
    relaxed_case = OwnPowerModels.powermodels_to_powercase(deepcopy(relaxed_data))
    pm_relaxed = solve_pm_full_66(relaxed_data, default_optimizer)

    println("\n--- Q9a: thermal CONSTRAINT REMOVED from own model (include_branch_thermal=false) vs PM thermal RELAXED (rate_a=1e6) ---")
    println("  (structurally different problems: own has fewer constraints entirely, PM keeps the constraint just non-binding)")
    @printf("  PM (thermal relaxed): status=%s objective=%.6f\n", pm_relaxed.status, pm_relaxed.objective)
    own_removed = solve_ac_opf_own(case; optimizer=default_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:active_losses, include_branch_thermal=false)
    @printf("  own (thermal removed): status=%s objective=%.6f iterations=%.0f\n", own_removed.termination_status, own_removed.objective, own_removed.solve_iterations)
    gap_removed_pct = 100.0 * abs(own_removed.objective - pm_relaxed.objective) / (1.0 + abs(pm_relaxed.objective))
    @printf("  objective gap = %.4f%%\n", gap_removed_pct)

    println("\n--- Q9b: thermal RELAXED identically on both sides (own gets the SAME inflated rate_a=1e6, constraint kept structurally present) ---")
    @printf("  PM (thermal relaxed rate_a=1e6): status=%s objective=%.6f\n", pm_relaxed.status, pm_relaxed.objective)
    own_relaxed = solve_ac_opf_own(relaxed_case; optimizer=default_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:active_losses, include_branch_thermal=true)
    @printf("  own (thermal relaxed rate_a=1e6): status=%s objective=%.6f iterations=%.0f\n", own_relaxed.termination_status, own_relaxed.objective, own_relaxed.solve_iterations)
    gap_relaxed_pct = 100.0 * abs(own_relaxed.objective - pm_relaxed.objective) / (1.0 + abs(pm_relaxed.objective))
    @printf("  objective gap = %.4f%%\n", gap_relaxed_pct)
end

function investigate(label::String, match_substr::String, own_optimizer)
    println("\n" * "="^90)
    println("SCENARIO: $(label)")
    println("="^90)

    raw_data, case = scenario_data_66(match_substr)
    data = OwnPowerModels.build_ac_opf_data(case)
    default_optimizer = build_ac_optimizer()

    pm = solve_pm_full_66(raw_data, default_optimizer)
    own = solve_ac_opf_own(case; optimizer=own_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero, objective=:active_losses)
    @printf("PM:  status=%s objective=%.6f\n", pm.status, pm.objective)
    @printf("Own: status=%s objective=%.6f iterations=%.0f\n", own.termination_status, own.objective, own.solve_iterations)

    q1_q2_compare(case, pm, own)
    calc_flows_at_pm = q3_equality_at_pm(case, data, pm)
    q4_q5_q6_inequalities(case, pm, own, calc_flows_at_pm)
    q7_q8_loadflow(case, "PM's solution x^p*", pm)
    q7_q8_loadflow(case, "own model's solution x^e*", own)
    q9_no_thermal(raw_data, case, default_optimizer)
end

function main()
    default_optimizer = build_ac_optimizer()
    adaptive_optimizer = optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => 3000, "max_cpu_time" => 300.0, "mu_strategy" => "adaptive")

    investigate("1888_rte_qg_limits_x0.6 (needs mu_strategy=adaptive to converge -- see script 65)", "qg_limits_x0p6", adaptive_optimizer)
    investigate("1888_rte_qg_limits_x0.8", "qg_limits_x0p8", default_optimizer)
end

main()
