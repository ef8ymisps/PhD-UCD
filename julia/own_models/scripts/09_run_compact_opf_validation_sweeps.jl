# Checkpointed compact OPF validation sweeps for the Julia own models.
#
# The script runs each (case, scenario, model) independently and appends one
# summary row immediately after completion.  It intentionally avoids detail
# rows by default so large systems can resume without retaining per-bus or
# per-branch result tables in memory.

include(joinpath(@__DIR__, "..", "src", "OwnPowerModels.jl"))

using .OwnPowerModels
using CSV
using DataFrames
using HiGHS
using Ipopt
using JuMP
using PowerModels
using ProgressMeter

const DEFAULT_CASES = [
    "pglib_opf_case5_pjm",
    "pglib_opf_case14_ieee",
    "pglib_opf_case30_ieee",
    "pglib_opf_case118_ieee",
    "pglib_opf_case300_ieee",
    "pglib_opf_case1354_pegase",
    "pglib_opf_case1888_rte",
    "pglib_opf_case6470_rte",
]

const SUMMARY_COLUMNS = [
    :base_case,
    :case_name,
    :model,
    :scenario,
    :target_type,
    :target_id,
    :parameter_name,
    :parameter_value,
    :scenario_id,
    :own_status,
    :pm_status,
    :own_solved,
    :pm_solved,
    :outcome_match,
    :own_objective,
    :pm_objective,
    :objective_diff,
    :objective_rel_diff,
    :own_solve_time_sec,
    :pm_solve_time_sec,
    :solve_time_diff_vs_pm,
    :solve_time_ratio_vs_pm,
    :max_pg_diff,
    :mean_pg_diff,
    :std_pg_diff,
    :max_qg_diff,
    :mean_qg_diff,
    :std_qg_diff,
    :max_vm_diff,
    :mean_vm_diff,
    :std_vm_diff,
    :max_va_diff,
    :mean_va_diff,
    :std_va_diff,
    :max_pf_diff,
    :mean_pf_diff,
    :std_pf_diff,
    :max_qf_diff,
    :mean_qf_diff,
    :std_qf_diff,
    :max_loading_pct_diff,
    :mean_loading_pct_diff,
    :std_loading_pct_diff,
    :own_solve_iterations,
    :pm_solve_iterations,
    :n_pmin_both,
    :n_pmin_only_own,
    :n_pmin_only_pm,
    :n_pmax_both,
    :n_pmax_only_own,
    :n_pmax_only_pm,
    :n_qmin_both,
    :n_qmin_only_own,
    :n_qmin_only_pm,
    :n_qmax_both,
    :n_qmax_only_own,
    :n_qmax_only_pm,
    :n_vmin_both,
    :n_vmin_only_own,
    :n_vmin_only_pm,
    :n_vmax_both,
    :n_vmax_only_own,
    :n_vmax_only_pm,
    :n_thermal_both,
    :n_thermal_only_own,
    :n_thermal_only_pm,
    :n_angle_lower_both,
    :n_angle_lower_only_own,
    :n_angle_lower_only_pm,
    :n_angle_upper_both,
    :n_angle_upper_only_own,
    :n_angle_upper_only_pm,
    :own_error,
    :pm_error,
]

function arg_value(name::String, default::String)
    prefix = name * "="
    for arg in ARGS
        if startswith(arg, prefix)
            return arg[length(prefix) + 1:end]
        end
    end
    return default
end

has_flag(name::String) = any(==(name), ARGS)

function split_csv_arg(text::String)
    return [String(strip(item)) for item in split(text, ",") if !isempty(strip(item))]
end

function parse_models(text::String)
    models = Symbol.(lowercase.(split_csv_arg(text)))
    bad = [m for m in models if !(m in (:ac, :dc))]
    if !isempty(bad)
        error("Unsupported model(s): $(join(string.(bad), ", ")). Use ac, dc, or ac,dc.")
    end
    return models
end

function parse_float_list(text::String)
    return [parse(Float64, item) for item in split_csv_arg(text)]
end

function case_path(case_dir::AbstractString, case_name::AbstractString)
    if isfile(case_name)
        return normpath(case_name)
    end

    candidate = normpath(joinpath(case_dir, case_name))
    if isfile(candidate)
        return candidate
    end

    if !endswith(candidate, ".m")
        candidate_m = candidate * ".m"
        if isfile(candidate_m)
            return candidate_m
        end
    end

    error("Case file not found: $(case_name)")
