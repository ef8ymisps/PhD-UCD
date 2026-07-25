# Bus-shunt on/off sensitivity experiment for objective-mismatch scenarios.
#
# For the union of current-Julia and PM-like objective-mismatch scenarios,
# solve:
#   - PowerModels ACP;
#   - own current Julia AC OPF;
#   - own PM-like Julia AC OPF;
# with original bus shunts and with explicit bus shunts zeroed.
#
# Outputs:
#   results/own_vs_powermodels/shunt_toggle_solution_summary_julia.csv
#   results/own_vs_powermodels/shunt_toggle_variable_differences_julia.csv
#   results/own_vs_powermodels/shunt_toggle_binding_differences_julia.csv
#   results/own_vs_powermodels/shunt_toggle_binding_summary_julia.csv

const TWENTYFIVE_SCRIPT_PATH = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH)
end

using CSV
using DataFrames

const SHUNT_TOGGLE_OUT = joinpath(ROOT, "results", "own_vs_powermodels")

const KNOWN_LABELS_SHUNT_TOGGLE = Dict(
    "1888_rte__base__none__none__none__none" => "base",
    "1888_rte_load_x0p8__load_scaling__none__none__load_multiplier__0p8" => "load_x0.8",
    "1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2" => "load_x1.2_reactive_stress",
    "1888_rte_qg_limits_x0p6__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p6" => "qg_limits_x0.6",
    "1888_rte_qg_limits_x0p8__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p8" => "qg_limits_x0.8",
)

normalize_shunt_toggle_label(label::AbstractString) = get(KNOWN_LABELS_SHUNT_TOGGLE, String(label), String(label))

function expected_scenario_label_shunt_toggle(entry)
    raw_id = String(entry.scenario_id)
    if haskey(KNOWN_LABELS_SHUNT_TOGGLE, raw_id)
        return KNOWN_LABELS_SHUNT_TOGGLE[raw_id]
    elseif entry.scenario == "base"
        return "base"
    else
        return raw_id
    end
end

function scenario_data_for_shunt_toggle(case_label::String, scenario_label::String)
    case_file = "pglib_opf_case$(case_label)"
    data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
    entries = compact_scenarios(data, case_label, build_ac_optimizer(); include_no_reference=true)
    for entry in entries
        if expected_scenario_label_shunt_toggle(entry) == scenario_label || String(entry.scenario_id) == scenario_label
            return entry.data
        end
    end
    error("Could not find scenario $(case_label)/$(scenario_label)")
end

function load_shunt_toggle_targets()
    paths = [
        joinpath(ROOT, "results", "own_vs_powermodels", "objective_mismatch_details_julia_current.csv"),
        joinpath(ROOT, "results", "own_vs_powermodels", "objective_mismatch_details_julia_pm_like.csv"),
    ]
    targets = NamedTuple[]
    seen = Set{Tuple{String, String}}()
    for path in paths
        isfile(path) || continue
        df = DataFrame(CSV.File(path))
        for row in eachrow(df)
            case_label = String(row.case)
            scenario_label = normalize_shunt_toggle_label(String(row.scenario))
            key = (case_label, scenario_label)
            if !(key in seen)
                push!(targets, (case=case_label, scenario=scenario_label))
                push!(seen, key)
            end
        end
    end
    return targets
end

function bus_shunt_totals(data)
    total_gs = 0.0
    total_bs = 0.0
    n_active = 0
    if haskey(data, "shunt")
        for (_, shunt) in data["shunt"]
            if Int(get(shunt, "status", 1)) != 0
                total_gs += Float64(get(shunt, "gs", 0.0))
                total_bs += Float64(get(shunt, "bs", 0.0))
                n_active += 1
            end
        end
    end
    if haskey(data, "bus")
        for (_, bus) in data["bus"]
            total_gs += Float64(get(bus, "gs", 0.0))
            total_bs += Float64(get(bus, "bs", 0.0))
        end
    end
    return (n_active_shunts=n_active, total_gs=total_gs, total_bs=total_bs)
end

