using PowerModels
using Ipopt
using DataFrames
using CSV
using JSON

const SUCCESSFUL_TERMINATION_STATUSES = Set([
    "OPTIMAL",
    "LOCALLY_SOLVED",
    "ALMOST_OPTIMAL",
    "ALMOST_LOCALLY_SOLVED",
])

function sorted_pm_ids(collection)
    return sort(collect(keys(collection)); by = x -> parse(Int, string(x)))
end

function safe_get(data, keys...; default = NaN)
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

function is_successful_result(result)
    status = uppercase(string(safe_get(result, "termination_status"; default = "")))
    return status in SUCCESSFUL_TERMINATION_STATUSES
end

function check_result_status(label, result)
    status = safe_get(result, "termination_status"; default = "MISSING")

    if is_successful_result(result)
        println(label, " status: ", status)
    else
        println(label, " status: ", status, " (check before trusting the solution)")
    end

    return is_successful_result(result)
end

function print_solver_summary(label, result)
    println("\n==============================")
    println(uppercase(label), " SOLVER SUMMARY")
    println("==============================")

    println(label, " status: ", safe_get(result, "termination_status"; default = "MISSING"))
    println(label, " solve time: ", safe_get(result, "solve_time"; default = "MISSING"))

    objective = economic_objective(label, result)
    if !ismissing(objective)
        println(label, " objective: ", objective)
    elseif !ismissing(safe_get(result, "objective"; default = missing))
        println(label, " solver objective: ", safe_get(result, "objective"; default = missing), " (not an economic OPF objective)")
    end

    return nothing
end

function apply_generator_pg_dispatch(network_data, dispatch_result)
    checked_network_data = deepcopy(network_data)

    for gen_id in sorted_pm_ids(checked_network_data["gen"])
        pg = safe_get(dispatch_result, "solution", "gen", gen_id, "pg"; default = missing)

        if !ismissing(pg)
            checked_network_data["gen"][gen_id]["pg"] = pg
        end
    end

    return checked_network_data
end

function run_ac_pf_with_dispatch(network_data, dispatch_result; optimizer = Ipopt.Optimizer)
    checked_network_data = apply_generator_pg_dispatch(network_data, dispatch_result)
    result_ac_pf_check = solve_ac_pf(checked_network_data, optimizer)

    return (
        network_data = checked_network_data,
        result = result_ac_pf_check,
    )
end

function dispatch_ac_pf_generator_check_table(network_data, dispatch_result, result_ac_pf_check)
    rows = Vector{NamedTuple}()

    for gen_id in sorted_pm_ids(network_data["gen"])
        gen_data = network_data["gen"][gen_id]
        dispatch_pg = safe_get(dispatch_result, "solution", "gen", gen_id, "pg"; default = missing)
        ac_pf_pg = safe_get(result_ac_pf_check, "solution", "gen", gen_id, "pg"; default = missing)
        ac_pf_qg = safe_get(result_ac_pf_check, "solution", "gen", gen_id, "qg"; default = missing)

        push!(rows, (
            gen_id = gen_id,
            bus = gen_data["gen_bus"],
            bus_type = network_data["bus"][string(gen_data["gen_bus"])]["bus_type"],
            dispatch_pg = dispatch_pg,
            ac_pf_pg = ac_pf_pg,
            pg_difference = (!ismissing(dispatch_pg) && !ismissing(ac_pf_pg)) ? ac_pf_pg - dispatch_pg : missing,
            ac_pf_qg = ac_pf_qg,
            qmin = gen_data["qmin"],
            qmax = gen_data["qmax"],
            qg_status = limit_status(ac_pf_qg, gen_data["qmin"], gen_data["qmax"], "qg"),
        ))
    end

    return DataFrame(rows)
end

function print_dispatch_ac_pf_check(network_data, dispatch_result, result_ac_pf_check; label = "DC OPF -> AC PF")
    gen_check = dispatch_ac_pf_generator_check_table(network_data, dispatch_result, result_ac_pf_check)
    violations = constraint_violations_dataframe(network_data, result_ac_pf_check; label = label)

    pg_differences = nonmissing_values(gen_check.pg_difference)
    max_pg_difference = isempty(pg_differences) ? missing : maximum(abs.(pg_differences))

    println("\n==============================")
    println(uppercase(label), " CHECK")
    println("==============================")
    println("AC PF status: ", safe_get(result_ac_pf_check, "termination_status"; default = "MISSING"))
    println("AC PF solve time: ", safe_get(result_ac_pf_check, "solve_time"; default = "MISSING"))
    println("Maximum generator pg adjustment in AC PF: ", printable_value(max_pg_difference))
    println("Limit issue count: ", nrow(violations))

    if nrow(violations) > 0
        println("\nDetected AC feasibility issues:")
        for row in eachrow(violations)
            println(
                row.element, " ", row.id,
                " | ", row.quantity,
                " = ", printable_value(row.value),
                " | limits = [", printable_value(row.lower), ", ", printable_value(row.upper), "]",
                " | ", row.status
            )
        end
    end

    return (
        generator_check = gen_check,
        violations = violations,
    )
end

