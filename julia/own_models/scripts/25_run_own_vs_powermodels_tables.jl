# Set A: Julia own-model vs PowerModels, per-case inspection tables.
#
# For each of the 8 default PGLib cases' base scenario, writes 6 CSVs to
# results/own_vs_powermodels/julia/<case>/:
#   generators.csv, buses.csv, branches.csv  -- one row per element, own vs
#     PowerModels value, diff, limits, and per-limit binding flags. These are
#     NOT new computations -- they're compare_ac_opf_{generator,bus,branch}_rows
#     (julia/own_models/src/analysis/opf_solution_inspection.jl), the same
#     functions already behind ac_opf_summary_row and the original
#     binding-constraint-mismatch tables, just written straight to CSV per
#     case instead of only being aggregated into one summary row.
#   shunts.csv -- static input data (bus id, gs, bs, status), no PM
#     comparison needed since shunts aren't solved for.
#   constraints_inventory.csv -- one row per constraint TYPE (not instance):
#     formula, where it's implemented (file:line), how many instances exist
#     in this case.
#   constraints_binding.csv -- one row per constraint INSTANCE across the 9
#     inequality-type categories (pg/qg/vm bounds, thermal, angle diff),
#     with own/PM value, bound, margin (positive = slack, ~0 = binding), and
#     binding flags for both -- sorted by own_margin ascending so the
#     closest-to-binding instances are on top. Power balance (equality)
#     constraints are listed in the inventory but not here -- "margin to a
#     bound" isn't a meaningful concept for an equality constraint.

# Module inclusion happens exactly once, via 09's own internal
# `include(OwnPowerModels.jl); using .OwnPowerModels` -- including it a
# second time here would create a second, distinct OwnPowerModels module
# instance, and Main would then see two different `PowerCase` bindings
# (ambiguity error), since compact_scenarios (used by the spotlight-scenario
# section below) is pulled in from 09 the same way scripts 16/18/20/24 do.
const NINE_SCRIPT_PATH = joinpath(@__DIR__, "09_run_compact_opf_validation_sweeps.jl")
let
    src = read(NINE_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(NINE_SCRIPT_PATH).")
    Base.include_string(Main, stripped, NINE_SCRIPT_PATH)
end

using JSON
using SHA

const ROOT = normpath(joinpath(@__DIR__, "..", "..", ".."))
const CASE_DIR = joinpath(ROOT, "data", "raw", "pglib", "matpower_m", "base_case")
const OUT_ROOT = joinpath(ROOT, "results", "own_vs_powermodels", "julia")
const PM_LIKE_OUT_ROOT = joinpath(ROOT, "results", "own_vs_powermodels", "julia_pm_like")
const REFERENCE_DIR = joinpath(ROOT, "results", "reference", "powermodels", "ac_opf")

const DEFAULT_CASES = [
    "pglib_opf_case5_pjm", "pglib_opf_case14_ieee", "pglib_opf_case30_ieee",
    "pglib_opf_case118_ieee", "pglib_opf_case300_ieee", "pglib_opf_case1354_pegase",
    "pglib_opf_case1888_rte", "pglib_opf_case6470_rte",
]

short_case_name(case_file::String) = replace(case_file, "pglib_opf_case" => "")

function build_ac_optimizer()
    return optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => 3000)
end

# ------------------------------------------------------------
# Constraints inventory (static structure + per-case instance counts)
# ------------------------------------------------------------

