# PM-like constrained deviation-minimization against PowerModels OPF points.
#
# This is the standard/all-constraints deviation diagnostic, not the later
# equality-only diagnostic. For each PM-like objective-mismatch scenario:
#   1. solve PowerModels ACP to get the target OPF point x^{p*}, and align its
#      reference angle so the own model's slack bus reads 0 (hygiene fix --
#      PM's reported angle at the slack bus can carry solver-tolerance noise);
#   2. read the own model's own previously-solved point x^{e*} straight off
#      disk (same raw per-scenario CSVs script 46 reads) -- no re-solve;
#   3. solve own PM-like AC OPF with the generation-cost objective replaced by
#      an L1 deviation objective to x^{p*}, via TRUE substitution
#      (target + Delta+ - Delta-, see add_ac_target_deviation_variables! in
#      variables.jl) rather than a linking equality constraint -- for each
#      variant, in TWO warm-started versions: seeded at x^{p*} itself
#      (warm_start_source=:target, "does it stay there") and seeded at
#      x^{e*} (warm_start_source=:own, "can it still get there starting from
#      a realistic point");
#   4. keep all normal PM-like OPF equality and inequality constraints active;
#   5. also emit a third, NOT-solved "fixed_no_solve" reference row per
#      variant: the raw x^{e*}-minus-x^{p*} gap decomposed into Delta+/Delta-
#      with zero optimization, sitting in the same table for scale
#      comparison against the two solved versions above;
#   6. export variable deviations and binding levels at the deviation point
#      for both solved versions.
#
# See the plan file (deviation-minimization rebuild) for the full report on
# why the previous linking-equality-constraint implementation could get
# stuck at a spurious nonzero local optimum for reduced variants, and why
# this true-substitution rebuild plus target-seeded and own-seeded
# warm-starts fixes it.
#
# Outputs (now with an extra `warm_start_source` column: "target", "own", or
# "fixed_no_solve"):
#   results/own_vs_powermodels/pm_like_constrained_deviation_minimization_julia.csv
#   results/own_vs_powermodels/pm_like_constrained_deviation_variable_differences_julia.csv
#   results/own_vs_powermodels/pm_like_constrained_deviation_binding_differences_julia.csv
#   results/own_vs_powermodels/pm_like_constrained_deviation_binding_summary_julia.csv

const TWENTYFIVE_SCRIPT_PATH = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH)
end

using CSV
using DataFrames
using JuMP

const PM_LIKE_DEVIATION_OUT = joinpath(ROOT, "results", "own_vs_powermodels")
const PM_LIKE_DEVIATION_OWN_POINT_SOURCE = "julia_pm_like"
const PM_LIKE_DEVIATION_WARM_START_SOURCES = (:target, :own)

const KNOWN_LABELS_PM_LIKE_DEVIATION = Dict(
    "1888_rte__base__none__none__none__none" => "base",
    "1888_rte_load_x0p8__load_scaling__none__none__load_multiplier__0p8" => "load_x0.8",
    "1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2" => "load_x1.2_reactive_stress",
    "1888_rte_qg_limits_x0p6__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p6" => "qg_limits_x0.6",
    "1888_rte_qg_limits_x0p8__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p8" => "qg_limits_x0.8",
)

const PM_LIKE_DEVIATION_VARIANTS = [
    (variant="full", include_qg=true, include_vm=true, include_va=true),
    (variant="no_qg", include_qg=false, include_vm=true, include_va=true),
    (variant="no_v", include_qg=true, include_vm=false, include_va=false),
    (variant="pg_only", include_qg=false, include_vm=false, include_va=false),
]

normalize_pm_like_deviation_label(label::AbstractString) = get(KNOWN_LABELS_PM_LIKE_DEVIATION, String(label), String(label))

function expected_scenario_label_pm_like_deviation(entry)
    raw_id = String(entry.scenario_id)
    if haskey(KNOWN_LABELS_PM_LIKE_DEVIATION, raw_id)
        return KNOWN_LABELS_PM_LIKE_DEVIATION[raw_id]
    elseif entry.scenario == "base"
        return "base"
    else
        return raw_id
    end