end

function clean_case_label(path::AbstractString; strip_case_prefix::Bool=true)
    stem = splitext(basename(path))[1]
    if strip_case_prefix
        stem = replace(stem, "pglib_opf_case" => "")
        stem = replace(stem, "pglib_opf_" => "")
    end
    return stem
end

function key_text(value)
    if value === nothing || value === missing
        return ""
    end
    if value isa AbstractFloat && isnan(value)
        return ""
    end
    return string(value)
end

function scenario_id(case_name, scenario, target_type, target_id, parameter_name, parameter_value)
    parts = [
        key_text(case_name),
        key_text(scenario),
        key_text(target_type),
        key_text(target_id),
        key_text(parameter_name),
        key_text(parameter_value),
    ]
    text = join([isempty(part) ? "none" : part for part in parts], "__")
    text = replace(text, "." => "p", "-" => "m")
    text = replace(text, r"[^A-Za-z0-9_]+" => "_")
    return strip(text, ['_'])
end

function keys_sorted_by_int(d)
    return sort(collect(keys(d)); by=x -> parse(Int, string(x)))
end

function is_solved_status(status)
    return string(status) in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")
end

function safe_objective(result)
    if result === nothing
        return NaN
    end
    if result isa Dict
        return Float64(get(result, "objective", NaN))
    end
    return hasproperty(result, :objective) ? Float64(getproperty(result, :objective)) : NaN
end

function safe_pm_status(result)
    return result === nothing ? "ERROR" : string(get(result, "termination_status", "UNKNOWN"))
end

function safe_own_status(result)
    return result === nothing ? "ERROR" : string(result.termination_status)
end

function field_or(row, name::Symbol, default)
    return hasproperty(row, name) ? getproperty(row, name) : default
end

function finite_pair(a, b)
    return isfinite(a) && isfinite(b)
end

function append_summary_row!(path::String, row::NamedTuple)
    mkpath(dirname(path))
    frame = DataFrame([row])
    frame = frame[:, SUMMARY_COLUMNS]
    CSV.write(path, frame; append=isfile(path), writeheader=!isfile(path))
end

function completed_keys(summary_path::String)
    completed = Set{Tuple{String, String}}()
    if !isfile(summary_path)
        return completed
    end
    frame = DataFrame(CSV.File(summary_path; select=[:model, :scenario_id]))
    for row in eachrow(frame)
        push!(completed, (String(row.model), String(row.scenario_id)))
    end
    return completed
end

function make_entry(
    base_case::String,
    case_name::String,
    scenario::String,
    data;
    target_type::String="",
    target_id::String="",
    parameter_name::String="",
    parameter_value=""
)
    data["name"] = case_name
    sid = scenario_id(case_name, scenario, target_type, target_id, parameter_name, parameter_value)
    return (
        base_case=base_case,
        case_name=case_name,
        scenario=scenario,
        target_type=target_type,
        target_id=target_id,
        parameter_name=parameter_name,
        parameter_value=key_text(parameter_value),
        scenario_id=sid,
        data=data,
    )
end

function add_entry!(entries, args...; kwargs...)
    entry = make_entry(args...; kwargs...)
    push!(entries, entry)
    return entry
end

function scale_branch_rates!(data, multiplier::Float64)
    for branch in values(data["branch"])
        rate = Float64(get(branch, "rate_a", 0.0))
        branch["rate_a"] = rate > 0.0 ? rate * multiplier : rate
    end
end

function relax_limits!(data; rate_multiplier::Float64, angle_limit_deg::Float64, voltage_lower::Float64, voltage_upper::Float64)
    limit = deg2rad(angle_limit_deg)
    for branch in values(data["branch"])
        rate = Float64(get(branch, "rate_a", 0.0))
        branch["rate_a"] = rate > 0.0 ? rate * rate_multiplier : rate
        branch["angmin"] = -limit
        branch["angmax"] = limit
    end
    for bus in values(data["bus"])
        bus["vmin"] = min(Float64(bus["vmin"]), voltage_lower)
        bus["vmax"] = max(Float64(bus["vmax"]), voltage_upper)
    end
end

