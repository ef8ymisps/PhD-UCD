# N-1 branch and generator contingency screening on case118, Julia side.
#
# Per the tool decision (docs/formulations/security_constraints.md):
# post-contingency PF uses PowerModels.jl's native compute_ac_pf
# (Newton-Raphson), not the own model's PF solver, not Ipopt. Base-case
# dispatch comes from the own AC-OPF (Ipopt). Output schema matches the
# Python pypowsybl_security_validation.jl N-1 path column-for-column so
# case118 results are directly comparable across own-Python/pypowsybl/Julia.

include(joinpath(@__DIR__, "..", "src", "OwnPowerModels.jl"))

using .OwnPowerModels
using CSV
using DataFrames
using Ipopt
using JuMP
using PowerModels

function main()
    case_path = joinpath(@__DIR__, "..", "..", "..", "data", "raw", "pglib", "matpower_m", "base_case", "pglib_opf_case118_ieee.m")
    output_dir = joinpath(@__DIR__, "..", "..", "..", "results", "own_julia", "security")
    mkpath(output_dir)

    data = PowerModels.parse_file(case_path)
    case = OwnPowerModels.powermodels_to_powercase(deepcopy(data))

    println("Solving base AC OPF for ", case.name)
    ac_optimizer = optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0)
    ac_opf_result = solve_ac_opf_own(case; optimizer=ac_optimizer, silent=true)
    println("  status=", ac_opf_result.termination_status, " objective=", ac_opf_result.objective)
    if !(string(ac_opf_result.termination_status) in ("LOCALLY_SOLVED", "OPTIMAL"))
        error("Base AC OPF did not solve for $(case.name); cannot screen contingencies.")
    end

    println("PowerModels.compute_ac_pf N-1 screening (branches + generators):")
    results = run_n1_screening(case, data, ac_opf_result; include_branches=true, include_generators=true)
    n_secure = sum(results.secure)
    println("  Screened ", nrow(results), " contingencies; secure=", n_secure, "; insecure=", nrow(results) - n_secure)
    for element_type in ("branch", "generator")
        subset = results[results.element_type .== element_type, :]
        n_secure_type = isempty(subset) ? 0 : sum(subset.secure)
        println("    ", element_type, ": ", nrow(subset), " screened, ", n_secure_type, " secure")
    end

    results_path = joinpath(output_dir, "n1_julia_compute_pf_case118_contingencies.csv")
    CSV.write(results_path, results)
    println("  Saved results to: ", results_path)
end

main()
