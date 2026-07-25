# Validate selected own AC-OPF operating points by feeding their dispatch and
# voltage solution into the Newton-Raphson AC power-flow solver.
#
# Targets are read from the signed objective-mismatch tables:
#   results/own_vs_powermodels/objective_mismatch_details_julia_current.csv
#   results/own_vs_powermodels/objective_mismatch_details_julia_pm_like.csv
#
# Outputs:
#   results/own_vs_powermodels/opf_solution_loadflow_validation_julia.csv
#   results/own_vs_powermodels/opf_vs_pf_variable_differences_julia.csv
#   results/own_vs_powermodels/opf_vs_pf_binding_comparison_julia.csv

const TWENTYFIVE_SCRIPT_PATH = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH)
end

using DataFrames
using CSV

const KNOWN_LABELS_LF = Dict(
    "1888_rte__base__none__none__none__none" => "base",
    "1888_rte_load_x0p8__load_scaling__none__none__load_multiplier__0p8" => "load_x0.8",
    "1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2" => "load_x1.2_reactive_stress",
    "1888_rte_qg_limits_x0p8__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p8" => "qg_limits_x0.8",
)

const VARIANT_CONFIGS = [
    (
        variant="current",
        root=OUT_ROOT,
        objective_path=joinpath(ROOT, "results", "own_vs_powermodels", "objective_mismatch_details_julia_current.csv"),
    ),
    (
        variant="pm_like",
        root=PM_LIKE_OUT_ROOT,
        objective_path=joinpath(ROOT, "results", "own_vs_powermodels", "objective_mismatch_details_julia_pm_like.csv"),
    ),
]

function expected_scenario_label_lf(entry)
    raw_id = String(entry.scenario_id)
    if haskey(KNOWN_LABELS_LF, raw_id)
        return KNOWN_LABELS_LF[raw_id]
    elseif entry.scenario == "base"
        return "base"
    else
        return raw_id
    end
end

function scenario_data_for(case_label::String, scenario_label::String)
    case_file = "pglib_opf_case$(case_label)"
    data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
    entries = compact_scenarios(data, case_label, build_ac_optimizer(); include_no_reference=true)
    for entry in entries
        if expected_scenario_label_lf(entry) == scenario_label
            return entry.data
        end
    end
    error("Could not find scenario $(case_label)/$(scenario_label)")
end

function dict_from_columns(df::DataFrame, id_col::Symbol, value_col::Symbol)
    out = Dict{String, Float64}()
    for row in eachrow(df)
        out[string(row[id_col])] = Float64(row[value_col])
    end
    return out
end

function opf_case_from_tables(case::OwnPowerModels.PowerCase, out_dir::String)
    buses_df = DataFrame(CSV.File(joinpath(out_dir, "buses.csv")))
    gens_df = DataFrame(CSV.File(joinpath(out_dir, "generators.csv")))

    own_vm = dict_from_columns(buses_df, :bus_id, :own_vm)
    own_va = dict_from_columns(buses_df, :bus_id, :own_va)
    own_pg = dict_from_columns(gens_df, :gen_id, :own_pg)
    own_qg = dict_from_columns(gens_df, :gen_id, :own_qg)

    buses = [
        OwnPowerModels.Bus(
            bus.id,
            bus.index,
            bus.bus_type,
            get(own_vm, bus.id, bus.vm0),
            get(own_va, bus.id, bus.va0),
            bus.vmin,
            bus.vmax,
        )
        for bus in case.buses
    ]
    generators = [
        OwnPowerModels.Generator(
            gen.id,
            gen.index,
            gen.bus,
            get(own_pg, gen.id, gen.pg0),
            get(own_qg, gen.id, gen.qg0),
            gen.pmin,
            gen.pmax,
            gen.qmin,
            gen.qmax,
            gen.status,
            gen.cost_c2,
            gen.cost_c1,
            gen.cost_c0,
        )
        for gen in case.generators
    ]

    return OwnPowerModels.PowerCase(
        case.name,
        case.baseMVA,
        buses,
        case.branches,
        generators,
        case.loads,
        case.shunts,
        case.slack_bus_id,
        case.bus_id_to_index,
    )
end

_absmax(values) = isempty(values) ? 0.0 : maximum(abs.(values))

function pf_flow_value(pf_result, branch_id::String, field::String)
    if !haskey(pf_result.branch_flows, branch_id)
        return NaN
    end
    return pf_result.branch_flows[branch_id][field]
end

