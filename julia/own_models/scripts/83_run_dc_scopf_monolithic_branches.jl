# Solves the monolithic preventive DC-SCOPF for K = the FINAL, already-
# corrected contingency list per system -- topologies/<case>/final_k.csv
# (Total branch/transformer contingencies minus islanding minus
# infeasible-alone minus conflicting-pair exclusions; see
# python/own_models/scripts/62_run_final_contingency_list.py, the single
# canonical source both languages read -- no re-deriving the exclusions
# here). Islanding contingencies are ALWAYS out of scope for preventive
# SCOPF (no load-shedding mechanism to represent a stranded island), so
# that is not a separate "approach" variant worth naming -- just
# "monolithic_branches" (LINE AND TRANSFORMER outages together; see 81's
# own note on why transformers are modeled identically to lines for DC
# purposes).
#
# Evidence written to results/scopf/dc/<case>/monolithic_branches/ -- same
# three-file schema as 73/75/76/79/80/81, now also including
# peak_memory_mb (Windows GetProcessMemoryInfo, see scopf_evidence.jl) and
# an ACTUALLY populated solve_iterations (MOI.SimplexIterations(), a
# native JuMP/HiGHS attribute -- unlike the Python port, which has no such
# structured attribute and instead regex-parses HiGHS's console log; see
# python/own_models/src/analysis/scopf_evidence.py and
# own_models/src/opf/solver_iterations.py's own docstring for why).
#
# stage_timings.csv's "master_solve" is the wall-clock around the whole
# optimize!(model) call; "solver_reported_time" (MOI.SolveTimeSec(), a
# native JuMP/HiGHS attribute) is HiGHS's OWN reported time for the same
# call, recorded ALONGSIDE it -- on the Python side these two differ by
# ~10-20x at 118_ieee's scale (Pyomo's own model-to-HiGHS translation
# overhead dominating over actual solver work); on the Julia side JuMP
# does most of that translation eagerly, during model BUILD, not at
# optimize! time, so the two are expected to already be close here -- this
# pair of numbers is what actually shows that difference, not a guess.

include(joinpath(@__DIR__, "..", "src", "OwnPowerModels.jl"))
using .OwnPowerModels
using JuMP
using MathOptInterface
using PowerModels
using CSV
using DataFrames
using Printf

const MOI = MathOptInterface

PowerModels.silence()

const CASE_DIR = normpath(joinpath(@__DIR__, "..", "..", "..", "data", "raw", "pglib", "matpower_m", "base_case"))
const RESULTS_ROOT = normpath(joinpath(@__DIR__, "..", "..", "..", "results", "scopf"))
const TOPOLOGIES_ROOT = normpath(joinpath(@__DIR__, "..", "..", "..", "topologies"))

load_case_file(filename) = OwnPowerModels.powermodels_to_powercase(deepcopy(PowerModels.parse_file(joinpath(CASE_DIR, filename))))

"""Branch ids in the final K, read straight out of
topologies/<label>/final_k.csv -- no re-derivation."""
function final_k_branch_ids(label::String)
    path = joinpath(TOPOLOGIES_ROOT, label, "final_k.csv")
    isfile(path) || error("$path not found -- run python/own_models/scripts/62_run_final_contingency_list.py for $label first.")
    return Set(string.(CSV.read(path, DataFrame).branch_id))
end

"""The single canonical anchor dispatch every language's DC-SCOPF build
must derive its automatic-response weights from -- see
python/own_models/scripts/75_run_anchor_pg.py's own module docstring for
why this is required (not just convenient) for correctness on an
LP-degenerate anchor like 60nordic's."""
function anchor_pg(label::String)
    path = joinpath(TOPOLOGIES_ROOT, label, "anchor_pg.csv")
    isfile(path) || error("$path not found -- run python/own_models/scripts/75_run_anchor_pg.py for $label first.")
    df = CSV.read(path, DataFrame)
    return Dict(string(row.generator_id) => Float64(row.pg) for row in eachrow(df))
end