function verify_dispatch_with_ac_pf(
    network_data,
    dispatch_result;
    case_name,
    source_formulation = "DC OPF",
    scenario = "ac feasibility check",
    load_scale = 1.0,
    outage_type = "none",
    outage_id = missing,
    optimizer = Ipopt.Optimizer,
)
    if !is_successful_result(dispatch_result)
        status = safe_get(dispatch_result, "termination_status"; default = "MISSING")
        error("Cannot run AC PF verification because the dispatch result status is $(status).")
    end

    check = run_ac_pf_with_dispatch(network_data, dispatch_result; optimizer = optimizer)
    label = source_formulation * " -> AC PF"
    check_summary = print_dispatch_ac_pf_check(network_data, dispatch_result, check.result; label = label)

    comparison_row = result_comparison_row(
        check.network_data,
        check.result;
        case_name = case_name,
        formulation = label,
        scenario = scenario,
        load_scale = load_scale,
        outage_type = outage_type,
        outage_id = outage_id,
        notes = "AC PF verification using active dispatch from " * source_formulation,
    )

    return (
        network_data = check.network_data,
        result = check.result,
        comparison_row = comparison_row,
        generator_check = check_summary.generator_check,
        violations = check_summary.violations,
    )
end

function voltage_status(vm, vmin, vmax; tolerance = 1e-4)
    if ismissing(vm)
        return "NO VM"
    elseif vm < vmin - tolerance
        return "LOW VOLTAGE VIOLATION"
    elseif vm > vmax + tolerance
        return "HIGH VOLTAGE VIOLATION"
    elseif abs(vm - vmin) < tolerance
        return "AT LOWER LIMIT"
    elseif abs(vm - vmax) < tolerance
        return "AT UPPER LIMIT"
    end

    return "OK"
end

function limit_status(value, lower, upper, label; tolerance = 1e-4)
    if ismissing(value)
        return "NO " * uppercase(label)
    elseif value < lower - tolerance
        return uppercase(label) * " BELOW MIN"
    elseif value > upper + tolerance
        return uppercase(label) * " ABOVE MAX"
    elseif abs(value - lower) < tolerance
        return uppercase(label) * " AT MIN"
    elseif abs(value - upper) < tolerance
        return uppercase(label) * " AT MAX"
    end

    return "OK"
end

function flow_status(max_flow, rate_a; tolerance = 1e-4)
    if ismissing(max_flow)
        return "NO FLOW"
    elseif rate_a <= 0
        return "NO RATE_A LIMIT"
    elseif max_flow > rate_a + tolerance
        return "THERMAL LIMIT VIOLATION"
    elseif abs(max_flow - rate_a) < tolerance
        return "AT THERMAL LIMIT"
    end

    return "OK"
end

function printable_value(value; digits = 5)
    if ismissing(value)
        return "missing"
    elseif value isa Number
        return string(round(value, digits = digits))
    end

    return string(value)
end

function get_bus_voltage_table(network_data, result)
    rows = Vector{NamedTuple}()

    for bus_id in sorted_pm_ids(network_data["bus"])
        bus_data = network_data["bus"][bus_id]

        vm = safe_get(result, "solution", "bus", bus_id, "vm"; default = missing)
        va = safe_get(result, "solution", "bus", bus_id, "va"; default = missing)
        vmin = bus_data["vmin"]
        vmax = bus_data["vmax"]

        push!(rows, (
            bus_id = bus_id,
            bus_type = bus_data["bus_type"],
            vm = vm,
            va = va,
            vmin = vmin,
            vmax = vmax,
            status = voltage_status(vm, vmin, vmax),
        ))
    end

    return DataFrame(rows)
end

function get_generator_dispatch_table(network_data, result_ac_pf, result_dc_pf, result_ac_opf, result_dc_opf)
    rows = Vector{NamedTuple}()

    for gen_id in sorted_pm_ids(network_data["gen"])
        gen_data = network_data["gen"][gen_id]

        push!(rows, (
            gen_id = gen_id,
            bus = gen_data["gen_bus"],
            pg_original = gen_data["pg"],
            qg_original = gen_data["qg"],
            pmin = gen_data["pmin"],
            pmax = gen_data["pmax"],
            qmin = gen_data["qmin"],
            qmax = gen_data["qmax"],
            pg_ac_pf = safe_get(result_ac_pf, "solution", "gen", gen_id, "pg"; default = missing),
            pg_dc_pf = safe_get(result_dc_pf, "solution", "gen", gen_id, "pg"; default = missing),
            pg_ac_opf = safe_get(result_ac_opf, "solution", "gen", gen_id, "pg"; default = missing),
            pg_dc_opf = safe_get(result_dc_opf, "solution", "gen", gen_id, "pg"; default = missing),
            qg_ac_pf = safe_get(result_ac_pf, "solution", "gen", gen_id, "qg"; default = missing),
            qg_ac_opf = safe_get(result_ac_opf, "solution", "gen", gen_id, "qg"; default = missing),
        ))
    end

    return DataFrame(rows)
end

function get_generator_result_table(network_data, result; label = "result")
    rows = Vector{NamedTuple}()

    for gen_id in sorted_pm_ids(network_data["gen"])
        gen_data = network_data["gen"][gen_id]
        pg = safe_get(result, "solution", "gen", gen_id, "pg"; default = missing)
        qg = safe_get(result, "solution", "gen", gen_id, "qg"; default = missing)

        push!(rows, (
            gen_id = gen_id,
            label = label,
            bus = gen_data["gen_bus"],
            pg = pg,
            qg = qg,
            pmin = gen_data["pmin"],
            pmax = gen_data["pmax"],
            qmin = gen_data["qmin"],
            qmax = gen_data["qmax"],
            pg_status = limit_status(pg, gen_data["pmin"], gen_data["pmax"], "pg"),
            qg_status = limit_status(qg, gen_data["qmin"], gen_data["qmax"], "qg"),
        ))
    end

    return DataFrame(rows)
