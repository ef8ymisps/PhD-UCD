include("C:/Users/Efthymis/OneDrive - University College Dublin/power-systems-tools-learning/julia/own_models/src/data/case_loader.jl")
include("C:/Users/Efthymis/OneDrive - University College Dublin/power-systems-tools-learning/julia/own_models/src/pf/dc_power_flow.jl")

name = "pglib_opf_case5_pjm.m"
case_path = joinpath("C:/Users/Efthymis/OneDrive - University College Dublin/power-systems-tools-learning/julia/own_models", "data", "raw", "pglib-opf", name)

case = load_powercase_from_powermodels(case_path)

println("\n==============================")
println("OWN DC POWER FLOW")
println("==============================")

println("Case name: ", case.name)
println("Base MVA: ", case.baseMVA)
println("Number of buses: ", length(case.buses))
println("Number of branches: ", length(case.branches))
println("Slack bus: ", case.slack_bus_id)

result = solve_dc_power_flow(case)

println("\nBus voltage angles:")
for bus in case.buses
    println("Bus ", bus.id, " | theta = ", result.theta[bus.index])
end

println("\nBranch active power flows:")
for branch in case.branches
    if haskey(result.branch_flows, branch.id)
        println(
            "Branch ", branch.id,
            " | ", branch.from_bus, " -> ", branch.to_bus,
            " | pf = ", result.branch_flows[branch.id]
        )
    end
end

println("\nSlack injection:")
println(result.slack_injection)