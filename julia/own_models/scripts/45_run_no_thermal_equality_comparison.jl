# Diagnostic: with thermal (flow) constraints removed on BOTH sides -- the
# own model via its own include_branch_thermal=false toggle, PowerModels via
# an inflated rate_a (PowerModels has no equivalent toggle, so this mirrors
# the repo's existing relax_limits! technique of scaling rate_a rather than
# deleting it) -- and the REAL PGLib generation-cost objective UNCHANGED
# (unlike script 43, which additionally swapped to a uniform linear cost;
# here only the inequality/thermal side is touched), compare:
#
#   1. own model's AC-OPF objective vs PowerModels' AC-OPF objective, now
#      that the source of thermal-driven alternate local optima is removed
#      -- does own model still land on a cheaper point, or do they converge?
#   2. g^e(x) at own's own (thermal-free) solution -- should be ~0 by
#      construction (it's an equality constraint of that very solve), kept
#      here for completeness/symmetry.
#   3. g^e(x^p*_no_thermal) -- the own model's power-balance formula
#      evaluated at PowerModels' NEW (thermal-free) solution, with zero
#      re-optimization. This is the actual new check: does the own model's
#      equality-constraint physics still hold at PM's thermal-free point?
#
# Covers the Julia own model (arc-based/"julia_pm_like", the sole AC-OPF
# formulation -- bus-injection/"julia_current" was deleted and is no longer
# comparable) across the scenarios already used throughout the Q3-Q9
# investigation. PowerModels' thermal-free AC-OPF is solved ONCE per
# scenario. PM's full post-solve state (Vm/Va/Pg/Qg) is also exported so
# python/own_models/scripts/50_run_no_thermal_equality_comparison.py can
# evaluate ITS OWN g^e at the same PM point without needing to re-solve
# PowerModels from Python.

const TWENTYFIVE_SCRIPT_PATH_45 = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH_45, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH_45).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH_45)
end

const MATERIAL_TOL_45 = 1e-4
const OUT_ROOT_45 = joinpath(ROOT, "results", "own_vs_powermodels", "_organized_for_meeting", "10_opf_solution_inspection", "1888_rte_objective_gap")
const HUGE_RATE_A_45 = 1.0e6

# (scenario label, match_text, [model variants that use this scenario])
const NO_THERMAL_SCENARIOS = [
    (scenario="load_x0.8", match_text="load_x0p8__load_scaling", variants=[:pm_like]),
    (scenario="load_x1.2_reactive_stress", match_text="load_x1p2__reactive_power_stress", variants=[:pm_like]),
    (scenario="qg_limits_x0.6", match_text="qg_limits_x0p6__reactive_generation_limits", variants=[:pm_like]),
]

function find_entry_45(entries, match_text::String)
    matches = filter(e -> occursin(match_text, String(e.scenario_id)), entries)
    isempty(matches) && error("No 1888_rte scenario matched substring: $(match_text)")
    length(matches) > 1 && error("Multiple 1888_rte scenarios matched substring $(match_text)")
    return only(matches)
end

function remove_thermal_limits_pm!(data)
    for branch in values(data["branch"])
        branch["rate_a"] = HUGE_RATE_A_45
    end
end

