# Generator-outage analog of 83_run_dc_scopf_monolithic_branches.jl --
# solves the monolithic preventive DC-SCOPF for K^G = the FINAL generator-
# outage contingency list per system -- topologies/<case>/
# final_k_generators.csv (every active generator's own outage, minus
# infeasible-alone minus conflicting-pair exclusions; see
# python/own_models/scripts/72_run_generator_final_contingency_list.py,
# the single canonical source both languages read). No islanding
# exclusion here -- a generator outage never disconnects the network
# (only branches can); see 72's own module docstring for the full
# argument.
#
# Evidence written to results/scopf/dc/<case>/monolithic_generators/ --
# same three-file schema as monolithic_branches, `language=julia`
# distinguishing these rows from Python's own in the SAME shared files,
# and from monolithic_branches' own rows via the `approach` column.

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

"""Generator ids in the final K^G, read straight out of
topologies/<label>/final_k_generators.csv -- no re-derivation."""
function final_k_generator_ids(label::String)
    path = joinpath(TOPOLOGIES_ROOT, label, "final_k_generators.csv")
    isfile(path) || error("$path not found -- run python/own_models/scripts/72_run_generator_final_contingency_list.py for $label first.")
    return Set(string.(CSV.read(path, DataFrame).generator_id))
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
    final_ids = final_k_generator_ids(label)
    K = [k for k in generate_generator_n1_contingencies(case) if k.element_id in final_ids]
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
        case_name=label, formulation="dc", approach="monolithic_generators",
        termination_status=result.termination_status, objective=result.objective,
        n_buses=length(case.buses), n_branches=length(case.branches), n_generators=length(case.generators),
        n_contingencies_total=length(K), n_contingencies_branch=0, n_contingencies_generator=length(K),
        n_contingencies_skipped=length(skipped), n_variables=num_variables(model),
        n_binary_variables=n_binary_variables, n_constraints=total_constraint_count(model),
        solve_iterations=solve_iteration_count(model), master_solve_time_s=t_solve, total_runtime_s=t_total,
    )
    stage_rows = scopf_stage_timing_rows(
        label, "monolithic_generators",
        Dict(
            "model_build_including_anchor_solve" => t_build, "master_solve" => t_solve,
            "solver_reported_time" => MOI.get(model, MOI.SolveTimeSec()), "total_runtime" => t_total,
        ),
    )
    output_dir = joinpath(RESULTS_ROOT, "dc", label, "monolithic_generators")
    write_scopf_evidence!(output_dir; summary_row=summary, contingency_rows=[], stage_timing_rows=stage_rows)

    @printf(
        "%-10s |K^G|=%3d status=%-12s objective=%12s build=%6.2fs solve=%6.2fs mem=%6.1fMB\n",
        label, length(K), string(result.termination_status),
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
