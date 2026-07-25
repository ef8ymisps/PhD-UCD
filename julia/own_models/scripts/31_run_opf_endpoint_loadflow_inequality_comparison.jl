# Compare inequality-constraint levels after PowerModels load-flow validation
# from both OPF endpoints.
#
# For each objective-mismatch scenario, this script:
#   1. takes the own OPF solution and writes it into a PowerModels data dict as
#      the PF dispatch/start point,
#   2. takes the PowerModels OPF solution and writes it into a second
#      PowerModels data dict as the PF dispatch/start point,
#   3. runs PowerModels.compute_ac_pf with Newton-Raphson on both endpoints,
#   4. recomputes AC branch flows with PowerModels.calc_branch_flow_ac, and
#   5. compares all inequality values/margins after PF.
#
# This is intentionally different from the repository Newton PF solver.  The
# point of this diagnostic is to use the PowerModels PF implementation itself
# as the independent AC load-flow operator.
#
# Outputs:
#   results/own_vs_powermodels/opf_endpoint_loadflow_validation_julia.csv
#   results/own_vs_powermodels/opf_endpoint_pf_variable_comparison_julia.csv
#   results/own_vs_powermodels/opf_endpoint_pf_inequality_levels_julia.csv
#   results/own_vs_powermodels/opf_endpoint_pf_inequality_summary_julia.csv

const TWENTYFIVE_SCRIPT_PATH = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH)
end

using DataFrames
using CSV

const PM_ENDPOINT_PF_KWARGS = (
    method = :newton,
    flat_start = false,
    ftol = 1e-8,
    xtol = 1e-8,
    iterations = 100,
)

const KNOWN_LABELS_ENDPOINT_LF = Dict(
    "1888_rte__base__none__none__none__none" => "base",
    "1888_rte_load_x0p8__load_scaling__none__none__load_multiplier__0p8" => "load_x0.8",
    "1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2" => "load_x1.2_reactive_stress",
    "1888_rte_qg_limits_x0p8__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p8" => "qg_limits_x0.8",
)

const ENDPOINT_VARIANT_CONFIGS = [
    (
        variant = "current",
        root = OUT_ROOT,
        objective_path = joinpath(ROOT, "results", "own_vs_powermodels", "objective_mismatch_details_julia_current.csv"),
    ),
    (
        variant = "pm_like",
        root = PM_LIKE_OUT_ROOT,
        objective_path = joinpath(ROOT, "results", "own_vs_powermodels", "objective_mismatch_details_julia_pm_like.csv"),
    ),
]

function expected_scenario_label_endpoint_lf(entry)
    raw_id = String(entry.scenario_id)
    if haskey(KNOWN_LABELS_ENDPOINT_LF, raw_id)
        return KNOWN_LABELS_ENDPOINT_LF[raw_id]
    elseif entry.scenario == "base"
        return "base"
    else
        return raw_id
    end
end

function scenario_data_for_endpoint_lf(case_label::String, scenario_label::String)
    case_file = "pglib_opf_case$(case_label)"
    data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
    entries = compact_scenarios(data, case_label, build_ac_optimizer(); include_no_reference = true)
    for entry in entries
        if expected_scenario_label_endpoint_lf(entry) == scenario_label
            return entry.data
        end
    end
    error("Could not find scenario $(case_label)/$(scenario_label)")
end

function sorted_pm_ids_endpoint_lf(d)
    return sort(collect(keys(d)); by = x -> parse(Int, string(x)))
end

function numeric_or_nan_endpoint_lf(value)
    if value === nothing || ismissing(value)
        return NaN
    end
    try
        number = Float64(value)
        return isfinite(number) ? number : NaN
    catch
        return NaN
    end
end

function nested_get_endpoint_lf(data, keys...; default = nothing)
    current = data
    for key in keys
        if current isa AbstractDict && haskey(current, key)
            current = current[key]
        else
            return default
        end
    end
    return current
end

function bus_number_endpoint_lf(bus_key::AbstractString, bus::Dict)
    value = get(bus, "bus_i", nothing)
    if value !== nothing
        return Int(round(Float64(value)))
    end
    return parse(Int, string(bus_key))
end

