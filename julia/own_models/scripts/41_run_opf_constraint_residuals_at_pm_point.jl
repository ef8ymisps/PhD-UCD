# Q3-Q6 plug-in-point evaluation for julia_current and julia_pm_like.
#
# Evaluates the OWN model's equality (power balance, Ohm's law) and
# inequality constraint formulas AT PowerModels' solution point x^{p*}, with
# zero re-optimization. This is a distinct question from what
# python/own_models/scripts/45_build_opf_solution_inspection_tables.py
# answers: script 45 compares two INDEPENDENTLY-SOLVED optimal points
# (own's own re-solve vs PowerModels' own solve); this script instead plugs
# PowerModels' own reported values directly into the own model's own
# constraint formulas and records the resulting residual/margin, with no
# solver call at all.
#
# Output goes into a new at_pm_point/ subfolder nested inside script 45's
# existing per-scenario folders -- never mixed into script 45's own files
# (which it fully overwrites on every run).

const TWENTYFIVE_SCRIPT_PATH_41 = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH_41, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH_41).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH_41)
end

const MATERIAL_TOL = 1e-4
const AT_PM_OUT_ROOT = joinpath(ROOT, "results", "own_vs_powermodels", "_organized_for_meeting", "10_opf_solution_inspection", "1888_rte_objective_gap", "by_model_scenario")

# Some of these scenario folders (e.g. julia_pm_like/load_x1.2_reactive_stress/
# at_pm_point/...) exceed Windows' legacy 260-char MAX_PATH once nested this
# deep -- confirmed by a real ENAMETOOLONG/"No such file" failure on first
# run. The `\\?\` prefix opts into the Win32 long-path API, same fix already
# used by python/own_models/scripts/45_build_opf_solution_inspection_tables.py's
# own `disk_path` helper.
function disk_path(path::String)
    abs_path = isabspath(path) ? path : abspath(path)
    if Sys.iswindows() && !startswith(abs_path, "\\\\?\\")
        return "\\\\?\\" * abs_path
    end
    return abs_path
end

const AT_PM_POINT_SCENARIOS = [
    (model_variant="julia_current", source_subdir="julia", scenario="load_x0.8", match_text="load_x0p8__load_scaling", formulation=:bus_injection),
    (model_variant="julia_current", source_subdir="julia", scenario="load_x1.2_reactive_stress", match_text="load_x1p2__reactive_power_stress", formulation=:bus_injection),
    (model_variant="julia_pm_like", source_subdir="julia_pm_like", scenario="load_x0.8", match_text="load_x0p8__load_scaling", formulation=:branch_flow),
    (model_variant="julia_pm_like", source_subdir="julia_pm_like", scenario="load_x1.2_reactive_stress", match_text="load_x1p2__reactive_power_stress", formulation=:branch_flow),
    (model_variant="julia_pm_like", source_subdir="julia_pm_like", scenario="qg_limits_x0.6", match_text="qg_limits_x0p6__reactive_generation_limits", formulation=:branch_flow),
]

function find_entry_at_pm(entries, match_text::String)
    matches = filter(e -> occursin(match_text, String(e.scenario_id)), entries)
    isempty(matches) && error("No 1888_rte scenario matched substring: $(match_text)")
    length(matches) > 1 && error("Multiple 1888_rte scenarios matched substring $(match_text)")
    return only(matches)
end

