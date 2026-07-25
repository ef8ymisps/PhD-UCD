# Re-solve objective-mismatch scenarios with Ipopt-output-aligned diagnostics.
#
# This script is intentionally focused on suspicious objective-mismatch cases.
# It exports:
#   1. A compact final-status table for each re-solve.
#   2. A full Ipopt iteration trace using the same columns as Ipopt's console
#      iteration table.
#   3. A notes table that documents the Ipopt options and column meanings used
#      for interpreting the diagnostics.
#
# Outputs:
#   results/own_vs_powermodels/solver_diagnostics_objective_mismatch_julia.csv
#   results/own_vs_powermodels/solver_diagnostics_objective_mismatch_ipopt_iterations_julia.csv
#   results/own_vs_powermodels/solver_diagnostics_ipopt_notes_julia.csv

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
using Ipopt
import MathOptInterface as MOI

const IPOPT_DIAGNOSTIC_OPTIONS = (
    print_level=0,
    max_iter=3000,
    tol=1.0e-8,
    dual_inf_tol=1.0,
    constr_viol_tol=1.0e-4,
    compl_inf_tol=1.0e-4,
    acceptable_tol=1.0e-6,
    acceptable_iter=15,
    acceptable_dual_inf_tol=1.0e10,
    acceptable_constr_viol_tol=1.0e-2,
    acceptable_compl_inf_tol=1.0e-2,
    acceptable_obj_change_tol=1.0e20,
    bound_relax_factor=1.0e-8,
    honor_original_bounds="no",
    inf_pr_output="original",
    print_info_string="no",
)

const KNOWN_LABELS_SOLVER_DIAG = Dict(
    "1888_rte__base__none__none__none__none" => "base",
    "1888_rte_load_x0p8__load_scaling__none__none__load_multiplier__0p8" => "load_x0.8",
    "1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2" => "load_x1.2_reactive_stress",
    "1888_rte_qg_limits_x0p6__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p6" => "qg_limits_x0.6",
    "1888_rte_qg_limits_x0p8__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p8" => "qg_limits_x0.8",
)

normalize_solver_diag_label(label::AbstractString) = get(KNOWN_LABELS_SOLVER_DIAG, String(label), String(label))

mutable struct IpoptTrace
    n_callbacks::Int
    alg_mod::Int
    iter_count::Int
    obj_value::Float64
    inf_pr::Float64
    inf_du::Float64
    mu::Float64
    lg_mu::Float64
    d_norm::Float64
    regularization_size::Float64
    lg_rg::Float64
    alpha_du::Float64
    alpha_pr::Float64
    ls_trials::Int
    rows::Vector{NamedTuple}
end

IpoptTrace() = IpoptTrace(0, 0, -1, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, NaN, -1, NamedTuple[])

safe_log10(x::Real) = isfinite(Float64(x)) && Float64(x) > 0.0 ? log10(Float64(x)) : NaN

function update_trace!(
    trace::IpoptTrace,
    alg_mod::Cint,
    iter_count::Cint,
    obj_value::Float64,
    inf_pr::Float64,
    inf_du::Float64,
    mu::Float64,
    d_norm::Float64,
    regularization_size::Float64,
    alpha_du::Float64,
    alpha_pr::Float64,
    ls_trials::Cint,
)
    trace.n_callbacks += 1
    trace.alg_mod = Int(alg_mod)
    trace.iter_count = Int(iter_count)
    trace.obj_value = obj_value
    trace.inf_pr = inf_pr
    trace.inf_du = inf_du
    trace.mu = mu
    trace.lg_mu = safe_log10(mu)
    trace.d_norm = d_norm
    trace.regularization_size = regularization_size
    trace.lg_rg = safe_log10(regularization_size)
    trace.alpha_du = alpha_du
    trace.alpha_pr = alpha_pr
    trace.ls_trials = Int(ls_trials)
    push!(
        trace.rows,
        (
            alg_mod=trace.alg_mod,
            iter=trace.iter_count,
            objective=trace.obj_value,
            inf_pr=trace.inf_pr,
            inf_du=trace.inf_du,
            mu=trace.mu,
            lg_mu=trace.lg_mu,
            step_norm=trace.d_norm,
            regularization_size=trace.regularization_size,
            lg_rg=trace.lg_rg,
            alpha_du=trace.alpha_du,
            alpha_pr=trace.alpha_pr,
            ls=trace.ls_trials,
        ),
    )
    return true