function tighten_voltage_limits!(data, lower::Float64, upper::Float64)
    half_width = max(0.0, (upper - lower) / 2.0)
    if half_width <= 0.0
        error("Voltage tightening bounds must have positive width: $(lower)-$(upper)")
    end

    for (bus_id, bus) in data["bus"]
        case_lower = Float64(bus["vmin"])
        case_upper = Float64(bus["vmax"])
        if case_lower > case_upper
            error("Bus $(bus_id) has invalid PGLib voltage limits: $(case_lower) > $(case_upper)")
        end
        center = clamp(Float64(bus["vm"]), case_lower, case_upper)
        bus["vmin"] = max(case_lower, center - half_width)
        bus["vmax"] = min(case_upper, center + half_width)
    end
end

function scale_generator_pmax!(data, gen_id::String, multiplier::Float64)
    gen = data["gen"][gen_id]
    gen["pmax"] = max(Float64(gen["pmin"]), Float64(gen["pmax"]) * multiplier)
end

function scale_loads!(data, multiplier::Float64; scale_p::Bool, scale_q::Bool)
    for load in values(data["load"])
        if scale_p
            load["pd"] = Float64(load["pd"]) * multiplier
        end
        if scale_q
            load["qd"] = Float64(load["qd"]) * multiplier
        end
    end
end

function set_system_angle_limit!(data, angle_limit_deg::Float64)
    limit = deg2rad(angle_limit_deg)
    for branch in values(data["branch"])
        branch["angmin"] = -limit
        branch["angmax"] = limit
    end
end

function tighten_qg_limits!(data, multiplier::Float64)
    for gen in values(data["gen"])
        if Int(get(gen, "gen_status", 1)) != 1
            continue
        end
        qmin = Float64(gen["qmin"])
        qmax = Float64(gen["qmax"])
        center = clamp(Float64(gen["qg"]), qmin, qmax)
        lower_width = max(0.0, center - qmin) * multiplier
        upper_width = max(0.0, qmax - center) * multiplier
        gen["qmin"] = center - lower_width
        gen["qmax"] = center + upper_width
        gen["qg"] = center
    end
end

function has_active_generator_at_bus(data, bus_id::String)
    for gen in values(data["gen"])
        if string(gen["gen_bus"]) == bus_id && Int(get(gen, "gen_status", 1)) == 1
            return true
        end
    end
    return false
end

function change_slack_bus!(data, new_slack_bus::String)
    for (bus_id, bus) in data["bus"]
        if Int(bus["bus_type"]) == 3
            bus["bus_type"] = has_active_generator_at_bus(data, string(bus_id)) ? 2 : 1
        end
    end
    data["bus"][new_slack_bus]["bus_type"] = 3
end

function apply_branch_outage!(data, branch_id::String)
    data["branch"][branch_id]["br_status"] = 0
end

function apply_generator_outage!(data, gen_id::String)
    data["gen"][gen_id]["gen_status"] = 0
end

function select_cheapest_dispatched_generator(case::PowerCase, result)
    candidates = [
        gen for gen in case.generators
        if gen.status == 1 && haskey(result.pg, gen.id) && result.pg[gen.id] > gen.pmin + 1e-7
    ]
    if isempty(candidates)
        return nothing
    end
    return first(sort(candidates; by=gen -> (gen.cost_c1, gen.cost_c2, gen.index)))
end

function select_alternate_slack_bus(case::PowerCase)
    for bus in case.buses
        if bus.id != case.slack_bus_id
            return bus.id
        end
    end
    return nothing
end

function select_branch_outages(case::PowerCase, result; top_k_loading::Int, top_k_flow::Int)
    candidates = NamedTuple[]
    for branch in case.branches
        if branch.status == 0 || !haskey(result.branch_flows, branch.id)
            continue
        end
        flow = result.branch_flows[branch.id]
        apparent = max(hypot(Float64(flow["pf"]), Float64(flow["qf"])), hypot(Float64(flow["pt"]), Float64(flow["qt"])))
        loading_pct = branch.rate_a > 0.0 ? 100.0 * apparent / branch.rate_a : Inf
        push!(candidates, (branch_id=branch.id, base_flow_pu=apparent, rate_a_pu=branch.rate_a, base_loading_pct=loading_pct))
    end

    selected = Dict{String, NamedTuple}()
    for item in first(sort(candidates; by=x -> (x.base_loading_pct, x.base_flow_pu), rev=true), min(top_k_loading, length(candidates)))
        selected[item.branch_id] = item
    end
    for item in first(sort(candidates; by=x -> x.base_flow_pu, rev=true), min(top_k_flow, length(candidates)))
        selected[item.branch_id] = item
    end
    return sort(collect(values(selected)); by=x -> parse(Int, x.branch_id))
