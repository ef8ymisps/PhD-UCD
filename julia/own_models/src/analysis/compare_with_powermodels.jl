# ============================================================
# Comparison utilities: OwnPowerModels vs PowerModels.jl
# ============================================================

struct ComparisonSummary
    model_name::String
    objective_diff::Float64
    max_pg_diff::Float64
    max_qg_diff::Float64
    max_vm_diff::Float64
    max_va_diff::Float64
    max_pf_diff::Float64
    max_qf_diff::Float64
end

function _safe_max(values)
    return isempty(values) ? Inf : maximum(values)
end

function _pm_solution(pm_result, section)
    return get(pm_result["solution"], section, Dict())
end

function _print_header(title)
    println("\n==============================")
    println(title)
    println("==============================")
end

# ------------------------------------------------------------
# DC PF comparison
# ------------------------------------------------------------

function compare_dc_pf_with_powermodels(case, own_dc_pf, pm_dc_pf; verbose=true)
    pm_bus = _pm_solution(pm_dc_pf, "bus")
    pm_branch = _pm_solution(pm_dc_pf, "branch")

    theta_diffs = Float64[]
    pf_diffs = Float64[]

    if verbose
        _print_header("DC PF BUS ANGLE COMPARISON")
    end

    for bus in case.buses
        bus_id = bus.id

        if !haskey(pm_bus, bus_id)
            continue
        end

        own_theta = own_dc_pf.theta[bus.index]
        pm_theta = pm_bus[bus_id]["va"]
        diff = own_theta - pm_theta

        push!(theta_diffs, abs(diff))

        if verbose
            println(
                "Bus ", bus_id,
                " | own = ", round(own_theta, digits=6),
                " | PowerModels = ", round(pm_theta, digits=6),
                " | diff = ", round(diff, digits=6)
            )
        end
    end

    if verbose
        _print_header("DC PF BRANCH FLOW COMPARISON")
    end

    for branch in case.branches
        branch_id = branch.id

        if !haskey(own_dc_pf.branch_flows, branch_id) || !haskey(pm_branch, branch_id)
            continue
        end

        own_pf = own_dc_pf.branch_flows[branch_id]
        pm_pf = pm_branch[branch_id]["pf"]
        diff = own_pf - pm_pf

        push!(pf_diffs, abs(diff))

        if verbose
            println(
                "Branch ", branch_id,
                " | own pf = ", round(own_pf, digits=6),
                " | PowerModels pf = ", round(pm_pf, digits=6),
                " | diff = ", round(diff, digits=6)
            )
        end
    end

    summary = ComparisonSummary(
        "DC PF",
        NaN,
        NaN,
        NaN,
        NaN,
        _safe_max(theta_diffs),
        _safe_max(pf_diffs),
        NaN
    )

    if verbose
        _print_header("DC PF FINAL DIAGNOSTIC")
        println("Max bus angle diff = ", summary.max_va_diff)
        println("Max branch pf diff = ", summary.max_pf_diff)
    end

    return summary
end

# ------------------------------------------------------------
# DC OPF comparison
# ------------------------------------------------------------