"""Branch-flow (julia_pm_like) power-balance residual: arc-sum formula from
add_ac_power_balance! (ac/model.jl), using Ohm's-law-derived Pf/Pt/Qf/Qt
(calc_ac_branch_flows, own model's own formula) at the given Vm/Va -- same
pattern as script 41's :branch_flow method."""
function power_balance_residual_branch_flow(case, data, pg::Dict{String,Float64}, qg::Dict{String,Float64}, vm::Dict{String,Float64}, va::Dict{String,Float64})
    flows = OwnPowerModels.calc_ac_branch_flows(case, vm, va)
    arcs_from = Dict(bus.id => String[] for bus in case.buses)
    arcs_to = Dict(bus.id => String[] for bus in case.buses)
    for branch in data.active_branches
        push!(arcs_from[branch.from_bus], branch.id)
        push!(arcs_to[branch.to_bus], branch.id)
    end

    max_abs_p = 0.0
    max_abs_q = 0.0
    for bus in case.buses
        bus_id = bus.id
        pgen = sum((get(pg, g, 0.0) for g in data.gens_by_bus[bus_id]); init=0.0)
        qgen = sum((get(qg, g, 0.0) for g in data.gens_by_bus[bus_id]); init=0.0)

        own_lhs_p = sum((haskey(flows, b) ? flows[b].pf : 0.0 for b in arcs_from[bus_id]); init=0.0) +
                    sum((haskey(flows, b) ? flows[b].pt : 0.0 for b in arcs_to[bus_id]); init=0.0)
        own_rhs_p = pgen - data.pd_by_bus[bus_id] - data.gs_by_bus[bus_id] * vm[bus_id]^2
        max_abs_p = max(max_abs_p, abs(own_lhs_p - own_rhs_p))

        own_lhs_q = sum((haskey(flows, b) ? flows[b].qf : 0.0 for b in arcs_from[bus_id]); init=0.0) +
                    sum((haskey(flows, b) ? flows[b].qt : 0.0 for b in arcs_to[bus_id]); init=0.0)
        own_rhs_q = qgen - data.qd_by_bus[bus_id] + data.bs_by_bus[bus_id] * vm[bus_id]^2
        max_abs_q = max(max_abs_q, abs(own_lhs_q - own_rhs_q))
    end
    return max_abs_p, max_abs_q
end

function pm_solution_dicts(pm_result::Dict)
    solution = get(pm_result, "solution", Dict{String,Any}())
    pg = Dict(string(gid) => Float64(g["pg"]) for (gid, g) in get(solution, "gen", Dict()))
    qg = Dict(string(gid) => Float64(g["qg"]) for (gid, g) in get(solution, "gen", Dict()))
    vm = Dict(string(bid) => Float64(b["vm"]) for (bid, b) in get(solution, "bus", Dict()))
    va = Dict(string(bid) => Float64(b["va"]) for (bid, b) in get(solution, "bus", Dict()))
    return pg, qg, vm, va
end

function solve_own_variant(variant::Symbol, case, ac_optimizer)
    variant == :pm_like || error("Unsupported own AC OPF variant: $(variant). Expected :pm_like (bus-injection was deleted).")
    return OwnPowerModels.solve_ac_opf_own(
        case; optimizer=ac_optimizer, silent=true, fix_reference_angle=true, include_branch_thermal=false, warm_start=:zero,
    )
end

variant_label(variant::Symbol) = variant == :pm_like ? "julia_pm_like" : error("Unsupported own AC OPF variant: $(variant).")