end

function select_generator_outages(case::PowerCase, result; top_k_cheapest::Int, top_k_pg::Int)
    candidates = NamedTuple[]
    for gen in case.generators
        if gen.status == 0 || !haskey(result.pg, gen.id)
            continue
        end
        pg = Float64(result.pg[gen.id])
        if pg <= gen.pmin + 1e-7
            continue
        end
        push!(candidates, (
            gen_id=gen.id,
            bus_id=gen.bus,
            base_pg_pu=pg,
            pmax_pu=gen.pmax,
            cost_c1=gen.cost_c1,
            cost_c2=gen.cost_c2,
            index=gen.index,
        ))
    end

    selected = Dict{String, NamedTuple}()
    for item in first(sort(candidates; by=x -> (x.cost_c1, x.cost_c2, x.index)), min(top_k_cheapest, length(candidates)))
        selected[item.gen_id] = item
    end
    for item in first(sort(candidates; by=x -> x.base_pg_pu, rev=true), min(top_k_pg, length(candidates)))
        selected[item.gen_id] = item
    end
    return sort(collect(values(selected)); by=x -> parse(Int, x.gen_id))
end

function build_base_selection_result(base_data, ac_optimizer)
    case = OwnPowerModels.powermodels_to_powercase(deepcopy(base_data))
    try
        result = solve_ac_opf_own(case; optimizer=ac_optimizer, silent=true)
        if is_solved_status(result.termination_status)
            return case, result, ""
        end
        return case, nothing, "Base AC selection solve status: $(result.termination_status)"
    catch err
        return case, nothing, sprint(showerror, err)
    end
end