function bus_key_by_number_endpoint_lf(data::Dict)
    mapping = Dict{Int, String}()
    for (bus_key, bus) in data["bus"]
        mapping[bus_number_endpoint_lf(string(bus_key), bus)] = string(bus_key)
    end
    return mapping
end

function bus_key_endpoint_lf(data::Dict, bus_id, mapping = bus_key_by_number_endpoint_lf(data))
    key = string(bus_id)
    haskey(data["bus"], key) && return key
    try
        return get(mapping, Int(round(Float64(bus_id))), key)
    catch
        return key
    end
end

function bus_type_label_endpoint_lf(value)
    bus_type = Int(round(Float64(value)))
    bus_type == 1 && return "PQ"
    bus_type == 2 && return "PV"
    bus_type == 3 && return "slack"
    bus_type == 4 && return "isolated"
    return "unknown"
end

function bus_type_for_bus_endpoint_lf(data::Dict, bus_id)::String
    key = bus_key_endpoint_lf(data, bus_id)
    if haskey(data["bus"], key)
        return bus_type_label_endpoint_lf(get(data["bus"][key], "bus_type", 0))
    end
    return "unknown"
end

function bus_type_for_gen_endpoint_lf(data::Dict, gen_id::String)::String
    if !haskey(data["gen"], gen_id)
        return "unknown"
    end
    return bus_type_for_bus_endpoint_lf(data, data["gen"][gen_id]["gen_bus"])
end

function endpoint_column_value(row, prefix::String, field::String)
    return numeric_or_nan_endpoint_lf(row[Symbol("$(prefix)_$(field)")])
end

function apply_endpoint_solution_to_pm_data!(data::Dict, out_dir::String, prefix::String)
    buses_df = DataFrame(CSV.File(joinpath(out_dir, "buses.csv")))
    gens_df = DataFrame(CSV.File(joinpath(out_dir, "generators.csv")))
    mapping = bus_key_by_number_endpoint_lf(data)

    counts = Dict(
        "n_applied_bus_vm" => 0,
        "n_applied_bus_va" => 0,
        "n_applied_gen_pg" => 0,
        "n_applied_gen_qg" => 0,
        "n_applied_gen_vg" => 0,
    )

    for row in eachrow(buses_df)
        bus_key = bus_key_endpoint_lf(data, row.bus_id, mapping)
        haskey(data["bus"], bus_key) || continue

        vm = endpoint_column_value(row, prefix, "vm")
        va = endpoint_column_value(row, prefix, "va")
        if isfinite(vm)
            data["bus"][bus_key]["vm"] = vm
            data["bus"][bus_key]["vm_start"] = vm
            counts["n_applied_bus_vm"] += 1
        end
        if isfinite(va)
            data["bus"][bus_key]["va"] = va
            data["bus"][bus_key]["va_start"] = va
            counts["n_applied_bus_va"] += 1
        end
    end

    for row in eachrow(gens_df)
        gen_id = string(row.gen_id)
        haskey(data["gen"], gen_id) || continue

        pg = endpoint_column_value(row, prefix, "pg")
        qg = endpoint_column_value(row, prefix, "qg")
        if isfinite(pg)
            data["gen"][gen_id]["pg"] = pg
            data["gen"][gen_id]["pg_start"] = pg
            counts["n_applied_gen_pg"] += 1
        end
        if isfinite(qg)
            data["gen"][gen_id]["qg"] = qg
            data["gen"][gen_id]["qg_start"] = qg
            counts["n_applied_gen_qg"] += 1
        end

        bus_key = bus_key_endpoint_lf(data, data["gen"][gen_id]["gen_bus"], mapping)
        if haskey(data["bus"], bus_key) && haskey(data["bus"][bus_key], "vm")
            data["gen"][gen_id]["vg"] = data["bus"][bus_key]["vm"]
            data["gen"][gen_id]["vg_start"] = data["bus"][bus_key]["vm"]
            counts["n_applied_gen_vg"] += 1
        end
    end

    return counts
end

function pf_status_converged_endpoint_lf(status)
    status === true && return true
    text = uppercase(string(status))
    return text in ("TRUE", "CONVERGED", "OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")