function constraints_inventory_rows(case::OwnPowerModels.PowerCase)
    n_bus = length(case.buses)
    n_gen = length(case.generators)
    n_branch = length(case.branches)
    return [
        (constraint_type="power_balance_p", applies_to="bus", formula="sum(p_arc) = sum(Pg) - Pd - Gs*Vm^2", file_line="ac/model.jl:156-180 (add_ac_power_balance!)", n_instances=n_bus, tolerance_used=NaN),
        (constraint_type="power_balance_q", applies_to="bus", formula="sum(q_arc) = sum(Qg) - Qd + Bs*Vm^2", file_line="ac/model.jl:156-180 (add_ac_power_balance!)", n_instances=n_bus, tolerance_used=NaN),
        (constraint_type="pg_min", applies_to="generator", formula="Pg >= Pmin", file_line="ac/model.jl:96-137 (add_ac_variables!, set_lower_bound)", n_instances=n_gen, tolerance_used=1e-6),
        (constraint_type="pg_max", applies_to="generator", formula="Pg <= Pmax", file_line="ac/model.jl:96-137 (add_ac_variables!, set_upper_bound)", n_instances=n_gen, tolerance_used=1e-6),
        (constraint_type="qg_min", applies_to="generator", formula="Qg >= Qmin", file_line="ac/model.jl:96-137 (add_ac_variables!, set_lower_bound)", n_instances=n_gen, tolerance_used=1e-6),
        (constraint_type="qg_max", applies_to="generator", formula="Qg <= Qmax", file_line="ac/model.jl:96-137 (add_ac_variables!, set_upper_bound)", n_instances=n_gen, tolerance_used=1e-6),
        (constraint_type="vm_min", applies_to="bus", formula="Vm >= Vmin", file_line="ac/model.jl:96-137 (add_ac_variables!, set_lower_bound)", n_instances=n_bus, tolerance_used=1e-6),
        (constraint_type="vm_max", applies_to="bus", formula="Vm <= Vmax", file_line="ac/model.jl:96-137 (add_ac_variables!, set_upper_bound)", n_instances=n_bus, tolerance_used=1e-6),
        (constraint_type="thermal", applies_to="branch", formula="max(Sf, St) <= rate_a", file_line="ac/model.jl:234-283 (add_ac_branch_constraints!, thermal_from/thermal_to)", n_instances=n_branch, tolerance_used=1e-6),
        (constraint_type="angle_lower", applies_to="branch", formula="theta_i - theta_j >= angmin", file_line="ac/model.jl:234-283 (add_ac_branch_constraints!, angle_diff)", n_instances=n_branch, tolerance_used=1e-6),
        (constraint_type="angle_upper", applies_to="branch", formula="theta_i - theta_j <= angmax", file_line="ac/model.jl:234-283 (add_ac_branch_constraints!, angle_diff)", n_instances=n_branch, tolerance_used=1e-6),
    ]
end

# ------------------------------------------------------------
# Constraints binding, long format across the 9 inequality categories
# ------------------------------------------------------------