function compact_scenarios(base_data, label::String, ac_optimizer; include_no_reference::Bool=false, on_entry=nothing)
    entries = NamedTuple[]
    function emit!(args...; kwargs...)
        entry = make_entry(args...; kwargs...)
        if on_entry === nothing
            push!(entries, entry)
        else
            on_entry(entry)
        end
        return entry
    end

    base = deepcopy(base_data)
    base["name"] = label
    selection_case, selection_result, selection_error = build_base_selection_result(base, ac_optimizer)

    emit!(label, label, "base", deepcopy(base))

    relaxed = deepcopy(base)
    relax_limits!(relaxed; rate_multiplier=10.0, angle_limit_deg=180.0, voltage_lower=0.90, voltage_upper=1.10)
    emit!(label, "$(label)_relaxed", "relaxed_limits", relaxed; parameter_name="relaxed_limits", parameter_value=10.0)

    for multiplier in [1.0, 0.8, 0.6, 0.4]
        data = deepcopy(base)
        scale_branch_rates!(data, multiplier)
        emit!(
            label,
            "$(label)_all_branch_rates_x$(multiplier)",
            "branch_congestion",
            data;
            target_type="system",
            target_id="all_branches",
            parameter_name="rate_multiplier",
            parameter_value=multiplier,
        )
    end

    for (lower, upper) in [(0.95, 1.05), (0.98, 1.02), (0.99, 1.01)]
        data = deepcopy(base)
        tighten_voltage_limits!(data, lower, upper)
        emit!(
            label,
            label,
            "tight_voltage_limits",
            data;
            parameter_name="voltage_bounds",
            parameter_value="$(lower)-$(upper)",
        )
    end

    if selection_result !== nothing
        gen = select_cheapest_dispatched_generator(selection_case, selection_result)
        if gen !== nothing
            for multiplier in [1.0, 0.8, 0.6]
                data = deepcopy(base)
                scale_generator_pmax!(data, gen.id, multiplier)
                emit!(
                    label,
                    "$(label)_gen_$(gen.id)_pmax_x$(multiplier)",
                    "generator_capacity_reduction",
                    data;
                    target_type="generator",
                    target_id=gen.id,
                    parameter_name="pmax_multiplier",
                    parameter_value=multiplier,
                )
            end
            for multiplier in [1.0, 1.2, 1.5]
                data = deepcopy(base)
                scale_generator_pmax!(data, gen.id, multiplier)
                emit!(
                    label,
                    "$(label)_gen_$(gen.id)_pmax_x$(multiplier)",
                    "generator_capacity_expansion",
                    data;
                    target_type="generator",
                    target_id=gen.id,
                    parameter_name="pmax_multiplier",
                    parameter_value=multiplier,
                )
            end
        end
    elseif !isempty(selection_error)
        println("  Selection scenarios skipped for $(label): $(selection_error)")
    end

    for multiplier in [0.8, 1.0, 1.2, 1.4]
        data = deepcopy(base)
        scale_loads!(data, multiplier; scale_p=true, scale_q=true)
        emit!(label, "$(label)_load_x$(multiplier)", "load_scaling", data; parameter_name="load_multiplier", parameter_value=multiplier)
    end

    for angle_limit_deg in [30.0, 20.0, 10.0, 5.0]
        data = deepcopy(base)
        set_system_angle_limit!(data, angle_limit_deg)
        emit!(label, "$(label)_angle_$(angle_limit_deg)deg", "system_angle_limits", data; parameter_name="angle_limit_deg", parameter_value=angle_limit_deg)
    end

    for multiplier in [1.0, 1.2, 1.4]
        data = deepcopy(base)
        scale_loads!(data, multiplier; scale_p=false, scale_q=true)
        emit!(label, "$(label)_load_x$(multiplier)", "reactive_power_stress", data; parameter_name="q_load_multiplier", parameter_value=multiplier)
    end

    for multiplier in [1.0, 0.8, 0.6]
        data = deepcopy(base)
        tighten_qg_limits!(data, multiplier)
        emit!(
            label,
            "$(label)_qg_limits_x$(multiplier)",
            "reactive_generation_limits",
            data;
            target_type="system",
            target_id="all_generators",
            parameter_name="qg_limit_multiplier",
            parameter_value=multiplier,
        )
    end

    alternate_slack = selection_result === nothing ? nothing : select_alternate_slack_bus(selection_case)
    if alternate_slack !== nothing
        data = deepcopy(base)
        change_slack_bus!(data, alternate_slack)
        emit!(
            label,
            "$(label)_slack_$(alternate_slack)",
            "slack_consistency",
            data;
            target_type="bus",
            target_id=alternate_slack,
            parameter_name="slack_bus",
            parameter_value=alternate_slack,
        )
    end

    if include_no_reference
        emit!(label, label, "slack_without_reference", deepcopy(base); parameter_name="fix_reference_angle", parameter_value=false)
    end

    if selection_result !== nothing
        for outage in select_branch_outages(selection_case, selection_result; top_k_loading=3, top_k_flow=3)
            data = deepcopy(base)
            apply_branch_outage!(data, outage.branch_id)
            emit!(
                label,
                "$(label)_branch_$(outage.branch_id)_out",
                "branch_outage",
                data;
                target_type="branch",
                target_id=outage.branch_id,
                parameter_name="status",
                parameter_value=0.0,
            )
        end

        for outage in select_generator_outages(selection_case, selection_result; top_k_cheapest=2, top_k_pg=2)
            data = deepcopy(base)
            apply_generator_outage!(data, outage.gen_id)
            emit!(
                label,
                "$(label)_gen_$(outage.gen_id)_out",
                "generator_outage",
                data;
                target_type="generator",
                target_id=outage.gen_id,
                parameter_name="status",
                parameter_value=0.0,
            )
        end
    end

    return on_entry === nothing ? entries : nothing
end