end

function scenario_data_for_pm_like_deviation(case_label::String, scenario_label::String)
    case_file = "pglib_opf_case$(case_label)"
    data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
    entries = compact_scenarios(data, case_label, build_ac_optimizer(); include_no_reference=true)
    for entry in entries
        if expected_scenario_label_pm_like_deviation(entry) == scenario_label || String(entry.scenario_id) == scenario_label
            return entry.data
        end
    end
    error("Could not find scenario $(case_label)/$(scenario_label)")
end

function load_pm_like_deviation_targets()
    path = joinpath(ROOT, "results", "own_vs_powermodels", "objective_mismatch_details_julia_pm_like.csv")
    isfile(path) || error("Missing PM-like objective mismatch table: $(path)")
    df = DataFrame(CSV.File(path))
    targets = NamedTuple[]
    seen = Set{Tuple{String, String}}()
    for row in eachrow(df)
        case_label = String(row.case)
        scenario_label = normalize_pm_like_deviation_label(String(row.scenario))
        key = (case_label, scenario_label)
        if !(key in seen)
            push!(targets, (case=case_label, scenario=scenario_label))
            push!(seen, key)
        end
    end
    return targets
end

function tight_deviation_optimizer()
    return optimizer_with_attributes(
        Ipopt.Optimizer,
        "print_level" => 0,
        "max_iter" => 3000,
        "tol" => 1.0e-10,
        "constr_viol_tol" => 1.0e-9,
        "compl_inf_tol" => 1.0e-9,
        "dual_inf_tol" => 1.0,
    )
end

is_good_status(text::AbstractString) = text in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")

function solve_pm_target_pm_like_deviation(data)
    optimizer = tight_deviation_optimizer()
    pm = PowerModels.instantiate_model(deepcopy(data), PowerModels.ACPPowerModel, PowerModels.build_opf)
    elapsed = @elapsed result = PowerModels.optimize_model!(pm, optimizer=optimizer)
    status = string(get(result, "termination_status", "UNKNOWN"))
    if !is_good_status(status)
        error("PowerModels target did not solve: status=$(status)")
    end
    sol = result["solution"]
    target = (
        pg=Dict(string(k) => Float64(v["pg"]) for (k, v) in sol["gen"]),
        qg=Dict(string(k) => Float64(v["qg"]) for (k, v) in sol["gen"]),
        vm=Dict(string(k) => Float64(v["vm"]) for (k, v) in sol["bus"]),
        va=Dict(string(k) => Float64(v["va"]) for (k, v) in sol["bus"]),
    )
    return (
        model=pm.model,
        result=result,
        target=target,
        status=status,
        objective=Float64(get(result, "objective", NaN)),
        solve_time_sec=elapsed,
        solve_iterations=OwnPowerModels._safe_barrier_iterations(pm.model),
    )
end

"""Shift PM's raw voltage angles so the own model's slack bus reads exactly
0 -- both solvers fix the same slack bus at angle 0 by convention, but PM's
reported value can carry small solver-tolerance noise, which otherwise
forces a nonzero element-level "deviation" at the slack bus whenever Va is
an included group. Hygiene fix, not a correctness fix (see plan file)."""
function align_target_reference_angle(target, slack_bus_id::String)
    offset = get(target.va, slack_bus_id, 0.0)
    aligned_va = Dict(id => v - offset for (id, v) in target.va)
    return (pg=target.pg, qg=target.qg, vm=target.vm, va=aligned_va), offset
end

"""Read the own model's own solution point x^{e*} straight off the
already-written raw per-scenario CSVs -- no re-solve, x^{e*} is already on
disk (same source script 46 reads for the g^e/h^e feasibility check)."""
function read_own_point_pm_like_deviation(scenario_label::String)
    base = joinpath(ROOT, "results", "own_vs_powermodels", PM_LIKE_DEVIATION_OWN_POINT_SOURCE, "1888_rte", scenario_label)
    gens = CSV.read(joinpath(base, "generators.csv"), DataFrame)
    buses = CSV.read(joinpath(base, "buses.csv"), DataFrame)
    return (
        pg=Dict(string(r.gen_id) => Float64(r.own_pg) for r in eachrow(gens)),
        qg=Dict(string(r.gen_id) => Float64(r.own_qg) for r in eachrow(gens)),
        vm=Dict(string(r.bus_id) => Float64(r.own_vm) for r in eachrow(buses)),
        va=Dict(string(r.bus_id) => Float64(r.own_va) for r in eachrow(buses)),
    )
