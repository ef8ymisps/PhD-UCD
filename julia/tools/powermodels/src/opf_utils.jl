using PowerModels
using Ipopt
using HiGHS

function run_ac_optimal_power_flow(case_path)
    println("\nRunning AC Optimal Power Flow...")
    result = solve_ac_opf(case_path, Ipopt.Optimizer)
    PowerModels.print_summary(result["solution"])
    return result
end

function run_dc_optimal_power_flow(case_path)
    println("\nRunning DC Optimal Power Flow...")
    result = solve_dc_opf(case_path, HiGHS.Optimizer)
    PowerModels.print_summary(result["solution"])
    return result
end

function print_opf_summary(result_ac_opf, result_dc_opf)
    println("\n==============================")
    println("OPTIMAL POWER FLOW SUMMARY")
    println("==============================")

    println("AC OPF status: ", result_ac_opf["termination_status"])
    println("AC OPF solve time: ", result_ac_opf["solve_time"])
    println("AC OPF objective: ", result_ac_opf["objective"])

    println("DC OPF status: ", result_dc_opf["termination_status"])
    println("DC OPF solve time: ", result_dc_opf["solve_time"])
    println("DC OPF objective: ", result_dc_opf["objective"])

    println("\nAC OPF solution:")
    print_summary(result_ac_opf["solution"])

    println("\nDC OPF solution:")
    print_summary(result_dc_opf["solution"])
end