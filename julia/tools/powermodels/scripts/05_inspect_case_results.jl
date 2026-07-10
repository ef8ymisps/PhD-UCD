using PowerModels
using Ipopt
using HiGHS

println("Inspecting case:")
println(case_path)

# ------------------------------------------------------------
# Parse network data
# ------------------------------------------------------------

network_data = PowerModels.parse_file(case_path)

println("\n==============================")
println("CASE SUMMARY")
println("==============================")

println("Case name: ", get(network_data, "name", "unknown"))
println("Base MVA: ", network_data["baseMVA"])
println("Number of buses: ", length(network_data["bus"]))
println("Number of branches: ", length(network_data["branch"]))
println("Number of generators: ", length(network_data["gen"]))
println("Number of loads: ", length(network_data["load"]))

# ------------------------------------------------------------
# Run PF and OPF studies
# ------------------------------------------------------------

println("\nRunning AC Power Flow...")
result_ac_pf = solve_ac_pf(case_path, Ipopt.Optimizer)

println("\nRunning DC Power Flow...")
result_dc_pf = solve_dc_pf(case_path, HiGHS.Optimizer)

println("\nRunning AC Optimal Power Flow...")
result_ac_opf = solve_ac_opf(case_path, Ipopt.Optimizer)

println("\nRunning DC Optimal Power Flow...")
result_dc_opf = solve_dc_opf(case_path, HiGHS.Optimizer)

# ------------------------------------------------------------
# Solver status summary
# ------------------------------------------------------------

println("\n==============================")
println("SOLVER STATUS SUMMARY")
println("==============================")

println("AC PF status:  ", result_ac_pf["termination_status"])
println("DC PF status:  ", result_dc_pf["termination_status"])
println("AC OPF status: ", result_ac_opf["termination_status"])
println("DC OPF status: ", result_dc_opf["termination_status"])

println("\nAC PF solve time:  ", result_ac_pf["solve_time"])
println("DC PF solve time:  ", result_dc_pf["solve_time"])
println("AC OPF solve time: ", result_ac_opf["solve_time"])
println("DC OPF solve time: ", result_dc_opf["solve_time"])

# ------------------------------------------------------------
# Objective comparison
# ------------------------------------------------------------

println("\n==============================")
println("OBJECTIVE COMPARISON")
println("==============================")

if haskey(result_ac_opf, "objective")
    println("AC OPF objective: ", result_ac_opf["objective"])
end

if haskey(result_dc_opf, "objective")
    println("DC OPF objective: ", result_dc_opf["objective"])
end

if haskey(result_ac_opf, "objective") && haskey(result_dc_opf, "objective")
    println("Difference AC - DC: ", result_ac_opf["objective"] - result_dc_opf["objective"])
end

# ------------------------------------------------------------
# Voltage limit inspection
# ------------------------------------------------------------

println("\n==============================")
println("AC OPF VOLTAGE LIMIT CHECK")
println("==============================")

for bus_id in sort(collect(keys(network_data["bus"])); by = x -> parse(Int, x))

    bus_data = network_data["bus"][bus_id]
    bus_sol = result_ac_opf["solution"]["bus"][bus_id]

    vmin = bus_data["vmin"]
    vmax = bus_data["vmax"]

    if haskey(bus_sol, "vm")
        vm = bus_sol["vm"]

        status = "OK"

        if vm < vmin
            status = "LOW VOLTAGE VIOLATION"
        elseif vm > vmax
            status = "HIGH VOLTAGE VIOLATION"
        elseif abs(vm - vmin) < 1e-4
            status = "AT LOWER LIMIT"
        elseif abs(vm - vmax) < 1e-4
            status = "AT UPPER LIMIT"
        end

        println(
            "Bus ", bus_id,
            " | vm = ", round(vm, digits=5),
            " | limits = [", vmin, ", ", vmax, "]",
            " | ", status
        )
    end
end

# ------------------------------------------------------------
# Reactive power inspection: AC PF vs AC OPF
# ------------------------------------------------------------

println("\n==============================")
println("REACTIVE POWER CHECK")
println("==============================")