"""Read PowerModels' solution point x^{p*} straight off the already-written
raw CSVs (results/own_vs_powermodels/<source_subdir>/1888_rte/<scenario>/) --
no re-solve of anything, PowerModels' point is already on disk."""
function read_pm_point(source_subdir::String, scenario::String)
    base = joinpath(ROOT, "results", "own_vs_powermodels", source_subdir, "1888_rte", scenario)
    gens = CSV.read(joinpath(base, "generators.csv"), DataFrame)
    buses = CSV.read(joinpath(base, "buses.csv"), DataFrame)
    branches = CSV.read(joinpath(base, "branches.csv"), DataFrame)

    return (
        pm_pg=Dict(string(r.gen_id) => Float64(r.pm_pg) for r in eachrow(gens)),
        pm_qg=Dict(string(r.gen_id) => Float64(r.pm_qg) for r in eachrow(gens)),
        pm_vm=Dict(string(r.bus_id) => Float64(r.pm_vm) for r in eachrow(buses)),
        pm_va=Dict(string(r.bus_id) => Float64(r.pm_va) for r in eachrow(buses)),
        pm_pf=Dict(string(r.branch_id) => Float64(r.pm_pf) for r in eachrow(branches)),
        pm_qf=Dict(string(r.branch_id) => Float64(r.pm_qf) for r in eachrow(branches)),
        pm_pt=Dict(string(r.branch_id) => Float64(r.pm_pt) for r in eachrow(branches)),
        pm_qt=Dict(string(r.branch_id) => Float64(r.pm_qt) for r in eachrow(branches)),
        branches_raw=branches,
    )
end

"""Power balance residual for the bus-injection (julia_current) formulation:
own model's own formula (add_ac_power_balance!, ac/constraints.jl) evaluated
at PM's Vm*/Va*/Pg*/Qg*, via the same Ybus-based calc_bus_power_injections
used by the real solve -- no JuMP, pure arithmetic."""
function power_balance_residual_rows(::Val{:bus_injection}, case, data, pm)
    vm_vec = [pm.pm_vm[b] for b in data.bus_ids]
    va_vec = [pm.pm_va[b] for b in data.bus_ids]
    P, Q = OwnPowerModels.calc_bus_power_injections(data.Ybus, vm_vec, va_vec)

    rows_p = NamedTuple[]
    rows_q = NamedTuple[]
    for (idx, bus_id) in enumerate(data.bus_ids)
        pgen = sum((get(pm.pm_pg, g, 0.0) for g in data.gens_by_bus[bus_id]); init=0.0)
        qgen = sum((get(pm.pm_qg, g, 0.0) for g in data.gens_by_bus[bus_id]); init=0.0)

        own_lhs_p = pgen - data.pd_by_bus[bus_id]
        own_rhs_p = P[idx]
        residual_p = own_lhs_p - own_rhs_p
        push!(rows_p, (bus_id=bus_id, own_lhs=own_lhs_p, own_rhs=own_rhs_p, residual=residual_p, satisfied_1e4=abs(residual_p) <= MATERIAL_TOL))

        own_lhs_q = qgen - data.qd_by_bus[bus_id]
        own_rhs_q = Q[idx]
        residual_q = own_lhs_q - own_rhs_q
        push!(rows_q, (bus_id=bus_id, own_lhs=own_lhs_q, own_rhs=own_rhs_q, residual=residual_q, satisfied_1e4=abs(residual_q) <= MATERIAL_TOL))
    end
    return DataFrame(rows_p), DataFrame(rows_q)
end

"""Power balance residual for the branch-flow (julia_pm_like) formulation:
own model's own arc-sum formula (add_ac_power_balance!, ac/model.jl)
evaluated at PM's own reported Pf*/Qf*/Pt*/Qt* directly (no Ohm's-law
recompute needed for this particular residual -- that's the separate
Ohm's-law-consistency check below)."""
function power_balance_residual_rows(::Val{:branch_flow}, case, data, pm)
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
        pgen = sum((get(pm.pm_pg, g, 0.0) for g in data.gens_by_bus[bus_id]); init=0.0)
        qgen = sum((get(pm.pm_qg, g, 0.0) for g in data.gens_by_bus[bus_id]); init=0.0)

        own_lhs_p = sum((get(pm.pm_pf, b, 0.0) for b in arcs_from[bus_id]); init=0.0) +
                    sum((get(pm.pm_pt, b, 0.0) for b in arcs_to[bus_id]); init=0.0)
        own_rhs_p = pgen - data.pd_by_bus[bus_id] - data.gs_by_bus[bus_id] * pm.pm_vm[bus_id]^2
        residual_p = own_lhs_p - own_rhs_p
        push!(rows_p, (bus_id=bus_id, own_lhs=own_lhs_p, own_rhs=own_rhs_p, residual=residual_p, satisfied_1e4=abs(residual_p) <= MATERIAL_TOL))

        own_lhs_q = sum((get(pm.pm_qf, b, 0.0) for b in arcs_from[bus_id]); init=0.0) +
                    sum((get(pm.pm_qt, b, 0.0) for b in arcs_to[bus_id]); init=0.0)
        own_rhs_q = qgen - data.qd_by_bus[bus_id] + data.bs_by_bus[bus_id] * pm.pm_vm[bus_id]^2
        residual_q = own_lhs_q - own_rhs_q
        push!(rows_q, (bus_id=bus_id, own_lhs=own_lhs_q, own_rhs=own_rhs_q, residual=residual_q, satisfied_1e4=abs(residual_q) <= MATERIAL_TOL))
    end
    return DataFrame(rows_p), DataFrame(rows_q)
