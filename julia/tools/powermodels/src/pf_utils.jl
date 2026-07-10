using PowerModels
using Ipopt
using HiGHS

function run_ac_power_flow(case_path)
    println("\nRunning AC Power Flow...")
    result = solve_ac_pf(case_path, Ipopt.Optimizer)
    PowerModels.print_summary(result["solution"])
    return result
end
function run_dc_power_flow(case_path)
    println("\nRunning DC Power Flow...")
    result = solve_dc_pf(case_path, HiGHS.Optimizer)
    PowerModels.print_summary(result["solution"])
    return result
end

function print_pf_summary(result_ac_pf, result_dc_pf)
    println("\n==============================")
    println("POWER FLOW SUMMARY")
    println("==============================")

    println("AC PF status: ", result_ac_pf["termination_status"])
    println("AC PF solve time: ", result_ac_pf["solve_time"])

    println("DC PF status: ", result_dc_pf["termination_status"])
    println("DC PF solve time: ", result_dc_pf["solve_time"])

    println("\nAC PF solution:")
    print_summary(result_ac_pf["solution"])

    println("\nDC PF solution:")
    print_summary(result_dc_pf["solution"])
end