end

function traced_ipopt_optimizer(trace::IpoptTrace)
    optimizer = Ipopt.Optimizer()
    for option_name in propertynames(IPOPT_DIAGNOSTIC_OPTIONS)
        value = getproperty(IPOPT_DIAGNOSTIC_OPTIONS, option_name)
        MOI.set(optimizer, MOI.RawOptimizerAttribute(String(option_name)), value)
    end
    return optimizer
end

optimizer_for_trace(trace::IpoptTrace) = () -> traced_ipopt_optimizer(trace)

function attach_ipopt_trace!(model::JuMP.Model, trace::IpoptTrace)
    callback = function (args...)
        update_trace!(trace, args...)
    end
    MOI.set(model, Ipopt.CallbackFunction(), callback)
    return nothing
end

function expected_scenario_label_solver_diag(entry)
    raw_id = String(entry.scenario_id)
    if haskey(KNOWN_LABELS_SOLVER_DIAG, raw_id)
        return KNOWN_LABELS_SOLVER_DIAG[raw_id]
    elseif entry.scenario == "base"
        return "base"
    else
        return raw_id
    end
end

function scenario_data_for_solver_diag(case_label::String, scenario_label::String)
    case_file = "pglib_opf_case$(case_label)"
    data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
    entries = compact_scenarios(data, case_label, build_ac_optimizer(); include_no_reference=true)
    for entry in entries
        if expected_scenario_label_solver_diag(entry) == scenario_label || String(entry.scenario_id) == scenario_label
            return entry.data
        end
    end
    error("Could not find scenario $(case_label)/$(scenario_label)")
end

function safe_str(f, model)
    try
        return string(f(model))
    catch
        return "unavailable"
    end
end

function safe_num(f, model)
    try
        value = f(model)
        if value isa Missing
            return NaN
        elseif value isa AbstractVector
            return isempty(value) ? NaN : Float64(first(value))
        else
            return Float64(value)
        end
    catch
        return NaN
    end
end

function raw_status_class(raw_status_text::String)
    if raw_status_text == "Solve_Succeeded"
        return "desired_tolerances_satisfied"
    elseif raw_status_text == "Solved_To_Acceptable_Level"
        return "acceptable_level_only"
    elseif raw_status_text == "Maximum_Iterations_Exceeded"
        return "iteration_limit"
    elseif raw_status_text == "Infeasible_Problem_Detected"
        return "local_infeasibility"
    elseif raw_status_text == "Search_Direction_Becomes_Too_Small"
        return "slow_progress"
    else
        return "other_or_unavailable"
    end
end