end

function calculate_pm_branch_flows!(data::Dict, result::Dict)
    try
        solution = get(result, "solution", Dict{String, Any}())
        PowerModels.update_data!(data, solution)
        flows = PowerModels.calc_branch_flow_ac(data)
        PowerModels.update_data!(data, flows)
        if !haskey(result, "solution")
            result["solution"] = Dict{String, Any}()
        end
        if haskey(flows, "branch")
            result["solution"]["branch"] = flows["branch"]
        end
        return true, ""
    catch err
        return false, sprint(showerror, err)
    end
end

function run_pm_endpoint_pf(data::Dict)
    pf_data = deepcopy(data)
    result = Dict{String, Any}()
    elapsed = NaN
    exception = ""

    try
        elapsed = @elapsed result = PowerModels.compute_ac_pf(
            pf_data;
            method = PM_ENDPOINT_PF_KWARGS.method,
            flat_start = PM_ENDPOINT_PF_KWARGS.flat_start,
            ftol = PM_ENDPOINT_PF_KWARGS.ftol,
            xtol = PM_ENDPOINT_PF_KWARGS.xtol,
            iterations = PM_ENDPOINT_PF_KWARGS.iterations,
        )
    catch err
        exception = sprint(showerror, err)
        result = Dict{String, Any}(
            "termination_status" => "EXCEPTION",
            "solution" => Dict{String, Any}(),
        )
    end

    branch_ok, branch_error = isempty(exception) ? calculate_pm_branch_flows!(pf_data, result) : (false, "")
    status = get(result, "termination_status", "UNKNOWN")

    return (
        data = pf_data,
        result = result,
        bus_key_by_number = bus_key_by_number_endpoint_lf(pf_data),
        pf_engine = "PowerModels.compute_ac_pf",
        pf_method = "newton",
        pf_kwargs = "method=:newton; flat_start=false; ftol=1e-8; xtol=1e-8; iterations=100",
        status = string(status),
        converged = pf_status_converged_endpoint_lf(status),
        solve_time = get(result, "solve_time", elapsed),
        branch_flows_calculated = branch_ok,
        branch_flow_error = branch_error,
        exception = exception,
    )
end

function pf_solution_value_endpoint_lf(pf, section::String, id, field::String)
    key = string(id)
    value = nested_get_endpoint_lf(pf.result, "solution", section, key, field; default = nothing)
    if value !== nothing && !ismissing(value)
        return numeric_or_nan_endpoint_lf(value)
    end

    data_key = key
    if section == "bus"
        data_key = bus_key_endpoint_lf(pf.data, key, pf.bus_key_by_number)
    end

    value = nested_get_endpoint_lf(pf.data, section, data_key, field; default = nothing)
    return numeric_or_nan_endpoint_lf(value)
end

function pf_branch_flow_value_endpoint_lf(pf, branch_id::String, field::String)
    return pf_solution_value_endpoint_lf(pf, "branch", branch_id, field)
end

function branch_angle_value_endpoint_lf(data::Dict, pf, branch_id::String)
    if !haskey(data["branch"], branch_id)
        return NaN
    end
    branch = data["branch"][branch_id]
    f_key = bus_key_endpoint_lf(pf.data, branch["f_bus"], pf.bus_key_by_number)
    t_key = bus_key_endpoint_lf(pf.data, branch["t_bus"], pf.bus_key_by_number)
    va_f = pf_solution_value_endpoint_lf(pf, "bus", f_key, "va")
    va_t = pf_solution_value_endpoint_lf(pf, "bus", t_key, "va")
    return va_f - va_t
end