function compare_dc_opf_with_powermodels(case, own_dc_opf, pm_dc_opf; verbose=true)
    pm_gen = _pm_solution(pm_dc_opf, "gen")
    pm_bus = _pm_solution(pm_dc_opf, "bus")
    pm_branch = _pm_solution(pm_dc_opf, "branch")

    pg_diffs = Float64[]
    theta_diffs = Float64[]
    pf_diffs = Float64[]

    objective_diff = abs(own_dc_opf.objective - pm_dc_opf["objective"])

    if verbose
        _print_header("DC OPF OBJECTIVE COMPARISON")
        println("Own objective = ", own_dc_opf.objective)
        println("PowerModels objective = ", pm_dc_opf["objective"])
        println("Difference = ", own_dc_opf.objective - pm_dc_opf["objective"])
    end

    if verbose
        _print_header("DC OPF GENERATOR DISPATCH COMPARISON")
    end

    for gen in case.generators
        gen_id = gen.id

        if !haskey(own_dc_opf.pg, gen_id) || !haskey(pm_gen, gen_id)
            continue
        end

        own_pg = own_dc_opf.pg[gen_id]
        pm_pg = pm_gen[gen_id]["pg"]
        diff = own_pg - pm_pg

        push!(pg_diffs, abs(diff))

        if verbose
            println(
                "Gen ", gen_id,
                " | own pg = ", round(own_pg, digits=6),
                " | PowerModels pg = ", round(pm_pg, digits=6),
                " | diff = ", round(diff, digits=6)
            )
        end
    end

    if verbose
        _print_header("DC OPF BUS ANGLE COMPARISON")
    end

    for bus in case.buses
        bus_id = bus.id

        if !haskey(own_dc_opf.theta, bus_id) || !haskey(pm_bus, bus_id)
            continue
        end

        own_theta = own_dc_opf.theta[bus_id]
        pm_theta = pm_bus[bus_id]["va"]
        diff = own_theta - pm_theta

        push!(theta_diffs, abs(diff))

        if verbose
            println(
                "Bus ", bus_id,
                " | own = ", round(own_theta, digits=6),
                " | PowerModels = ", round(pm_theta, digits=6),
                " | diff = ", round(diff, digits=6)
            )
        end
    end

    if verbose
        _print_header("DC OPF BRANCH FLOW COMPARISON")
    end

    for branch in case.branches
        branch_id = branch.id

        if !haskey(own_dc_opf.branch_flows, branch_id) || !haskey(pm_branch, branch_id)
            continue
        end

        own_pf = own_dc_opf.branch_flows[branch_id]
        pm_pf = pm_branch[branch_id]["pf"]
        diff = own_pf - pm_pf

        push!(pf_diffs, abs(diff))

        if verbose
            println(
                "Branch ", branch_id,
                " | own pf = ", round(own_pf, digits=6),
                " | PowerModels pf = ", round(pm_pf, digits=6),
                " | diff = ", round(diff, digits=6)
            )
        end
    end

    summary = ComparisonSummary(
        "DC OPF",
        objective_diff,
        _safe_max(pg_diffs),
        NaN,
        NaN,
        _safe_max(theta_diffs),
        _safe_max(pf_diffs),
        NaN
    )

    if verbose
        _print_header("DC OPF FINAL DIAGNOSTIC")
        println("Max Pg diff = ", summary.max_pg_diff)
        println("Max Va diff = ", summary.max_va_diff)
        println("Max Pf diff = ", summary.max_pf_diff)
        println("Objective diff = ", summary.objective_diff)

        if summary.max_pg_diff < 1e-5 &&
           summary.max_pf_diff < 1e-5 &&
           summary.objective_diff < 1e-4
            println("Result: own DC OPF matches PowerModels closely.")
        else
            println("Result: differences detected.")
        end
    end

    return summary
end

# ------------------------------------------------------------
# AC PF comparison
# ------------------------------------------------------------