function solver_info_row(; variant, case_label, scenario_label, solver_source, model, trace, objective, solve_time_sec)
    termination = safe_str(termination_status, model)
    raw = safe_str(raw_status, model)
    primal = safe_str(primal_status, model)
    dual = safe_str(dual_status, model)
    visible_primal_ok = isfinite(trace.inf_pr) && trace.inf_pr <= IPOPT_DIAGNOSTIC_OPTIONS.constr_viol_tol
    visible_dual_ok = isfinite(trace.inf_du) && trace.inf_du <= IPOPT_DIAGNOSTIC_OPTIONS.dual_inf_tol
    visible_mu_ok = isfinite(trace.mu) && trace.mu <= IPOPT_DIAGNOSTIC_OPTIONS.compl_inf_tol
    solved_to_desired = raw == "Solve_Succeeded"
    return (
        variant=variant,
        case=case_label,
        scenario=scenario_label,
        solver_source=solver_source,
        termination_status=termination,
        raw_status=raw,
        raw_status_class=raw_status_class(raw),
        primal_status=primal,
        dual_status=dual,
        result_count=safe_num(result_count, model),
        objective=objective,
        solve_time_sec=solve_time_sec,
        barrier_iterations=safe_num(barrier_iterations, model),
        objective_bound=safe_num(objective_bound, model),
        relative_gap=safe_num(relative_gap, model),
        ipopt_callback_count=trace.n_callbacks,
        ipopt_final_iter=trace.iter_count,
        ipopt_final_objective_column=trace.obj_value,
        ipopt_final_inf_pr_original_unscaled=trace.inf_pr,
        ipopt_final_inf_du_internal_scaled=trace.inf_du,
        ipopt_final_mu_barrier_parameter=trace.mu,
        ipopt_final_lg_mu=trace.lg_mu,
        ipopt_final_step_norm=trace.d_norm,
        ipopt_final_regularization_size=trace.regularization_size,
        ipopt_final_lg_rg=trace.lg_rg,
        ipopt_final_alpha_du=trace.alpha_du,
        ipopt_final_alpha_pr=trace.alpha_pr,
        ipopt_final_ls=trace.ls_trials,
        desired_tol=IPOPT_DIAGNOSTIC_OPTIONS.tol,
        dual_inf_tol=IPOPT_DIAGNOSTIC_OPTIONS.dual_inf_tol,
        constr_viol_tol=IPOPT_DIAGNOSTIC_OPTIONS.constr_viol_tol,
        compl_inf_tol=IPOPT_DIAGNOSTIC_OPTIONS.compl_inf_tol,
        acceptable_tol=IPOPT_DIAGNOSTIC_OPTIONS.acceptable_tol,
        acceptable_iter=IPOPT_DIAGNOSTIC_OPTIONS.acceptable_iter,
        acceptable_dual_inf_tol=IPOPT_DIAGNOSTIC_OPTIONS.acceptable_dual_inf_tol,
        acceptable_constr_viol_tol=IPOPT_DIAGNOSTIC_OPTIONS.acceptable_constr_viol_tol,
        acceptable_compl_inf_tol=IPOPT_DIAGNOSTIC_OPTIONS.acceptable_compl_inf_tol,
        bound_relax_factor=IPOPT_DIAGNOSTIC_OPTIONS.bound_relax_factor,
        honor_original_bounds=IPOPT_DIAGNOSTIC_OPTIONS.honor_original_bounds,
        inf_pr_output=IPOPT_DIAGNOSTIC_OPTIONS.inf_pr_output,
        reported_primal_within_constr_viol_tol=visible_primal_ok,
        reported_dual_within_dual_inf_tol=visible_dual_ok,
        barrier_mu_below_compl_inf_tol_not_complementarity=visible_mu_ok,
        visible_desired_checks_pass=solved_to_desired && visible_primal_ok && visible_dual_ok && visible_mu_ok,
        exact_complementarity_from_callback_available=false,
        diagnostic_note="inf_pr is original unscaled constraint violation because inf_pr_output=original; inf_du is scaled internal dual infeasibility; mu is barrier parameter, not exact complementarity.",
    )
end

function iteration_rows(; variant, case_label, scenario_label, solver_source, trace)
    rows = NamedTuple[]
    for row in trace.rows
        push!(
            rows,
            (
                variant=variant,
                case=case_label,
                scenario=scenario_label,
                solver_source=solver_source,
                alg_mod=row.alg_mod,
                iter=row.iter,
                objective=row.objective,
                inf_pr_original_unscaled=row.inf_pr,
                inf_du_internal_scaled=row.inf_du,
                mu_barrier_parameter=row.mu,
                lg_mu=row.lg_mu,
                step_norm=row.step_norm,
                regularization_size=row.regularization_size,
                lg_rg=row.lg_rg,
                alpha_du=row.alpha_du,
                alpha_pr=row.alpha_pr,
                ls=row.ls,
            ),
        )
    end
    return rows
end

function solve_own_diagnostic(data, case::OwnPowerModels.PowerCase, variant::String)
    trace = IpoptTrace()
    optimizer = optimizer_for_trace(trace)
    variant == "pm_like" || error("Unsupported variant $(variant). Expected \"pm_like\" (bus-injection was deleted; \"current\" targets are historical only).")
    model, ac_data, pg, qg, vm, va, branch_flows, _ = build_ac_opf_model(
        case; optimizer=optimizer, silent=true, fix_reference_angle=true, warm_start=:zero
    )
    attach_ipopt_trace!(model, trace)
    elapsed = @elapsed optimize!(model)
    result = OwnPowerModels.collect_ac_opf_result(model, case, ac_data, pg, qg, vm, va, branch_flows)
    return model, trace, result.objective, elapsed