function comparison_summary(entry, model::Symbol, own_result, pm_result, own_time::Float64, pm_time::Float64, own_error::String, pm_error::String, pm_iterations::Float64)
    own_status = safe_own_status(own_result)
    pm_status = safe_pm_status(pm_result)
    own_solved = is_solved_status(own_status)
    pm_solved = is_solved_status(pm_status)
    own_obj = safe_objective(own_result)
    pm_obj = safe_objective(pm_result)
    obj_diff = finite_pair(own_obj, pm_obj) ? own_obj - pm_obj : NaN
    obj_rel = finite_pair(obj_diff, pm_obj) ? abs(obj_diff) / (1.0 + abs(pm_obj)) : NaN

    metric_names = (
        :max_pg_diff, :mean_pg_diff, :std_pg_diff,
        :max_qg_diff, :mean_qg_diff, :std_qg_diff,
        :max_vm_diff, :mean_vm_diff, :std_vm_diff,
        :max_va_diff, :mean_va_diff, :std_va_diff,
        :max_pf_diff, :mean_pf_diff, :std_pf_diff,
        :max_qf_diff, :mean_qf_diff, :std_qf_diff,
        :max_loading_pct_diff, :mean_loading_pct_diff, :std_loading_pct_diff,
        :n_pmin_both, :n_pmin_only_own, :n_pmin_only_pm,
        :n_pmax_both, :n_pmax_only_own, :n_pmax_only_pm,
        :n_qmin_both, :n_qmin_only_own, :n_qmin_only_pm,
        :n_qmax_both, :n_qmax_only_own, :n_qmax_only_pm,
        :n_vmin_both, :n_vmin_only_own, :n_vmin_only_pm,
        :n_vmax_both, :n_vmax_only_own, :n_vmax_only_pm,
        :n_thermal_both, :n_thermal_only_own, :n_thermal_only_pm,
        :n_angle_lower_both, :n_angle_lower_only_own, :n_angle_lower_only_pm,
        :n_angle_upper_both, :n_angle_upper_only_own, :n_angle_upper_only_pm,
    )
    metrics = Dict{Symbol, Float64}(name => NaN for name in metric_names)
    own_iterations = own_result === nothing ? NaN : Float64(field_or(own_result, :solve_iterations, NaN))

    if own_solved && pm_solved && own_result !== nothing && pm_result !== nothing
        case = OwnPowerModels.powermodels_to_powercase(deepcopy(entry.data))
        raw = model == :ac ?
            ac_opf_summary_row(case, own_result, pm_result; scenario=entry.scenario) :
            dc_opf_summary_row(case, own_result, pm_result; scenario=entry.scenario)
        for name in metric_names
            metrics[name] = Float64(field_or(raw, name, NaN))
        end
    end

    return (
        base_case=entry.base_case,
        case_name=entry.case_name,
        model=string(model),
        scenario=entry.scenario,
        target_type=entry.target_type,
        target_id=entry.target_id,
        parameter_name=entry.parameter_name,
        parameter_value=entry.parameter_value,
        scenario_id=entry.scenario_id,
        own_status=own_status,
        pm_status=pm_status,
        own_solved=own_solved,
        pm_solved=pm_solved,
        outcome_match=own_solved == pm_solved,
        own_objective=own_obj,
        pm_objective=pm_obj,
        objective_diff=obj_diff,
        objective_rel_diff=obj_rel,
        own_solve_time_sec=own_time,
        pm_solve_time_sec=pm_time,
        solve_time_diff_vs_pm=finite_pair(own_time, pm_time) ? own_time - pm_time : NaN,
        solve_time_ratio_vs_pm=(isfinite(own_time) && isfinite(pm_time) && pm_time > 0.0) ? own_time / pm_time : NaN,
        max_pg_diff=metrics[:max_pg_diff],
        mean_pg_diff=metrics[:mean_pg_diff],
        std_pg_diff=metrics[:std_pg_diff],
        max_qg_diff=metrics[:max_qg_diff],
        mean_qg_diff=metrics[:mean_qg_diff],
        std_qg_diff=metrics[:std_qg_diff],
        max_vm_diff=metrics[:max_vm_diff],
        mean_vm_diff=metrics[:mean_vm_diff],
        std_vm_diff=metrics[:std_vm_diff],
        max_va_diff=metrics[:max_va_diff],
        mean_va_diff=metrics[:mean_va_diff],
        std_va_diff=metrics[:std_va_diff],
        max_pf_diff=metrics[:max_pf_diff],
        mean_pf_diff=metrics[:mean_pf_diff],
        std_pf_diff=metrics[:std_pf_diff],
        max_qf_diff=metrics[:max_qf_diff],
        mean_qf_diff=metrics[:mean_qf_diff],
        std_qf_diff=metrics[:std_qf_diff],
        max_loading_pct_diff=metrics[:max_loading_pct_diff],
        mean_loading_pct_diff=metrics[:mean_loading_pct_diff],
        std_loading_pct_diff=metrics[:std_loading_pct_diff],
        own_solve_iterations=own_iterations,
        pm_solve_iterations=pm_iterations,
        n_pmin_both=metrics[:n_pmin_both],
        n_pmin_only_own=metrics[:n_pmin_only_own],
        n_pmin_only_pm=metrics[:n_pmin_only_pm],
        n_pmax_both=metrics[:n_pmax_both],
        n_pmax_only_own=metrics[:n_pmax_only_own],
        n_pmax_only_pm=metrics[:n_pmax_only_pm],
        n_qmin_both=metrics[:n_qmin_both],
        n_qmin_only_own=metrics[:n_qmin_only_own],
        n_qmin_only_pm=metrics[:n_qmin_only_pm],
        n_qmax_both=metrics[:n_qmax_both],
        n_qmax_only_own=metrics[:n_qmax_only_own],
        n_qmax_only_pm=metrics[:n_qmax_only_pm],
        n_vmin_both=metrics[:n_vmin_both],
        n_vmin_only_own=metrics[:n_vmin_only_own],
        n_vmin_only_pm=metrics[:n_vmin_only_pm],
        n_vmax_both=metrics[:n_vmax_both],
        n_vmax_only_own=metrics[:n_vmax_only_own],
        n_vmax_only_pm=metrics[:n_vmax_only_pm],
        n_thermal_both=metrics[:n_thermal_both],
        n_thermal_only_own=metrics[:n_thermal_only_own],
        n_thermal_only_pm=metrics[:n_thermal_only_pm],
        n_angle_lower_both=metrics[:n_angle_lower_both],
        n_angle_lower_only_own=metrics[:n_angle_lower_only_own],
        n_angle_lower_only_pm=metrics[:n_angle_lower_only_pm],
        n_angle_upper_both=metrics[:n_angle_upper_both],
        n_angle_upper_only_own=metrics[:n_angle_upper_only_own],
        n_angle_upper_only_pm=metrics[:n_angle_upper_only_pm],
        own_error=own_error,
        pm_error=pm_error,
    )