function pf_inequality_value_endpoint_lf(data::Dict, pf, constraint_type, element_id::String, bound::Float64)
    constraint_type = String(constraint_type)
    if constraint_type in ("pg_min", "pg_max")
        value = pf_solution_value_endpoint_lf(pf, "gen", element_id, "pg")
        margin = constraint_type == "pg_min" ? value - bound : bound - value
        return value, margin
    elseif constraint_type in ("qg_min", "qg_max")
        value = pf_solution_value_endpoint_lf(pf, "gen", element_id, "qg")
        margin = constraint_type == "qg_min" ? value - bound : bound - value
        return value, margin
    elseif constraint_type in ("vm_min", "vm_max")
        value = pf_solution_value_endpoint_lf(pf, "bus", element_id, "vm")
        margin = constraint_type == "vm_min" ? value - bound : bound - value
        return value, margin
    elseif constraint_type == "thermal"
        pfv = pf_branch_flow_value_endpoint_lf(pf, element_id, "pf")
        qfv = pf_branch_flow_value_endpoint_lf(pf, element_id, "qf")
        ptv = pf_branch_flow_value_endpoint_lf(pf, element_id, "pt")
        qtv = pf_branch_flow_value_endpoint_lf(pf, element_id, "qt")
        sf = sqrt(pfv^2 + qfv^2)
        st = sqrt(ptv^2 + qtv^2)
        value = max(sf, st)
        margin = bound - value
        return value, margin
    elseif constraint_type in ("angle_lower", "angle_upper")
        value = branch_angle_value_endpoint_lf(data, pf, element_id)
        margin = constraint_type == "angle_lower" ? value - bound : bound - value
        return value, margin
    end
    return NaN, NaN
end

function pf_inequality_evidence_endpoint_lf(data::Dict, constraint_type, element_id::String)
    constraint_type = String(constraint_type)
    if constraint_type in ("pg_min", "pg_max")
        bus_type = bus_type_for_gen_endpoint_lf(data, element_id)
        if bus_type == "slack"
            return (
                pf_check_independence = "powermodels_compute_ac_pf_slack_pg_rebalanced",
                pf_value_source = "PowerModels.compute_ac_pf may rebalance slack-generator Pg; non-slack Pg remains specified dispatch",
                bus_type = bus_type,
                bus_voltage_check_class = "not_voltage",
                independently_rederived_after_pf = true,
            )
        end
        return (
            pf_check_independence = "powermodels_compute_ac_pf_specified_pg_dispatch",
            pf_value_source = "PowerModels.compute_ac_pf uses non-slack Pg as specified active-power dispatch",
            bus_type = bus_type,
            bus_voltage_check_class = "not_voltage",
            independently_rederived_after_pf = false,
        )
    elseif constraint_type in ("qg_min", "qg_max")
        return (
            pf_check_independence = "powermodels_compute_ac_pf_rederived_qg",
            pf_value_source = "PowerModels.compute_ac_pf returns generator Qg from the solved AC PF state",
            bus_type = bus_type_for_gen_endpoint_lf(data, element_id),
            bus_voltage_check_class = "not_voltage",
            independently_rederived_after_pf = true,
        )
    elseif constraint_type in ("vm_min", "vm_max")
        bus_type = bus_type_for_bus_endpoint_lf(data, element_id)
        if bus_type == "PQ"
            return (
                pf_check_independence = "powermodels_compute_ac_pf_rederived_pq_bus_vm",
                pf_value_source = "PowerModels.compute_ac_pf Newton-solved PQ-bus voltage magnitude",
                bus_type = bus_type,
                bus_voltage_check_class = "rederived_pq_bus_vm",
                independently_rederived_after_pf = true,
            )
        end
        return (
            pf_check_independence = "powermodels_compute_ac_pf_voltage_setpoint_pinned",
            pf_value_source = "PowerModels.compute_ac_pf retains PV/slack voltage magnitude setpoints from the OPF endpoint",
            bus_type = bus_type,
            bus_voltage_check_class = "pinned_pv_or_slack_vm",
            independently_rederived_after_pf = false,
        )
    elseif constraint_type == "thermal"
        return (
            pf_check_independence = "powermodels_compute_ac_pf_rederived_branch_flow",
            pf_value_source = "Branch apparent flow recomputed by PowerModels.calc_branch_flow_ac from the compute_ac_pf voltage state",
            bus_type = "branch",
            bus_voltage_check_class = "not_voltage",
            independently_rederived_after_pf = true,
        )
    elseif constraint_type in ("angle_lower", "angle_upper")
        return (
            pf_check_independence = "powermodels_compute_ac_pf_rederived_angle",
            pf_value_source = "Branch angle difference recomputed from the compute_ac_pf voltage angles",
            bus_type = "branch",
            bus_voltage_check_class = "not_voltage",
            independently_rederived_after_pf = true,
        )
    end
    return (
        pf_check_independence = "unknown",
        pf_value_source = "unknown",
        bus_type = "unknown",
        bus_voltage_check_class = "unknown",
        independently_rederived_after_pf = false,
    )
