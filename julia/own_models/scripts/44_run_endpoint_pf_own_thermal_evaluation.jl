# Q7/Q8 follow-up: evaluate the OWN model's own thermal inequality formula
# (h^e_thermal, the same Ohm's-law-based Sf/St computation from
# calc_ac_branch_flows used in the Q3-Q6 work) AT the post-load-flow
# operating point, for both PF-seed endpoints (own's OPF solution and
# PowerModels' OPF solution).
#
# script 31 (opf_endpoint_pf_inequality_levels_julia.csv) already checks
# "value vs bound" post-PF, but for thermal it always uses
# PowerModels.calc_branch_flow_ac's own apparent-power computation for BOTH
# endpoints -- it never actually evaluates the own model's own Ohm's-law
# formula there. pg/qg/vm/angle bounds don't have this gap (same formula for
# both models, since the bound itself is shared -- established in the Q3-Q6
# work), so this script is intentionally thermal-only.
#
# Reuses the ALREADY-COMPUTED post-PF Vm/Va from
# opf_endpoint_pf_variable_comparison_julia.csv -- no load flow is re-run
# here, this is a pure plug-in-point evaluation of h^e_thermal at an
# already-converged post-PF state.

const TWENTYFIVE_SCRIPT_PATH_44 = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH_44, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH_44).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH_44)
end

const MATERIAL_TOL_44 = 1e-4
const RESULTS_ROOT_44 = joinpath(ROOT, "results", "own_vs_powermodels")
const OUT_ROOT_44 = joinpath(RESULTS_ROOT_44, "_organized_for_meeting", "10_opf_solution_inspection", "1888_rte_objective_gap")

# (own model_variant label, script-31 "variant" label, match_text for scenario
# reconstruction, script-31 scenario-column value to join against, clean
# output label for this project's own convention)
const ENDPOINT_THERMAL_SCENARIOS = [
    (model_variant="julia_current", pf_variant="current", match_text="load_x0p8__load_scaling", pf_scenario="load_x0.8", out_scenario="load_x0.8"),
    (model_variant="julia_current", pf_variant="current", match_text="load_x1p2__reactive_power_stress", pf_scenario="load_x1.2_reactive_stress", out_scenario="load_x1.2_reactive_stress"),
    (model_variant="julia_pm_like", pf_variant="pm_like", match_text="load_x0p8__load_scaling", pf_scenario="load_x0.8", out_scenario="load_x0.8"),
    (model_variant="julia_pm_like", pf_variant="pm_like", match_text="load_x1p2__reactive_power_stress", pf_scenario="load_x1.2_reactive_stress", out_scenario="load_x1.2_reactive_stress"),
    (model_variant="julia_pm_like", pf_variant="pm_like", match_text="qg_limits_x0p6__reactive_generation_limits", pf_scenario="1888_rte_qg_limits_x0p6__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p6", out_scenario="qg_limits_x0.6"),
]

function find_entry_44(entries, match_text::String)
    matches = filter(e -> occursin(match_text, String(e.scenario_id)), entries)
    isempty(matches) && error("No 1888_rte scenario matched substring: $(match_text)")
    length(matches) > 1 && error("Multiple 1888_rte scenarios matched substring $(match_text)")
    return only(matches)
end

"""Read post-PF Vm/Va for one endpoint (`prefix` = "own_pf_value" or
"pm_pf_value") from the already-computed
opf_endpoint_pf_variable_comparison_julia.csv -- no PF re-solve here."""
function read_post_pf_voltage_state(pf_variant::String, pf_scenario::String, value_col::Symbol)
    path = joinpath(RESULTS_ROOT_44, "opf_endpoint_pf_variable_comparison_julia.csv")
    df = CSV.read(path, DataFrame)
    sub = filter(r -> r.variant == pf_variant && r.case == "1888_rte" && r.scenario == pf_scenario, df)
    isempty(sub) && error("No post-PF variable rows found for variant=$(pf_variant) scenario=$(pf_scenario)")

    vm = Dict{String, Float64}()
    va = Dict{String, Float64}()
    for r in eachrow(sub)
        bus_id = string(r.element_id)
        if r.quantity == "vm"
            vm[bus_id] = Float64(r[value_col])
        elseif r.quantity == "va"
            va[bus_id] = Float64(r[value_col])
        end
    end
    return vm, va
end

"""Read PowerModels-engine's own post-PF thermal value (Sf, i.e. max(Sf,St)
as `value`) for the same endpoint, from the existing
opf_endpoint_pf_inequality_levels_julia.csv, for cross-checking against the
own model's Ohm's-law recompute below (an Ohm's-law-consistency check, like
Q3, but now AT the post-PF point instead of the raw OPF point)."""
function read_pm_engine_thermal(pf_variant::String, pf_scenario::String, value_col::Symbol)
    path = joinpath(RESULTS_ROOT_44, "opf_endpoint_pf_inequality_levels_julia.csv")
    df = CSV.read(path, DataFrame)
    sub = filter(r -> r.variant == pf_variant && r.case == "1888_rte" && r.scenario == pf_scenario && r.constraint_type == "thermal", df)
    result = Dict{String, Float64}()
    for r in eachrow(sub)
        result[string(r.element_id)] = Float64(r[value_col])
    end
    return result