end

function run_one_model(entry, model::Symbol; ac_optimizer, dc_optimizer, fix_reference_angle::Bool=true, write_detail_rows::Bool=false, detail_dir::String="")
    case = OwnPowerModels.powermodels_to_powercase(deepcopy(entry.data))
    own_result = nothing
    pm_result = nothing
    own_error = ""
    pm_error = ""
    own_time = NaN
    pm_time = NaN
    pm_iterations = NaN

    if model == :ac
        start = time()
        try
            own_result = solve_ac_opf_own(case; optimizer=ac_optimizer, silent=true, fix_reference_angle=fix_reference_angle)
        catch err
            own_error = sprint(showerror, err)
        finally
            own_time = time() - start
        end

        start = time()
        try
            pm = PowerModels.instantiate_model(deepcopy(entry.data), ACPPowerModel, PowerModels.build_opf)
            pm_result = PowerModels.optimize_model!(pm, optimizer=ac_optimizer)
            pm_iterations = OwnPowerModels._safe_barrier_iterations(pm.model)
        catch err
            pm_error = sprint(showerror, err)
        finally
            pm_time = time() - start
        end
    elseif model == :dc
        start = time()
        try
            own_result = solve_dc_opf_own(case; optimizer=dc_optimizer, silent=true, fix_reference_angle=fix_reference_angle)
        catch err
            own_error = sprint(showerror, err)
        finally
            own_time = time() - start
        end

        start = time()
        try
            pm = PowerModels.instantiate_model(deepcopy(entry.data), DCPPowerModel, PowerModels.build_opf)
            pm_result = PowerModels.optimize_model!(pm, optimizer=dc_optimizer)
            # DC is solved by HiGHS (simplex), not Ipopt (barrier) -- reusing
            # _safe_barrier_iterations here (as AC does) would always return
            # NaN silently, since HiGHS doesn't support MOI.BarrierIterations.
            # own_solve_dc_opf's own iteration count already uses the correct
            # simplex counterpart (results.jl::_safe_simplex_iterations); this
            # mirrors that for PowerModels' side of the DC comparison.
            pm_iterations = OwnPowerModels._safe_simplex_iterations(pm.model)
        catch err
            pm_error = sprint(showerror, err)
        finally
            pm_time = time() - start
        end
    else
        error("Unsupported model: $(model)")
    end

    if write_detail_rows && own_result !== nothing && pm_result !== nothing &&
       is_solved_status(safe_own_status(own_result)) && is_solved_status(safe_pm_status(pm_result))
        try
            if model == :ac
                write_ac_opf_detail_rows!(detail_dir, case, own_result, pm_result; scenario=entry.scenario)
            else
                write_dc_opf_detail_rows!(detail_dir, case, own_result, pm_result; scenario=entry.scenario)
            end
        catch err
            println("  detail-row write failed for ", model, " ", entry.scenario_id, ": ", sprint(showerror, err))
        end
    end

    return comparison_summary(entry, model, own_result, pm_result, own_time, pm_time, own_error, pm_error, pm_iterations)
