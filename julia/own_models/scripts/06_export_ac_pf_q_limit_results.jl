using JSON

repo_root = normpath(joinpath(@__DIR__, "..", "..", ".."))
include(joinpath(repo_root, "julia", "own_models", "src", "OwnPowerModels.jl"))
using .OwnPowerModels
using PowerModels

const DEFAULT_CASES = [
    "pglib_opf_case14_ieee.m",
    "pglib_opf_case30_ieee.m",
    "pglib_opf_case118_ieee.m",
]

const SCENARIOS = [
    "base",
    "wide_q_limits",
    "single_pv_qmax",
    "multi_pv_slack_qmax",
]

function sorted_keys(dict)
    return sort(collect(keys(dict)); by = key -> parse(Int, string(key)))
end

function case_stem(case_file::AbstractString)
    return replace(basename(case_file), ".m" => "")
end

function active_gen_buses(case)
    buses = Set{String}()
    for gen in case.generators
        gen.status == 1 && push!(buses, gen.bus)
    end
    return buses
end

function non_slack_pv_gen_buses(case)
    gen_buses = active_gen_buses(case)
    return [
        bus.id for bus in case.buses
        if bus.bus_type == 2 && bus.id != case.slack_bus_id && bus.id in gen_buses
    ]
end

function set_bus_q_limits!(data, bus_id::String, qmin_total::Float64, qmax_total::Float64)
    gen_ids = [
        string(gen_id) for gen_id in sorted_keys(data["gen"])
        if string(data["gen"][gen_id]["gen_bus"]) == bus_id && Int(data["gen"][gen_id]["gen_status"]) == 1
    ]
    isempty(gen_ids) && return
    qmin_each = qmin_total / length(gen_ids)
    qmax_each = qmax_total / length(gen_ids)
    for gen_id in gen_ids
        data["gen"][gen_id]["qmin"] = qmin_each
        data["gen"][gen_id]["qmax"] = qmax_each
    end
end

function widen_all_q_limits!(data; width = 10.0)
    for gen_id in sorted_keys(data["gen"])
        if Int(data["gen"][gen_id]["gen_status"]) == 1
            data["gen"][gen_id]["qmin"] = -width
            data["gen"][gen_id]["qmax"] = width
        end
    end
end

function scenario_data(base_data, scenario::String)
    data = deepcopy(base_data)
    base_case = OwnPowerModels.powermodels_to_powercase(base_data)
    fixed = OwnPowerModels.solve_ac_power_flow_newton(base_case)
    pv_buses = non_slack_pv_gen_buses(base_case)
    margin = 1e-3

    if scenario == "base"
        return data
    elseif scenario == "wide_q_limits"
        widen_all_q_limits!(data)
        return data
    elseif scenario == "single_pv_qmax"
        isempty(pv_buses) && return data
        widen_all_q_limits!(data)
        bus_id = pv_buses[1]
        q_required = get(fixed.qg_by_bus, bus_id, 0.0)
        qmax_total = q_required - margin
        set_bus_q_limits!(data, bus_id, qmax_total - 10.0, qmax_total)
        return data
    elseif scenario == "multi_pv_slack_qmax"
        widen_all_q_limits!(data)
        for bus_id in pv_buses
            q_required = get(fixed.qg_by_bus, bus_id, 0.0)
            qmax_total = q_required - margin
            set_bus_q_limits!(data, bus_id, qmax_total - 10.0, qmax_total)
        end
        q_slack = get(fixed.qg_by_bus, base_case.slack_bus_id, 0.0)
        qmax_slack = q_slack - margin
        set_bus_q_limits!(data, base_case.slack_bus_id, qmax_slack - 10.0, qmax_slack)
        return data
    else
        error("Unknown Q-limit scenario: $scenario")
    end
end

function vector_by_bus(case, values)
    return Dict(bus.id => values[case.bus_id_to_index[bus.id]] for bus in case.buses)
end

function switching_event_dict(event)
    return Dict(
        "outer_iteration" => event.outer_iteration,
        "bus_id" => event.bus_id,
        "from_type" => event.from_type,
        "to_type" => event.to_type,
        "required_q" => event.required_q,
        "qmin_bus" => event.qmin_bus,
        "qmax_bus" => event.qmax_bus,
        "fixed_q" => event.fixed_q,
        "violation_direction" => event.violation_direction,
        "violation_magnitude" => event.violation_magnitude,
    )
end

function result_dict(case_name::String, case_file::String, scenario::String, case, result)
    return Dict(
        "case_name" => case_name,
        "case_file" => case_file,
        "scenario" => scenario,
        "converged" => result.converged,
        "power_flow_converged" => result.power_flow_converged,
        "q_limit_converged" => result.q_limit_converged,
        "slack_q_violation" => result.slack_q_violation,
        "iterations" => result.iterations,
        "outer_iterations" => result.outer_iterations,
        "mismatch_norm" => result.mismatch_norm,
        "termination_reason" => result.termination_reason,
        "vm" => vector_by_bus(case, result.vm),
        "va" => vector_by_bus(case, result.va),
        "qg_by_bus" => result.qg_by_bus,
        "qg_by_generator" => result.qg_by_generator,
        "final_bus_types" => result.final_bus_types,
        "switched_buses" => collect(result.switched_buses),
        "switching_events" => [switching_event_dict(event) for event in result.switching_events],
        "branch_flows" => result.branch_flows,
    )
end

function write_json(path, data)
    open(path, "w") do io
        JSON.print(io, data, 2)
    end
end

function export_case(case_file::String, output_dir::String)
    case_path = isabspath(case_file) ? case_file : joinpath(repo_root, "data", "raw", "pglib-opf", case_file)
    isfile(case_path) || error("Case file not found: $case_path")
    base_data = PowerModels.parse_file(case_path)
    name = case_stem(case_path)

    for scenario in SCENARIOS
        modified_data = scenario_data(base_data, scenario)
        case = OwnPowerModels.powermodels_to_powercase(modified_data)
        result = OwnPowerModels.solve_ac_power_flow_with_q_limits(case)

        case_json = joinpath(output_dir, "$(name)_$(scenario)_case.json")
        result_json = joinpath(output_dir, "$(name)_$(scenario)_julia_result.json")
        write_json(case_json, modified_data)
        write_json(result_json, result_dict(name, case_path, scenario, case, result))
        println("wrote $(basename(result_json))")
    end
end

function main()
    output_dir = joinpath(repo_root, "results", "reference", "own_julia", "ac_pf_q_limits")
    mkpath(output_dir)
    cases = isempty(ARGS) ? DEFAULT_CASES : ARGS
    for case_file in cases
        export_case(case_file, output_dir)
    end
    println("Saved Julia AC PF Q-limit exports to: $output_dir")
end

main()
