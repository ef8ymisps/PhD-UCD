# ============================================================
# Compact metrics for DC OPF -> AC PF feasibility studies
# ============================================================

struct DCToACStudyRow
    case_name::String
    dc_opf_status::String
    ac_opf_status::String
    dc_objective::Float64
    ac_objective::Float64
    objective_gap::Float64
    ac_pf_converged_after_dc::Bool
    dc_dispatch_ac_feasible::Bool
    n_voltage_violations::Int
    n_reactive_violations::Int
    n_branch_violations::Int
    max_voltage_violation::Float64
    max_reactive_violation::Float64
    max_branch_violation::Float64
end

function _max_violation_magnitude(violations)
    return isempty(violations) ? 0.0 : maximum(v.magnitude for v in violations)
end

function build_dc_to_ac_study_row(
    case_name::String,
    dc_opf_result,
    ac_opf_result,
    dc_to_ac_result
)
    dc_status = string(dc_opf_result.termination_status)
    ac_status = string(ac_opf_result.termination_status)

    dc_obj = dc_opf_result.objective
    ac_obj = ac_opf_result.objective
    obj_gap = ac_obj - dc_obj

    return DCToACStudyRow(
        case_name,
        dc_status,
        ac_status,
        dc_obj,
        ac_obj,
        obj_gap,
        dc_to_ac_result.ac_pf_result.converged,
        dc_to_ac_result.feasible,
        length(dc_to_ac_result.voltage_violations),
        length(dc_to_ac_result.reactive_violations),
        length(dc_to_ac_result.branch_violations),
        _max_violation_magnitude(dc_to_ac_result.voltage_violations),
        _max_violation_magnitude(dc_to_ac_result.reactive_violations),
        _max_violation_magnitude(dc_to_ac_result.branch_violations)
    )
end

function print_dc_to_ac_study_row(row::DCToACStudyRow)
    println("\n==============================")
    println("DC -> AC STUDY ROW")
    println("==============================")

    println("Case: ", row.case_name)
    println("DC OPF status: ", row.dc_opf_status)
    println("AC OPF status: ", row.ac_opf_status)
    println("DC objective: ", row.dc_objective)
    println("AC objective: ", row.ac_objective)
    println("Objective gap AC - DC: ", row.objective_gap)

    println("\nAC PF converged after DC dispatch: ", row.ac_pf_converged_after_dc)
    println("DC dispatch AC feasible: ", row.dc_dispatch_ac_feasible)

    println("\nVoltage violations: ", row.n_voltage_violations)
    println("Reactive violations: ", row.n_reactive_violations)
    println("Branch violations: ", row.n_branch_violations)

    println("\nMax voltage violation: ", row.max_voltage_violation)
    println("Max reactive violation: ", row.max_reactive_violation)
    println("Max branch violation: ", row.max_branch_violation)
end

function dc_to_ac_study_row_as_named_tuple(row::DCToACStudyRow)
    return (
        case_name = row.case_name,
        dc_opf_status = row.dc_opf_status,
        ac_opf_status = row.ac_opf_status,
        dc_objective = row.dc_objective,
        ac_objective = row.ac_objective,
        objective_gap = row.objective_gap,
        ac_pf_converged_after_dc = row.ac_pf_converged_after_dc,
        dc_dispatch_ac_feasible = row.dc_dispatch_ac_feasible,
        n_voltage_violations = row.n_voltage_violations,
        n_reactive_violations = row.n_reactive_violations,
        n_branch_violations = row.n_branch_violations,
        max_voltage_violation = row.max_voltage_violation,
        max_reactive_violation = row.max_reactive_violation,
        max_branch_violation = row.max_branch_violation
    )
end