end

function main()
    case_dir = arg_value("--case-dir", joinpath("data", "raw", "pglib", "matpower_m", "base_case"))
    output_dir = arg_value("--output-dir", joinpath("results", "own_julia", "opf_validation_sweeps"))
    summary_path = joinpath(output_dir, "compact_opf_validation_summary.csv")
    cases = split_csv_arg(arg_value("--cases", join(DEFAULT_CASES, ",")))
    models = parse_models(arg_value("--models", "ac,dc"))
    # 500 (Ipopt's own default) is insufficient for large/stressed systems --
    # case6470_rte's base AC-OPF needed 564 iterations, case1888_rte's
    # reactive_generation_limits scenario hit the same wall. Raised to 3000.
    max_iter = parse(Int, arg_value("--max-iter", "3000"))
    scenario_filter = Set(split_csv_arg(arg_value("--only-scenario-ids", "")))
    scenario_limit = parse(Int, arg_value("--max-scenarios", "0"))
    strip_prefix = !has_flag("--keep-case-prefix")
    overwrite = has_flag("--overwrite")
    quiet = has_flag("--quiet")
    include_no_reference = has_flag("--include-no-reference")
    write_detail_rows = has_flag("--write-detail-rows")
    detail_dir = joinpath(output_dir, "detail_rows")

    if overwrite && isfile(summary_path)
        rm(summary_path)
    end

    completed = completed_keys(summary_path)
    ac_optimizer = optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => max_iter)
    dc_optimizer = optimizer_with_attributes(HiGHS.Optimizer, "output_flag" => false)
    scenarios_seen = 0

    mkpath(output_dir)
    println("Writing checkpointed summary to: ", abspath(summary_path))
    println("Models: ", join(string.(models), ", "), " | Ipopt max_iter=", max_iter)

    progress = ProgressUnknown(desc="Solving:", spinner=true, showspeed=true)

    try
        for raw_case in cases
            path = case_path(case_dir, raw_case)
            label = clean_case_label(path; strip_case_prefix=strip_prefix)
            if !quiet
                println("\nCASE ", label)
            end

            base_data = PowerModels.parse_file(path)

            function handle_entry(entry)
                if !isempty(scenario_filter) && !(entry.scenario_id in scenario_filter)
                    return
                end
                scenarios_seen += 1
                if scenario_limit > 0 && scenarios_seen > scenario_limit
                    println("Reached --max-scenarios=$(scenario_limit); stopping.")
                    throw(InterruptException())
                end

                for model in models
                    key = (string(model), entry.scenario_id)
                    if key in completed
                        continue
                    end

                    fix_reference_angle = !(entry.scenario == "slack_without_reference")
                    row = run_one_model(entry, model; ac_optimizer=ac_optimizer, dc_optimizer=dc_optimizer, fix_reference_angle=fix_reference_angle, write_detail_rows=write_detail_rows, detail_dir=detail_dir)
                    append_summary_row!(summary_path, row)
                    push!(completed, key)
                    next!(progress; showvalues=[(:case, label), (:model, model), (:scenario, entry.scenario_id)])
                    GC.gc()
                end
            end

            try
                compact_scenarios(base_data, label, ac_optimizer; include_no_reference=include_no_reference, on_entry=handle_entry)
            catch err
                if err isa InterruptException
                    return
                end
                rethrow(err)
            end
        end
    finally
        finish!(progress)
    end

    println("\nDone. Summary: ", abspath(summary_path))
end

main()