end

function get_branch_flow_table(network_data, result; label = "result")
    rows = Vector{NamedTuple}()

    for branch_id in sorted_pm_ids(network_data["branch"])
        branch_data = network_data["branch"][branch_id]

        pf = safe_get(result, "solution", "branch", branch_id, "pf"; default = missing)
        pt = safe_get(result, "solution", "branch", branch_id, "pt"; default = missing)
        qf = safe_get(result, "solution", "branch", branch_id, "qf"; default = missing)
        qt = safe_get(result, "solution", "branch", branch_id, "qt"; default = missing)

        sf = (!ismissing(pf) && !ismissing(qf)) ? sqrt(pf^2 + qf^2) : missing
        st = (!ismissing(pt) && !ismissing(qt)) ? sqrt(pt^2 + qt^2) : missing
        max_apparent_flow = (!ismissing(sf) && !ismissing(st)) ? max(sf, st) : missing

        active_values = [value for value in (pf, pt) if !ismissing(value)]
        max_active_flow = isempty(active_values) ? missing : maximum(abs.(active_values))
        max_flow = !ismissing(max_apparent_flow) ? max_apparent_flow : max_active_flow
        rate_a = branch_data["rate_a"]

        push!(rows, (
            branch_id = branch_id,
            label = label,
            f_bus = branch_data["f_bus"],
            t_bus = branch_data["t_bus"],
            rate_a = rate_a,
            pf = pf,
            pt = pt,
            qf = qf,
            qt = qt,
            sf = sf,
            st = st,
            max_apparent_flow = max_apparent_flow,
            max_active_flow = max_active_flow,
            max_flow = max_flow,
            status = flow_status(max_flow, rate_a),
        ))
    end

    return DataFrame(rows)
end

function nonmissing_values(values)
    return [value for value in values if !ismissing(value) && value isa Number && !isnan(value)]
end

function row_with_extreme(table, value_column, extreme_function)
    values = nonmissing_values(table[!, value_column])

    if isempty(values)
        return missing
    end

    extreme_value = extreme_function(values)
    row_index = findfirst(
        value -> !ismissing(value) && value isa Number && !isnan(value) && value == extreme_value,
        table[!, value_column],
    )
    return table[row_index, :]
end

function total_active_load(network_data)
    return sum(load["pd"] for load in values(network_data["load"]) if get(load, "status", 1) != 0)
end

function total_reactive_load(network_data)
    return sum(load["qd"] for load in values(network_data["load"]) if get(load, "status", 1) != 0)
end

function active_element_count(network_data, element_key, status_key)
    return count(element -> get(element, status_key, 1) != 0, values(network_data[element_key]))
end

function is_power_flow_formulation(formulation)
    upper_formulation = uppercase(string(formulation))
    return upper_formulation in ["AC PF", "DC PF"] ||
        occursin("-> AC PF", upper_formulation) ||
        occursin("-> DC PF", upper_formulation)
end

function economic_objective(formulation, result)
    if is_power_flow_formulation(formulation)
        return missing
    end

    return safe_get(result, "objective"; default = missing)
end

function active_branch_ids(network_data)
    return [
        branch_id
        for branch_id in sorted_pm_ids(network_data["branch"])
        if get(network_data["branch"][branch_id], "br_status", 1) != 0
    ]
end

function active_generator_ids(network_data)
    return [
        gen_id
        for gen_id in sorted_pm_ids(network_data["gen"])
        if get(network_data["gen"][gen_id], "gen_status", 1) != 0
    ]
end

function scale_loads(network_data, load_scale; scale_reactive = true)
    scaled_network_data = deepcopy(network_data)

    for load in values(scaled_network_data["load"])
        if get(load, "status", 1) == 0
            continue
        end

        load["pd"] *= load_scale
        if scale_reactive
            load["qd"] *= load_scale
        end
    end

    return scaled_network_data
end

function apply_branch_outage(network_data, branch_id)
    outage_network_data = deepcopy(network_data)
    branch_key = string(branch_id)

    if !haskey(outage_network_data["branch"], branch_key)
        error("Branch $(branch_key) does not exist.")
    end

    outage_network_data["branch"][branch_key]["br_status"] = 0
    return outage_network_data
end

function apply_generator_outage(network_data, gen_id)
    outage_network_data = deepcopy(network_data)
    gen_key = string(gen_id)

    if !haskey(outage_network_data["gen"], gen_key)
        error("Generator $(gen_key) does not exist.")
    end

    outage_network_data["gen"][gen_key]["gen_status"] = 0
    outage_network_data["gen"][gen_key]["pg"] = 0.0
    outage_network_data["gen"][gen_key]["qg"] = 0.0

    return outage_network_data
end

function make_stress_case(
    network_data;
    load_scale = 1.0,
    outage_type = "none",
    outage_id = missing,
    scale_reactive = true,
)
    stressed_network_data = scale_loads(network_data, load_scale; scale_reactive = scale_reactive)

    if outage_type == "none" || ismissing(outage_id)
        return stressed_network_data
    elseif outage_type == "branch"
        return apply_branch_outage(stressed_network_data, outage_id)
    elseif outage_type == "generator"
        return apply_generator_outage(stressed_network_data, outage_id)
    end

    error("Unsupported outage_type: $(outage_type). Use \"none\", \"branch\", or \"generator\".")
