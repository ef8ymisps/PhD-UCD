# Completing the "both reference points feasible" argument for the
# deviation-minimization rebuild: evaluates the OWN model's equality
# (power balance, Ohm's law) constraint formulas AT the own model's OWN
# solution point x^{e*}, with zero re-optimization. Mirrors script 41
# (which does the same at PowerModels' x^{p*}) almost verbatim -- only the
# column prefix read from the raw per-scenario CSVs changes (own_* instead
# of pm_*).
#
# This is a confirmatory / independent cross-check, not new territory:
# g^e(x^{e*})=0 is already implicit in a successful Ipopt solve (its own
# constr_viol_tol enforces it internally) -- but the whole point of this
# investigation's methodology (see Q3) is to never trust a solver's status
# flag as the only evidence of feasibility.
#
# The inequality half (h^e(x^{e*})) needs NO new computation: the existing
# pg_bounds.csv/qg_bounds.csv/vm_bounds.csv/angle_difference.csv/thermal.csv
# (script 45's output) already carry own_value/own_margin/own_violates_limit
# -- that IS h^e(x^{e*}), already computed. This script only produces the
# equality side.

const TWENTYFIVE_SCRIPT_PATH_46 = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH_46, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH_46).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH_46)
end

const MATERIAL_TOL_46 = 1e-4
const AT_OWN_OUT_ROOT = joinpath(ROOT, "results", "own_vs_powermodels", "_organized_for_meeting", "10_opf_solution_inspection", "1888_rte_objective_gap", "by_model_scenario")

function disk_path_46(path::String)
    abs_path = isabspath(path) ? path : abspath(path)
    if Sys.iswindows() && !startswith(abs_path, "\\\\?\\")
        return "\\\\?\\" * abs_path
    end
    return abs_path
end

const AT_OWN_POINT_SCENARIOS = [
    (model_variant="julia_current", source_subdir="julia", scenario="load_x0.8", match_text="load_x0p8__load_scaling", formulation=:bus_injection),
    (model_variant="julia_current", source_subdir="julia", scenario="load_x1.2_reactive_stress", match_text="load_x1p2__reactive_power_stress", formulation=:bus_injection),
    (model_variant="julia_pm_like", source_subdir="julia_pm_like", scenario="load_x0.8", match_text="load_x0p8__load_scaling", formulation=:branch_flow),
    (model_variant="julia_pm_like", source_subdir="julia_pm_like", scenario="load_x1.2_reactive_stress", match_text="load_x1p2__reactive_power_stress", formulation=:branch_flow),
    (model_variant="julia_pm_like", source_subdir="julia_pm_like", scenario="qg_limits_x0.6", match_text="qg_limits_x0p6__reactive_generation_limits", formulation=:branch_flow),
]

function find_entry_at_own(entries, match_text::String)
    matches = filter(e -> occursin(match_text, String(e.scenario_id)), entries)
    isempty(matches) && error("No 1888_rte scenario matched substring: $(match_text)")
    length(matches) > 1 && error("Multiple 1888_rte scenarios matched substring $(match_text)")
    return only(matches)
end

"""Read the own model's OWN solution point x^{e*} straight off the
already-written raw CSVs -- no re-solve, x^{e*} is already on disk."""
function read_own_point(source_subdir::String, scenario::String)
    base = joinpath(ROOT, "results", "own_vs_powermodels", source_subdir, "1888_rte", scenario)
    gens = CSV.read(joinpath(base, "generators.csv"), DataFrame)
    buses = CSV.read(joinpath(base, "buses.csv"), DataFrame)
    branches = CSV.read(joinpath(base, "branches.csv"), DataFrame)

    return (
        own_pg=Dict(string(r.gen_id) => Float64(r.own_pg) for r in eachrow(gens)),
        own_qg=Dict(string(r.gen_id) => Float64(r.own_qg) for r in eachrow(gens)),
        own_vm=Dict(string(r.bus_id) => Float64(r.own_vm) for r in eachrow(buses)),
        own_va=Dict(string(r.bus_id) => Float64(r.own_va) for r in eachrow(buses)),
        own_pf=Dict(string(r.branch_id) => Float64(r.own_pf) for r in eachrow(branches)),
        own_qf=Dict(string(r.branch_id) => Float64(r.own_qf) for r in eachrow(branches)),
        own_pt=Dict(string(r.branch_id) => Float64(r.own_pt) for r in eachrow(branches)),
        own_qt=Dict(string(r.branch_id) => Float64(r.own_qt) for r in eachrow(branches)),
    )
end