end

function process_endpoint_thermal(spec, entries)
    entry = find_entry_44(entries, spec.match_text)
    case = OwnPowerModels.powermodels_to_powercase(deepcopy(entry.data))

    rate_by_branch = Dict{String, Float64}()
    from_bus_by_branch = Dict{String, String}()
    to_bus_by_branch = Dict{String, String}()
    for branch in case.branches
        branch.status == 0 && continue
        rate_by_branch[branch.id] = branch.rate_a
        from_bus_by_branch[branch.id] = branch.from_bus
        to_bus_by_branch[branch.id] = branch.to_bus
    end

    rows = NamedTuple[]
    for (endpoint_source, value_col) in (("own_opf_solution", :own_pf_value), ("powermodels_opf_solution", :pm_pf_value))
        vm, va = read_post_pf_voltage_state(spec.pf_variant, spec.pf_scenario, value_col)
        pm_engine_thermal = read_pm_engine_thermal(spec.pf_variant, spec.pf_scenario, value_col)
        flows = OwnPowerModels.calc_ac_branch_flows(case, vm, va)

        for (branch_id, rate_a) in rate_by_branch
            rate_a > 0.0 || continue
            haskey(flows, branch_id) || continue
            f = flows[branch_id]
            calc_sf = hypot(f.pf, f.qf)
            calc_st = hypot(f.pt, f.qt)
            calc_value = max(calc_sf, calc_st)
            own_thermal_margin = rate_a - calc_value
            pm_engine_value = get(pm_engine_thermal, branch_id, NaN)
            # pm_engine_value is genuinely PowerModels.jl's own thermal formula
            # (PowerModels.calc_branch_flow_ac, called by script 31's
            # calculate_pm_branch_flows!) evaluated at this same post-PF
            # state -- this IS h^p_thermal(x_endpoint,LF), not a proxy.
            pm_thermal_margin = rate_a - pm_engine_value
            pm_thermal_violates_limit = isfinite(pm_thermal_margin) && pm_thermal_margin < -MATERIAL_TOL_44
            push!(rows, (
                model_variant=spec.model_variant,
                scenario=spec.out_scenario,
                endpoint_source=endpoint_source,
                branch_id=branch_id,
                from_bus=from_bus_by_branch[branch_id],
                to_bus=to_bus_by_branch[branch_id],
                rate_a=rate_a,
                calc_sf=calc_sf,
                calc_st=calc_st,
                calc_value=calc_value,
                own_thermal_margin=own_thermal_margin,
                own_thermal_violates_limit=own_thermal_margin < -MATERIAL_TOL_44,
                pm_engine_name="PowerModels.jl calc_branch_flow_ac",
                pm_engine_value=pm_engine_value,
                pm_thermal_margin=pm_thermal_margin,
                pm_thermal_violates_limit=pm_thermal_violates_limit,
                ohms_consistency_residual=calc_value - pm_engine_value,
                ohms_consistency_satisfied_1e4=abs(calc_value - pm_engine_value) <= MATERIAL_TOL_44,
            ))
        end
    end
    return rows
end

function main()
    ac_optimizer = build_ac_optimizer()
    case_label = "1888_rte"
    base_data = PowerModels.parse_file(joinpath(CASE_DIR, "pglib_opf_case$(case_label).m"))
    entries = compact_scenarios(base_data, case_label, ac_optimizer; include_no_reference=true)

    println("Q7/Q8 follow-up: own model's h^e_thermal at post-PF operating points")
    all_rows = NamedTuple[]
    for spec in ENDPOINT_THERMAL_SCENARIOS
        rows = process_endpoint_thermal(spec, entries)
        append!(all_rows, rows)
        n_violations = count(r -> r.own_thermal_violates_limit, rows)
        n_pm_violations = count(r -> r.pm_thermal_violates_limit, rows)
        max_resid = isempty(rows) ? NaN : maximum(abs.(getfield.(rows, :ohms_consistency_residual)))
        println("  $(spec.model_variant)/$(spec.pf_scenario): $(length(rows)) branch-endpoint rows, $(n_violations) own_thermal violations, $(n_pm_violations) pm_thermal violations, max ohms-consistency residual = $(max_resid)")
    end

    df = DataFrame(all_rows)
    out_path = joinpath(OUT_ROOT_44, "endpoint_pf_own_thermal_evaluation.csv")
    mkpath(OUT_ROOT_44)
    CSV.write(out_path, df)
    println("Wrote: $(out_path)")
end

main()