function constraints_binding_rows(gen_rows, bus_rows, branch_rows)
    rows = NamedTuple[]
    for r in gen_rows
        push!(rows, (constraint_type="pg_min", element_id=r.gen_id, bound=r.pmin, own_value=r.own_pg, pm_value=r.pm_pg, own_margin=r.own_pg - r.pmin, pm_margin=r.pm_pg - r.pmin, own_binding=r.own_at_pmin, pm_binding=r.pm_at_pmin))
        push!(rows, (constraint_type="pg_max", element_id=r.gen_id, bound=r.pmax, own_value=r.own_pg, pm_value=r.pm_pg, own_margin=r.pmax - r.own_pg, pm_margin=r.pmax - r.pm_pg, own_binding=r.own_at_pmax, pm_binding=r.pm_at_pmax))
        push!(rows, (constraint_type="qg_min", element_id=r.gen_id, bound=r.qmin, own_value=r.own_qg, pm_value=r.pm_qg, own_margin=r.own_qg - r.qmin, pm_margin=r.pm_qg - r.qmin, own_binding=r.own_at_qmin, pm_binding=r.pm_at_qmin))
        push!(rows, (constraint_type="qg_max", element_id=r.gen_id, bound=r.qmax, own_value=r.own_qg, pm_value=r.pm_qg, own_margin=r.qmax - r.own_qg, pm_margin=r.qmax - r.pm_qg, own_binding=r.own_at_qmax, pm_binding=r.pm_at_qmax))
    end
    for r in bus_rows
        push!(rows, (constraint_type="vm_min", element_id=r.bus_id, bound=r.vmin, own_value=r.own_vm, pm_value=r.pm_vm, own_margin=r.own_vm - r.vmin, pm_margin=r.pm_vm - r.vmin, own_binding=r.own_at_vmin, pm_binding=r.pm_at_vmin))
        push!(rows, (constraint_type="vm_max", element_id=r.bus_id, bound=r.vmax, own_value=r.own_vm, pm_value=r.pm_vm, own_margin=r.vmax - r.own_vm, pm_margin=r.vmax - r.pm_vm, own_binding=r.own_at_vmax, pm_binding=r.pm_at_vmax))
    end
    for r in branch_rows
        push!(rows, (constraint_type="thermal", element_id=r.branch_id, bound=r.rate_a, own_value=r.own_loading_abs, pm_value=r.pm_loading_abs, own_margin=r.own_flow_margin, pm_margin=r.pm_flow_margin, own_binding=r.own_binding, pm_binding=r.pm_binding))
        push!(rows, (constraint_type="angle_lower", element_id=r.branch_id, bound=r.angmin, own_value=r.own_angle_diff, pm_value=r.pm_angle_diff, own_margin=r.own_angle_diff - r.angmin, pm_margin=r.pm_angle_diff - r.angmin, own_binding=r.own_angle_lower_binding, pm_binding=r.pm_angle_lower_binding))
        push!(rows, (constraint_type="angle_upper", element_id=r.branch_id, bound=r.angmax, own_value=r.own_angle_diff, pm_value=r.pm_angle_diff, own_margin=r.angmax - r.own_angle_diff, pm_margin=r.angmax - r.pm_angle_diff, own_binding=r.own_angle_upper_binding, pm_binding=r.pm_angle_upper_binding))
    end
    df = DataFrame(rows)
    sort!(df, :own_margin)
    return df
end

function shunts_rows(case::OwnPowerModels.PowerCase)
    return [(shunt_id=s.id, bus_id=s.bus, gs=s.gs, bs=s.bs, status=s.status) for s in case.shunts]
end

# ------------------------------------------------------------
# Optional PowerModels reference-JSON export -- same schema as
# julia/tools/powermodels/scripts/07_export_ac_opf_reference.jl's
# reference_payload, self-contained here (not included from that script) to
# avoid include-ordering complexity. Lets an overnight full-sweep run
# produce, in one pass, both Set A's own comparison tables AND the reference
# files Python's Set B needs later -- without solving PowerModels twice.
# ------------------------------------------------------------

function json_ready(value)
    if value isa AbstractDict
        return Dict(string(k) => json_ready(v) for (k, v) in value)
    elseif value isa AbstractVector
        return [json_ready(v) for v in value]
    elseif value isa Tuple
        return [json_ready(v) for v in value]
    elseif value isa Missing
        return nothing
    elseif value isa Symbol
        return string(value)
    elseif value isa AbstractFloat
        return isfinite(value) ? value : nothing
    elseif value isa Number || value isa AbstractString || value isa Bool || value === nothing
        return value
    else
        return string(value)
    end
end

"""Windows' classic ~260-char MAX_PATH limit bites some of the longer
scenario_id-derived filenames this script writes (e.g. multi-target
reactive_generation_limits/qg_limits names on 1354_pegase land right at
261 chars) -- `open` then fails with a plain "No such file or directory"
even though the parent directory exists. The `\\\\?\\` extended-length
prefix (absolute, backslash-separated path, no `.`/`..` segments) is the
standard escape hatch; mirrors Python's own `disk_path()` workaround
(python/own_models/scripts/55_run_full_sweep_active_losses_table.py)."""
function long_path(path)
    Sys.iswindows() || return path
    resolved = normpath(abspath(path))
    resolved = replace(resolved, "/" => "\\")
    startswith(resolved, "\\\\?\\") && return resolved
    return "\\\\?\\" * resolved