end

function branch_loading_percent(row)
    return !ismissing(row.max_flow) && row.rate_a > 0 ? 100.0 * row.max_flow / row.rate_a : missing
end

function binding_constraints_dataframe(
    network_data,
    result;
    label = "result",
    case_name = "",
    scenario = "",
    load_scale = 1.0,
    outage_type = "none",
    outage_id = missing,
    voltage_tolerance = 1e-4,
    generator_tolerance = 1e-4,
    branch_loading_threshold = 99.0,
)
    rows = Vector{NamedTuple}()

    for row in eachrow(get_bus_voltage_table(network_data, result))
        if !ismissing(row.vm) && abs(row.vm - row.vmin) <= voltage_tolerance
            push!(rows, (
                case_name = case_name,
                scenario = scenario,
                load_scale = load_scale,
                outage_type = outage_type,
                outage_id = outage_id,
                label = label,
                element = "bus",
                id = row.bus_id,
                quantity = "vm",
                value = row.vm,
                limit = row.vmin,
                binding_type = "vmin",
                loading_percent = missing,
                status = "AT LOWER LIMIT",
            ))
        end

        if !ismissing(row.vm) && abs(row.vm - row.vmax) <= voltage_tolerance
            push!(rows, (
                case_name = case_name,
                scenario = scenario,
                load_scale = load_scale,
                outage_type = outage_type,
                outage_id = outage_id,
                label = label,
                element = "bus",
                id = row.bus_id,
                quantity = "vm",
                value = row.vm,
                limit = row.vmax,
                binding_type = "vmax",
                loading_percent = missing,
                status = "AT UPPER LIMIT",
            ))
        end
    end

    for row in eachrow(get_generator_result_table(network_data, result; label = label))
        if !ismissing(row.pg) && abs(row.pg - row.pmin) <= generator_tolerance
            push!(rows, (
                case_name = case_name,
                scenario = scenario,
                load_scale = load_scale,
                outage_type = outage_type,
                outage_id = outage_id,
                label = label,
                element = "gen",
                id = row.gen_id,
                quantity = "pg",
                value = row.pg,
                limit = row.pmin,
                binding_type = "pmin",
                loading_percent = missing,
                status = "PG AT MIN",
            ))
        end

        if !ismissing(row.pg) && abs(row.pg - row.pmax) <= generator_tolerance
            push!(rows, (
                case_name = case_name,
                scenario = scenario,
                load_scale = load_scale,
                outage_type = outage_type,
                outage_id = outage_id,
                label = label,
                element = "gen",
                id = row.gen_id,
                quantity = "pg",
                value = row.pg,
                limit = row.pmax,
                binding_type = "pmax",
                loading_percent = missing,
                status = "PG AT MAX",
            ))
        end

        if !ismissing(row.qg) && abs(row.qg - row.qmin) <= generator_tolerance
            push!(rows, (
                case_name = case_name,
                scenario = scenario,
                load_scale = load_scale,
                outage_type = outage_type,
                outage_id = outage_id,
                label = label,
                element = "gen",
                id = row.gen_id,
                quantity = "qg",
                value = row.qg,
                limit = row.qmin,
                binding_type = "qmin",
                loading_percent = missing,
                status = "QG AT MIN",
            ))
        end

        if !ismissing(row.qg) && abs(row.qg - row.qmax) <= generator_tolerance
            push!(rows, (
                case_name = case_name,
                scenario = scenario,
                load_scale = load_scale,
                outage_type = outage_type,
                outage_id = outage_id,
                label = label,
                element = "gen",
                id = row.gen_id,
                quantity = "qg",
                value = row.qg,
                limit = row.qmax,
                binding_type = "qmax",
                loading_percent = missing,
                status = "QG AT MAX",
            ))
        end
    end

    for row in eachrow(get_branch_flow_table(network_data, result; label = label))
        loading = branch_loading_percent(row)

        if !ismissing(loading) && loading >= branch_loading_threshold
            push!(rows, (
                case_name = case_name,
                scenario = scenario,
                load_scale = load_scale,
                outage_type = outage_type,
                outage_id = outage_id,
                label = label,
                element = "branch",
                id = row.branch_id,
                quantity = "flow",
                value = row.max_flow,
                limit = row.rate_a,
                binding_type = "rate_a",
                loading_percent = loading,
                status = loading >= 100.0 ? "AT/ABOVE THERMAL LIMIT" : "NEAR THERMAL LIMIT",
            ))
        end
    end

    return DataFrame(rows)
end

