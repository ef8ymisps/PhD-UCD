# ============================================================
# AC OPF validation suite
# OwnPowerModels vs PowerModels.jl under controlled stress cases.
# ============================================================

using JuMP
using Ipopt
using PowerModels

struct ACOPFValidationTolerances
    objective_rel_tol::Float64
    pg_tol::Float64
    qg_tol::Float64
    vm_tol::Float64
    va_tol::Float64
    pf_tol::Float64
    qf_tol::Float64
end

struct ACOPFValidationResult
    summary_rows::Vector{NamedTuple}
    generator_rows::Vector{NamedTuple}
    bus_rows::Vector{NamedTuple}
    branch_rows::Vector{NamedTuple}
    angle_rows::Vector{NamedTuple}
end

function default_ac_opf_validation_tolerances()
    return ACOPFValidationTolerances(
        1e-5,
        1e-5,
        1e-5,
        1e-5,
        1e-5,
        1e-5,
        1e-5
    )
end

function _ac_validation_solved_status(status::String)
    return status in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")
end

function _ac_validation_status(pm_or_own_result)
    return string(pm_or_own_result.termination_status)
end

function _ac_validation_status(pm_result::Dict)
    return string(pm_result["termination_status"])
end

function _ac_validation_rel_objective_diff(own_obj, pm_obj)
    return abs(own_obj - pm_obj) / (1.0 + abs(pm_obj))
end

function _ac_validation_add_case_name(case_name::String, row)
    return (; case_name = case_name, row...)
end

function _ac_validation_relaxed_data(
    data;
    branch_rate_multiplier::Float64=10.0,
    generator_limit_multiplier::Float64=10.0,
    voltage_lower::Float64=0.5,
    voltage_upper::Float64=1.5
)
    d = deepcopy(data)

    for branch in values(d["branch"])
        if branch["rate_a"] > 0.0
            branch["rate_a"] *= branch_rate_multiplier
        end
    end

    for gen in values(d["gen"])
        if gen["gen_status"] == 1
            gen["pmax"] *= generator_limit_multiplier
            gen["qmin"] *= generator_limit_multiplier
            gen["qmax"] *= generator_limit_multiplier
        end
    end

    for bus in values(d["bus"])
        bus["vmin"] = min(Float64(bus["vmin"]), voltage_lower)
        bus["vmax"] = max(Float64(bus["vmax"]), voltage_upper)
    end

    return d
end

function _ac_validation_load_scaled_data(data, load_factor::Float64)
    d = deepcopy(data)

    for load in values(d["load"])
        load["pd"] *= load_factor
        load["qd"] *= load_factor
    end

    return d
end

function _ac_validation_base_solution(data; ipopt_optimizer=Ipopt.Optimizer)
    case = powermodels_to_powercase(deepcopy(data))
    own = solve_ac_opf_own(
        case;
        optimizer=optimizer_with_attributes(ipopt_optimizer, "print_level" => 0),
        silent=true
    )
    return case, own
end

function _ac_validation_branch_congested_data(
    data;
    branch_rate_factor::Float64=0.95,
    ipopt_optimizer=Ipopt.Optimizer
)
    d = deepcopy(data)
    case, own = _ac_validation_base_solution(data; ipopt_optimizer=ipopt_optimizer)

    best_id = ""
    best_loading = -Inf
    best_flow = 0.0

    for branch in case.branches
        if branch.status == 0 || branch.rate_a <= 0.0 || !haskey(own.branch_flows, branch.id)
            continue
        end

        flow = own.branch_flows[branch.id]
        sf = sqrt(flow["pf"]^2 + flow["qf"]^2)
        st = sqrt(flow["pt"]^2 + flow["qt"]^2)
        loading_abs = max(sf, st)
        loading_pct = loading_abs / branch.rate_a

        if loading_abs > 1e-6 && loading_pct > best_loading
            best_id = branch.id
            best_loading = loading_pct
            best_flow = loading_abs
        end
    end

    if best_id != ""
        d["branch"][best_id]["rate_a"] = branch_rate_factor * best_flow
    end

    return d, best_id
end