end

function write_json(path, value)
    mkpath(dirname(path))
    open(long_path(path), "w") do io
        JSON.print(io, json_ready(value), 4)
    end
end

function sorted_ids(d)
    return sort(collect(keys(d)); by=x -> parse(Int, string(x)))
end

function safe_get(d, keys...; default=nothing)
    current = d
    for key in keys
        if current isa AbstractDict && haskey(current, key)
            current = current[key]
        else
            return default
        end
    end
    return current
end

function reference_payload(case_name, scenario_name, data, pm_result, pm_iterations, pm_solve_time_sec)
    solution = get(pm_result, "solution", Dict{String,Any}())
    status = string(get(pm_result, "termination_status", "UNKNOWN"))
    solved = status in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")

    generators = Dict{String,Any}()
    for gen_id in sorted_ids(data["gen"])
        gen = data["gen"][gen_id]
        generators[string(gen_id)] = Dict(
            "pg" => safe_get(solution, "gen", gen_id, "pg"), "qg" => safe_get(solution, "gen", gen_id, "qg"),
            "pmin" => gen["pmin"], "pmax" => gen["pmax"], "qmin" => gen["qmin"], "qmax" => gen["qmax"],
            "cost" => get(gen, "cost", []),
        )
    end
    buses = Dict{String,Any}()
    for bus_id in sorted_ids(data["bus"])
        bus = data["bus"][bus_id]
        buses[string(bus_id)] = Dict(
            "va" => safe_get(solution, "bus", bus_id, "va"), "vm" => safe_get(solution, "bus", bus_id, "vm"),
            "vmin" => bus["vmin"], "vmax" => bus["vmax"],
        )
    end
    branches = Dict{String,Any}()
    for branch_id in sorted_ids(data["branch"])
        branch = data["branch"][branch_id]
        branches[string(branch_id)] = Dict(
            "pf" => safe_get(solution, "branch", branch_id, "pf"), "qf" => safe_get(solution, "branch", branch_id, "qf"),
            "pt" => safe_get(solution, "branch", branch_id, "pt"), "qt" => safe_get(solution, "branch", branch_id, "qt"),
            "rate_a" => branch["rate_a"], "angmin" => get(branch, "angmin", -pi / 2), "angmax" => get(branch, "angmax", pi / 2),
        )
    end

    return Dict(
        "case_name" => case_name, "scenario" => scenario_name, "termination_status" => status, "solved" => solved,
        "objective" => get(pm_result, "objective", nothing),
        "solve_iterations" => isfinite(pm_iterations) ? pm_iterations : nothing,
        "solve_time_sec" => isfinite(pm_solve_time_sec) ? pm_solve_time_sec : nothing,
        "generators" => generators, "buses" => buses, "branches" => branches,
    )
end

function export_reference_json(label::String, scenario_label::String, data, pm_result, pm_iterations, pm_solve_time_sec)
    stem = "pglib_opf_case$(label)_$(scenario_label)"
    write_json(joinpath(REFERENCE_DIR, "$(stem)_case.json"), data)
    write_json(
        joinpath(REFERENCE_DIR, "$(stem)_reference.json"),
        reference_payload(label, scenario_label, data, pm_result, pm_iterations, pm_solve_time_sec),
    )
end

"""Core worker: solve own+PM on `data` (a PowerModels dict, already carrying
whatever scenario perturbation was applied) and write the 6 CSVs to
results/own_vs_powermodels/julia/<label>/<scenario_label>/. Shared by the
per-case base-scenario loop and the per-scenario spotlight loop below --
scenario generation differs (raw parse vs compact_scenarios), everything
after that point is identical."""
const SUMMARY_PATH = joinpath(OUT_ROOT, "summary.csv")
const PM_LIKE_SUMMARY_PATH = joinpath(PM_LIKE_OUT_ROOT, "summary.csv")

