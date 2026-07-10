using PowerModels
using Ipopt
using HiGHS


println("\n==============================")
println("RUNNING POWER FLOWS")
println("==============================")
println("Case path: ", case_path)

println("\nRunning AC Power Flow...")
result_ac_pf = solve_ac_pf(case_path, Ipopt.Optimizer)

println("\nAC PF status:")
println(result_ac_pf["termination_status"])

println("\nAC PF solve time:")
println(result_ac_pf["solve_time"])

println("\nAC PF summary:")
print_summary(result_ac_pf["solution"])


println("\n----------------------------------------")
println("Running DC Power Flow...")
result_dc_pf = solve_dc_pf(case_path, HiGHS.Optimizer)

println("\nDC PF status:")
println(result_dc_pf["termination_status"])

println("\nDC PF solve time:")
println(result_dc_pf["solve_time"])

println("\nDC PF summary:")
print_summary(result_dc_pf["solution"])