function compare_ac_pf_with_powermodels(case, own_ac_pf, pm_ac_pf; verbose=true)
    pm_bus = _pm_solution(pm_ac_pf, "bus")
    pm_branch = _pm_solution(pm_ac_pf, "branch")

    vm_diffs = Float64[]
    va_diffs = Float64[]
    pf_diffs = Float64[]
    qf_diffs = Float64[]

    if verbose
        _print_header("AC PF VOLTAGE COMPARISON")
    end

    for bus in case.buses
        bus_id = bus.id

        if !haskey(pm_bus, bus_id)
            continue
        end

        own_vm = own_ac_pf.vm[bus.index]
        own_va = own_ac_pf.va[bus.index]

        pm_vm = pm_bus[bus_id]["vm"]
        pm_va = pm_bus[bus_id]["va"]

        push!(vm_diffs, abs(own_vm - pm_vm))
        push!(va_diffs, abs(own_va - pm_va))

        if verbose
            println(
                "Bus ", bus_id,
                " | own vm = ", round(own_vm, digits=6),
                " | PowerModels vm = ", round(pm_vm, digits=6),
                " | Δvm = ", round(own_vm - pm_vm, digits=6),
                " | own va = ", round(own_va, digits=6),
                " | PowerModels va = ", round(pm_va, digits=6),
                " | Δva = ", round(own_va - pm_va, digits=6)
            )
        end
    end

    if verbose
        _print_header("AC PF BRANCH FLOW COMPARISON")
    end

    for branch in case.branches
        branch_id = branch.id

        if !haskey(own_ac_pf.branch_flows, branch_id) || !haskey(pm_branch, branch_id)
            continue
        end

        own_flow = own_ac_pf.branch_flows[branch_id]
        pm_flow = pm_branch[branch_id]

        own_pf = own_flow["pf"]
        own_qf = own_flow["qf"]

        pm_pf = pm_flow["pf"]
        pm_qf = pm_flow["qf"]

        push!(pf_diffs, abs(own_pf - pm_pf))
        push!(qf_diffs, abs(own_qf - pm_qf))

        if verbose
            println(
                "Branch ", branch_id,
                " | own pf = ", round(own_pf, digits=6),
                " | PowerModels pf = ", round(pm_pf, digits=6),
                " | Δpf = ", round(own_pf - pm_pf, digits=6),
                " | own qf = ", round(own_qf, digits=6),
                " | PowerModels qf = ", round(pm_qf, digits=6),
                " | Δqf = ", round(own_qf - pm_qf, digits=6)
            )
        end
    end

    summary = ComparisonSummary(
        "AC PF",
        NaN,
        NaN,
        NaN,
        _safe_max(vm_diffs),
        _safe_max(va_diffs),
        _safe_max(pf_diffs),
        _safe_max(qf_diffs)
    )

    if verbose
        _print_header("AC PF FINAL DIAGNOSTIC")
        println("Max Vm diff = ", summary.max_vm_diff)
        println("Max Va diff = ", summary.max_va_diff)
        println("Max Pf diff = ", summary.max_pf_diff)
        println("Max Qf diff = ", summary.max_qf_diff)
    end

    return summary
end

# ------------------------------------------------------------
# AC OPF comparison
# ------------------------------------------------------------