function _ac_validation_generator_limited_data(
    data;
    generator_pmax_factor::Float64=0.5,
    ipopt_optimizer=Ipopt.Optimizer
)
    d = deepcopy(data)
    case, own = _ac_validation_base_solution(data; ipopt_optimizer=ipopt_optimizer)

    best_id = ""
    best_margin = -Inf
    best_gen = nothing

    for gen in case.generators
        if gen.status == 0 || !haskey(own.pg, gen.id)
            continue
        end

        margin = own.pg[gen.id] - gen.pmin

        if margin > best_margin && margin > 1e-6
            best_id = gen.id
            best_margin = margin
            best_gen = gen
        end
    end

    if best_id != ""
        d["gen"][best_id]["pmax"] = best_gen.pmin + generator_pmax_factor * (own.pg[best_id] - best_gen.pmin)
    end

    return d, best_id
end

function _ac_validation_pass(summary_row, tolerances::ACOPFValidationTolerances)
    if !summary_row.outcome_match
        return false
    end

    if !(summary_row.own_solved && summary_row.pm_solved)
        return true
    end

    return summary_row.objective_rel_diff <= tolerances.objective_rel_tol &&
        summary_row.max_pg_diff <= tolerances.pg_tol &&
        summary_row.max_qg_diff <= tolerances.qg_tol &&
        summary_row.max_vm_diff <= tolerances.vm_tol &&
        summary_row.max_va_diff <= tolerances.va_tol &&
        summary_row.max_pf_diff <= tolerances.pf_tol &&
        summary_row.max_qf_diff <= tolerances.qf_tol
end

function run_ac_opf_validation_case(
    case_name::String,
    scenario::String,
    data;
    tolerances::ACOPFValidationTolerances=default_ac_opf_validation_tolerances(),
    ipopt_optimizer=Ipopt.Optimizer
)
    case = powermodels_to_powercase(deepcopy(data))
    optimizer = optimizer_with_attributes(ipopt_optimizer, "print_level" => 0)

    own_result = nothing
    pm_result = nothing
    own_status = "ERROR"
    pm_status = "ERROR"
    own_error = ""
    pm_error = ""

    try
        own_result = solve_ac_opf_own(
            case;
            optimizer=optimizer,
            silent=true
        )
        own_status = string(own_result.termination_status)
    catch err
        own_error = sprint(showerror, err)
    end

    try
        pm_result = PowerModels.solve_ac_opf(deepcopy(data), optimizer)
        pm_status = string(pm_result["termination_status"])
    catch err
        pm_error = sprint(showerror, err)
    end

    own_solved = _ac_validation_solved_status(own_status)
    pm_solved = _ac_validation_solved_status(pm_status)
    status_match = own_status == pm_status
    outcome_match = own_solved == pm_solved

    generator_rows = NamedTuple[]
    bus_rows = NamedTuple[]
    branch_rows = NamedTuple[]
    angle_rows = NamedTuple[]

    if own_result !== nothing && pm_result !== nothing && own_solved && pm_solved
        append!(
            generator_rows,
            [
                _ac_validation_add_case_name(case_name, row)
                for row in compare_ac_opf_generator_rows(case, own_result, pm_result; scenario=scenario)
            ]
        )
        append!(
            bus_rows,
            [
                _ac_validation_add_case_name(case_name, row)
                for row in compare_ac_opf_bus_rows(case, own_result, pm_result; scenario=scenario)
            ]
        )
        append!(
            branch_rows,
            [
                _ac_validation_add_case_name(case_name, row)
                for row in compare_ac_opf_branch_rows(case, own_result, pm_result; scenario=scenario)
            ]
        )
        append!(
            angle_rows,
            [
                _ac_validation_add_case_name(case_name, row)
                for row in inspect_branch_angle_limits(case, own_result; scenario=scenario)
            ]
        )

        raw_summary = ac_opf_summary_row(case, own_result, pm_result; scenario=scenario)
        objective_abs_diff = abs(raw_summary.objective_diff)
        objective_rel_diff = _ac_validation_rel_objective_diff(raw_summary.own_objective, raw_summary.pm_objective)

        summary = (
            case_name = case_name,
            scenario = scenario,
            own_status = own_status,
            pm_status = pm_status,
            status_match = status_match,
            outcome_match = outcome_match,
            own_solved = own_solved,
            pm_solved = pm_solved,
            own_objective = raw_summary.own_objective,
            pm_objective = raw_summary.pm_objective,
            objective_abs_diff = objective_abs_diff,
            objective_rel_diff = objective_rel_diff,
            max_pg_diff = raw_summary.max_pg_diff,
            max_qg_diff = raw_summary.max_qg_diff,
            max_vm_diff = raw_summary.max_vm_diff,
            max_va_diff = raw_summary.max_va_diff,
            max_pf_diff = raw_summary.max_pf_diff,
            max_qf_diff = raw_summary.max_qf_diff,
            own_error = own_error,
            pm_error = pm_error,
            pass = false
        )
    else
        summary = (
            case_name = case_name,
            scenario = scenario,
            own_status = own_status,
            pm_status = pm_status,
            status_match = status_match,
            outcome_match = outcome_match,
            own_solved = own_solved,
            pm_solved = pm_solved,
            own_objective = NaN,
            pm_objective = NaN,
            objective_abs_diff = NaN,
            objective_rel_diff = NaN,
            max_pg_diff = NaN,
            max_qg_diff = NaN,
            max_vm_diff = NaN,
            max_va_diff = NaN,
            max_pf_diff = NaN,
            max_qf_diff = NaN,
            own_error = own_error,
            pm_error = pm_error,
            pass = false
        )
    end

    summary = merge(summary, (pass = _ac_validation_pass(summary, tolerances),))

    return (
        summary_row = summary,
        generator_rows = generator_rows,
        bus_rows = bus_rows,
        branch_rows = branch_rows,
        angle_rows = angle_rows
    )