end

"""Ohm's-law consistency: recompute Pf/Qf/Pt/Qt from PM's own Vm*/Va* via
calc_ac_branch_flows (new function, admittance_matrix.jl) and diff against
PM's own reported flow. Applies to all variants -- even bus-injection
implicitly relies on this same formula for its thermal/angle limits."""
function ohms_consistency_tables(case, pm)
    flows = OwnPowerModels.calc_ac_branch_flows(case, pm.pm_vm, pm.pm_va)
    pf_rows = NamedTuple[]
    qf_rows = NamedTuple[]
    pt_rows = NamedTuple[]
    qt_rows = NamedTuple[]
    for branch in case.branches
        branch.status == 0 && continue
        haskey(flows, branch.id) || continue
        calc = flows[branch.id]
        pm_pf = get(pm.pm_pf, branch.id, NaN)
        pm_qf = get(pm.pm_qf, branch.id, NaN)
        pm_pt = get(pm.pm_pt, branch.id, NaN)
        pm_qt = get(pm.pm_qt, branch.id, NaN)

        push!(pf_rows, (branch_id=branch.id, from_bus=branch.from_bus, to_bus=branch.to_bus, pm_value=pm_pf, calc_value=calc.pf, residual=calc.pf - pm_pf, satisfied_1e4=abs(calc.pf - pm_pf) <= MATERIAL_TOL))
        push!(qf_rows, (branch_id=branch.id, from_bus=branch.from_bus, to_bus=branch.to_bus, pm_value=pm_qf, calc_value=calc.qf, residual=calc.qf - pm_qf, satisfied_1e4=abs(calc.qf - pm_qf) <= MATERIAL_TOL))
        push!(pt_rows, (branch_id=branch.id, from_bus=branch.from_bus, to_bus=branch.to_bus, pm_value=pm_pt, calc_value=calc.pt, residual=calc.pt - pm_pt, satisfied_1e4=abs(calc.pt - pm_pt) <= MATERIAL_TOL))
        push!(qt_rows, (branch_id=branch.id, from_bus=branch.from_bus, to_bus=branch.to_bus, pm_value=pm_qt, calc_value=calc.qt, residual=calc.qt - pm_qt, satisfied_1e4=abs(calc.qt - pm_qt) <= MATERIAL_TOL))
    end
    return DataFrame(pf_rows), DataFrame(qf_rows), DataFrame(pt_rows), DataFrame(qt_rows)
end

