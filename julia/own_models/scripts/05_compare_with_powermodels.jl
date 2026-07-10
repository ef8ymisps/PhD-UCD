include("../src/OwnPowerModels.jl")

using .OwnPowerModels
using PowerModels
using DataFrames
using CSV

function project_root()
    return normpath(joinpath(@__DIR__, "..", "..", ".."))
end

function arg_value(name::String, default::String)
    prefix = name * "="
    for arg in ARGS
        if startswith(arg, prefix)
            return arg[length(prefix)+1:end]
        end
    end
    return default
end

root = project_root()
case_path = normpath(joinpath(root, arg_value("--case", "data/raw/pglib/matpower_m/pglib_opf_case14_ieee.m")))
output_path = normpath(joinpath(root, arg_value("--output", "results/own_vs_powermodels_compute_ac_pf_summary.csv")))

println("Comparing own fixed-type AC Newton PF against PowerModels.compute_ac_pf")
println("Case: ", case_path)

data = PowerModels.parse_file(case_path)
case = OwnPowerModels.load_powercase_from_powermodels(case_path)

comparison = OwnPowerModels.compare_own_fixed_ac_pf_with_powermodels_compute(
    case,
    data;
    own_kwargs = Dict{Symbol,Any}(:tol => 1e-8, :max_iter => 50),
    nlsolve_kwargs = Dict{Symbol,Any}(:method => :newton, :flat_start => false, :ftol => 1e-8, :iterations => 400),
    start_mode = :powermodels_default,
    calc_branch_flows = true,
)

summary = DataFrame([comparison.summary])
mkpath(dirname(output_path))
CSV.write(output_path, summary)

println(summary)
println("Wrote summary: ", output_path)