function result_comparison_row(
    network_data,
    result;
    case_name,
    formulation,
    scenario = "baseline",
    load_scale = 1.0,
    outage_type = "none",
    outage_id = missing,
    notes = "",
)
    bus_table = get_bus_voltage_table(network_data, result)
    branch_table = get_branch_flow_table(network_data, result; label = formulation)

    voltage_rows = bus_table[.!ismissing.(bus_table.vm), :]
    max_voltage_row = nrow(voltage_rows) == 0 ? missing : row_with_extreme(voltage_rows, :vm, maximum)
    min_voltage_row = nrow(voltage_rows) == 0 ? missing : row_with_extreme(voltage_rows, :vm, minimum)

    branch_table.loading_percent = [
        !ismissing(row.max_flow) && row.rate_a > 0 ? 100.0 * row.max_flow / row.rate_a : missing
        for row in eachrow(branch_table)
    ]

    loaded_branch_rows = branch_table[.!ismissing.(branch_table.loading_percent), :]
    max_loading_row = nrow(loaded_branch_rows) == 0 ? missing :
        row_with_extreme(loaded_branch_rows, :loading_percent, maximum)

    binding_count = nrow(binding_constraints_dataframe(network_data, result; label = formulation))
    issue_table = constraint_violations_dataframe(network_data, result; label = formulation)
    violation_count = count(row -> occursin("VIOLATION", row.status) || occursin("ABOVE", row.status) || occursin("BELOW", row.status), eachrow(issue_table))

    return (
        Case = case_name,
        Scenario = scenario,
        Formulation = formulation,
        Status = string(safe_get(result, "termination_status"; default = "MISSING")),
        Successful = is_successful_result(result),
        Objective = economic_objective(formulation, result),
        Solver_Objective = safe_get(result, "objective"; default = missing),
        Solve_Time = safe_get(result, "solve_time"; default = missing),
        Load_Scale = load_scale,
        Outage_Type = outage_type,
        Outage_ID = outage_id,
        Bus_Count = length(network_data["bus"]),
        Branch_Count = length(network_data["branch"]),
        Active_Branch_Count = active_element_count(network_data, "branch", "br_status"),
        Generator_Count = length(network_data["gen"]),
        Active_Generator_Count = active_element_count(network_data, "gen", "gen_status"),
        Total_PD = total_active_load(network_data),
        Total_QD = total_reactive_load(network_data),
        Maximum_Voltage = ismissing(max_voltage_row) ? missing : max_voltage_row.vm,
        Maximum_Voltage_Bus = ismissing(max_voltage_row) ? missing : max_voltage_row.bus_id,
        Minimum_Voltage = ismissing(min_voltage_row) ? missing : min_voltage_row.vm,
        Minimum_Voltage_Bus = ismissing(min_voltage_row) ? missing : min_voltage_row.bus_id,
        Maximum_Branch_Loading_Percent = ismissing(max_loading_row) ? missing : max_loading_row.loading_percent,
        Maximum_Branch_Loading_ID = ismissing(max_loading_row) ? missing : max_loading_row.branch_id,
        Maximum_Branch_Flow = ismissing(max_loading_row) ? missing : max_loading_row.max_flow,
        Maximum_Branch_Rate_A = ismissing(max_loading_row) ? missing : max_loading_row.rate_a,
        Binding_Constraint_Count = binding_count,
        Limit_Issue_Count = nrow(issue_table),
        Violation_Count = violation_count,
        Notes = notes,
    )
end

function result_comparison_table(rows...)
    return DataFrame(collect(rows))
end

function result_comparison_table(rows::AbstractVector)
    return DataFrame(rows)
end

function save_comparison_table(table, output_path)
    output_dir = dirname(output_path)
    if !isempty(output_dir)
        mkpath(output_dir)
    end

    CSV.write(output_path, table)
    return output_path
end

function result_project_root()
    return normpath(joinpath(@__DIR__, ".."))
end

function result_output_dir(parts...)
    return joinpath(result_project_root(), "results", parts...)
end

function summary_output_path(case_label)
    return result_output_dir("summaries", case_label * "_pf_opf_summary.csv")
end

function raw_result_output_path(case_label, file_stem)
    return result_output_dir("raw_outputs", case_label, file_stem * ".json")
end

function figure_output_dir(category, parts...)
    return result_output_dir("figures", category, parts...)
end

function json_ready(value)
    if value isa AbstractDict
        return Dict(string(key) => json_ready(child) for (key, child) in value)
    elseif value isa AbstractVector
        return [json_ready(child) for child in value]
    elseif value isa Tuple
        return [json_ready(child) for child in value]
    elseif value isa Missing
        return nothing
    elseif value isa Symbol
        return string(value)
    elseif value isa Number || value isa AbstractString || value isa Bool || value === nothing
        return value
    end

    return string(value)
end

function save_summary_table(table, case_label)
    output_path = summary_output_path(case_label)
    mkpath(dirname(output_path))
    CSV.write(output_path, table)
    return output_path
end

function save_raw_result_json(result, case_label, file_stem)
    output_path = raw_result_output_path(case_label, file_stem)
    mkpath(dirname(output_path))

    open(output_path, "w") do io
        JSON.print(io, json_ready(result), 4)
    end

    return output_path
end

function comparison_row_key(row, key_columns)
    return Tuple(ismissing(row[column]) ? "missing" : string(row[column]) for column in key_columns)
end

function append_new_comparison_rows(
    output_path,
    new_rows;
    key_columns = [:Case, :Scenario, :Formulation, :Load_Scale, :Outage_Type, :Outage_ID],
)
    new_table = new_rows isa DataFrame ? new_rows : result_comparison_table(new_rows)

    output_dir = dirname(output_path)
    if !isempty(output_dir)
        mkpath(output_dir)
    end

    if !isfile(output_path)
        CSV.write(output_path, new_table)
        return new_table
    end

    stored_table = CSV.read(output_path, DataFrame)
    stored_keys = Set(comparison_row_key(row, key_columns) for row in eachrow(stored_table))

    rows_to_add = [
        NamedTuple(row)
        for row in eachrow(new_table)
        if !(comparison_row_key(row, key_columns) in stored_keys)
    ]

    if isempty(rows_to_add)
        return stored_table
    end

    # append! mutates stored_table and supports the cols keyword for unioning columns
    append!(stored_table, DataFrame(rows_to_add); cols = :union)
    CSV.write(output_path, stored_table)
    return stored_table