function append_summary_row!(row::NamedTuple; summary_path::String=SUMMARY_PATH)
    df = DataFrame([row])
    mkpath(dirname(summary_path))
    CSV.write(summary_path, df; append=isfile(summary_path))
end

"""`OUT_ROOT`/`SUMMARY_PATH` (bus-injection) are frozen historical snapshots
from before the bus-injection formulation was deleted -- kept only because
scripts 30/31/41/46 still read them for historical comparison. This script
no longer regenerates them; the sole AC-OPF model (arc-based, formerly
"pm_like") always writes to `PM_LIKE_OUT_ROOT`/`PM_LIKE_SUMMARY_PATH`."""
function own_variant_paths(own_variant::Symbol)
    own_variant == :pm_like_zero || error("Unsupported own AC OPF variant: $(own_variant). Expected :pm_like_zero.")
    return PM_LIKE_OUT_ROOT, PM_LIKE_SUMMARY_PATH
end

function solve_own_ac_variant(case::OwnPowerModels.PowerCase, ac_optimizer; own_variant::Symbol)
    own_variant == :pm_like_zero || error("Unsupported own AC OPF variant: $(own_variant). Expected :pm_like_zero.")
    return solve_ac_opf_own(case; optimizer=ac_optimizer, silent=true, fix_reference_angle=true, warm_start=:zero)
end

function safe_scenario_folder(label::String)
    if length(label) <= 80
        return label
    end
    digest = bytes2hex(sha1(label))[1:10]
    return string(label[1:min(end, 60)], "__", digest)
end

function process_data(label::String, scenario_label::String, data; ac_optimizer=build_ac_optimizer(), export_reference::Bool=false, own_variant::Symbol=:pm_like_zero, write_summary::Bool=true)
    out_root, summary_path = own_variant_paths(own_variant)
    println("=== $(label) / $(scenario_label) / $(own_variant) ===")
    out_dir = joinpath(out_root, label, safe_scenario_folder(scenario_label))
    mkpath(out_dir)
    if safe_scenario_folder(scenario_label) != scenario_label
        open(joinpath(out_dir, "scenario_label.txt"), "w") do io
            write(io, scenario_label)
        end
    end

    case = OwnPowerModels.powermodels_to_powercase(deepcopy(data))

    own_start = time()
    own_result = solve_own_ac_variant(case, ac_optimizer; own_variant=own_variant)
    own_solve_time_sec = time() - own_start

    pm_model = PowerModels.instantiate_model(deepcopy(data), PowerModels.ACPPowerModel, PowerModels.build_opf)
    pm_start = time()
    pm_result = PowerModels.optimize_model!(pm_model, optimizer=ac_optimizer)
    pm_solve_time_sec = time() - pm_start
    pm_iterations = OwnPowerModels._safe_barrier_iterations(pm_model.model)

    if export_reference
        try
            export_reference_json(label, scenario_label, data, pm_result, pm_iterations, pm_solve_time_sec)
        catch err
            println("  reference export failed for $(label)/$(scenario_label): ", sprint(showerror, err))
        end
    end

    own_status = string(own_result.termination_status)
    pm_status = string(get(pm_result, "termination_status", "UNKNOWN"))
    own_ok = own_status in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")
    pm_ok = pm_status in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")

    pm_objective = get(pm_result, "objective", NaN)
    obj_diff = (own_ok && pm_ok) ? own_result.objective - pm_objective : NaN
    obj_rel_diff = (own_ok && pm_ok) ? abs(obj_diff) / (1.0 + abs(pm_objective)) : NaN

    if write_summary
        append_summary_row!((
            case=label, scenario=scenario_label,
            own_status=own_status, pm_status=pm_status, outcome_match=own_ok == pm_ok,
            own_objective=own_ok ? own_result.objective : NaN, pm_objective=pm_ok ? pm_objective : NaN,
            objective_diff=obj_diff, objective_rel_diff=obj_rel_diff,
            own_solve_time_sec=own_solve_time_sec, pm_solve_time_sec=pm_solve_time_sec,
            own_solve_iterations=own_result.solve_iterations, pm_solve_iterations=pm_iterations,
        ); summary_path=summary_path)
    end

    if !own_ok || !pm_ok
        println("  SKIPPED (tables) -- own_ok=$(own_ok) pm_ok=$(pm_ok)")
        return
    end

    gen_rows = OwnPowerModels.compare_ac_opf_generator_rows(case, own_result, pm_result)
    bus_rows = OwnPowerModels.compare_ac_opf_bus_rows(case, own_result, pm_result)
    branch_rows = OwnPowerModels.compare_ac_opf_branch_rows(case, own_result, pm_result)

    # Drop `scenario` -- it's redundant with the output subfolder (which
    # already encodes case/scenario), and Python's Set B tables never had it
    # to begin with.
    drop_scenario(df) = select(df, Not(:scenario))
    CSV.write(joinpath(out_dir, "generators.csv"), drop_scenario(DataFrame(gen_rows)))
    CSV.write(joinpath(out_dir, "buses.csv"), drop_scenario(DataFrame(bus_rows)))
    CSV.write(joinpath(out_dir, "branches.csv"), drop_scenario(DataFrame(branch_rows)))
    CSV.write(joinpath(out_dir, "shunts.csv"), DataFrame(shunts_rows(case)))
    CSV.write(joinpath(out_dir, "constraints_inventory.csv"), DataFrame(constraints_inventory_rows(case)))
    CSV.write(joinpath(out_dir, "constraints_binding.csv"), constraints_binding_rows(gen_rows, bus_rows, branch_rows))

    println("  wrote 6 CSVs to $(out_dir)")
