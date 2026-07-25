# Equality-only deviation-minimization diagnostic.
#
# For objective-mismatch scenarios:
#   1. solve PowerModels OPF to get the reference point (xP*, uP*), and align
#      its reference angle so the own model's slack bus reads 0;
#   2. solve the corresponding own OPF to get the suspicious point (xE*, uE*);
#   3. build an equality-only own NLP, warm-started from (xE*, uE*), using
#      the TRUE-substitution deviation variables (add_ac_target_deviation_variables!,
#      see variables.jl) with enforce_bounds=false instead of the old
#      linking-equality-constraint route;
#   4. minimize L1 deviation to (xP*, uP*) over Pg/Qg/Vm/Va;
#   5. evaluate generator/voltage/thermal/angle inequality levels afterward.
#
# The equality-only model keeps:
#   - AC power-balance equalities;
#   - branch Ohm equalities for branch-flow formulations;
#   - reference-angle equality.
#
# It removes:
#   - active/reactive generator bounds for online generators;
#   - voltage magnitude bounds;
#   - branch thermal limits;
#   - branch angle limits;
#   - PM-like simple p/q arc-flow bounds.
#
# Outputs:
#   results/own_vs_powermodels/equality_only_deviation_minimization_julia.csv
#   results/own_vs_powermodels/equality_only_deviation_variable_differences_julia.csv
#   results/own_vs_powermodels/equality_only_deviation_inequality_levels_julia.csv
#   results/own_vs_powermodels/equality_only_deviation_binding_summary_julia.csv