function data_with_bus_shunts_zeroed(data)
    toggled = deepcopy(data)
    if haskey(toggled, "shunt")
        for (_, shunt) in toggled["shunt"]
            shunt["gs"] = 0.0
            shunt["bs"] = 0.0
        end
    end
    if haskey(toggled, "bus")
        for (_, bus) in toggled["bus"]
            if haskey(bus, "gs")
                bus["gs"] = 0.0
            end
            if haskey(bus, "bs")
                bus["bs"] = 0.0
            end
        end
    end
    return toggled
end

function solve_powermodels_shunt_toggle(data)
    pm_model = PowerModels.instantiate_model(deepcopy(data), PowerModels.ACPPowerModel, PowerModels.build_opf)
    elapsed = @elapsed pm_result = PowerModels.optimize_model!(pm_model, optimizer=build_ac_optimizer())
    return (
        result=pm_result,
        status=string(get(pm_result, "termination_status", "UNKNOWN")),
        objective=Float64(get(pm_result, "objective", NaN)),
        solve_time_sec=elapsed,
        solve_iterations=OwnPowerModels._safe_barrier_iterations(pm_model.model),
    )
end

function solve_own_shunt_toggle(case::OwnPowerModels.PowerCase, model_family::String)
    model_family == "own_pm_like" || error("Unsupported own model family $(model_family). Expected \"own_pm_like\" (bus-injection was deleted).")
    elapsed = @elapsed result = solve_ac_opf_own(
        case; optimizer=build_ac_optimizer(), silent=true, fix_reference_angle=true, warm_start=:zero
    )
    return (
        result=result,
        status=string(result.termination_status),
        objective=result.objective,
        solve_time_sec=elapsed,
        solve_iterations=result.solve_iterations,
    )
end

is_solved_status(status::AbstractString) = status in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")

function pm_solution_section(pm_result, section::String)
    return get(get(pm_result, "solution", Dict{String, Any}()), section, Dict{String, Any}())
end

function add_value!(value_map, case_label, scenario_label, model_family, mode, element_type, element_id, quantity, value)
    key = (case_label, scenario_label, model_family, element_type, element_id, quantity, mode)
    value_map[key] = value
    return nothing
end

function record_own_values!(value_map, case_label, scenario_label, model_family, mode, case, result)
    for gen in case.generators
        haskey(result.pg, gen.id) && add_value!(value_map, case_label, scenario_label, model_family, mode, "generator", gen.id, "pg", result.pg[gen.id])
        haskey(result.qg, gen.id) && add_value!(value_map, case_label, scenario_label, model_family, mode, "generator", gen.id, "qg", result.qg[gen.id])
    end
    for bus in case.buses
        haskey(result.vm, bus.id) && add_value!(value_map, case_label, scenario_label, model_family, mode, "bus", bus.id, "vm", result.vm[bus.id])
        haskey(result.va, bus.id) && add_value!(value_map, case_label, scenario_label, model_family, mode, "bus", bus.id, "va", result.va[bus.id])
    end
    for branch in case.branches
        if haskey(result.branch_flows, branch.id)
            flow = result.branch_flows[branch.id]
            for quantity in ("pf", "qf", "pt", "qt")
                add_value!(value_map, case_label, scenario_label, model_family, mode, "branch", branch.id, quantity, flow[quantity])
            end
        end
    end
    return nothing
end