end

function failed_result(error_value)
    return Dict{String, Any}(
        "termination_status" => "ERROR",
        "solve_time" => missing,
        "objective" => missing,
        "solution" => Dict{String, Any}(),
        "error" => sprint(showerror, error_value),
    )
end

function run_ac_opf_stress_case(
    network_data;
    case_name,
    scenario = "baseline",
    load_scale = 1.0,
    outage_type = "none",
    outage_id = missing,
    scale_reactive = true,
    optimizer = Ipopt.Optimizer,
    branch_loading_threshold = 99.0,
    notes = "",
)
    stressed_network_data = make_stress_case(
        network_data;
        load_scale = load_scale,
        outage_type = outage_type,
        outage_id = outage_id,
        scale_reactive = scale_reactive,
    )

    result = try
        solve_ac_opf(stressed_network_data, optimizer)
    catch error_value
        failed_result(error_value)
    end

    comparison_row = result_comparison_row(
        stressed_network_data,
        result;
        case_name = case_name,
        formulation = "AC OPF",
        scenario = scenario,
        load_scale = load_scale,
        outage_type = outage_type,
        outage_id = outage_id,
        notes = notes,
    )

    bindings = binding_constraints_dataframe(
        stressed_network_data,
        result;
        label = "AC OPF",
        case_name = case_name,
        scenario = scenario,
        load_scale = load_scale,
        outage_type = outage_type,
        outage_id = outage_id,
        branch_loading_threshold = branch_loading_threshold,
    )

    issues = constraint_violations_dataframe(stressed_network_data, result; label = "AC OPF")

    return (
        network_data = stressed_network_data,
        result = result,
        comparison_row = comparison_row,
        bindings = bindings,
        issues = issues,
    )
end

function stress_results_to_tables(stress_results)
    comparison_rows = [stress_result.comparison_row for stress_result in stress_results]
    binding_tables = [stress_result.bindings for stress_result in stress_results if nrow(stress_result.bindings) > 0]
    issue_tables = [stress_result.issues for stress_result in stress_results if nrow(stress_result.issues) > 0]

    return (
        comparison = result_comparison_table(comparison_rows),
        bindings = isempty(binding_tables) ? DataFrame() : vcat(binding_tables...; cols = :union),
        issues = isempty(issue_tables) ? DataFrame() : vcat(issue_tables...; cols = :union),
    )
end

function run_load_scaling_ac_opf(
    network_data,
    load_scales;
    case_name,
    scenario = "load scaling",
    scale_reactive = true,
    optimizer = Ipopt.Optimizer,
    branch_loading_threshold = 99.0,
)
    stress_results = [
        run_ac_opf_stress_case(
            network_data;
            case_name = case_name,
            scenario = scenario,
            load_scale = load_scale,
            outage_type = "none",
            outage_id = missing,
            scale_reactive = scale_reactive,
            optimizer = optimizer,
            branch_loading_threshold = branch_loading_threshold,
            notes = "Load scaled by $(load_scale)",
        )
        for load_scale in load_scales
    ]

    tables = stress_results_to_tables(stress_results)
    return (
        results = stress_results,
        comparison = tables.comparison,
        bindings = tables.bindings,
        issues = tables.issues,
    )
end

function run_n1_branch_outages_ac_opf(
    network_data;
    case_name,
    branch_ids = active_branch_ids(network_data),
    load_scale = 1.0,
    scenario = "N-1 branch outage",
    scale_reactive = true,
    optimizer = Ipopt.Optimizer,
    branch_loading_threshold = 99.0,
)
    stress_results = [
        run_ac_opf_stress_case(
            network_data;
            case_name = case_name,
            scenario = scenario,
            load_scale = load_scale,
            outage_type = "branch",
            outage_id = branch_id,
            scale_reactive = scale_reactive,
            optimizer = optimizer,
            branch_loading_threshold = branch_loading_threshold,
            notes = "Branch $(branch_id) outaged",
        )
        for branch_id in branch_ids
    ]

    tables = stress_results_to_tables(stress_results)
    return (
        results = stress_results,
        comparison = tables.comparison,
        bindings = tables.bindings,
        issues = tables.issues,
    )
end

function run_n1_generator_outages_ac_opf(
    network_data;
    case_name,
    gen_ids = active_generator_ids(network_data),
    load_scale = 1.0,
    scenario = "N-1 generator outage",
    scale_reactive = true,
    optimizer = Ipopt.Optimizer,
    branch_loading_threshold = 99.0,
)
    stress_results = [
        run_ac_opf_stress_case(
            network_data;
            case_name = case_name,
            scenario = scenario,
            load_scale = load_scale,
            outage_type = "generator",
            outage_id = gen_id,
            scale_reactive = scale_reactive,
            optimizer = optimizer,
            branch_loading_threshold = branch_loading_threshold,
            notes = "Generator $(gen_id) outaged",
        )
        for gen_id in gen_ids
    ]

    tables = stress_results_to_tables(stress_results)
    return (
        results = stress_results,
        comparison = tables.comparison,
        bindings = tables.bindings,
        issues = tables.issues,
    )