"""Power balance residual, bus-injection (julia_current): same as script
41's :bus_injection method, evaluated at x^{e*} instead of x^{p*}."""
function power_balance_residual_rows_own(::Val{:bus_injection}, case, data, own)
    vm_vec = [own.own_vm[b] for b in data.bus_ids]
    va_vec = [own.own_va[b] for b in data.bus_ids]
    P, Q = OwnPowerModels.calc_bus_power_injections(data.Ybus, vm_vec, va_vec)

    rows_p = NamedTuple[]
    rows_q = NamedTuple[]
    for (idx, bus_id) in enumerate(data.bus_ids)
        pgen = sum((get(own.own_pg, g, 0.0) for g in data.gens_by_bus[bus_id]); init=0.0)
        qgen = sum((get(own.own_qg, g, 0.0) for g in data.gens_by_bus[bus_id]); init=0.0)

        own_lhs_p = pgen - data.pd_by_bus[bus_id]
        own_rhs_p = P[idx]
        residual_p = own_lhs_p - own_rhs_p
        push!(rows_p, (bus_id=bus_id, own_lhs=own_lhs_p, own_rhs=own_rhs_p, residual=residual_p, satisfied_1e4=abs(residual_p) <= MATERIAL_TOL_46))

        own_lhs_q = qgen - data.qd_by_bus[bus_id]
        own_rhs_q = Q[idx]
        residual_q = own_lhs_q - own_rhs_q
        push!(rows_q, (bus_id=bus_id, own_lhs=own_lhs_q, own_rhs=own_rhs_q, residual=residual_q, satisfied_1e4=abs(residual_q) <= MATERIAL_TOL_46))
    end
    return DataFrame(rows_p), DataFrame(rows_q)
end

"""Power balance residual, branch-flow (julia_pm_like): same as script 41's
:branch_flow method, evaluated at x^{e*}'s own reported Pf*/Qf*/Pt*/Qt*."""
function power_balance_residual_rows_own(::Val{:branch_flow}, case, data, own)
    arcs_from = Dict(bus.id => String[] for bus in case.buses)
    arcs_to = Dict(bus.id => String[] for bus in case.buses)
    for branch in data.active_branches
        push!(arcs_from[branch.from_bus], branch.id)
        push!(arcs_to[branch.to_bus], branch.id)
    end

    rows_p = NamedTuple[]
    rows_q = NamedTuple[]
    for bus in case.buses
        bus_id = bus.id
        pgen = sum((get(own.own_pg, g, 0.0) for g in data.gens_by_bus[bus_id]); init=0.0)
        qgen = sum((get(own.own_qg, g, 0.0) for g in data.gens_by_bus[bus_id]); init=0.0)

        own_lhs_p = sum((get(own.own_pf, b, 0.0) for b in arcs_from[bus_id]); init=0.0) +
                    sum((get(own.own_pt, b, 0.0) for b in arcs_to[bus_id]); init=0.0)
        own_rhs_p = pgen - data.pd_by_bus[bus_id] - data.gs_by_bus[bus_id] * own.own_vm[bus_id]^2
        residual_p = own_lhs_p - own_rhs_p
        push!(rows_p, (bus_id=bus_id, own_lhs=own_lhs_p, own_rhs=own_rhs_p, residual=residual_p, satisfied_1e4=abs(residual_p) <= MATERIAL_TOL_46))

        own_lhs_q = sum((get(own.own_qf, b, 0.0) for b in arcs_from[bus_id]); init=0.0) +
                    sum((get(own.own_qt, b, 0.0) for b in arcs_to[bus_id]); init=0.0)
        own_rhs_q = qgen - data.qd_by_bus[bus_id] + data.bs_by_bus[bus_id] * own.own_vm[bus_id]^2
        residual_q = own_lhs_q - own_rhs_q
        push!(rows_q, (bus_id=bus_id, own_lhs=own_lhs_q, own_rhs=own_rhs_q, residual=residual_q, satisfied_1e4=abs(residual_q) <= MATERIAL_TOL_46))
    end
    return DataFrame(rows_p), DataFrame(rows_q)
end