function record_pm_values!(value_map, case_label, scenario_label, mode, case, pm_result)
    pm_gen = pm_solution_section(pm_result, "gen")
    pm_bus = pm_solution_section(pm_result, "bus")
    pm_branch = pm_solution_section(pm_result, "branch")
    for gen in case.generators
        if haskey(pm_gen, gen.id)
            add_value!(value_map, case_label, scenario_label, "powermodels", mode, "generator", gen.id, "pg", Float64(pm_gen[gen.id]["pg"]))
            add_value!(value_map, case_label, scenario_label, "powermodels", mode, "generator", gen.id, "qg", Float64(pm_gen[gen.id]["qg"]))
        end
    end
    for bus in case.buses
        if haskey(pm_bus, bus.id)
            add_value!(value_map, case_label, scenario_label, "powermodels", mode, "bus", bus.id, "vm", Float64(pm_bus[bus.id]["vm"]))
            add_value!(value_map, case_label, scenario_label, "powermodels", mode, "bus", bus.id, "va", Float64(pm_bus[bus.id]["va"]))
        end
    end
    for branch in case.branches
        if haskey(pm_branch, branch.id)
            for quantity in ("pf", "qf", "pt", "qt")
                add_value!(value_map, case_label, scenario_label, "powermodels", mode, "branch", branch.id, quantity, Float64(pm_branch[branch.id][quantity]))
            end
        end
    end
    return nothing
end

function max_abs_change_from_value_map(value_map, case_label, scenario_label, model_family, element_type, quantities)
    values = Float64[]
    for key in keys(value_map)
        k_case, k_scenario, k_family, k_element_type, k_element_id, k_quantity, k_mode = key
        if k_case == case_label && k_scenario == scenario_label && k_family == model_family &&
           k_element_type == element_type && k_quantity in quantities && k_mode == "bus_shunts_off"
            on_key = (k_case, k_scenario, k_family, k_element_type, k_element_id, k_quantity, "bus_shunts_on")
            if haskey(value_map, on_key)
                diff = value_map[key] - value_map[on_key]
                isfinite(diff) && push!(values, abs(diff))
            end
        end
    end
    return isempty(values) ? NaN : maximum(values)
end

function variable_difference_rows(value_map)
    rows = NamedTuple[]
    for key in sort(collect(keys(value_map)); by=string)
        case_label, scenario_label, model_family, element_type, element_id, quantity, mode = key
        mode == "bus_shunts_off" || continue
        on_key = (case_label, scenario_label, model_family, element_type, element_id, quantity, "bus_shunts_on")
        haskey(value_map, on_key) || continue
        value_on = value_map[on_key]
        value_off = value_map[key]
        diff = value_off - value_on
        push!(rows, (
            case=case_label,
            scenario=scenario_label,
            model_family=model_family,
            element_type=element_type,
            element_id=element_id,
            quantity=quantity,
            value_bus_shunts_on=value_on,
            value_bus_shunts_off=value_off,
            diff_off_minus_on=diff,
            abs_diff=abs(diff),
        ))
    end
    return rows
end

function binding_rows_with_context(gen_rows, bus_rows, branch_rows; case_label, scenario_label, model_family, mode)
    rows = NamedTuple[]
    for row in eachrow(constraints_binding_rows(gen_rows, bus_rows, branch_rows))
        relation = Bool(row.own_binding) == Bool(row.pm_binding) ? "same" :
            (Bool(row.own_binding) ? "only_own_binding" : "only_pm_binding")
        push!(rows, (
            case=case_label,
            scenario=scenario_label,
            model_family=model_family,
            bus_shunt_mode=mode,
            constraint_type=String(row.constraint_type),
            element_id=String(row.element_id),
            bound=Float64(row.bound),
            own_value=Float64(row.own_value),
            pm_value=Float64(row.pm_value),
            value_diff_own_minus_pm=Float64(row.own_value) - Float64(row.pm_value),
            abs_value_diff=abs(Float64(row.own_value) - Float64(row.pm_value)),
            own_margin=Float64(row.own_margin),
            pm_margin=Float64(row.pm_margin),
            margin_diff_own_minus_pm=Float64(row.own_margin) - Float64(row.pm_margin),
            abs_margin_diff=abs(Float64(row.own_margin) - Float64(row.pm_margin)),
            own_binding=Bool(row.own_binding),
            pm_binding=Bool(row.pm_binding),
            binding_relation=relation,
        ))
    end
    return rows
end