"""Best-effort solver iteration count via JuMP/HiGHS's own native MOI
attribute -- SimplexIterations if any ran, else BarrierIterations (HiGHS'
own IPM path), else NaN if the model was solved entirely by presolve (both
attributes legitimately 0) or the attribute isn't supported."""
function solve_iteration_count(model::Model)
    try
        simplex = MOI.get(model, MOI.SimplexIterations())
        simplex > 0 && return Float64(simplex)
        barrier = MOI.get(model, MOI.BarrierIterations())
        return Float64(max(simplex, barrier))
    catch
        return NaN
    end
end

function run_case(label::String, filename::String)
    case = load_case_file(filename)
    final_ids = final_k_branch_ids(label)
    K = [k for k in generate_branch_n1_contingencies(case) if k.element_id in final_ids]
    n_line = count(k -> branch_equipment_type(case, first(b for b in case.branches if b.id == k.element_id)) == "line", K)
    n_trans = length(K) - n_line
    anchor = anchor_pg(label)

    t_total_start = time()
    t0 = time()
    model, data, theta, pg, Pf, _constraints, skipped = build_dc_scopf_model(case; silent=true, contingencies=K, anchor_pg=anchor)
    t_build = time() - t0
    t0 = time()
    optimize!(model)
    t_solve = time() - t0
    result = OwnPowerModels.collect_dc_opf_result(model, case, data, theta, pg, Pf)
    t_total = time() - t_total_start

    n_binary_variables = count(is_binary, all_variables(model))
    summary = scopf_summary_row(;
        case_name=label, formulation="dc", approach="monolithic_branches",
        termination_status=result.termination_status, objective=result.objective,
        n_buses=length(case.buses), n_branches=length(case.branches), n_generators=length(case.generators),
        n_contingencies_total=length(K), n_contingencies_branch=length(K), n_contingencies_generator=0,
        n_contingencies_skipped=length(skipped), n_variables=num_variables(model),
        n_binary_variables=n_binary_variables, n_constraints=total_constraint_count(model),
        solve_iterations=solve_iteration_count(model), master_solve_time_s=t_solve, total_runtime_s=t_total,
    )
    # NOT scopf_contingency_rows -- contingencies.csv would be entirely
    # redundant with topologies/<system>/final_k.csv and
    # contingency_screening.csv here (both written ONCE, not per-language,
    # by 62_run_final_contingency_list.py) and worse: status/skip_reason
    # are always constant ("enforced"/blank), since K is already the
    # pre-filtered final_k, and separately writing it from BOTH this
    # script and Python's own into the SAME shared file produced two
    # confusing near-identical blocks (same branches, differently
    # formatted ids -- "outage::line::2" here vs "branch_2" there) with no
    # language column to explain why. That schema stays available in
    # scopf_evidence.jl's own scopf_contingency_rows for whenever a real
    # decomposition approach needs to track PER-CONTINGENCY enforced/
    # filtered status that actually varies -- just not exercised here.
    stage_rows = scopf_stage_timing_rows(
        label, "monolithic_branches",
        Dict(
            "model_build_including_anchor_solve" => t_build, "master_solve" => t_solve,
            "solver_reported_time" => MOI.get(model, MOI.SolveTimeSec()), "total_runtime" => t_total,
        ),
    )
    output_dir = joinpath(RESULTS_ROOT, "dc", label, "monolithic_branches")
    write_scopf_evidence!(output_dir; summary_row=summary, contingency_rows=[], stage_timing_rows=stage_rows)

    @printf(
        "%-10s |K|=%3d (lines=%3d, trans=%2d) status=%-12s objective=%12s build=%6.2fs solve=%6.2fs mem=%6.1fMB\n",
        label, length(K), n_line, n_trans, string(result.termination_status),
        isnan(result.objective) ? "NaN" : @sprintf("%.4f", result.objective), t_build, t_solve,
        coalesce(peak_memory_mb(), NaN),
    )
end

function main()
    cases = [
        ("5_pjm", "pglib_opf_case5_pjm.m"), ("14_ieee", "pglib_opf_case14_ieee.m"),
        ("30_ieee", "pglib_opf_case30_ieee.m"), ("118_ieee", "pglib_opf_case118_ieee.m"),
        ("60nordic", "case60nordic.m"),
    ]
    for (label, filename) in cases
        run_case(label, filename)
    end
end

main()