"""Ohm's-law consistency at x^{e*}: recompute Pf/Qf/Pt/Qt from the own
model's own Vm*/Va* via calc_ac_branch_flows and diff against the own
model's own reported flow (own_pf etc, from the raw export). For the
bus-injection formulation this is a genuine cross-check (that formulation
has no explicit flow variables); for branch-flow it confirms the solved
Ohm's-law-pinning constraints truly hold to full precision."""
function ohms_consistency_tables_own(case, own)
    flows = OwnPowerModels.calc_ac_branch_flows(case, own.own_vm, own.own_va)
    pf_rows = NamedTuple[]
    qf_rows = NamedTuple[]
    pt_rows = NamedTuple[]
    qt_rows = NamedTuple[]
    for branch in case.branches
        branch.status == 0 && continue
        haskey(flows, branch.id) || continue
        calc = flows[branch.id]
        own_pf = get(own.own_pf, branch.id, NaN)
        own_qf = get(own.own_qf, branch.id, NaN)
        own_pt = get(own.own_pt, branch.id, NaN)
        own_qt = get(own.own_qt, branch.id, NaN)

        push!(pf_rows, (branch_id=branch.id, from_bus=branch.from_bus, to_bus=branch.to_bus, own_value=own_pf, calc_value=calc.pf, residual=calc.pf - own_pf, satisfied_1e4=abs(calc.pf - own_pf) <= MATERIAL_TOL_46))
        push!(qf_rows, (branch_id=branch.id, from_bus=branch.from_bus, to_bus=branch.to_bus, own_value=own_qf, calc_value=calc.qf, residual=calc.qf - own_qf, satisfied_1e4=abs(calc.qf - own_qf) <= MATERIAL_TOL_46))
        push!(pt_rows, (branch_id=branch.id, from_bus=branch.from_bus, to_bus=branch.to_bus, own_value=own_pt, calc_value=calc.pt, residual=calc.pt - own_pt, satisfied_1e4=abs(calc.pt - own_pt) <= MATERIAL_TOL_46))
        push!(qt_rows, (branch_id=branch.id, from_bus=branch.from_bus, to_bus=branch.to_bus, own_value=own_qt, calc_value=calc.qt, residual=calc.qt - own_qt, satisfied_1e4=abs(calc.qt - own_qt) <= MATERIAL_TOL_46))
    end
    return DataFrame(pf_rows), DataFrame(qf_rows), DataFrame(pt_rows), DataFrame(qt_rows)
end

function process_at_own_point_scenario(spec, entries)
    entry = find_entry_at_own(entries, spec.match_text)
    case = OwnPowerModels.powermodels_to_powercase(deepcopy(entry.data))
    data = OwnPowerModels.build_ac_opf_data(case)
    own = read_own_point(spec.source_subdir, spec.scenario)

    pb_p, pb_q = power_balance_residual_rows_own(Val(spec.formulation), case, data, own)
    pf_df, qf_df, pt_df, qt_df = ohms_consistency_tables_own(case, own)

    out_dir = joinpath(AT_OWN_OUT_ROOT, spec.model_variant, spec.scenario, "at_own_point")
    mkpath(disk_path_46(out_dir))
    write_at_own_csv(name) = disk_path_46(joinpath(out_dir, name))
    CSV.write(write_at_own_csv("power_balance_p.csv"), pb_p)
    CSV.write(write_at_own_csv("power_balance_q.csv"), pb_q)
    CSV.write(write_at_own_csv("pf_ohms_at_own_point.csv"), pf_df)
    CSV.write(write_at_own_csv("qf_ohms_at_own_point.csv"), qf_df)
    CSV.write(write_at_own_csv("pt_ohms_at_own_point.csv"), pt_df)
    CSV.write(write_at_own_csv("qt_ohms_at_own_point.csv"), qt_df)
    CSV.write(write_at_own_csv("at_own_point_manifest.csv"), DataFrame([(
        model_variant=spec.model_variant, case="1888_rte", scenario=spec.scenario,
        formulation=String(spec.formulation), status="included",
    )]))

    max_pb = isempty(pb_p) ? NaN : max(maximum(abs.(pb_p.residual)), maximum(abs.(pb_q.residual)))
    println("  wrote at_own_point tables for $(spec.model_variant)/$(spec.scenario) -- max |power balance residual| = $(max_pb)")
end

function main()
    ac_optimizer = build_ac_optimizer()
    case_label = "1888_rte"
    base_data = PowerModels.parse_file(joinpath(CASE_DIR, "pglib_opf_case$(case_label).m"))
    entries = compact_scenarios(base_data, case_label, ac_optimizer; include_no_reference=true)

    println("Completing the feasibility argument: g^e(x^e*) residuals at the own model's own point")
    println("Scenario entries available: $(length(entries))")

    for spec in AT_OWN_POINT_SCENARIOS
        try
            process_at_own_point_scenario(spec, entries)
        catch err
            println("  ERROR on $(spec.model_variant)/$(spec.scenario): ", sprint(showerror, err))
            showerror(stdout, err, catch_backtrace())
            println()
        end
    end
end

main()