function binding_summary_rows(binding_rows)
    isempty(binding_rows) && return NamedTuple[]
    df = DataFrame(binding_rows)
    rows = NamedTuple[]
    for group in groupby(df, [:case, :scenario, :model_family, :bus_shunt_mode])
        mismatches = group[group.binding_relation .!= "same", :]
        push!(rows, (
            case=group.case[1],
            scenario=group.scenario[1],
            model_family=group.model_family[1],
            bus_shunt_mode=group.bus_shunt_mode[1],
            n_constraints=nrow(group),
            n_binding_mismatches=nrow(mismatches),
            n_only_own_binding=sum(mismatches.binding_relation .== "only_own_binding"),
            n_only_pm_binding=sum(mismatches.binding_relation .== "only_pm_binding"),
            max_abs_value_diff=isempty(group.abs_value_diff) ? NaN : maximum(skipmissing(group.abs_value_diff)),
            max_abs_margin_diff=isempty(group.abs_margin_diff) ? NaN : maximum(skipmissing(group.abs_margin_diff)),
            n_pg_mismatches=sum(in.(mismatches.constraint_type, Ref(["pg_min", "pg_max"]))),
            n_qg_mismatches=sum(in.(mismatches.constraint_type, Ref(["qg_min", "qg_max"]))),
            n_vm_mismatches=sum(in.(mismatches.constraint_type, Ref(["vm_min", "vm_max"]))),
            n_thermal_mismatches=sum(mismatches.constraint_type .== "thermal"),
            n_angle_mismatches=sum(in.(mismatches.constraint_type, Ref(["angle_lower", "angle_upper"]))),
        ))
    end
    return rows
end