const THIRTYFOUR_SCRIPT_PATH = joinpath(@__DIR__, "34_run_pm_like_constrained_deviation_minimization.jl")
let
    src = read(THIRTYFOUR_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(THIRTYFOUR_SCRIPT_PATH).")
    Base.include_string(Main, stripped, THIRTYFOUR_SCRIPT_PATH)
end

const EQUALITY_ONLY_OUT = joinpath(ROOT, "results", "own_vs_powermodels")

function load_equality_only_targets()
    # "current" (bus-injection) config removed -- that formulation was
    # deleted; objective_mismatch_details_julia_current.csv is a frozen
    # historical file only.
    configs = [
        (variant="pm_like", path=joinpath(ROOT, "results", "own_vs_powermodels", "objective_mismatch_details_julia_pm_like.csv")),
    ]
    targets = NamedTuple[]
    seen = Set{Tuple{String, String, String}}()
    for cfg in configs
        isfile(cfg.path) || continue
        df = DataFrame(CSV.File(cfg.path))
        for row in eachrow(df)
            case_label = String(row.case)
            scenario_label = normalize_pm_like_deviation_label(String(row.scenario))
            key = (cfg.variant, case_label, scenario_label)
            if !(key in seen)
                push!(targets, (variant=cfg.variant, case=case_label, scenario=scenario_label))
                push!(seen, key)
            end
        end
    end
    return targets
end

"""Equality-only model (arc-based, the sole AC-OPF formulation):
true-substitution deviation variables with enforce_bounds=false (drops
Pg/Qg/Vm bounds entirely, matching this script's header spec), plus
unbounded arc p/q flow variables (enforce_arc_bounds=false)."""
function build_pm_like_equality_only_model(case::OwnPowerModels.PowerCase, target, own_start)
    data = OwnPowerModels.build_ac_arc_data(case)
    model = Model(tight_deviation_optimizer())
    set_silent(model)

    pg, qg, vm, va, deviation_slacks = OwnPowerModels.add_ac_target_deviation_variables!(
        model, case, data.base;
        target_pg=target.pg, target_qg=target.qg, target_vm=target.vm, target_va=target.va,
        own_pg=own_start.pg, own_qg=own_start.qg, own_vm=own_start.vm, own_va=own_start.va,
        include_pg=true, include_qg=true, include_vm=true, include_va=true,
        enforce_bounds=false,
        warm_start_source=:own,
    )
    p, q = OwnPowerModels.add_ac_arc_variables!(model, data; enforce_arc_bounds=false)
    reference_angle = OwnPowerModels.add_ac_reference_angle!(model, case, va; fix_reference_angle=true)
    branch_flows = OwnPowerModels.add_ac_branch_constraints!(
        model,
        data,
        vm,
        va,
        p,
        q;
        include_branch_thermal=false,
        include_branch_angles=false,
        deduplicate_angle_constraints=false,
    )
    power_balance_p, power_balance_q = OwnPowerModels.add_ac_power_balance!(model, case, data, pg, qg, vm, p, q)
    OwnPowerModels.add_ac_target_deviation_objective!(model, deviation_slacks)
    constraints = (
        reference_angle=reference_angle,
        power_balance_p=power_balance_p,
        power_balance_q=power_balance_q,
        branch_angle_diff=branch_flows.angle_diff,
        branch_thermal_from=branch_flows.thermal_from,
        branch_thermal_to=branch_flows.thermal_to,
        branch_ohms_from=branch_flows.ohms_from,
        branch_ohms_to=branch_flows.ohms_to,
        deviation_slacks=deviation_slacks,
    )
    return model, data.base, pg, qg, vm, va, branch_flows, constraints
end

function solve_normal_own_for_equality_start(case::OwnPowerModels.PowerCase, variant::String)
    variant == "pm_like" || error("Unsupported equality-only variant $(variant). Expected \"pm_like\" (bus-injection was deleted).")
    return solve_ac_opf_own(case; optimizer=build_ac_optimizer(), silent=true, fix_reference_angle=true, warm_start=:zero)
end

function solve_equality_only_deviation(case::OwnPowerModels.PowerCase, variant::String, target, start_result)
    variant == "pm_like" || error("Unsupported equality-only variant $(variant). Expected \"pm_like\" (bus-injection was deleted).")
    model, data, pg, qg, vm, va, branch_flows, constraints =
        build_pm_like_equality_only_model(case, target, start_result)
    elapsed = @elapsed optimize!(model)
    result = OwnPowerModels.collect_ac_opf_result(model, case, data, pg, qg, vm, va, branch_flows)
    return (
        model=model,
        data=data,
        pg=pg,
        qg=qg,
        vm=vm,
        va=va,
        branch_flows=branch_flows,
        constraints=constraints,
        result=result,
        status=string(termination_status(model)),
        deviation_objective=try objective_value(model) catch; NaN end,
        solve_time_sec=elapsed,
        solve_iterations=OwnPowerModels._safe_barrier_iterations(model),
    )
end

function inequality_rows_with_context(gen_rows, bus_rows, branch_rows; case_label, scenario_label, variant_label)
    rows = NamedTuple[]
    for row in eachrow(constraints_binding_rows(gen_rows, bus_rows, branch_rows))
        relation = Bool(row.own_binding) == Bool(row.pm_binding) ? "same" :
            (Bool(row.own_binding) ? "only_equality_binding" : "only_pm_binding")
        violation = Float64(row.own_margin) < -1.0e-6
        push!(rows, (
            case=case_label,
            scenario=scenario_label,
            variant=variant_label,
            constraint_type=String(row.constraint_type),
            element_id=String(row.element_id),
            bound=Float64(row.bound),
            equality_only_value=Float64(row.own_value),
            pm_value=Float64(row.pm_value),
            value_diff_equality_minus_pm=Float64(row.own_value) - Float64(row.pm_value),
            abs_value_diff=abs(Float64(row.own_value) - Float64(row.pm_value)),
            equality_only_margin=Float64(row.own_margin),
            pm_margin=Float64(row.pm_margin),
            margin_diff_equality_minus_pm=Float64(row.own_margin) - Float64(row.pm_margin),
            abs_margin_diff=abs(Float64(row.own_margin) - Float64(row.pm_margin)),
            equality_only_binding=Bool(row.own_binding),
            pm_binding=Bool(row.pm_binding),
            equality_only_violates_limit=violation,
            binding_relation=relation,
        ))
    end
    return rows
end

function equality_binding_summary_rows(inequality_rows)
    isempty(inequality_rows) && return NamedTuple[]
    df = DataFrame(inequality_rows)
    rows = NamedTuple[]
    for group in groupby(df, [:case, :scenario, :variant])
        mismatches = group[group.binding_relation .!= "same", :]
        violations = group[group.equality_only_violates_limit .== true, :]
        push!(rows, (
            case=group.case[1],
            scenario=group.scenario[1],
            variant=group.variant[1],
            n_constraints=nrow(group),
            n_binding_mismatches=nrow(mismatches),
            n_only_equality_binding=sum(mismatches.binding_relation .== "only_equality_binding"),
            n_only_pm_binding=sum(mismatches.binding_relation .== "only_pm_binding"),
            n_equality_only_violations=nrow(violations),
            max_violation=isempty(violations.equality_only_margin) ? 0.0 : maximum(-violations.equality_only_margin),
            max_abs_value_diff=isempty(group.abs_value_diff) ? NaN : maximum(skipmissing(group.abs_value_diff)),
            max_abs_margin_diff=isempty(group.abs_margin_diff) ? NaN : maximum(skipmissing(group.abs_margin_diff)),
            n_pg_mismatches=sum(in.(mismatches.constraint_type, Ref(["pg_min", "pg_max"]))),
            n_qg_mismatches=sum(in.(mismatches.constraint_type, Ref(["qg_min", "qg_max"]))),
            n_vm_mismatches=sum(in.(mismatches.constraint_type, Ref(["vm_min", "vm_max"]))),
            n_thermal_mismatches=sum(mismatches.constraint_type .== "thermal"),
            n_angle_mismatches=sum(in.(mismatches.constraint_type, Ref(["angle_lower", "angle_upper"]))),
            n_pg_violations=sum(in.(violations.constraint_type, Ref(["pg_min", "pg_max"]))),
            n_qg_violations=sum(in.(violations.constraint_type, Ref(["qg_min", "qg_max"]))),
            n_vm_violations=sum(in.(violations.constraint_type, Ref(["vm_min", "vm_max"]))),
            n_thermal_violations=sum(violations.constraint_type .== "thermal"),
            n_angle_violations=sum(in.(violations.constraint_type, Ref(["angle_lower", "angle_upper"]))),
        ))
    end
    return rows
end

function run_one_equality_only_target(target)
    println("Equality-only deviation $(target.variant) / $(target.case) / $(target.scenario)")
    data = scenario_data_for_pm_like_deviation(target.case, target.scenario)
    case = OwnPowerModels.powermodels_to_powercase(deepcopy(data))
    pm = solve_pm_target_pm_like_deviation(data)
    aligned_target, angle_offset = align_target_reference_angle(pm.target, case.slack_bus_id)
    own_start = solve_normal_own_for_equality_start(case, target.variant)
    equality = solve_equality_only_deviation(case, target.variant, aligned_target, own_start)
    good = is_good_status(equality.status)

    pg_values = Dict(gen.id => equality.result.pg[gen.id] for gen in case.generators if haskey(equality.result.pg, gen.id))
    qg_values = Dict(gen.id => equality.result.qg[gen.id] for gen in case.generators if haskey(equality.result.qg, gen.id))
    vm_values = Dict(bus.id => equality.result.vm[bus.id] for bus in case.buses if haskey(equality.result.vm, bus.id))
    va_values = Dict(bus.id => equality.result.va[bus.id] for bus in case.buses if haskey(equality.result.va, bus.id))

    if good
        slacks = equality.constraints.deviation_slacks
        pg_stats = group_stats("Pg", true, slacks, pg_values, aligned_target.pg)
        qg_stats = group_stats("Qg", true, slacks, qg_values, aligned_target.qg)
        vm_stats = group_stats("Vm", true, slacks, vm_values, aligned_target.vm)
        va_stats = group_stats("Va", true, slacks, va_values, aligned_target.va)
    else
        pg_stats = qg_stats = vm_stats = va_stats = (max_abs=NaN, total_l1=NaN, n=0, source="not_solved")
    end

    summary_row = (
        case=target.case,
        scenario=target.scenario,
        variant=target.variant,
        target_source="powermodels_reference",
        start_source="own_opf_solution",
        reference_angle_offset_rad=angle_offset,
        pm_status=pm.status,
        pm_objective=pm.objective,
        own_start_status=string(own_start.termination_status),
        own_start_objective=own_start.objective,
        equality_only_status=equality.status,
        equality_only_solved=good,
        equality_only_l1_deviation=equality.deviation_objective,
        equality_only_l1_deviation_nonnegative=max(0.0, equality.deviation_objective),
        equality_only_solve_time_sec=equality.solve_time_sec,
        equality_only_solve_iterations=equality.solve_iterations,
        max_abs_pg_deviation=pg_stats.max_abs,
        total_l1_pg_deviation=pg_stats.total_l1,
        max_abs_qg_deviation=qg_stats.max_abs,
        total_l1_qg_deviation=qg_stats.total_l1,
        max_abs_vm_deviation=vm_stats.max_abs,
        total_l1_vm_deviation=vm_stats.total_l1,
        max_abs_va_deviation=va_stats.max_abs,
        total_l1_va_deviation=va_stats.total_l1,
    )

    variable_rows_out = good ? variable_difference_rows(target.case, target.scenario, target.variant, "own", equality.result, aligned_target) : NamedTuple[]
    inequality_rows_out = NamedTuple[]
    if good
        gen_rows = OwnPowerModels.compare_ac_opf_generator_rows(case, equality.result, pm.result)
        bus_rows = OwnPowerModels.compare_ac_opf_bus_rows(case, equality.result, pm.result)
        branch_rows = OwnPowerModels.compare_ac_opf_branch_rows(case, equality.result, pm.result)
        inequality_rows_out = inequality_rows_with_context(
            gen_rows,
            bus_rows,
            branch_rows;
            case_label=target.case,
            scenario_label=target.scenario,
            variant_label=target.variant,
        )
    end
    return summary_row, variable_rows_out, inequality_rows_out
end

function main()
    summary_rows = NamedTuple[]
    variable_rows_out = NamedTuple[]
    inequality_rows_out = NamedTuple[]

    for target in load_equality_only_targets()
        summary_row, var_rows, ineq_rows = run_one_equality_only_target(target)
        push!(summary_rows, summary_row)
        append!(variable_rows_out, var_rows)
        append!(inequality_rows_out, ineq_rows)
    end

    mkpath(EQUALITY_ONLY_OUT)
    summary_path = joinpath(EQUALITY_ONLY_OUT, "equality_only_deviation_minimization_julia.csv")
    variable_path = joinpath(EQUALITY_ONLY_OUT, "equality_only_deviation_variable_differences_julia.csv")
    inequality_path = joinpath(EQUALITY_ONLY_OUT, "equality_only_deviation_inequality_levels_julia.csv")
    binding_summary_path = joinpath(EQUALITY_ONLY_OUT, "equality_only_deviation_binding_summary_julia.csv")

    CSV.write(summary_path, DataFrame(summary_rows))
    CSV.write(variable_path, DataFrame(variable_rows_out))
    CSV.write(inequality_path, DataFrame(inequality_rows_out))
    CSV.write(binding_summary_path, DataFrame(equality_binding_summary_rows(inequality_rows_out)))

    println("Wrote $(summary_path)")
    println("Wrote $(variable_path)")
    println("Wrote $(inequality_path)")
    println("Wrote $(binding_summary_path)")
end

main()