end

function process_case(case_file::String; scenario_label::String="base")
    label = short_case_name(case_file)
    data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
    process_data(label, scenario_label, data)
end

# ------------------------------------------------------------
# Spotlight scenarios: 1888_rte's two non-uniqueness cases from the original
# AC-OPF validation (results/presentable/opf_validation_findings/
# julia_ac_spotlight_1888_rte_load_x{0.8,1.2}...png). compact_scenarios
# (used below) comes from the script-09 inclusion at the top of this file --
# not re-included here, see the comment up there for why.
# ------------------------------------------------------------

const SPOTLIGHT_SCENARIOS = [
    ("1888_rte", "load_x0p8__load_scaling", "load_x0.8"),
    ("1888_rte", "load_x1p2__reactive_power_stress", "load_x1.2_reactive_stress"),
    ("1888_rte", "qg_limits_x0p8__reactive_generation_limits", "qg_limits_x0.8"),
]

function process_spotlight_scenarios()
    ac_optimizer = build_ac_optimizer()
    for (case_label, scenario_id_substring, out_label) in SPOTLIGHT_SCENARIOS
        data = PowerModels.parse_file(joinpath(CASE_DIR, "pglib_opf_case$(case_label).m"))
        entries = compact_scenarios(data, case_label, ac_optimizer; include_no_reference=true)
        entry = only(filter(e -> occursin(scenario_id_substring, e.scenario_id), entries))
        try
            process_data(case_label, out_label, entry.data; ac_optimizer=ac_optimizer)
        catch err
            println("  ERROR on $(case_label)/$(out_label): ", sprint(showerror, err))
        end
    end
end

function main()
    for case_file in DEFAULT_CASES
        try
            process_case(case_file)
        catch err
            println("  ERROR on $(case_file): ", sprint(showerror, err))
        end
    end
    process_spotlight_scenarios()
end

main()