end

function slack_group_stats(plus::Dict, minus::Dict)
    ids = collect(keys(plus))
    isempty(ids) && return (max_abs=0.0, total_l1=0.0, n=0)
    signed = [value(plus[id]) - value(minus[id]) for id in ids]
    l1 = [value(plus[id]) + value(minus[id]) for id in ids]
    return (max_abs=maximum(abs.(signed)), total_l1=sum(l1), n=length(ids))
end

function free_group_stats(values::Dict{String, Float64}, target::Dict{String, Float64})
    ids = [id for id in keys(target) if haskey(values, id)]
    isempty(ids) && return (max_abs=NaN, total_l1=NaN, n=0)
    diffs = [values[id] - target[id] for id in ids]
    return (max_abs=maximum(abs.(diffs)), total_l1=sum(abs.(diffs)), n=length(ids))
end

function group_stats(group_name::String, included::Bool, slacks, values::Dict{String, Float64}, target::Dict{String, Float64})
    if included
        plus = getproperty(slacks, Symbol("$(group_name)_plus"))
        minus = getproperty(slacks, Symbol("$(group_name)_minus"))
        stats = slack_group_stats(plus, minus)
        return (max_abs=stats.max_abs, total_l1=stats.total_l1, n=stats.n, source="pinned_slack")
    else
        stats = free_group_stats(values, target)
        return (max_abs=stats.max_abs, total_l1=stats.total_l1, n=stats.n, source="posthoc_free")
    end
end

"""Zero-optimization baseline: decompose the already-known x^{e*}-minus-
x^{p*} gap into Delta+/Delta- directly, no solve. Mathematically identical
to the raw own_value-minus-target_value diff columns already on disk
(Q1/Q2) -- included here only as a same-table, same-units reference row."""
function fixed_group_stats(values::Dict{String, Float64}, target::Dict{String, Float64})
    ids = [id for id in keys(target) if haskey(values, id)]
    isempty(ids) && return (max_abs=NaN, total_l1=NaN, n=0, source="fixed_no_solve")
    diffs = [values[id] - target[id] for id in ids]
    return (max_abs=maximum(abs.(diffs)), total_l1=sum(abs.(diffs)), n=length(ids), source="fixed_no_solve")
end

function own_cost_at_solution(case::OwnPowerModels.PowerCase, pg)
    return sum(
        gen.cost_c2 * value(pg[gen.id])^2 + gen.cost_c1 * value(pg[gen.id]) + gen.cost_c0
        for gen in case.generators if gen.status == 1; init=0.0
    )
end

function solve_pm_like_deviation(case::OwnPowerModels.PowerCase, target, own, variant, warm_start_source::Symbol)
    model, data, pg, qg, vm, va, branch_flows, constraints = build_ac_opf_model(
        case;
        optimizer=tight_deviation_optimizer(),
        silent=true,
        fix_reference_angle=true,
        warm_start=:zero,
        objective=:target_deviation,
        target_pg=target.pg,
        target_qg=target.qg,
        target_vm=target.vm,
        target_va=target.va,
        own_pg=own.pg,
        own_qg=own.qg,
        own_vm=own.vm,
        own_va=own.va,
        include_pg=true,
        include_qg=variant.include_qg,
        include_vm=variant.include_vm,
        include_va=variant.include_va,
        enforce_deviation_bounds=true,
        warm_start_source=warm_start_source,
    )
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
        cost_at_deviation_point=has_values(model) ? own_cost_at_solution(case, pg) : NaN,
        solve_time_sec=elapsed,
        solve_iterations=OwnPowerModels._safe_barrier_iterations(model),
    )
end