function main()
    summary_rows = NamedTuple[]
    binding_rows = NamedTuple[]
    value_map = Dict{Tuple{String, String, String, String, String, String, String}, Float64}()
    objective_map = Dict{Tuple{String, String, String, String}, Float64}()
    iteration_map = Dict{Tuple{String, String, String, String}, Float64}()

    for target in load_shunt_toggle_targets()
        original_data = scenario_data_for_shunt_toggle(target.case, target.scenario)
        for (mode, data) in (
            ("bus_shunts_on", deepcopy(original_data)),
            ("bus_shunts_off", data_with_bus_shunts_zeroed(original_data)),
        )
            totals = bus_shunt_totals(data)
            println("Shunt toggle $(target.case) / $(target.scenario) / $(mode)")
            case = OwnPowerModels.powermodels_to_powercase(deepcopy(data))

            pm = solve_powermodels_shunt_toggle(data)
            pm_ok = is_solved_status(pm.status)
            objective_map[(target.case, target.scenario, "powermodels", mode)] = pm.objective
            iteration_map[(target.case, target.scenario, "powermodels", mode)] = pm.solve_iterations
            push!(summary_rows, (
                case=target.case,
                scenario=target.scenario,
                model_family="powermodels",
                bus_shunt_mode=mode,
                status=pm.status,
                solved=pm_ok,
                objective=pm.objective,
                solve_time_sec=pm.solve_time_sec,
                solve_iterations=pm.solve_iterations,
                n_active_shunts=totals.n_active_shunts,
                total_gs=totals.total_gs,
                total_bs=totals.total_bs,
            ))
            pm_ok && record_pm_values!(value_map, target.case, target.scenario, mode, case, pm.result)

            for model_family in ("own_pm_like",)
                own = solve_own_shunt_toggle(case, model_family)
                own_ok = is_solved_status(own.status)
                objective_map[(target.case, target.scenario, model_family, mode)] = own.objective
                iteration_map[(target.case, target.scenario, model_family, mode)] = own.solve_iterations
                push!(summary_rows, (
                    case=target.case,
                    scenario=target.scenario,
                    model_family=model_family,
                    bus_shunt_mode=mode,
                    status=own.status,
                    solved=own_ok,
                    objective=own.objective,
                    solve_time_sec=own.solve_time_sec,
                    solve_iterations=own.solve_iterations,
                    n_active_shunts=totals.n_active_shunts,
                    total_gs=totals.total_gs,
                    total_bs=totals.total_bs,
                ))
                if own_ok
                    record_own_values!(value_map, target.case, target.scenario, model_family, mode, case, own.result)
                end
                if own_ok && pm_ok
                    gen_rows = OwnPowerModels.compare_ac_opf_generator_rows(case, own.result, pm.result)
                    bus_rows = OwnPowerModels.compare_ac_opf_bus_rows(case, own.result, pm.result)
                    branch_rows = OwnPowerModels.compare_ac_opf_branch_rows(case, own.result, pm.result)
                    append!(
                        binding_rows,
                        binding_rows_with_context(
                            gen_rows,
                            bus_rows,
                            branch_rows;
                            case_label=target.case,
                            scenario_label=target.scenario,
                            model_family=model_family,
                            mode=mode,
                        ),
                    )
                end
            end
        end
    end

    summary_df = DataFrame(summary_rows)
    summary_df[!, :trusted_objective] = [
        Bool(row.solved) ? Float64(row.objective) : NaN
        for row in eachrow(summary_df)
    ]
    summary_df[!, :objective_change_off_minus_on] = [
        row.bus_shunt_mode == "bus_shunts_on" ? 0.0 :
            (Bool(row.solved) ? row.objective - get(objective_map, (row.case, row.scenario, row.model_family, "bus_shunts_on"), NaN) : NaN)
        for row in eachrow(summary_df)
    ]
    summary_df[!, :iteration_change_off_minus_on] = [
        row.bus_shunt_mode == "bus_shunts_on" ? 0.0 :
            (Bool(row.solved) ? row.solve_iterations - get(iteration_map, (row.case, row.scenario, row.model_family, "bus_shunts_on"), NaN) : NaN)
        for row in eachrow(summary_df)
    ]
    summary_df[!, :max_abs_pg_change_off_minus_on] = [
        row.bus_shunt_mode == "bus_shunts_on" ? 0.0 :
            max_abs_change_from_value_map(value_map, row.case, row.scenario, row.model_family, "generator", ("pg",))
        for row in eachrow(summary_df)
    ]
    summary_df[!, :max_abs_qg_change_off_minus_on] = [
        row.bus_shunt_mode == "bus_shunts_on" ? 0.0 :
            max_abs_change_from_value_map(value_map, row.case, row.scenario, row.model_family, "generator", ("qg",))
        for row in eachrow(summary_df)
    ]
    summary_df[!, :max_abs_vm_change_off_minus_on] = [
        row.bus_shunt_mode == "bus_shunts_on" ? 0.0 :
            max_abs_change_from_value_map(value_map, row.case, row.scenario, row.model_family, "bus", ("vm",))
        for row in eachrow(summary_df)
    ]
    summary_df[!, :max_abs_va_change_off_minus_on] = [
        row.bus_shunt_mode == "bus_shunts_on" ? 0.0 :
            max_abs_change_from_value_map(value_map, row.case, row.scenario, row.model_family, "bus", ("va",))
        for row in eachrow(summary_df)
    ]
    summary_df[!, :max_abs_branch_flow_change_off_minus_on] = [
        row.bus_shunt_mode == "bus_shunts_on" ? 0.0 :
            max_abs_change_from_value_map(value_map, row.case, row.scenario, row.model_family, "branch", ("pf", "qf", "pt", "qt"))
        for row in eachrow(summary_df)
    ]

    mkpath(SHUNT_TOGGLE_OUT)
    CSV.write(joinpath(SHUNT_TOGGLE_OUT, "shunt_toggle_solution_summary_julia.csv"), summary_df)
    CSV.write(joinpath(SHUNT_TOGGLE_OUT, "shunt_toggle_variable_differences_julia.csv"), DataFrame(variable_difference_rows(value_map)))
    CSV.write(joinpath(SHUNT_TOGGLE_OUT, "shunt_toggle_binding_differences_julia.csv"), DataFrame(binding_rows))
    CSV.write(joinpath(SHUNT_TOGGLE_OUT, "shunt_toggle_binding_summary_julia.csv"), DataFrame(binding_summary_rows(binding_rows)))

    println("Wrote shunt-toggle outputs to $(SHUNT_TOGGLE_OUT)")
end

main()