end

function _ac_validation_scenario_data(
    data;
    load_factors,
    branch_rate_factor::Float64,
    generator_pmax_factor::Float64,
    ipopt_optimizer=Ipopt.Optimizer
)
    scenarios = Vector{Tuple{String, Any}}()

    push!(scenarios, ("base", deepcopy(data)))
    push!(scenarios, ("relaxed", _ac_validation_relaxed_data(data)))

    congested_data, branch_id = _ac_validation_branch_congested_data(
        data;
        branch_rate_factor=branch_rate_factor,
        ipopt_optimizer=ipopt_optimizer
    )
    push!(scenarios, ("branch_congested_" * branch_id, congested_data))

    gen_limited_data, gen_id = _ac_validation_generator_limited_data(
        data;
        generator_pmax_factor=generator_pmax_factor,
        ipopt_optimizer=ipopt_optimizer
    )
    push!(scenarios, ("generator_limited_" * gen_id, gen_limited_data))

    for factor in load_factors
        push!(scenarios, ("load_x" * string(factor), _ac_validation_load_scaled_data(data, Float64(factor))))
    end

    return scenarios
end

function run_ac_opf_validation_suite(
    case_names;
    case_dir::String,
    load_factors=1.0:0.05:2.0,
    branch_rate_factor::Float64=0.95,
    generator_pmax_factor::Float64=0.5,
    tolerances::ACOPFValidationTolerances=default_ac_opf_validation_tolerances(),
    ipopt_optimizer=Ipopt.Optimizer,
    verbose::Bool=true
)
    summary_rows = NamedTuple[]
    generator_rows = NamedTuple[]
    bus_rows = NamedTuple[]
    branch_rows = NamedTuple[]
    angle_rows = NamedTuple[]

    for case_name in case_names
        if verbose
            println("\n==============================")
            println("AC OPF VALIDATION: ", case_name)
            println("==============================")
        end

        case_path = joinpath(case_dir, case_name)
        data = PowerModels.parse_file(case_path)

        scenarios = _ac_validation_scenario_data(
            data;
            load_factors=load_factors,
            branch_rate_factor=branch_rate_factor,
            generator_pmax_factor=generator_pmax_factor,
            ipopt_optimizer=ipopt_optimizer
        )

        for (scenario, scenario_data) in scenarios
            result = run_ac_opf_validation_case(
                case_name,
                scenario,
                scenario_data;
                tolerances=tolerances,
                ipopt_optimizer=ipopt_optimizer
            )

            push!(summary_rows, result.summary_row)
            append!(generator_rows, result.generator_rows)
            append!(bus_rows, result.bus_rows)
            append!(branch_rows, result.branch_rows)
            append!(angle_rows, result.angle_rows)

            if verbose
                println(
                    scenario,
                    " | own = ", result.summary_row.own_status,
                    " | pm = ", result.summary_row.pm_status,
                    " | pass = ", result.summary_row.pass
                )
            end
        end
    end

    return ACOPFValidationResult(
        summary_rows,
        generator_rows,
        bus_rows,
        branch_rows,
        angle_rows
    )
end