function main()
    ac_optimizer = build_ac_optimizer()
    case_label = "1888_rte"
    base_data = PowerModels.parse_file(joinpath(CASE_DIR, "pglib_opf_case$(case_label).m"))
    entries = compact_scenarios(base_data, case_label, ac_optimizer; include_no_reference=true)

    println("Diagnostic: thermal removed both sides, real cost kept -- equality-constraint check")
    rows = NamedTuple[]
    pm_state_rows = NamedTuple[]
    own_state_rows = NamedTuple[]

    for spec in NO_THERMAL_SCENARIOS
        entry = find_entry_45(entries, spec.match_text)
        case = OwnPowerModels.powermodels_to_powercase(deepcopy(entry.data))
        data = OwnPowerModels.build_ac_opf_data(case)

        pm_data_no_thermal = deepcopy(entry.data)
        remove_thermal_limits_pm!(pm_data_no_thermal)
        pm_model = PowerModels.instantiate_model(pm_data_no_thermal, PowerModels.ACPPowerModel, PowerModels.build_opf)
        pm_result = PowerModels.optimize_model!(pm_model, optimizer=ac_optimizer)
        pm_status = string(get(pm_result, "termination_status", "UNKNOWN"))
        pm_objective = get(pm_result, "objective", NaN)
        pm_pg, pm_qg, pm_vm, pm_va = pm_solution_dicts(pm_result)

        for bus_id in data.bus_ids
            push!(pm_state_rows, (scenario=spec.scenario, element_type="bus", element_id=bus_id, vm=pm_vm[bus_id], va=pm_va[bus_id], pg=NaN, qg=NaN))
        end
        for gen_id in data.gen_ids
            haskey(pm_pg, gen_id) || continue
            push!(pm_state_rows, (scenario=spec.scenario, element_type="gen", element_id=gen_id, vm=NaN, va=NaN, pg=pm_pg[gen_id], qg=pm_qg[gen_id]))
        end

        for variant in spec.variants
            own_result = solve_own_variant(variant, case, ac_optimizer)
            own_ok = own_result.termination_status in (JuMP.LOCALLY_SOLVED, JuMP.OPTIMAL, JuMP.ALMOST_LOCALLY_SOLVED, JuMP.ALMOST_OPTIMAL)
            own_objective = own_ok ? own_result.objective : NaN

            if own_ok
                for bus_id in data.bus_ids
                    push!(own_state_rows, (model_variant=variant_label(variant), scenario=spec.scenario, element_type="bus", element_id=bus_id, vm=own_result.vm[bus_id], va=own_result.va[bus_id], pg=NaN, qg=NaN))
                end
                for gen_id in data.gen_ids
                    haskey(own_result.pg, gen_id) || continue
                    push!(own_state_rows, (model_variant=variant_label(variant), scenario=spec.scenario, element_type="gen", element_id=gen_id, vm=NaN, va=NaN, pg=own_result.pg[gen_id], qg=own_result.qg[gen_id]))
                end
            end

            max_p_at_own, max_q_at_own = power_balance_residual_branch_flow(case, data, own_result.pg, own_result.qg, own_result.vm, own_result.va)
            max_p_at_pm, max_q_at_pm = power_balance_residual_branch_flow(case, data, pm_pg, pm_qg, pm_vm, pm_va)

            gap_abs = own_objective - pm_objective
            gap_pct = 100.0 * gap_abs / pm_objective

            row = (
                model_variant=variant_label(variant),
                scenario=spec.scenario,
                own_objective=own_objective,
                own_status=string(own_result.termination_status),
                pm_objective=pm_objective,
                pm_status=pm_status,
                own_vs_pm_gap_abs=gap_abs,
                own_vs_pm_gap_pct=gap_pct,
                own_cheaper_than_pm=gap_abs < 0.0,
                max_abs_g_own_at_own_p=max_p_at_own,
                max_abs_g_own_at_own_q=max_q_at_own,
                g_own_at_own_satisfied_1e4=max(max_p_at_own, max_q_at_own) <= MATERIAL_TOL_45,
                max_abs_g_own_at_pm_p=max_p_at_pm,
                max_abs_g_own_at_pm_q=max_q_at_pm,
                g_own_at_pm_satisfied_1e4=max(max_p_at_pm, max_q_at_pm) <= MATERIAL_TOL_45,
            )
            push!(rows, row)
            println("  $(row.model_variant)/$(spec.scenario): own=$(own_objective) pm=$(pm_objective) gap_pct=$(gap_pct) | g_own_at_own(P,Q)=($(max_p_at_own),$(max_q_at_own)) | g_own_at_pm(P,Q)=($(max_p_at_pm),$(max_q_at_pm))")
        end
    end

    out_path = joinpath(OUT_ROOT_45, "no_thermal_equality_comparison_julia.csv")
    mkpath(OUT_ROOT_45)
    CSV.write(out_path, DataFrame(rows))
    println("Wrote: $(out_path)")

    pm_state_path = joinpath(OUT_ROOT_45, "no_thermal_pm_solution_state_1888_rte.csv")
    CSV.write(pm_state_path, DataFrame(pm_state_rows))
    println("Wrote: $(pm_state_path)")

    own_state_path = joinpath(OUT_ROOT_45, "no_thermal_own_solution_state_1888_rte_julia.csv")
    CSV.write(own_state_path, DataFrame(own_state_rows))
    println("Wrote: $(own_state_path)")
end

main()