function compare_ac_opf_with_powermodels(case, own_ac_opf, pm_ac_opf; verbose=true)
    pm_gen = _pm_solution(pm_ac_opf, "gen")
    pm_bus = _pm_solution(pm_ac_opf, "bus")
    pm_branch = _pm_solution(pm_ac_opf, "branch")

    pg_diffs = Float64[]
    qg_diffs = Float64[]
    vm_diffs = Float64[]
    va_diffs = Float64[]
    pf_diffs = Float64[]
    qf_diffs = Float64[]

    objective_diff = abs(own_ac_opf.objective - pm_ac_opf["objective"])

    if verbose
        _print_header("AC OPF OBJECTIVE COMPARISON")
        println("Own objective = ", own_ac_opf.objective)
        println("PowerModels objective = ", pm_ac_opf["objective"])
        println("Difference = ", own_ac_opf.objective - pm_ac_opf["objective"])
    end

    if verbose
        _print_header("AC OPF GENERATOR DISPATCH COMPARISON")
    end

    for gen in case.generators
        gen_id = gen.id

        if !haskey(own_ac_opf.pg, gen_id) || !haskey(pm_gen, gen_id)
            continue
        end

        own_pg = own_ac_opf.pg[gen_id]
        own_qg = own_ac_opf.qg[gen_id]

        pm_pg = pm_gen[gen_id]["pg"]
        pm_qg = pm_gen[gen_id]["qg"]

        push!(pg_diffs, abs(own_pg - pm_pg))
        push!(qg_diffs, abs(own_qg - pm_qg))

        if verbose
            println(
                "Gen ", gen_id,
                " | own pg = ", round(own_pg, digits=6),
                " | PowerModels pg = ", round(pm_pg, digits=6),
                " | Δpg = ", round(own_pg - pm_pg, digits=6),
                " | own qg = ", round(own_qg, digits=6),
                " | PowerModels qg = ", round(pm_qg, digits=6),
                " | Δqg = ", round(own_qg - pm_qg, digits=6)
            )
        end
    end

    if verbose
        _print_header("AC OPF VOLTAGE COMPARISON")
    end

    for bus in case.buses
        bus_id = bus.id

        if !haskey(own_ac_opf.vm, bus_id) || !haskey(pm_bus, bus_id)
            continue
        end

        own_vm = own_ac_opf.vm[bus_id]
        own_va = own_ac_opf.va[bus_id]

        pm_vm = pm_bus[bus_id]["vm"]
        pm_va = pm_bus[bus_id]["va"]

        push!(vm_diffs, abs(own_vm - pm_vm))
        push!(va_diffs, abs(own_va - pm_va))

        if verbose
            println(
                "Bus ", bus_id,
                " | own vm = ", round(own_vm, digits=6),
                " | PowerModels vm = ", round(pm_vm, digits=6),
                " | Δvm = ", round(own_vm - pm_vm, digits=6),
                " | own va = ", round(own_va, digits=6),
                " | PowerModels va = ", round(pm_va, digits=6),
                " | Δva = ", round(own_va - pm_va, digits=6)
            )
        end
    end

    if verbose
        _print_header("AC OPF BRANCH FLOW COMPARISON")
    end

    for branch in case.branches
        branch_id = branch.id

        if !haskey(own_ac_opf.branch_flows, branch_id) || !haskey(pm_branch, branch_id)
            continue
        end

        own_flow = own_ac_opf.branch_flows[branch_id]
        pm_flow = pm_branch[branch_id]

        own_pf = own_flow["pf"]
        own_qf = own_flow["qf"]

        pm_pf = pm_flow["pf"]
        pm_qf = pm_flow["qf"]

        push!(pf_diffs, abs(own_pf - pm_pf))
        push!(qf_diffs, abs(own_qf - pm_qf))

        if verbose
            println(
                "Branch ", branch_id,
                " | own pf = ", round(own_pf, digits=6),
                " | PowerModels pf = ", round(pm_pf, digits=6),
                " | Δpf = ", round(own_pf - pm_pf, digits=6),
                " | own qf = ", round(own_qf, digits=6),
                " | PowerModels qf = ", round(pm_qf, digits=6),
                " | Δqf = ", round(own_qf - pm_qf, digits=6)
            )
        end
    end

    summary = ComparisonSummary(
        "AC OPF",
        objective_diff,
        _safe_max(pg_diffs),
        _safe_max(qg_diffs),
        _safe_max(vm_diffs),
        _safe_max(va_diffs),
        _safe_max(pf_diffs),
        _safe_max(qf_diffs)
    )

    if verbose
        _print_header("AC OPF FINAL DIAGNOSTIC")
        println("Max Pg diff = ", summary.max_pg_diff)
        println("Max Qg diff = ", summary.max_qg_diff)
        println("Max Vm diff = ", summary.max_vm_diff)
        println("Max Va diff = ", summary.max_va_diff)
        println("Max Pf diff = ", summary.max_pf_diff)
        println("Max Qf diff = ", summary.max_qf_diff)
        println("Objective diff = ", summary.objective_diff)

        if summary.max_pg_diff < 1e-5 &&
           summary.max_qg_diff < 1e-5 &&
           summary.max_vm_diff < 1e-5 &&
           summary.max_va_diff < 1e-5 &&
           summary.max_pf_diff < 1e-5 &&
           summary.max_qf_diff < 1e-5 &&
           summary.objective_diff < 1e-4
            println("Result: own AC OPF matches PowerModels closely.")
        else
            println("Result: differences detected.")
        end
    end

    return summary
end