end

function endpoint_variable_comparison_rows(variant, case_label, scenario_label, data, own_pf, pm_pf)
    rows = NamedTuple[]
    for bus_id in sorted_pm_ids_endpoint_lf(data["bus"])
        for quantity in ("vm", "va")
            own_value = pf_solution_value_endpoint_lf(own_pf, "bus", bus_id, quantity)
            pm_value = pf_solution_value_endpoint_lf(pm_pf, "bus", bus_id, quantity)
            push!(rows, (
                variant = variant,
                case = case_label,
                scenario = scenario_label,
                pf_engine = "PowerModels.compute_ac_pf",
                pf_method = "newton",
                element_type = "bus",
                element_id = bus_id,
                quantity = quantity,
                own_pf_value = own_value,
                pm_pf_value = pm_value,
                diff = own_value - pm_value,
                abs_diff = abs(own_value - pm_value),
            ))
        end
    end

    for gen_id in sorted_pm_ids_endpoint_lf(data["gen"])
        for quantity in ("pg", "qg")
            own_value = pf_solution_value_endpoint_lf(own_pf, "gen", gen_id, quantity)
            pm_value = pf_solution_value_endpoint_lf(pm_pf, "gen", gen_id, quantity)
            push!(rows, (
                variant = variant,
                case = case_label,
                scenario = scenario_label,
                pf_engine = "PowerModels.compute_ac_pf",
                pf_method = "newton",
                element_type = "generator",
                element_id = gen_id,
                quantity = quantity,
                own_pf_value = own_value,
                pm_pf_value = pm_value,
                diff = own_value - pm_value,
                abs_diff = abs(own_value - pm_value),
            ))
        end
    end

    for branch_id in sorted_pm_ids_endpoint_lf(data["branch"])
        for quantity in ("pf", "qf", "pt", "qt")
            own_value = pf_branch_flow_value_endpoint_lf(own_pf, branch_id, quantity)
            pm_value = pf_branch_flow_value_endpoint_lf(pm_pf, branch_id, quantity)
            push!(rows, (
                variant = variant,
                case = case_label,
                scenario = scenario_label,
                pf_engine = "PowerModels.compute_ac_pf",
                pf_method = "newton",
                element_type = "branch",
                element_id = branch_id,
                quantity = quantity,
                own_pf_value = own_value,
                pm_pf_value = pm_value,
                diff = own_value - pm_value,
                abs_diff = abs(own_value - pm_value),
            ))
        end
    end
    return rows
end

function binding_flag_endpoint_lf(margin, tol::Float64)
    return isfinite(margin) && abs(margin) <= tol
end