end

function binding_constraint_key(row)
    return (
        string(row.element),
        string(row.id),
        string(row.quantity),
        string(row.binding_type),
    )
end

function new_binding_constraints(reference_bindings, candidate_bindings)
    if nrow(candidate_bindings) == 0
        return candidate_bindings
    end

    reference_keys = Set(binding_constraint_key(row) for row in eachrow(reference_bindings))
    new_rows = [
        NamedTuple(row)
        for row in eachrow(candidate_bindings)
        if !(binding_constraint_key(row) in reference_keys)
    ]

    return isempty(new_rows) ? DataFrame() : DataFrame(new_rows)
end

function binding_constraints_by_contingency(reference_bindings, candidate_bindings)
    new_bindings = new_binding_constraints(reference_bindings, candidate_bindings)

    if nrow(new_bindings) == 0
        return DataFrame()
    end

    grouped = groupby(new_bindings, [:case_name, :scenario, :load_scale, :outage_type, :outage_id])
    return combine(
        grouped,
        nrow => :new_binding_count,
        :status => (values -> join(unique(values), "; ")) => :binding_statuses,
    )
end

function bus_results_dataframe(network_data, result)
    return get_bus_voltage_table(network_data, result)
end

function gen_results_dataframe(network_data, result; label = "result")
    return get_generator_result_table(network_data, result; label = label)
end

function branch_results_dataframe(network_data, result; label = "result")
    return get_branch_flow_table(network_data, result; label = label)
end

function save_results_to_csv(network_data, result, output_dir; label = "result")
    mkpath(output_dir)

    bus_path = joinpath(output_dir, label * "_bus_results.csv")
    gen_path = joinpath(output_dir, label * "_gen_results.csv")
    branch_path = joinpath(output_dir, label * "_branch_results.csv")

    CSV.write(bus_path, bus_results_dataframe(network_data, result))
    CSV.write(gen_path, gen_results_dataframe(network_data, result; label = label))
    CSV.write(branch_path, branch_results_dataframe(network_data, result; label = label))

    return (bus = bus_path, gen = gen_path, branch = branch_path)
end

function inspect_voltage_limits(network_data, result)
    println("\n==============================")
    println("VOLTAGE LIMIT CHECK")
    println("==============================")

    for row in eachrow(get_bus_voltage_table(network_data, result))
        if ismissing(row.vm)
            continue
        end

        println(
            "Bus ", row.bus_id,
            " | vm = ", printable_value(row.vm),
            " | limits = [", row.vmin, ", ", row.vmax, "]",
            " | ", row.status
        )
    end

    return nothing
end

function inspect_reactive_power_limits(network_data, result_ac_pf, result_ac_opf)
    println("\n==============================")
    println("REACTIVE POWER CHECK")
    println("==============================")

    for gen_id in sorted_pm_ids(network_data["gen"])
        gen_data = network_data["gen"][gen_id]
        qg_pf = safe_get(result_ac_pf, "solution", "gen", gen_id, "qg"; default = missing)
        qg_opf = safe_get(result_ac_opf, "solution", "gen", gen_id, "qg"; default = missing)

        println(
            "Gen ", gen_id,
            " | qmin = ", gen_data["qmin"],
            " | qmax = ", gen_data["qmax"],
            " | AC PF qg = ", printable_value(qg_pf),
            " | ", limit_status(qg_pf, gen_data["qmin"], gen_data["qmax"], "PF qg"),
            " | AC OPF qg = ", printable_value(qg_opf),
            " | ", limit_status(qg_opf, gen_data["qmin"], gen_data["qmax"], "OPF qg")
        )
    end

    return nothing
end

function compare_generator_dispatch(network_data, result_ac_pf, result_dc_pf, result_ac_opf, result_dc_opf)
    println("\n==============================")
    println("GENERATOR ACTIVE POWER DISPATCH")
    println("==============================")

    for row in eachrow(get_generator_dispatch_table(network_data, result_ac_pf, result_dc_pf, result_ac_opf, result_dc_opf))
        println(
            "Gen ", row.gen_id,
            " | original pg = ", printable_value(row.pg_original),
            " | AC PF pg = ", printable_value(row.pg_ac_pf),
            " | DC PF pg = ", printable_value(row.pg_dc_pf),
            " | AC OPF pg = ", printable_value(row.pg_ac_opf),
            " | DC OPF pg = ", printable_value(row.pg_dc_opf)
        )
    end

    return nothing
end

function inspect_branch_flows(network_data, result; label = "RESULT")
    println("\n==============================")
    println(uppercase(label), " BRANCH FLOW CHECK")
    println("==============================")

    for row in eachrow(get_branch_flow_table(network_data, result; label = label))
        println(
            "Branch ", row.branch_id,
            " | ", row.f_bus, " -> ", row.t_bus,
            " | rate_a = ", row.rate_a,
            " | pf = ", printable_value(row.pf),
            " | pt = ", printable_value(row.pt),
            " | qf = ", printable_value(row.qf),
            " | qt = ", printable_value(row.qt),
            " | max flow = ", printable_value(row.max_flow),
            " | ", row.status
        )
    end

    return nothing
end

function inspect_ac_opf_branch_flows(network_data, result_ac_opf)
    return inspect_branch_flows(network_data, result_ac_opf; label = "AC OPF")