function variable_difference_rows(variant, case_label, scenario_label, out_dir, opf_case, pf_result)
    rows = NamedTuple[]
    buses_df = DataFrame(CSV.File(joinpath(out_dir, "buses.csv")))
    gens_df = DataFrame(CSV.File(joinpath(out_dir, "generators.csv")))
    branches_df = DataFrame(CSV.File(joinpath(out_dir, "branches.csv")))

    for row in eachrow(buses_df)
        bus_id = string(row.bus_id)
        idx = opf_case.bus_id_to_index[bus_id]
        push!(rows, (
            variant=variant, case=case_label, scenario=scenario_label,
            element_type="bus", element_id=bus_id, quantity="vm",
            opf_value=Float64(row.own_vm), pf_value=pf_result.vm[idx],
            diff=Float64(row.own_vm) - pf_result.vm[idx], abs_diff=abs(Float64(row.own_vm) - pf_result.vm[idx]),
        ))
        push!(rows, (
            variant=variant, case=case_label, scenario=scenario_label,
            element_type="bus", element_id=bus_id, quantity="va",
            opf_value=Float64(row.own_va), pf_value=pf_result.va[idx],
            diff=Float64(row.own_va) - pf_result.va[idx], abs_diff=abs(Float64(row.own_va) - pf_result.va[idx]),
        ))
    end

    for row in eachrow(gens_df)
        gen_id = string(row.gen_id)
        pf_qg = get(pf_result.qg_by_generator, gen_id, NaN)
        push!(rows, (
            variant=variant, case=case_label, scenario=scenario_label,
            element_type="generator", element_id=gen_id, quantity="pg",
            opf_value=Float64(row.own_pg), pf_value=Float64(row.own_pg),
            diff=0.0, abs_diff=0.0,
        ))
        push!(rows, (
            variant=variant, case=case_label, scenario=scenario_label,
            element_type="generator", element_id=gen_id, quantity="qg",
            opf_value=Float64(row.own_qg), pf_value=pf_qg,
            diff=Float64(row.own_qg) - pf_qg, abs_diff=abs(Float64(row.own_qg) - pf_qg),
        ))
    end

    for row in eachrow(branches_df)
        branch_id = string(row.branch_id)
        for quantity in ("pf", "qf", "pt", "qt")
            opf_col = Symbol("own_$(quantity)")
            opf_value = Float64(row[opf_col])
            pf_value = pf_flow_value(pf_result, branch_id, quantity)
            push!(rows, (
                variant=variant, case=case_label, scenario=scenario_label,
                element_type="branch", element_id=branch_id, quantity=quantity,
                opf_value=opf_value, pf_value=pf_value,
                diff=opf_value - pf_value, abs_diff=abs(opf_value - pf_value),
            ))
        end
    end
    return rows
end

function pf_binding_value(case::OwnPowerModels.PowerCase, pf_result, constraint_type, element_id::String)
    constraint_type = String(constraint_type)
    if constraint_type in ("pg_min", "pg_max")
        gen = only(filter(g -> g.id == element_id, case.generators))
        value = gen.pg0
        bound = constraint_type == "pg_min" ? gen.pmin : gen.pmax
        margin = constraint_type == "pg_min" ? value - bound : bound - value
        return value, margin
    elseif constraint_type in ("qg_min", "qg_max")
        gen = only(filter(g -> g.id == element_id, case.generators))
        value = get(pf_result.qg_by_generator, element_id, NaN)
        bound = constraint_type == "qg_min" ? gen.qmin : gen.qmax
        margin = constraint_type == "qg_min" ? value - bound : bound - value
        return value, margin
    elseif constraint_type in ("vm_min", "vm_max")
        bus = only(filter(b -> b.id == element_id, case.buses))
        idx = case.bus_id_to_index[element_id]
        value = pf_result.vm[idx]
        bound = constraint_type == "vm_min" ? bus.vmin : bus.vmax
        margin = constraint_type == "vm_min" ? value - bound : bound - value
        return value, margin
    elseif constraint_type == "thermal"
        branch = only(filter(b -> b.id == element_id, case.branches))
        flow = pf_result.branch_flows[element_id]
        sf = sqrt(flow["pf"]^2 + flow["qf"]^2)
        st = sqrt(flow["pt"]^2 + flow["qt"]^2)
        value = max(sf, st)
        margin = branch.rate_a - value
        return value, margin
    elseif constraint_type in ("angle_lower", "angle_upper")
        branch = only(filter(b -> b.id == element_id, case.branches))
        f = case.bus_id_to_index[branch.from_bus]
        t = case.bus_id_to_index[branch.to_bus]
        value = pf_result.va[f] - pf_result.va[t]
        bound = constraint_type == "angle_lower" ? branch.angmin : branch.angmax
        margin = constraint_type == "angle_lower" ? value - bound : bound - value
        return value, margin
    end
    return NaN, NaN
end