function endpoint_inequality_rows(variant, case_label, scenario_label, out_dir, data, own_pf, pm_pf; tol = 1e-6)
    rows = NamedTuple[]
    constraints_df = DataFrame(CSV.File(joinpath(out_dir, "constraints_binding.csv")))
    for row in eachrow(constraints_df)
        constraint_type = string(row.constraint_type)
        element_id = string(row.element_id)
        bound = Float64(row.bound)
        own_pf_value, own_pf_margin = pf_inequality_value_endpoint_lf(data, own_pf, constraint_type, element_id, bound)
        pm_pf_value, pm_pf_margin = pf_inequality_value_endpoint_lf(data, pm_pf, constraint_type, element_id, bound)
        evidence = pf_inequality_evidence_endpoint_lf(data, constraint_type, element_id)
        own_pf_binding = binding_flag_endpoint_lf(own_pf_margin, tol)
        pm_pf_binding = binding_flag_endpoint_lf(pm_pf_margin, tol)
        push!(rows, (
            variant = variant,
            case = case_label,
            scenario = scenario_label,
            pf_engine = "PowerModels.compute_ac_pf",
            pf_method = "newton",
            pf_kwargs = own_pf.pf_kwargs,
            constraint_type = constraint_type,
            element_id = element_id,
            bound = bound,
            own_opf_value = Float64(row.own_value),
            pm_opf_value = Float64(row.pm_value),
            own_opf_margin = Float64(row.own_margin),
            pm_opf_margin = Float64(row.pm_margin),
            own_opf_binding = Bool(row.own_binding),
            pm_opf_binding = Bool(row.pm_binding),
            own_pf_value = own_pf_value,
            pm_pf_value = pm_pf_value,
            own_pf_margin = own_pf_margin,
            pm_pf_margin = pm_pf_margin,
            own_pf_converged = own_pf.converged,
            pm_pf_converged = pm_pf.converged,
            pf_check_independence = evidence.pf_check_independence,
            pf_value_source = evidence.pf_value_source,
            bus_type = evidence.bus_type,
            bus_voltage_check_class = evidence.bus_voltage_check_class,
            independently_rederived_after_pf = evidence.independently_rederived_after_pf,
            own_pf_binding = own_pf_binding,
            pm_pf_binding = pm_pf_binding,
            pf_value_diff = own_pf_value - pm_pf_value,
            abs_pf_value_diff = abs(own_pf_value - pm_pf_value),
            pf_margin_diff = own_pf_margin - pm_pf_margin,
            abs_pf_margin_diff = abs(own_pf_margin - pm_pf_margin),
            opf_binding_relation = Bool(row.own_binding) == Bool(row.pm_binding) ? "same" : (Bool(row.own_binding) ? "only_own_opf_binding" : "only_pm_opf_binding"),
            pf_binding_relation = own_pf_binding == pm_pf_binding ? "same" : (own_pf_binding ? "only_own_pf_binding" : "only_pm_pf_binding"),
            opf_to_pf_binding_relation_changed = (Bool(row.own_binding) != Bool(row.pm_binding)) != (own_pf_binding != pm_pf_binding),
        ))
    end
    return rows
end

function load_endpoint_lf_targets()
    targets = NamedTuple[]
    seen = Set{Tuple{String, String, String, String}}()
    for cfg in ENDPOINT_VARIANT_CONFIGS
        isfile(cfg.objective_path) || continue
        df = DataFrame(CSV.File(cfg.objective_path))
        for row in eachrow(df)
            key = (cfg.variant, cfg.root, String(row.case), String(row.scenario))
            if !(key in seen)
                push!(targets, (
                    variant = cfg.variant,
                    root = cfg.root,
                    case = String(row.case),
                    scenario = String(row.scenario),
                ))
                push!(seen, key)
            end
        end
    end
    return targets
end

function finite_values_endpoint_lf(values)
    return [Float64(x) for x in values if !ismissing(x) && x isa Number && isfinite(Float64(x))]
end

function endpoint_lf_summary_rows(inequality_rows)
    isempty(inequality_rows) && return NamedTuple[]
    df = DataFrame(inequality_rows)
    rows = NamedTuple[]
    grouped = groupby(df, [:variant, :case, :scenario])
    for group in grouped
        material = filter(r -> r.pf_binding_relation != "same", group)
        changed = filter(r -> r.opf_to_pf_binding_relation_changed, group)
        margin_diffs = finite_values_endpoint_lf(group.abs_pf_margin_diff)
        value_diffs = finite_values_endpoint_lf(group.abs_pf_value_diff)
        push!(rows, (
            variant = group.variant[1],
            case = group.case[1],
            scenario = group.scenario[1],
            pf_engine = "PowerModels.compute_ac_pf",
            pf_method = "newton",
            n_constraints = nrow(group),
            n_opf_binding_mismatches = sum(group.opf_binding_relation .!= "same"),
            n_pf_binding_mismatches = sum(group.pf_binding_relation .!= "same"),
            n_binding_relation_changed_after_pf = nrow(changed),
            max_abs_pf_margin_diff = isempty(margin_diffs) ? NaN : maximum(margin_diffs),
            max_abs_pf_value_diff = isempty(value_diffs) ? NaN : maximum(value_diffs),
            n_pf_binding_mismatch_pg = sum(in.(material.constraint_type, Ref(["pg_min", "pg_max"]))),
            n_pf_binding_mismatch_qg = sum(in.(material.constraint_type, Ref(["qg_min", "qg_max"]))),
            n_pf_binding_mismatch_vm = sum(in.(material.constraint_type, Ref(["vm_min", "vm_max"]))),
            n_pf_binding_mismatch_thermal = sum(material.constraint_type .== "thermal"),
            n_pf_binding_mismatch_angle = sum(in.(material.constraint_type, Ref(["angle_lower", "angle_upper"]))),
        ))
    end
    return rows