"""Thermal-at-PM's-point: genuine new computation, since script 45's
existing thermal.csv `pm_value` is PM's own reported apparent power, not the
own model's Ohm's-law formula evaluated at PM's Vm*/Va*. Derives Sf/St from
the Ohm's-law recompute above, joins rate_a from the raw branches.csv, and
diffs against script 45's existing own_margin (own model at x^{e*})."""
function thermal_at_pm_point_table(model_variant::String, scenario::String, branches_raw, pf_df, qf_df, pt_df, qt_df)
    existing_path = disk_path(joinpath(AT_PM_OUT_ROOT, model_variant, scenario, "thermal.csv"))
    existing = isfile(existing_path) ? CSV.read(existing_path, DataFrame) : nothing

    rate_by_branch = Dict{String,Float64}()
    from_bus_by_branch = Dict{String,String}()
    to_bus_by_branch = Dict{String,String}()
    for r in eachrow(branches_raw)
        bid = string(r.branch_id)
        rate_by_branch[bid] = Float64(r.rate_a)
        from_bus_by_branch[bid] = string(r.from_bus)
        to_bus_by_branch[bid] = string(r.to_bus)
    end

    calc_pf = Dict(string(r.branch_id) => Float64(r.calc_value) for r in eachrow(pf_df))
    calc_qf = Dict(string(r.branch_id) => Float64(r.calc_value) for r in eachrow(qf_df))
    calc_pt = Dict(string(r.branch_id) => Float64(r.calc_value) for r in eachrow(pt_df))
    calc_qt = Dict(string(r.branch_id) => Float64(r.calc_value) for r in eachrow(qt_df))

    own_margin_at_own_from = Dict{String,Float64}()
    own_margin_at_own_to = Dict{String,Float64}()
    if existing !== nothing
        for r in eachrow(existing)
            bid = string(r.branch_id)
            if r.constraint_type == "thermal_from"
                own_margin_at_own_from[bid] = r.own_margin
            elseif r.constraint_type == "thermal_to"
                own_margin_at_own_to[bid] = r.own_margin
            end
        end
    end

    rows = NamedTuple[]
    for (bid, rate_a) in rate_by_branch
        rate_a > 0.0 || continue
        cf = hypot(calc_pf[bid], calc_qf[bid])
        own_margin_at_pm_f = rate_a - cf
        own_margin_at_own_f = get(own_margin_at_own_from, bid, NaN)
        push!(rows, (
            constraint_type="thermal_from", branch_id=bid, from_bus=from_bus_by_branch[bid], to_bus=to_bus_by_branch[bid],
            calc_value=cf, own_margin_at_own=own_margin_at_own_f, own_margin_at_pm=own_margin_at_pm_f,
            margin_gap=own_margin_at_pm_f - own_margin_at_own_f, calc_violates_limit=own_margin_at_pm_f < -MATERIAL_TOL,
        ))

        ct = hypot(calc_pt[bid], calc_qt[bid])
        own_margin_at_pm_t = rate_a - ct
        own_margin_at_own_t = get(own_margin_at_own_to, bid, NaN)
        push!(rows, (
            constraint_type="thermal_to", branch_id=bid, from_bus=from_bus_by_branch[bid], to_bus=to_bus_by_branch[bid],
            calc_value=ct, own_margin_at_own=own_margin_at_own_t, own_margin_at_pm=own_margin_at_pm_t,
            margin_gap=own_margin_at_pm_t - own_margin_at_own_t, calc_violates_limit=own_margin_at_pm_t < -MATERIAL_TOL,
        ))
    end
    return DataFrame(rows)
end

"""Re-project script 45's existing pg_bounds/qg_bounds/vm_bounds/
angle_difference CSVs into simply-named at_pm_point files. For these four
families, PowerModels' own reported value combined with the SHARED bound
parameters (pmin/pmax/... are one column, not own_*/pm_* pairs -- confirmed
by reading a real raw CSV) already IS the own model's bound formula
evaluated at x^{p*}: `pm_margin` there is not new computation, just a
rename/re-projection for a dedicated, easy-to-find file."""
function reprojected_bound_table(model_variant::String, scenario::String, family::String)
    path = disk_path(joinpath(AT_PM_OUT_ROOT, model_variant, scenario, "$(family).csv"))
    isfile(path) || return DataFrame()
    df = CSV.read(path, DataFrame)
    isempty(df) && return DataFrame()

    out = DataFrame(constraint_type=df.constraint_type)
    if family in ("pg_bounds", "qg_bounds")
        out.gen_id = df.gen_id
        out.bus_id = df.bus_id
    elseif family == "vm_bounds"
        out.bus_id = df.bus_id
    else # angle_difference
        out.branch_id = df.branch_id
        out.from_bus = df.from_bus
        out.to_bus = df.to_bus
    end
    out.own_margin_at_own = df.own_margin
    out.own_margin_at_pm = df.pm_margin
    out.margin_gap = df.pm_margin .- df.own_margin
    out.pm_violates_limit = df.pm_violates_limit
    return out
