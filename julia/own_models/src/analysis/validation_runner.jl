# ============================================================
# Validation study runner
# Runs controlled validation cases and returns DataFrame-ready rows.
# ============================================================

struct ValidationOPFStudyRow
    scenario::String
    case_name::String
    dc_status::String
    ac_status::String
    dc_objective::Float64
    ac_objective::Float64
    dc_solved::Bool
    ac_solved::Bool
    dc_error::String
    ac_error::String
    dc_n_pg_at_lower::Int
    dc_n_pg_at_upper::Int
    dc_n_branches_binding::Int
    ac_n_pg_at_lower::Int
    ac_n_pg_at_upper::Int
    ac_n_qg_at_lower::Int
    ac_n_qg_at_upper::Int
    ac_n_vm_at_lower::Int
    ac_n_vm_at_upper::Int
    ac_n_branches_binding::Int
end

function _opf_status_string(result)
    return string(result.termination_status)
end

function _opf_solved(status::String)
    return status in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")
end

function _count_dc_generator_bindings(case::PowerCase, dc_result; tol::Float64=1e-6)
    n_lower = 0
    n_upper = 0

    for gen in case.generators
        if gen.status == 0 || !haskey(dc_result.pg, gen.id)
            continue
        end

        pg = dc_result.pg[gen.id]

        if abs(pg - gen.pmin) <= tol
            n_lower += 1
        end

        if abs(pg - gen.pmax) <= tol
            n_upper += 1
        end
    end

    return n_lower, n_upper
end

function _count_dc_branch_bindings(case::PowerCase, dc_result; tol::Float64=1e-6)
    n_binding = 0

    for branch in case.branches
        if branch.status == 0 || branch.rate_a <= 0.0 || !haskey(dc_result.branch_flows, branch.id)
            continue
        end

        if abs(abs(dc_result.branch_flows[branch.id]) - branch.rate_a) <= tol
            n_binding += 1
        end
    end

    return n_binding
end

function _count_ac_generator_bindings(case::PowerCase, ac_result; tol::Float64=1e-6)
    n_pg_lower = 0
    n_pg_upper = 0
    n_qg_lower = 0
    n_qg_upper = 0

    for gen in case.generators
        if gen.status == 0 || !haskey(ac_result.pg, gen.id) || !haskey(ac_result.qg, gen.id)
            continue
        end

        pg = ac_result.pg[gen.id]
        qg = ac_result.qg[gen.id]

        if abs(pg - gen.pmin) <= tol
            n_pg_lower += 1
        end

        if abs(pg - gen.pmax) <= tol
            n_pg_upper += 1
        end

        if abs(qg - gen.qmin) <= tol
            n_qg_lower += 1
        end

        if abs(qg - gen.qmax) <= tol
            n_qg_upper += 1
        end
    end

    return n_pg_lower, n_pg_upper, n_qg_lower, n_qg_upper
end

function _count_ac_voltage_bindings(case::PowerCase, ac_result; tol::Float64=1e-6)
    n_lower = 0
    n_upper = 0

    for bus in case.buses
        if !haskey(ac_result.vm, bus.id)
            continue
        end

        vm = ac_result.vm[bus.id]

        if abs(vm - bus.vmin) <= tol
            n_lower += 1
        end

        if abs(vm - bus.vmax) <= tol
            n_upper += 1
        end
    end

    return n_lower, n_upper
end

function _count_ac_branch_bindings(case::PowerCase, ac_result; tol::Float64=1e-6)
    n_binding = 0

    for branch in case.branches
        if branch.status == 0 || branch.rate_a <= 0.0 || !haskey(ac_result.branch_flows, branch.id)
            continue
        end

        flow = ac_result.branch_flows[branch.id]
        sf = sqrt(flow["pf"]^2 + flow["qf"]^2)
        st = sqrt(flow["pt"]^2 + flow["qt"]^2)

        if abs(max(sf, st) - branch.rate_a) <= tol
            n_binding += 1
        end
    end

    return n_binding
end