function variable_difference_rows(case_label, scenario_label, variant_label, warm_start_source_label, result, target)
    rows = NamedTuple[]
    for (gen_id, target_value) in sort(collect(target.pg); by=x -> parse(Int, x[1]))
        if haskey(result.pg, gen_id)
            value = result.pg[gen_id]
            push!(rows, (case=case_label, scenario=scenario_label, variant=variant_label, warm_start_source=warm_start_source_label, element_type="generator", element_id=gen_id, quantity="pg", deviation_value=value, pm_target_value=target_value, diff=value - target_value, abs_diff=abs(value - target_value)))
        end
    end
    for (gen_id, target_value) in sort(collect(target.qg); by=x -> parse(Int, x[1]))
        if haskey(result.qg, gen_id)
            value = result.qg[gen_id]
            push!(rows, (case=case_label, scenario=scenario_label, variant=variant_label, warm_start_source=warm_start_source_label, element_type="generator", element_id=gen_id, quantity="qg", deviation_value=value, pm_target_value=target_value, diff=value - target_value, abs_diff=abs(value - target_value)))
        end
    end
    for (bus_id, target_value) in sort(collect(target.vm); by=x -> parse(Int, x[1]))
        if haskey(result.vm, bus_id)
            value = result.vm[bus_id]
            push!(rows, (case=case_label, scenario=scenario_label, variant=variant_label, warm_start_source=warm_start_source_label, element_type="bus", element_id=bus_id, quantity="vm", deviation_value=value, pm_target_value=target_value, diff=value - target_value, abs_diff=abs(value - target_value)))
        end
    end
    for (bus_id, target_value) in sort(collect(target.va); by=x -> parse(Int, x[1]))
        if haskey(result.va, bus_id)
            value = result.va[bus_id]
            push!(rows, (case=case_label, scenario=scenario_label, variant=variant_label, warm_start_source=warm_start_source_label, element_type="bus", element_id=bus_id, quantity="va", deviation_value=value, pm_target_value=target_value, diff=value - target_value, abs_diff=abs(value - target_value)))
        end
    end
    return rows
end