end

function solve_powermodels_diagnostic(data)
    trace = IpoptTrace()
    optimizer = optimizer_for_trace(trace)
    pm_model = PowerModels.instantiate_model(deepcopy(data), PowerModels.ACPPowerModel, PowerModels.build_opf)
    JuMP.set_optimizer(pm_model.model, optimizer)
    attach_ipopt_trace!(pm_model.model, trace)
    elapsed = @elapsed pm_result = PowerModels.optimize_model!(pm_model)
    return pm_model.model, trace, Float64(get(pm_result, "objective", NaN)), elapsed
end

function load_solver_diag_targets()
    # "current" (bus-injection) config removed -- that formulation was
    # deleted; objective_mismatch_details_julia_current.csv is a frozen
    # historical file, not re-diagnosable now that build_ac_opf_model only
    # ever builds the arc-based model.
    configs = [
        (variant="pm_like", objective_path=joinpath(ROOT, "results", "own_vs_powermodels", "objective_mismatch_details_julia_pm_like.csv")),
    ]
    targets = NamedTuple[]
    seen = Set{Tuple{String, String, String}}()
    for cfg in configs
        isfile(cfg.objective_path) || continue
        df = DataFrame(CSV.File(cfg.objective_path))
        for row in eachrow(df)
            scenario_label = normalize_solver_diag_label(String(row.scenario))
            key = (cfg.variant, String(row.case), scenario_label)
            if !(key in seen)
                push!(targets, (variant=cfg.variant, case=String(row.case), scenario=scenario_label))
                push!(seen, key)
            end
        end
    end
    return targets
end

function notes_rows()
    return [
        (kind="option", name="tol", value=string(IPOPT_DIAGNOSTIC_OPTIONS.tol), meaning="Desired relative convergence tolerance; success also requires absolute dual, constraint-violation, and complementarity thresholds.", source="Ipopt Options documentation"),
        (kind="option", name="dual_inf_tol", value=string(IPOPT_DIAGNOSTIC_OPTIONS.dual_inf_tol), meaning="Desired absolute threshold for unscaled dual infeasibility in Ipopt termination test.", source="Ipopt Options documentation"),
        (kind="option", name="constr_viol_tol", value=string(IPOPT_DIAGNOSTIC_OPTIONS.constr_viol_tol), meaning="Desired absolute threshold for unscaled constraint and variable-bound violation.", source="Ipopt Options documentation"),
        (kind="option", name="compl_inf_tol", value=string(IPOPT_DIAGNOSTIC_OPTIONS.compl_inf_tol), meaning="Desired absolute threshold for unscaled complementarity. The JuMP callback does not expose exact complementarity directly.", source="Ipopt Options documentation"),
        (kind="option", name="acceptable_tol", value=string(IPOPT_DIAGNOSTIC_OPTIONS.acceptable_tol), meaning="Secondary acceptable relative convergence tolerance.", source="Ipopt Options documentation"),
        (kind="option", name="acceptable_iter", value=string(IPOPT_DIAGNOSTIC_OPTIONS.acceptable_iter), meaning="Number of consecutive acceptable iterates before acceptable-level termination.", source="Ipopt Options documentation"),
        (kind="option", name="bound_relax_factor", value=string(IPOPT_DIAGNOSTIC_OPTIONS.bound_relax_factor), meaning="Ipopt may relax variable bounds by this factor unless set to zero; the relaxation is limited by constr_viol_tol.", source="Ipopt Options documentation"),
        (kind="option", name="honor_original_bounds", value=string(IPOPT_DIAGNOSTIC_OPTIONS.honor_original_bounds), meaning="Whether Ipopt projects final points onto original variable bounds after bound relaxation.", source="Ipopt Options documentation"),
        (kind="option", name="inf_pr_output", value=string(IPOPT_DIAGNOSTIC_OPTIONS.inf_pr_output), meaning="With original, the callback inf_pr column is the original unscaled NLP constraint violation.", source="Ipopt Options documentation"),
        (kind="column", name="objective", value="", meaning="Unscaled objective value at the current point.", source="Ipopt Output documentation"),
        (kind="column", name="inf_pr", value="", meaning="Unscaled original constraint violation because inf_pr_output is original.", source="Ipopt Output documentation"),
        (kind="column", name="inf_du", value="", meaning="Scaled internal dual infeasibility; it is not directly comparable to inf_pr as the same physical residual.", source="Ipopt Output documentation"),
        (kind="column", name="lg_mu", value="", meaning="log10 of the barrier parameter mu.", source="Ipopt Output documentation"),
        (kind="column", name="step_norm", value="", meaning="Infinity norm of the primal step.", source="Ipopt Output documentation"),
        (kind="column", name="lg_rg", value="", meaning="log10 of the Hessian regularization term; NaN means no positive regularization value was reported.", source="Ipopt Output documentation"),
        (kind="column", name="alpha_du", value="", meaning="Dual step size.", source="Ipopt Output documentation"),
        (kind="column", name="alpha_pr", value="", meaning="Primal step size.", source="Ipopt Output documentation"),
        (kind="column", name="ls", value="", meaning="Number of backtracking line-search steps.", source="Ipopt Output documentation"),
        (kind="limitation", name="complementarity", value="not captured exactly", meaning="The intermediate callback exposes mu, not the final unscaled complementarity residual. This table labels mu separately to avoid overclaiming.", source="Ipopt.jl callback interface"),
    ]