end

function process_at_pm_point_scenario(spec, entries)
    entry = find_entry_at_pm(entries, spec.match_text)
    case = OwnPowerModels.powermodels_to_powercase(deepcopy(entry.data))
    data = OwnPowerModels.build_ac_opf_data(case)
    pm = read_pm_point(spec.source_subdir, spec.scenario)

    pb_p, pb_q = power_balance_residual_rows(Val(spec.formulation), case, data, pm)
    pf_df, qf_df, pt_df, qt_df = ohms_consistency_tables(case, pm)
    thermal_df = thermal_at_pm_point_table(spec.model_variant, spec.scenario, pm.branches_raw, pf_df, qf_df, pt_df, qt_df)

    pg_df = reprojected_bound_table(spec.model_variant, spec.scenario, "pg_bounds")
    qg_df = reprojected_bound_table(spec.model_variant, spec.scenario, "qg_bounds")
    vm_df = reprojected_bound_table(spec.model_variant, spec.scenario, "vm_bounds")
    angle_df = reprojected_bound_table(spec.model_variant, spec.scenario, "angle_difference")

    out_dir = joinpath(AT_PM_OUT_ROOT, spec.model_variant, spec.scenario, "at_pm_point")
    mkpath(disk_path(out_dir))
    write_at_pm_csv(name) = disk_path(joinpath(out_dir, name))
    CSV.write(write_at_pm_csv("power_balance_p.csv"), pb_p)
    CSV.write(write_at_pm_csv("power_balance_q.csv"), pb_q)
    CSV.write(write_at_pm_csv("pf_ohms_at_pm_point.csv"), pf_df)
    CSV.write(write_at_pm_csv("qf_ohms_at_pm_point.csv"), qf_df)
    CSV.write(write_at_pm_csv("pt_ohms_at_pm_point.csv"), pt_df)
    CSV.write(write_at_pm_csv("qt_ohms_at_pm_point.csv"), qt_df)
    CSV.write(write_at_pm_csv("pg_bounds_at_pm_point.csv"), pg_df)
    CSV.write(write_at_pm_csv("qg_bounds_at_pm_point.csv"), qg_df)
    CSV.write(write_at_pm_csv("vm_bounds_at_pm_point.csv"), vm_df)
    CSV.write(write_at_pm_csv("angle_difference_at_pm_point.csv"), angle_df)
    CSV.write(write_at_pm_csv("thermal_at_pm_point.csv"), thermal_df)
    CSV.write(write_at_pm_csv("at_pm_point_manifest.csv"), DataFrame([(
        model_variant=spec.model_variant, case="1888_rte", scenario=spec.scenario,
        formulation=String(spec.formulation), status="included",
    )]))

    max_pb = isempty(pb_p) ? NaN : max(maximum(abs.(pb_p.residual)), maximum(abs.(pb_q.residual)))
    println("  wrote at_pm_point tables for $(spec.model_variant)/$(spec.scenario) -- max |power balance residual| = $(max_pb)")
end

function main()
    ac_optimizer = build_ac_optimizer()
    case_label = "1888_rte"
    base_data = PowerModels.parse_file(joinpath(CASE_DIR, "pglib_opf_case$(case_label).m"))
    entries = compact_scenarios(base_data, case_label, ac_optimizer; include_no_reference=true)

    println("Q3-Q6 plug-in-point residuals at PowerModels' solution")
    println("Scenario entries available: $(length(entries))")

    for spec in AT_PM_POINT_SCENARIOS
        try
            process_at_pm_point_scenario(spec, entries)
        catch err
            println("  ERROR on $(spec.model_variant)/$(spec.scenario): ", sprint(showerror, err))
            showerror(stdout, err, catch_backtrace())
            println()
        end
    end
end

main()