end

function validation_row_endpoint_lf(variant, case_label, scenario_label, endpoint, counts, pf)
    return (
        variant = variant,
        case = case_label,
        scenario = scenario_label,
        endpoint = endpoint,
        pf_engine = pf.pf_engine,
        pf_method = pf.pf_method,
        pf_kwargs = pf.pf_kwargs,
        pf_converged = pf.converged,
        pf_termination_status = pf.status,
        pf_solve_time_sec = pf.solve_time,
        pf_iterations = missing,
        pf_iterations_source = "not_available_from_PowerModels.compute_ac_pf",
        branch_flows_calculated = pf.branch_flows_calculated,
        branch_flow_error = pf.branch_flow_error,
        exception = pf.exception,
        n_applied_bus_vm = counts["n_applied_bus_vm"],
        n_applied_bus_va = counts["n_applied_bus_va"],
        n_applied_gen_pg = counts["n_applied_gen_pg"],
        n_applied_gen_qg = counts["n_applied_gen_qg"],
        n_applied_gen_vg = counts["n_applied_gen_vg"],
    )
end

function main()
    validation_rows = NamedTuple[]
    variable_rows = NamedTuple[]
    inequality_rows = NamedTuple[]

    for target in load_endpoint_lf_targets()
        println("PowerModels endpoint PF comparison $(target.variant) / $(target.case) / $(target.scenario)")
        out_dir = joinpath(target.root, target.case, safe_scenario_folder(target.scenario))
        required = ["buses.csv", "generators.csv", "branches.csv", "constraints_binding.csv"]
        if any(!isfile(joinpath(out_dir, file)) for file in required)
            println("  missing exported OPF tables; skipping")
            continue
        end

        data = scenario_data_for_endpoint_lf(target.case, target.scenario)
        own_pf_data = deepcopy(data)
        pm_pf_data = deepcopy(data)
        own_counts = apply_endpoint_solution_to_pm_data!(own_pf_data, out_dir, "own")
        pm_counts = apply_endpoint_solution_to_pm_data!(pm_pf_data, out_dir, "pm")

        own_pf = run_pm_endpoint_pf(own_pf_data)
        pm_pf = run_pm_endpoint_pf(pm_pf_data)

        append!(variable_rows, endpoint_variable_comparison_rows(target.variant, target.case, target.scenario, data, own_pf, pm_pf))
        append!(inequality_rows, endpoint_inequality_rows(target.variant, target.case, target.scenario, out_dir, data, own_pf, pm_pf))

        push!(validation_rows, validation_row_endpoint_lf(target.variant, target.case, target.scenario, "own_opf_solution_warm_start", own_counts, own_pf))
        push!(validation_rows, validation_row_endpoint_lf(target.variant, target.case, target.scenario, "powermodels_opf_solution_warm_start", pm_counts, pm_pf))
    end

    out_root = joinpath(ROOT, "results", "own_vs_powermodels")
    CSV.write(joinpath(out_root, "opf_endpoint_loadflow_validation_julia.csv"), DataFrame(validation_rows))
    CSV.write(joinpath(out_root, "opf_endpoint_pf_variable_comparison_julia.csv"), DataFrame(variable_rows))
    CSV.write(joinpath(out_root, "opf_endpoint_pf_inequality_levels_julia.csv"), DataFrame(inequality_rows))
    CSV.write(joinpath(out_root, "opf_endpoint_pf_inequality_summary_julia.csv"), DataFrame(endpoint_lf_summary_rows(inequality_rows)))
    println("Wrote PowerModels endpoint load-flow inequality comparison outputs to $(out_root)")
end

main()