end

function main()
    rows = NamedTuple[]
    iter_rows = NamedTuple[]
    pm_cache = Dict{Tuple{String, String}, Tuple{Any, IpoptTrace, Float64, Float64}}()

    for target in load_solver_diag_targets()
        println("Solver diagnostic $(target.variant) / $(target.case) / $(target.scenario)")
        data = scenario_data_for_solver_diag(target.case, target.scenario)
        case = OwnPowerModels.powermodels_to_powercase(deepcopy(data))

        own_model, own_trace, own_objective, own_elapsed = solve_own_diagnostic(data, case, target.variant)
        push!(rows, solver_info_row(
            variant=target.variant,
            case_label=target.case,
            scenario_label=target.scenario,
            solver_source="own_$(target.variant)",
            model=own_model,
            trace=own_trace,
            objective=own_objective,
            solve_time_sec=own_elapsed,
        ))
        append!(iter_rows, iteration_rows(
            variant=target.variant,
            case_label=target.case,
            scenario_label=target.scenario,
            solver_source="own_$(target.variant)",
            trace=own_trace,
        ))

        pm_key = (target.case, target.scenario)
        if !haskey(pm_cache, pm_key)
            pm_cache[pm_key] = solve_powermodels_diagnostic(data)
        end
        pm_model, pm_trace, pm_objective, pm_elapsed = pm_cache[pm_key]
        push!(rows, solver_info_row(
            variant=target.variant,
            case_label=target.case,
            scenario_label=target.scenario,
            solver_source="powermodels_reference",
            model=pm_model,
            trace=pm_trace,
            objective=pm_objective,
            solve_time_sec=pm_elapsed,
        ))
        append!(iter_rows, iteration_rows(
            variant=target.variant,
            case_label=target.case,
            scenario_label=target.scenario,
            solver_source="powermodels_reference",
            trace=pm_trace,
        ))
    end

    out_dir = joinpath(ROOT, "results", "own_vs_powermodels")
    final_path = joinpath(out_dir, "solver_diagnostics_objective_mismatch_julia.csv")
    iterations_path = joinpath(out_dir, "solver_diagnostics_objective_mismatch_ipopt_iterations_julia.csv")
    notes_path = joinpath(out_dir, "solver_diagnostics_ipopt_notes_julia.csv")
    CSV.write(final_path, DataFrame(rows))
    CSV.write(iterations_path, DataFrame(iter_rows))
    CSV.write(notes_path, DataFrame(notes_rows()))
    println("Wrote $(final_path)")
    println("Wrote $(iterations_path)")
    println("Wrote $(notes_path)")
end

main()