for gen_id in sort(collect(keys(network_data["gen"])); by = x -> parse(Int, x))

    gen_data = network_data["gen"][gen_id]

    qmin = gen_data["qmin"]
    qmax = gen_data["qmax"]

    has_pf_qg = haskey(result_ac_pf["solution"]["gen"][gen_id], "qg")
    has_opf_qg = haskey(result_ac_opf["solution"]["gen"][gen_id], "qg")

    qg_pf = has_pf_qg ? result_ac_pf["solution"]["gen"][gen_id]["qg"] : NaN
    qg_opf = has_opf_qg ? result_ac_opf["solution"]["gen"][gen_id]["qg"] : NaN

    pf_status = "OK"
    opf_status = "OK"

    if has_pf_qg
        if qg_pf < qmin
            pf_status = "PF BELOW QMIN"
        elseif qg_pf > qmax
            pf_status = "PF ABOVE QMAX"
        end
    else
        pf_status = "NO PF QG"
    end

    if has_opf_qg
        if qg_opf < qmin
            opf_status = "OPF BELOW QMIN"
        elseif qg_opf > qmax
            opf_status = "OPF ABOVE QMAX"
        end
    else
        opf_status = "NO OPF QG"
    end

    println(
        "Gen ", gen_id,
        " | qmin = ", qmin,
        " | qmax = ", qmax,
        " | AC PF qg = ", round(qg_pf, digits=5),
        " | ", pf_status,
        " | AC OPF qg = ", round(qg_opf, digits=5),
        " | ", opf_status
    )
end

# ------------------------------------------------------------
# Active power dispatch comparison
# ------------------------------------------------------------

println("\n==============================")
println("GENERATOR ACTIVE POWER DISPATCH")
println("==============================")

for gen_id in sort(collect(keys(network_data["gen"])); by = x -> parse(Int, x))

    pg_original = network_data["gen"][gen_id]["pg"]

    pg_ac_pf = haskey(result_ac_pf["solution"]["gen"][gen_id], "pg") ?
        result_ac_pf["solution"]["gen"][gen_id]["pg"] : NaN

    pg_dc_pf = haskey(result_dc_pf["solution"]["gen"][gen_id], "pg") ?
        result_dc_pf["solution"]["gen"][gen_id]["pg"] : NaN

    pg_ac_opf = haskey(result_ac_opf["solution"]["gen"][gen_id], "pg") ?
        result_ac_opf["solution"]["gen"][gen_id]["pg"] : NaN

    pg_dc_opf = haskey(result_dc_opf["solution"]["gen"][gen_id], "pg") ?
        result_dc_opf["solution"]["gen"][gen_id]["pg"] : NaN

    println(
        "Gen ", gen_id,
        " | original pg = ", round(pg_original, digits=5),
        " | AC PF pg = ", round(pg_ac_pf, digits=5),
        " | DC PF pg = ", round(pg_dc_pf, digits=5),
        " | AC OPF pg = ", round(pg_ac_opf, digits=5),
        " | DC OPF pg = ", round(pg_dc_opf, digits=5)
    )
end

# ------------------------------------------------------------
# Branch flow comparison
# ------------------------------------------------------------

println("\n==============================")
println("AC OPF BRANCH FLOW CHECK")
println("==============================")

for branch_id in sort(collect(keys(network_data["branch"])); by = x -> parse(Int, x))

    branch_data = network_data["branch"][branch_id]

    if !haskey(result_ac_opf["solution"]["branch"], branch_id)
        println("Branch ", branch_id, " | no AC OPF branch solution found")
        continue
    end

    branch_sol = result_ac_opf["solution"]["branch"][branch_id]

    rate_a = branch_data["rate_a"]

    pf = haskey(branch_sol, "pf") ? branch_sol["pf"] : NaN
    pt = haskey(branch_sol, "pt") ? branch_sol["pt"] : NaN
    qf = haskey(branch_sol, "qf") ? branch_sol["qf"] : NaN
    qt = haskey(branch_sol, "qt") ? branch_sol["qt"] : NaN

    sf = sqrt(pf^2 + qf^2)
    st = sqrt(pt^2 + qt^2)

    max_flow = max(sf, st)

    status = "OK"

    if rate_a > 0 && max_flow > rate_a
        status = "THERMAL LIMIT VIOLATION"
    elseif rate_a > 0 && abs(max_flow - rate_a) < 1e-4
        status = "AT THERMAL LIMIT"
    end

    println(
        "Branch ", branch_id,
        " | ", branch_data["f_bus"], " -> ", branch_data["t_bus"],
        " | rate_a = ", rate_a,
        " | sf = ", round(sf, digits=5),
        " | st = ", round(st, digits=5),
        " | max = ", round(max_flow, digits=5),
        " | ", status
    )
end

# ------------------------------------------------------------
# Print compact summaries from PowerModels
# ------------------------------------------------------------

println("\n==============================")
println("AC PF SOLUTION SUMMARY")
println("==============================")
print_summary(result_ac_pf["solution"])

println("\n==============================")
println("DC PF SOLUTION SUMMARY")
println("==============================")
print_summary(result_dc_pf["solution"])

println("\n==============================")
println("AC OPF SOLUTION SUMMARY")
println("==============================")
print_summary(result_ac_opf["solution"])

println("\n==============================")
println("DC OPF SOLUTION SUMMARY")
println("==============================")
print_summary(result_dc_opf["solution"])