function run_opf_validation_case(
    scenario::String,
    case::PowerCase;
    run_dc::Bool=true,
    run_ac::Bool=true,
    tol::Float64=1e-6
)
    dc_status = "NOT_RUN"
    ac_status = "NOT_RUN"
    dc_objective = NaN
    ac_objective = NaN
    dc_solved = false
    ac_solved = false
    dc_error = ""
    ac_error = ""
    dc_n_pg_at_lower = 0
    dc_n_pg_at_upper = 0
    dc_n_branches_binding = 0
    ac_n_pg_at_lower = 0
    ac_n_pg_at_upper = 0
    ac_n_qg_at_lower = 0
    ac_n_qg_at_upper = 0
    ac_n_vm_at_lower = 0
    ac_n_vm_at_upper = 0
    ac_n_branches_binding = 0

    if run_dc
        try
            dc_result = solve_dc_opf_own(case)
            dc_status = _opf_status_string(dc_result)
            dc_solved = _opf_solved(dc_status)
            dc_objective = dc_result.objective

            if dc_solved
                dc_n_pg_at_lower, dc_n_pg_at_upper =
                    _count_dc_generator_bindings(case, dc_result; tol=tol)
                dc_n_branches_binding =
                    _count_dc_branch_bindings(case, dc_result; tol=tol)
            end
        catch err
            dc_status = "ERROR"
            dc_error = sprint(showerror, err)
        end
    end

    if run_ac
        try
            ac_result = solve_ac_opf_own(case)
            ac_status = _opf_status_string(ac_result)
            ac_solved = _opf_solved(ac_status)
            ac_objective = ac_result.objective

            if ac_solved
                ac_n_pg_at_lower, ac_n_pg_at_upper, ac_n_qg_at_lower, ac_n_qg_at_upper =
                    _count_ac_generator_bindings(case, ac_result; tol=tol)
                ac_n_vm_at_lower, ac_n_vm_at_upper =
                    _count_ac_voltage_bindings(case, ac_result; tol=tol)
                ac_n_branches_binding =
                    _count_ac_branch_bindings(case, ac_result; tol=tol)
            end
        catch err
            ac_status = "ERROR"
            ac_error = sprint(showerror, err)
        end
    end

    return ValidationOPFStudyRow(
        scenario,
        case.name,
        dc_status,
        ac_status,
        dc_objective,
        ac_objective,
        dc_solved,
        ac_solved,
        dc_error,
        ac_error,
        dc_n_pg_at_lower,
        dc_n_pg_at_upper,
        dc_n_branches_binding,
        ac_n_pg_at_lower,
        ac_n_pg_at_upper,
        ac_n_qg_at_lower,
        ac_n_qg_at_upper,
        ac_n_vm_at_lower,
        ac_n_vm_at_upper,
        ac_n_branches_binding
    )
end

function validation_opf_study_row_as_named_tuple(row::ValidationOPFStudyRow)
    return (
        scenario = row.scenario,
        case_name = row.case_name,
        dc_status = row.dc_status,
        ac_status = row.ac_status,
        dc_objective = row.dc_objective,
        ac_objective = row.ac_objective,
        dc_solved = row.dc_solved,
        ac_solved = row.ac_solved,
        dc_error = row.dc_error,
        ac_error = row.ac_error,
        dc_n_pg_at_lower = row.dc_n_pg_at_lower,
        dc_n_pg_at_upper = row.dc_n_pg_at_upper,
        dc_n_branches_binding = row.dc_n_branches_binding,
        ac_n_pg_at_lower = row.ac_n_pg_at_lower,
        ac_n_pg_at_upper = row.ac_n_pg_at_upper,
        ac_n_qg_at_lower = row.ac_n_qg_at_lower,
        ac_n_qg_at_upper = row.ac_n_qg_at_upper,
        ac_n_vm_at_lower = row.ac_n_vm_at_lower,
        ac_n_vm_at_upper = row.ac_n_vm_at_upper,
        ac_n_branches_binding = row.ac_n_branches_binding
    )
end

function run_validation_opf_study(
    validation_cases;
    run_dc::Bool=true,
    run_ac::Bool=true,
    tol::Float64=1e-6
)
    rows = ValidationOPFStudyRow[]

    for (scenario, case) in pairs(validation_cases)
        push!(
            rows,
            run_opf_validation_case(
                string(scenario),
                case;
                run_dc=run_dc,
                run_ac=run_ac,
                tol=tol
            )
        )
    end

    return [
        validation_opf_study_row_as_named_tuple(row)
        for row in rows
    ]
end