function binding_rows_with_context(gen_rows, bus_rows, branch_rows; case_label, scenario_label, variant_label, warm_start_source_label)
    rows = NamedTuple[]
    for row in eachrow(constraints_binding_rows(gen_rows, bus_rows, branch_rows))
        relation = Bool(row.own_binding) == Bool(row.pm_binding) ? "same" :
            (Bool(row.own_binding) ? "only_deviation_binding" : "only_pm_binding")
        push!(rows, (
            case=case_label,
            scenario=scenario_label,
            variant=variant_label,
            warm_start_source=warm_start_source_label,
            constraint_type=String(row.constraint_type),
            element_id=String(row.element_id),
            bound=Float64(row.bound),
            deviation_value=Float64(row.own_value),
            pm_value=Float64(row.pm_value),
            value_diff_deviation_minus_pm=Float64(row.own_value) - Float64(row.pm_value),
            abs_value_diff=abs(Float64(row.own_value) - Float64(row.pm_value)),
            deviation_margin=Float64(row.own_margin),
            pm_margin=Float64(row.pm_margin),
            margin_diff_deviation_minus_pm=Float64(row.own_margin) - Float64(row.pm_margin),
            abs_margin_diff=abs(Float64(row.own_margin) - Float64(row.pm_margin)),
            deviation_binding=Bool(row.own_binding),
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
    for group in groupby(df, [:case, :scenario, :variant, :warm_start_source])
        mismatches = group[group.binding_relation .!= "same", :]
        push!(rows, (
            case=group.case[1],
            scenario=group.scenario[1],
            variant=group.variant[1],
            warm_start_source=group.warm_start_source[1],
            n_constraints=nrow(group),
            n_binding_mismatches=nrow(mismatches),
            n_only_deviation_binding=sum(mismatches.binding_relation .== "only_deviation_binding"),
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

function run_one_target(target)
    println("PM-like constrained deviation $(target.case) / $(target.scenario)")
    data = scenario_data_for_pm_like_deviation(target.case, target.scenario)
    case = OwnPowerModels.powermodels_to_powercase(deepcopy(data))
    pm = solve_pm_target_pm_like_deviation(data)
    aligned_target, angle_offset = align_target_reference_angle(pm.target, case.slack_bus_id)
    own = read_own_point_pm_like_deviation(target.scenario)

    summary_rows = NamedTuple[]
    variable_rows = NamedTuple[]
    binding_rows = NamedTuple[]

    for variant in PM_LIKE_DEVIATION_VARIANTS
        println("  variant=$(variant.variant)")

        for warm_start_source in PM_LIKE_DEVIATION_WARM_START_SOURCES
            println("    warm_start_source=$(warm_start_source)")
            deviation = solve_pm_like_deviation(case, aligned_target, own, variant, warm_start_source)
            good = is_good_status(deviation.status)
            result = deviation.result

            qg_values = Dict(gen.id => result.qg[gen.id] for gen in case.generators if haskey(result.qg, gen.id))
            vm_values = Dict(bus.id => result.vm[bus.id] for bus in case.buses if haskey(result.vm, bus.id))
            va_values = Dict(bus.id => result.va[bus.id] for bus in case.buses if haskey(result.va, bus.id))
            pg_values = Dict(gen.id => result.pg[gen.id] for gen in case.generators if haskey(result.pg, gen.id))

            if good
                slacks = deviation.constraints.deviation_slacks
                pg_stats = group_stats("Pg", true, slacks, pg_values, aligned_target.pg)
                qg_stats = group_stats("Qg", variant.include_qg, slacks, qg_values, aligned_target.qg)
                vm_stats = group_stats("Vm", variant.include_vm, slacks, vm_values, aligned_target.vm)
                va_stats = group_stats("Va", variant.include_va, slacks, va_values, aligned_target.va)
            else
                pg_stats = qg_stats = vm_stats = va_stats = (max_abs=NaN, total_l1=NaN, n=0, source="not_solved")
            end

            push!(summary_rows, (
                case=target.case,
                scenario=target.scenario,
                variant=variant.variant,
                warm_start_source=String(warm_start_source),
                include_qg=variant.include_qg,
                include_vm=variant.include_vm,
                include_va=variant.include_va,
                pm_status=pm.status,
                pm_objective=pm.objective,
                pm_solve_time_sec=pm.solve_time_sec,
                pm_solve_iterations=pm.solve_iterations,
                reference_angle_offset_rad=angle_offset,
                deviation_status=deviation.status,
                deviation_solved=good,
                pinned_l1_deviation=deviation.deviation_objective,
                pinned_l1_deviation_nonnegative=max(0.0, deviation.deviation_objective),
                deviation_solve_time_sec=deviation.solve_time_sec,
                deviation_solve_iterations=deviation.solve_iterations,
                cost_at_deviation_point=deviation.cost_at_deviation_point,
                cost_diff_vs_pm=deviation.cost_at_deviation_point - pm.objective,
                cost_rel_diff_vs_pm=abs(deviation.cost_at_deviation_point - pm.objective) / (1.0 + abs(pm.objective)),
                max_abs_pg_deviation=pg_stats.max_abs,
                total_l1_pg_deviation=pg_stats.total_l1,
                pg_deviation_source=pg_stats.source,
                max_abs_qg_deviation=qg_stats.max_abs,
                total_l1_qg_deviation=qg_stats.total_l1,
                qg_deviation_source=qg_stats.source,
                max_abs_vm_deviation=vm_stats.max_abs,
                total_l1_vm_deviation=vm_stats.total_l1,
                vm_deviation_source=vm_stats.source,
                max_abs_va_deviation=va_stats.max_abs,
                total_l1_va_deviation=va_stats.total_l1,
                va_deviation_source=va_stats.source,
            ))

            if good
                append!(variable_rows, variable_difference_rows(target.case, target.scenario, variant.variant, String(warm_start_source), result, aligned_target))
                gen_rows = OwnPowerModels.compare_ac_opf_generator_rows(case, result, pm.result)
                bus_rows = OwnPowerModels.compare_ac_opf_bus_rows(case, result, pm.result)
                branch_rows = OwnPowerModels.compare_ac_opf_branch_rows(case, result, pm.result)
                append!(binding_rows, binding_rows_with_context(
                    gen_rows, bus_rows, branch_rows;
                    case_label=target.case,
                    scenario_label=target.scenario,
                    variant_label=variant.variant,
                    warm_start_source_label=String(warm_start_source),
                ))
            end
        end

        # Third row per variant: NOT a solve -- the raw x^{e*}-minus-x^{p*}
        # gap, decomposed into Delta+/Delta-, sitting in the same table as a
        # reference baseline (see fixed_group_stats docstring).
        pg_fixed = fixed_group_stats(own.pg, aligned_target.pg)
        qg_fixed = fixed_group_stats(own.qg, aligned_target.qg)
        vm_fixed = fixed_group_stats(own.vm, aligned_target.vm)
        va_fixed = fixed_group_stats(own.va, aligned_target.va)

        push!(summary_rows, (
            case=target.case,
            scenario=target.scenario,
            variant=variant.variant,
            warm_start_source="fixed_no_solve",
            include_qg=variant.include_qg,
            include_vm=variant.include_vm,
            include_va=variant.include_va,
            pm_status=pm.status,
            pm_objective=pm.objective,
            pm_solve_time_sec=pm.solve_time_sec,
            pm_solve_iterations=pm.solve_iterations,
            reference_angle_offset_rad=angle_offset,
            deviation_status="not_solved",
            deviation_solved=false,
            pinned_l1_deviation=NaN,
            pinned_l1_deviation_nonnegative=NaN,
            deviation_solve_time_sec=0.0,
            deviation_solve_iterations=0,
            cost_at_deviation_point=NaN,
            cost_diff_vs_pm=NaN,
            cost_rel_diff_vs_pm=NaN,
            max_abs_pg_deviation=pg_fixed.max_abs,
            total_l1_pg_deviation=pg_fixed.total_l1,
            pg_deviation_source=pg_fixed.source,
            max_abs_qg_deviation=qg_fixed.max_abs,
            total_l1_qg_deviation=qg_fixed.total_l1,
            qg_deviation_source=qg_fixed.source,
            max_abs_vm_deviation=vm_fixed.max_abs,
            total_l1_vm_deviation=vm_fixed.total_l1,
            vm_deviation_source=vm_fixed.source,
            max_abs_va_deviation=va_fixed.max_abs,
            total_l1_va_deviation=va_fixed.total_l1,
            va_deviation_source=va_fixed.source,
        ))
    end
    return summary_rows, variable_rows, binding_rows
end

function main()
    all_summary_rows = NamedTuple[]
    all_variable_rows = NamedTuple[]
    all_binding_rows = NamedTuple[]

    for target in load_pm_like_deviation_targets()
        summary_rows, variable_rows, binding_rows = run_one_target(target)
        append!(all_summary_rows, summary_rows)
        append!(all_variable_rows, variable_rows)
        append!(all_binding_rows, binding_rows)
    end

    mkpath(PM_LIKE_DEVIATION_OUT)
    summary_path = joinpath(PM_LIKE_DEVIATION_OUT, "pm_like_constrained_deviation_minimization_julia.csv")
    variable_path = joinpath(PM_LIKE_DEVIATION_OUT, "pm_like_constrained_deviation_variable_differences_julia.csv")
    binding_path = joinpath(PM_LIKE_DEVIATION_OUT, "pm_like_constrained_deviation_binding_differences_julia.csv")
    binding_summary_path = joinpath(PM_LIKE_DEVIATION_OUT, "pm_like_constrained_deviation_binding_summary_julia.csv")

    CSV.write(summary_path, DataFrame(all_summary_rows))
    CSV.write(variable_path, DataFrame(all_variable_rows))
    CSV.write(binding_path, DataFrame(all_binding_rows))
    CSV.write(binding_summary_path, DataFrame(binding_summary_rows(all_binding_rows)))

    println("Wrote $(summary_path)")
    println("Wrote $(variable_path)")
    println("Wrote $(binding_path)")
    println("Wrote $(binding_summary_path)")
end

main()