end

function compare_opf_objectives(result_ac_opf, result_dc_opf)
    println("\n==============================")
    println("OBJECTIVE COMPARISON")
    println("==============================")

    ac_objective = safe_get(result_ac_opf, "objective"; default = missing)
    dc_objective = safe_get(result_dc_opf, "objective"; default = missing)

    println("AC OPF objective = ", printable_value(ac_objective))
    println("DC OPF objective = ", printable_value(dc_objective))

    if !ismissing(ac_objective) && !ismissing(dc_objective)
        println("Difference AC - DC = ", ac_objective - dc_objective)
    end

    return nothing
end

function constraint_violations_dataframe(network_data, result; label = "result", tolerance = 1e-4)
    rows = Vector{NamedTuple}()

    for row in eachrow(get_bus_voltage_table(network_data, result))
        if row.status != "OK" && row.status != "NO VM"
            push!(rows, (
                label = label,
                element = "bus",
                id = row.bus_id,
                quantity = "vm",
                value = row.vm,
                lower = row.vmin,
                upper = row.vmax,
                status = row.status,
            ))
        end
    end

    for row in eachrow(get_generator_result_table(network_data, result; label = label))
        if row.pg_status != "OK" && !startswith(row.pg_status, "NO ")
            push!(rows, (
                label = label,
                element = "gen",
                id = row.gen_id,
                quantity = "pg",
                value = row.pg,
                lower = row.pmin,
                upper = row.pmax,
                status = row.pg_status,
            ))
        end

        if row.qg_status != "OK" && !startswith(row.qg_status, "NO ")
            push!(rows, (
                label = label,
                element = "gen",
                id = row.gen_id,
                quantity = "qg",
                value = row.qg,
                lower = row.qmin,
                upper = row.qmax,
                status = row.qg_status,
            ))
        end
    end

    for row in eachrow(get_branch_flow_table(network_data, result; label = label))
        if row.status == "THERMAL LIMIT VIOLATION" || row.status == "AT THERMAL LIMIT"
            push!(rows, (
                label = label,
                element = "branch",
                id = row.branch_id,
                quantity = "flow",
                value = row.max_flow,
                lower = 0.0,
                upper = row.rate_a,
                status = row.status,
            ))
        end
    end

    return DataFrame(rows)
end

function print_constraint_violations(network_data, result; label = "result")
    violations = constraint_violations_dataframe(network_data, result; label = label)

    println("\n==============================")
    println(uppercase(label), " CONSTRAINT VIOLATIONS")
    println("==============================")

    if nrow(violations) == 0
        println("No voltage, generator, or branch-flow limit issues found.")
        return violations
    end

    for row in eachrow(violations)
        println(
            row.element, " ", row.id,
            " | ", row.quantity,
            " = ", printable_value(row.value),
            " | limits = [", printable_value(row.lower), ", ", printable_value(row.upper), "]",
            " | ", row.status
        )
    end

    return violations
end

function compare_ac_dc_opf(network_data, result_ac_opf, result_dc_opf)
    println("\n==============================")
    println("AC VS DC OPF COMPARISON")
    println("==============================")

    print_solver_summary("AC OPF", result_ac_opf)
    print_solver_summary("DC OPF", result_dc_opf)
    compare_opf_objectives(result_ac_opf, result_dc_opf)

    println("\nGenerator dispatch differences:")
    for gen_id in sorted_pm_ids(network_data["gen"])
        pg_ac = safe_get(result_ac_opf, "solution", "gen", gen_id, "pg"; default = missing)
        pg_dc = safe_get(result_dc_opf, "solution", "gen", gen_id, "pg"; default = missing)
        diff = (!ismissing(pg_ac) && !ismissing(pg_dc)) ? pg_ac - pg_dc : missing

        println(
            "Gen ", gen_id,
            " | AC pg = ", printable_value(pg_ac),
            " | DC pg = ", printable_value(pg_dc),
            " | AC - DC = ", printable_value(diff)
        )
    end

    println("\nBranch active-flow differences:")
    for branch_id in sorted_pm_ids(network_data["branch"])
        pf_ac = safe_get(result_ac_opf, "solution", "branch", branch_id, "pf"; default = missing)
        pf_dc = safe_get(result_dc_opf, "solution", "branch", branch_id, "pf"; default = missing)
        diff = (!ismissing(pf_ac) && !ismissing(pf_dc)) ? pf_ac - pf_dc : missing

        println(
            "Branch ", branch_id,
            " | AC pf = ", printable_value(pf_ac),
            " | DC pf = ", printable_value(pf_dc),
            " | AC - DC = ", printable_value(diff)
        )
    end

    println("\nModel detail note:")
    println("AC OPF includes voltage magnitudes/angles and reactive power when present.")
    println("DC OPF is an active-power approximation, so vm, qg, qf, and qt are usually absent.")

    return (
        bus_ac = get_bus_voltage_table(network_data, result_ac_opf),
        gen_ac = get_generator_result_table(network_data, result_ac_opf; label = "AC OPF"),
        gen_dc = get_generator_result_table(network_data, result_dc_opf; label = "DC OPF"),
        branch_ac = get_branch_flow_table(network_data, result_ac_opf; label = "AC OPF"),
        branch_dc = get_branch_flow_table(network_data, result_dc_opf; label = "DC OPF"),
    )
end
