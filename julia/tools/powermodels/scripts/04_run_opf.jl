using PowerModels
using Ipopt
using HiGHS



println("\n==============================")
println("RUNNING OPTIMAL POWER FLOWS")
println("==============================")
println("Case path: ", case_path)

println("\nRunning AC Optimal Power Flow...")
result_ac_opf = solve_ac_opf(case_path, Ipopt.Optimizer)

println("\nAC OPF status:")
println(result_ac_opf["termination_status"])

println("\nAC OPF solve time:")
println(result_ac_opf["solve_time"])

println("\nAC OPF objective:")
println(result_ac_opf["objective"])

println("\nAC OPF summary:")
print_summary(result_ac_opf["solution"])


println("\n----------------------------------------")
println("Running DC Optimal Power Flow...")
result_dc_opf = solve_dc_opf(case_path, HiGHS.Optimizer)

println("\nDC OPF status:")
println(result_dc_opf["termination_status"])

println("\nDC OPF solve time:")
println(result_dc_opf["solve_time"])

println("\nDC OPF objective:")
println(result_dc_opf["objective"])

println("\nDC OPF summary:")
print_summary(result_dc_opf["solution"])