function binding_comparison_rows(variant, case_label, scenario_label, out_dir, opf_case, pf_result; tol=1e-6)
    rows = NamedTuple[]
    constraints_df = DataFrame(CSV.File(joinpath(out_dir, "constraints_binding.csv")))
    for row in eachrow(constraints_df)
        constraint_type = string(row.constraint_type)
        element_id = string(row.element_id)
        pf_value, pf_margin = pf_binding_value(opf_case, pf_result, constraint_type, element_id)
        opf_binding = Bool(row.own_binding)
        pf_binding = abs(pf_margin) <= tol
        if opf_binding != pf_binding
            push!(rows, (
                variant=variant, case=case_label, scenario=scenario_label,
                constraint_type=constraint_type, element_id=element_id,
                bound=Float64(row.bound),
                opf_value=Float64(row.own_value), pf_value=pf_value,
                opf_margin=Float64(row.own_margin), pf_margin=pf_margin,
                opf_binding=opf_binding, pf_binding=pf_binding,
                mismatch_direction=opf_binding ? "only_opf_binding" : "only_pf_binding",
            ))
        end
    end
    return rows
end

function load_targets()
    targets = NamedTuple[]
    for cfg in VARIANT_CONFIGS
        isfile(cfg.objective_path) || continue
        df = DataFrame(CSV.File(cfg.objective_path))
        for row in eachrow(df)
            push!(targets, (
                variant=cfg.variant,
                root=cfg.root,
                case=String(row.case),
                scenario=String(row.scenario),
            ))
        end
    end
    return targets
end

function main()
    summary_rows = NamedTuple[]
    variable_rows = NamedTuple[]
    binding_rows = NamedTuple[]

    for target in load_targets()
        println("Validating $(target.variant) / $(target.case) / $(target.scenario)")
        out_dir = joinpath(target.root, target.case, safe_scenario_folder(target.scenario))
        if !isfile(joinpath(out_dir, "buses.csv")) || !isfile(joinpath(out_dir, "generators.csv"))
            println("  missing exported OPF tables; skipping")
            continue
        end

        data = scenario_data_for(target.case, target.scenario)
        case = OwnPowerModels.powermodels_to_powercase(deepcopy(data))
        opf_case = opf_case_from_tables(case, out_dir)
        pf_result = solve_ac_power_flow_own(opf_case; method=:newton, tol=1e-8, max_iter=50)

        append!(variable_rows, variable_difference_rows(target.variant, target.case, target.scenario, out_dir, opf_case, pf_result))
        append!(binding_rows, binding_comparison_rows(target.variant, target.case, target.scenario, out_dir, opf_case, pf_result))

        case_vars = filter(r -> r.variant == target.variant && r.case == target.case && r.scenario == target.scenario, variable_rows)
        bus_vm = [r.abs_diff for r in case_vars if r.element_type == "bus" && r.quantity == "vm"]
        bus_va = [r.abs_diff for r in case_vars if r.element_type == "bus" && r.quantity == "va"]
        gen_qg = [r.abs_diff for r in case_vars if r.element_type == "generator" && r.quantity == "qg" && !isnan(r.abs_diff)]
        branch_pf = [r.abs_diff for r in case_vars if r.element_type == "branch" && r.quantity == "pf"]
        branch_qf = [r.abs_diff for r in case_vars if r.element_type == "branch" && r.quantity == "qf"]

        push!(summary_rows, (
            variant=target.variant,
            case=target.case,
            scenario=target.scenario,
            pf_converged=pf_result.converged,
            pf_termination_reason=pf_result.termination_reason,
            pf_iterations=pf_result.iterations,
            pf_mismatch_norm=pf_result.mismatch_norm,
            max_vm_opf_pf_diff=_absmax(bus_vm),
            max_va_opf_pf_diff=_absmax(bus_va),
            max_qg_opf_pf_diff=_absmax(gen_qg),
            max_pf_opf_pf_diff=_absmax(branch_pf),
            max_qf_opf_pf_diff=_absmax(branch_qf),
            n_binding_mismatches=sum(
                (r.variant == target.variant && r.case == target.case && r.scenario == target.scenario for r in binding_rows);
                init=0,
            ),
        ))
    end

    out_root = joinpath(ROOT, "results", "own_vs_powermodels")
    CSV.write(joinpath(out_root, "opf_solution_loadflow_validation_julia.csv"), DataFrame(summary_rows))
    CSV.write(joinpath(out_root, "opf_vs_pf_variable_differences_julia.csv"), DataFrame(variable_rows))
    CSV.write(joinpath(out_root, "opf_vs_pf_binding_comparison_julia.csv"), DataFrame(binding_rows))
    println("Wrote load-flow validation outputs to $(out_root)")
end

main()
