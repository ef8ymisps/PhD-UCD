using PowerModels
using DataFrames
using CSV
using Logging
using Plots
using Statistics

function project_root()
    return normpath(joinpath(@__DIR__, "..", "..", "..", ".."))
end

const ROOT = project_root()
const SRC_DIR = normpath(joinpath(ROOT, "julia", "tools", "powermodels", "src"))
include(joinpath(SRC_DIR, "replacement_note_experiments.jl"))

const OUTPUT_ROOT = joinpath(ROOT, "results", "presentable", "powermodels_lf_replacement_sweep")
const DOC_ROOT = joinpath(ROOT, "docs")

const AC_VARIANT_ORDER = [
    "original",
    "BV_from_warmstart",
    "G_PGQG_from",
    "G_PGQG_from_plus_BV_from",
    "G_PGQG_from_plus_GVG_from",
]

const DC_VARIANT_ORDER = [
    "original",
    "G_PG_from",
    "load_from",
    "G_PG_from_plus_load_from",
]

const LARGE_PROBLEMATIC_CASES = [
    "case300_ieee",
    "case1354_pegase",
    "case1888_rte",
    "case6470_rte",
    "case9241_pegase",
    "case13659_pegase",
    "case78484_epigrids",
]

function arg_value(name::String, default::String)
    prefix = name * "="
    for arg in ARGS
        if startswith(arg, prefix)
            return arg[length(prefix)+1:end]
        end
    end
    return default
end

function arg_bool(name::String)
    return any(arg -> arg == name, ARGS)
end

function output_mode_name(; base_only::Bool, api_sad_only::Bool, api_only::Bool, sad_only::Bool, include_api_sad::Bool, large_only::Bool)
    if api_only
        return large_only ? "api_large" : "api"
    elseif sad_only
        return large_only ? "sad_large" : "sad"
    elseif api_sad_only
        return "api_sad"
    elseif include_api_sad
        return large_only ? "mixed_large" : "mixed_full"
    elseif base_only
        return large_only ? "base_large" : "base_full"
    end
    return large_only ? "custom_large" : "custom_full"
end

function output_paths(mode_name::String)
    output_dir = joinpath(OUTPUT_ROOT, mode_name)
    return (
        output_dir = output_dir,
        figure_dir = joinpath(output_dir, "figures"),
        doc_path = joinpath(DOC_ROOT, "powermodels_lf_replacement_sweep_$(mode_name).md"),
    )
end

function wildcard_regex(pattern::String)
    escaped = replace(pattern, "." => "\\.", "*" => ".*", "?" => ".")
    return Regex("^" * escaped * "\$")
end

function matching_cases(case_dir::String, case_glob::String)
    pattern = wildcard_regex(case_glob)
    return sort([
        joinpath(case_dir, name)
        for name in readdir(case_dir)
        if isfile(joinpath(case_dir, name)) && occursin(pattern, name)
    ])
end

function matching_cases_with_family(case_dir::String, case_glob::String, source_family::String)
    paths = matching_cases(case_dir, case_glob)
    if source_family == "base"
        paths = [path for path in paths if !is_api_sad_case(splitext(basename(path))[1])]
    elseif source_family == "api"
        paths = [path for path in paths if occursin("__api", splitext(basename(path))[1]) || !is_api_sad_case(splitext(basename(path))[1])]
    elseif source_family == "sad"
        paths = [path for path in paths if occursin("__sad", splitext(basename(path))[1]) || !is_api_sad_case(splitext(basename(path))[1])]
    end
    return [(path=path, source_family=source_family) for path in paths]
end

function presentable_case_name(case_stem::AbstractString)
    return replace(string(case_stem), r"^pglib_opf_" => "")
end

function is_api_sad_case(case_stem::AbstractString)
    stem = string(case_stem)
    return occursin("__api", stem) || occursin("__sad", stem)
end

function case_family_name(case_stem::AbstractString)
    name = presentable_case_name(case_stem)
    return replace(replace(name, "__api" => ""), "__sad" => "")
end

function case_variant_family(case_stem::AbstractString; source_family::String="")
    if source_family in ("base", "api", "sad")
        return source_family
    end
    stem = string(case_stem)
    if occursin("__api", stem)
        return "api"
    elseif occursin("__sad", stem)
        return "sad"
    end
    return "base"
end

function case_search_plan(root_case_dir::String, case_glob::String; base_only::Bool, api_sad_only::Bool, api_only::Bool, sad_only::Bool, include_api_sad::Bool)
    base_dir = joinpath(root_case_dir, "base_case")
    api_dir = joinpath(root_case_dir, "api")
    sad_dir = joinpath(root_case_dir, "sad")

    has_split_layout = isdir(base_dir) || isdir(api_dir) || isdir(sad_dir)
    if has_split_layout
        plan = Tuple{String,String,String}[]
        if base_only || include_api_sad
            isdir(base_dir) || error("Expected base-case directory does not exist: $(base_dir)")
            push!(plan, (base_dir, case_glob, "base"))
        end
        if api_only || api_sad_only || include_api_sad
            isdir(api_dir) && push!(plan, (api_dir, case_glob, "api"))
        end
        if sad_only || api_sad_only || include_api_sad
            isdir(sad_dir) && push!(plan, (sad_dir, case_glob, "sad"))
        end
        return plan
    end

    return [(root_case_dir, case_glob, "")]
end

function collect_case_entries(root_case_dir::String, case_glob::String; base_only::Bool, api_sad_only::Bool, api_only::Bool, sad_only::Bool, include_api_sad::Bool)
    entries = NamedTuple[]
    split_layout = false
    for (dir, glob, source_family) in case_search_plan(
        root_case_dir,
        case_glob;
        base_only=base_only,
        api_sad_only=api_sad_only,
        api_only=api_only,
        sad_only=sad_only,
        include_api_sad=include_api_sad,
    )
        split_layout = split_layout || source_family != ""
        append!(entries, matching_cases_with_family(dir, glob, source_family))
    end

    if !split_layout
        if base_only
            entries = [entry for entry in entries if !is_api_sad_case(splitext(basename(entry.path))[1])]
        elseif api_sad_only
            entries = [entry for entry in entries if is_api_sad_case(splitext(basename(entry.path))[1])]
        elseif api_only
            entries = [entry for entry in entries if occursin("__api", splitext(basename(entry.path))[1])]
        elseif sad_only
            entries = [entry for entry in entries if occursin("__sad", splitext(basename(entry.path))[1])]
        end
    end

    return sort(entries; by=entry -> entry.path)
end

function source_family_for_entry(entry)
    if entry.source_family in ("base", "api", "sad")
        return entry.source_family
    end
    return case_variant_family(splitext(basename(entry.path))[1])
end

function canonical_ac_variants()
    variants = make_replacement_variants()
    return [(name=name, variant=variants[name]) for name in AC_VARIANT_ORDER]
end

function canonical_dc_variants(notes)
    variants = [
        (name="original", variant=(groups=String[], direction="from"), available=true),
        (name="G_PG_from", variant=(groups=["G_PG"], direction="from"), available=true),
    ]
    load_available = nrow(notes.load) > 0
    push!(variants, (name="load_from", variant=(groups=["L"], direction="from"), available=load_available))
    push!(variants, (name="G_PG_from_plus_load_from", variant=(groups=["G_PG", "L"], direction="from"), available=load_available))
    return variants
end

function parse_case_quiet(case_path::String; normalize_bus_types::Bool=true)
    return parse_case_for_experiment(case_path; quiet=true, normalize_bus_types=normalize_bus_types)
end

function case_statistics(data::Dict)
    active_gens = [gen for gen in values(data["gen"]) if get(gen, "gen_status", 1) != 0]
    active_loads = haskey(data, "load") ? [load for load in values(data["load"]) if get(load, "status", 1) != 0] : []
    n_buses = length(data["bus"])
    n_branches = length(data["branch"])
    n_generators = length(active_gens)
    base = base_mva(data)
    total_pg = sum(Float64(get(gen, "pg", 0.0)) for gen in active_gens) * base
    total_qg = sum(Float64(get(gen, "qg", 0.0)) for gen in active_gens) * base
    total_pd = sum(Float64(get(load, "pd", 0.0)) for load in active_loads) * base
    total_qd = sum(Float64(get(load, "qd", 0.0)) for load in active_loads) * base
    return (
        n_buses=n_buses,
        n_branches=n_branches,
        n_generators=n_generators,
        generator_to_bus_ratio=n_buses == 0 ? missing : n_generators / n_buses,
        total_pg=total_pg,
        total_pd=total_pd,
        total_qg=total_qg,
        total_qd=total_qd,
    )
end

function status_converged(status)
    text = uppercase(string(status))
    return text in ("TRUE", "CONVERGED", "OPTIMAL", "LOCALLY_SOLVED")
end

function get_nested(data, keys...; default=missing)
    current = data
    for key in keys
        if current isa AbstractDict && haskey(current, key)
            current = current[key]
        else
            return default
        end
    end
    return current
end

function pm_solve_time(result)
    return get(result, "solve_time", missing)
end

function ac_compute_call(data)
    return PowerModels.compute_ac_pf(
        data;
        method=:newton,
        ftol=1e-8,
        xtol=1e-8,
        iterations=100,
    )
end

function dc_compute_call(data)
    return PowerModels.compute_dc_pf(data)
end

function set_default_start_values!(data::Dict)
    call_set_ac_pf_start_values!(data)
    return data
end

function apply_bus_voltage_warmstart_only!(data::Dict, notes, direction::String)
    mapping = build_powermodels_mapping(data)
    applied = 0
    for note in eachrow(notes.bus_voltage)
        bus_key = get(mapping.bus_key_by_number, note.bus, nothing)
        if bus_key === nothing
            continue
        end
        data["bus"][bus_key]["vm_start"] = _direction_value(note, "V", direction)
        data["bus"][bus_key]["va_start"] = deg2rad(_direction_value(note, "theta", direction))
        applied += 1
    end
    return applied
end

function apply_presentable_variant!(data::Dict, notes, variant)
    groups = Set(variant.groups)
    direction = variant.direction
    mapping = build_powermodels_mapping(data)
    audit = verify_replacement_mapping(data, notes)
    counts = Dict(
        "n_applied_bus_voltage" => 0,
        "n_applied_gen_pg" => 0,
        "n_applied_gen_qg" => 0,
        "n_applied_gen_vg" => 0,
        "n_applied_load" => 0,
    )

    if "L" in groups
        for note in eachrow(notes.load)
            load, _, _ = _match_load(mapping, note.bus, note.order_at_bus)
            if load === nothing
                continue
            end
            load_id = string(load.load_id)
            data["load"][load_id]["pd"] = power_note_to_pu(data, _direction_value(note, "Pd", direction))
            data["load"][load_id]["qd"] = power_note_to_pu(data, _direction_value(note, "Qd", direction))
            counts["n_applied_load"] += 1
        end
    end

    if "G_PG" in groups || "G_QG" in groups
        for note in eachrow(notes.gen_pgqg)
            gen, _, _ = _match_generator(mapping, note.bus, note.order_at_bus)
            if gen === nothing
                continue
            end
            gen_id = string(gen.gen_id)
            if "G_PG" in groups
                data["gen"][gen_id]["pg"] = power_note_to_pu(data, _direction_value(note, "Pg", direction))
                counts["n_applied_gen_pg"] += 1
            end
            if "G_QG" in groups
                data["gen"][gen_id]["qg"] = power_note_to_pu(data, _direction_value(note, "Qg", direction))
                counts["n_applied_gen_qg"] += 1
            end
        end
    end

    if "G_VG" in groups
        for note in eachrow(notes.gen_vg)
            gen, _, _ = _match_generator(mapping, note.bus, note.order_at_bus)
            if gen === nothing
                continue
            end
            data["gen"][string(gen.gen_id)]["vg"] = _direction_value(note, "Vg", direction)
            counts["n_applied_gen_vg"] += 1
        end
    end

    set_default_start_values!(data)
    if "BV" in groups
        counts["n_applied_bus_voltage"] = apply_bus_voltage_warmstart_only!(data, notes, direction)
    end

    return audit, counts
end

function update_solution_and_flows!(data::Dict, result::Dict, lf_type::String)
    try
        solution = get(result, "solution", Dict{String,Any}())
        PowerModels.update_data!(data, solution)
        if lf_type == "AC"
            flows = PowerModels.calc_branch_flow_ac(data)
        else
            flows = PowerModels.calc_branch_flow_dc(data)
        end
        PowerModels.update_data!(data, flows)
        return true, ""
    catch err
        return false, sprint(showerror, err)
    end
end

function numeric_values(values)
    return [Float64(value) for value in values if value isa Number && isfinite(Float64(value))]
end

function max_or_missing(values)
    vals = numeric_values(values)
    return isempty(vals) ? missing : maximum(vals)
end

function min_or_missing(values)
    vals = numeric_values(values)
    return isempty(vals) ? missing : minimum(vals)
end

function first_slack_bus(data::Dict)
    slack_ids = [bus_id for (bus_id, bus) in data["bus"] if Int(round(get(bus, "bus_type", 1))) == 3]
    return isempty(slack_ids) ? missing : sort(slack_ids; by=x -> parse(Int, string(x)))[1]
end

function slack_generation(data::Dict, result::Dict; ac::Bool)
    slack_bus = first_slack_bus(data)
    if ismissing(slack_bus)
        return (slack_pg=missing, slack_qg=missing)
    end
    gen_ids = [
        gen_id for (gen_id, gen) in data["gen"]
        if string(Int(round(gen["gen_bus"]))) == string(slack_bus) && get(gen, "gen_status", 1) != 0
    ]
    pg_values = [get_nested(result, "solution", "gen", gen_id, "pg"; default=missing) for gen_id in gen_ids]
    qg_values = [get_nested(result, "solution", "gen", gen_id, "qg"; default=missing) for gen_id in gen_ids]
    pvals = numeric_values(pg_values)
    qvals = numeric_values(qg_values)
    return (
        slack_pg = isempty(pvals) ? missing : sum(pvals) * base_mva(data),
        slack_qg = !ac || isempty(qvals) ? missing : sum(qvals) * base_mva(data),
    )
end

function solution_diagnostics(data::Dict, result::Dict; lf_type::String)
    ac = lf_type == "AC"
    base = base_mva(data)
    bus_solution = get_nested(result, "solution", "bus"; default=Dict{String,Any}())
    branch_solution = haskey(data, "branch") ? data["branch"] : Dict{String,Any}()

    vm_values = [get(bus, "vm", missing) for bus in values(bus_solution)]
    va_values_deg = [rad2deg(Float64(get(bus, "va", 0.0))) for bus in values(bus_solution) if get(bus, "va", missing) isa Number]
    pf_values = [abs(Float64(get(branch, "pf", 0.0))) * base for branch in values(branch_solution) if get(branch, "pf", missing) isa Number]
    pt_values = [abs(Float64(get(branch, "pt", 0.0))) * base for branch in values(branch_solution) if get(branch, "pt", missing) isa Number]
    qf_values = [abs(Float64(get(branch, "qf", 0.0))) * base for branch in values(branch_solution) if get(branch, "qf", missing) isa Number]
    qt_values = [abs(Float64(get(branch, "qt", 0.0))) * base for branch in values(branch_solution) if get(branch, "qt", missing) isa Number]

    ratios = Float64[]
    for (branch_id, branch) in data["branch"]
        rate = Float64(get(branch, "rate_a", 0.0))
        rate <= 0.0 && continue
        sol = get(branch_solution, branch_id, Dict{String,Any}())
        pf = get(sol, "pf", missing)
        pt = get(sol, "pt", missing)
        qf = get(sol, "qf", missing)
        qt = get(sol, "qt", missing)
        if ac && pf isa Number && qf isa Number && pt isa Number && qt isa Number
            push!(ratios, max(sqrt(pf^2 + qf^2), sqrt(pt^2 + qt^2)) * base / rate)
        elseif !ac && pf isa Number && pt isa Number
            push!(ratios, max(abs(pf), abs(pt)) * base / rate)
        end
    end

    slack = slack_generation(data, result; ac=ac)
    return (
        max_vm = max_or_missing(vm_values),
        min_vm = min_or_missing(vm_values),
        max_abs_va_deg = isempty(va_values_deg) ? missing : maximum(abs.(va_values_deg)),
        slack_bus = first_slack_bus(data),
        slack_pg = slack.slack_pg,
        slack_qg = slack.slack_qg,
        max_branch_pf = max_or_missing(pf_values),
        max_branch_qf = ac ? max_or_missing(qf_values) : missing,
        max_branch_pt = max_or_missing(pt_values),
        max_branch_qt = ac ? max_or_missing(qt_values) : missing,
        max_branch_loading_ratio = isempty(ratios) ? missing : maximum(ratios),
        max_power_balance_residual = missing,
        residual_calculated = false,
    )
end

function empty_diagnostics()
    return (
        max_vm=missing,
        min_vm=missing,
        max_abs_va_deg=missing,
        slack_bus=missing,
        slack_pg=missing,
        slack_qg=missing,
        max_branch_pf=missing,
        max_branch_qf=missing,
        max_branch_pt=missing,
        max_branch_qt=missing,
        max_branch_loading_ratio=missing,
        max_power_balance_residual=missing,
        residual_calculated=false,
    )
end

function base_row(case_name, variant_name, variant, notes, stats, before, after, counts, audit; lf_type::String, source_family::String="base")
    unmatched = nrow(audit) == 0 ? 0 : nrow(audit[ismissing.(audit.matched_object_id), :])
    return Dict{Symbol,Any}(
        :case_name => case_name,
        :case_family => case_family_name(case_name),
        :case_variant_family => source_family,
        :variant_name => variant_name,
        :lf_type => lf_type,
        :groups => join(variant.groups, "+"),
        :direction => variant.direction,
        :n_buses => stats.n_buses,
        :n_branches => stats.n_branches,
        :n_generators => stats.n_generators,
        :generator_to_bus_ratio => stats.generator_to_bus_ratio,
        :n_bus_voltage_notes => nrow(notes.bus_voltage),
        :n_load_notes => nrow(notes.load),
        :n_gen_pgqg_notes => nrow(notes.gen_pgqg),
        :n_gen_vg_notes => nrow(notes.gen_vg),
        :n_unmatched_rows => unmatched,
        :mapping_success => unmatched == 0,
        :n_applied_bus_voltage => counts["n_applied_bus_voltage"],
        :n_applied_gen_pg => counts["n_applied_gen_pg"],
        :n_applied_gen_qg => counts["n_applied_gen_qg"],
        :n_applied_gen_vg => counts["n_applied_gen_vg"],
        :n_applied_load => counts["n_applied_load"],
        :total_pg => stats.total_pg,
        :total_pd => stats.total_pd,
        :total_qg => stats.total_qg,
        :total_qd => stats.total_qd,
        :gen_minus_load_mw_before => before.gen_minus_load_mw,
        :gen_minus_load_mw_after => after.gen_minus_load_mw,
    )
end

function run_one_pf(case_path::String, case_name::String, variant_name::String, variant, notes, stats; lf_type::String, available::Bool=true, source_family::String="base")
    data, bus_type_corrections = parse_case_quiet(case_path)
    before = balance(data)
    audit = verify_replacement_mapping(data, notes)
    counts = Dict(
        "n_applied_bus_voltage" => 0,
        "n_applied_gen_pg" => 0,
        "n_applied_gen_qg" => 0,
        "n_applied_gen_vg" => 0,
        "n_applied_load" => 0,
    )

    if available
        audit, counts = apply_presentable_variant!(data, notes, variant)
    end
    after = balance(data)
    row = base_row(case_name, variant_name, variant, notes, stats, before, after, counts, audit; lf_type=lf_type, source_family=source_family)
    row[:n_bus_type_corrections] = nrow(bus_type_corrections)
    row[:variant_available] = available

    row[:iteration_count_available] = false
    row[:iteration_count] = missing
    row[:iteration_count_source] = lf_type == "AC" ? "not_available_from_compute_ac_pf" : "not_applicable_to_compute_dc_pf"

    if lf_type == "AC"
        row[:reference_method] = "PowerModels.compute_ac_pf"
        row[:solver_family] = "NLsolve"
        row[:nlsolve_method] = "newton"
        row[:ftol] = 1e-8
        row[:xtol] = 1e-8
        row[:iterations_limit] = 100
    else
        row[:reference_method] = "PowerModels.compute_dc_pf"
        row[:solver_family] = "Julia linear solve"
        row[:nlsolve_method] = missing
        row[:ftol] = missing
        row[:xtol] = missing
        row[:iterations_limit] = missing
    end

    if !available
        row[:termination_status] = "NOT_AVAILABLE"
        row[:raw_status] = "NOT_AVAILABLE"
        row[:converged] = false
        row[:message_if_available] = "replacement-note family not parsed for this case"
        row[:exception_type] = missing
        row[:exception_message] = missing
        row[:solve_time_sec] = missing
        row[:powermodels_reported_solve_time_sec] = missing
        row[:branch_flows_calculated] = false
        row[:branch_flow_error] = missing
        diag = empty_diagnostics()
        for key in keys(diag)
            row[key] = getproperty(diag, key)
        end
        return row
    end

    try
        started = time()
        result = lf_type == "AC" ? ac_compute_call(data) : dc_compute_call(data)
        elapsed = time() - started
        status = string(get(result, "termination_status", "missing"))
        converged = status_converged(status)
        branch_ok, branch_err = converged ? update_solution_and_flows!(data, result, lf_type) : (false, "")
        diag = converged ? solution_diagnostics(data, result; lf_type=lf_type) : empty_diagnostics()

        row[:termination_status] = status
        row[:raw_status] = status
        row[:converged] = converged
        row[:message_if_available] = get(result, "message", missing)
        row[:exception_type] = missing
        row[:exception_message] = missing
        row[:solve_time_sec] = elapsed
        row[:powermodels_reported_solve_time_sec] = pm_solve_time(result)
        row[:branch_flows_calculated] = branch_ok
        row[:branch_flow_error] = branch_err == "" ? missing : branch_err
        for key in keys(diag)
            row[key] = getproperty(diag, key)
        end
    catch err
        diag = empty_diagnostics()
        row[:termination_status] = "EXCEPTION"
        row[:raw_status] = "EXCEPTION"
        row[:converged] = false
        row[:message_if_available] = missing
        row[:exception_type] = string(typeof(err))
        row[:exception_message] = sprint(showerror, err)
        row[:solve_time_sec] = missing
        row[:powermodels_reported_solve_time_sec] = missing
        row[:branch_flows_calculated] = false
        row[:branch_flow_error] = missing
        for key in keys(diag)
            row[key] = getproperty(diag, key)
        end
    end

    return row
end

function audit_summary_row(case_name::String, notes, audit::DataFrame; source_family::String="base")
    unmatched = nrow(audit) == 0 ? 0 : nrow(audit[ismissing.(audit.matched_object_id), :])
    by_type = Dict{String,Tuple{Int,Int}}()
    if nrow(audit) > 0
        for group in groupby(audit, :note_type)
            total = nrow(group)
            mapped = total - nrow(group[ismissing.(group.matched_object_id), :])
            by_type[string(group.note_type[1])] = (total, mapped)
        end
    end
    return (
        case_name=case_name,
        case_family=case_family_name(case_name),
        case_variant_family=source_family,
        n_bus_voltage_notes=nrow(notes.bus_voltage),
        n_load_notes=nrow(notes.load),
        n_gen_pgqg_notes=nrow(notes.gen_pgqg),
        n_gen_vg_notes=nrow(notes.gen_vg),
        n_audit_rows=nrow(audit),
        n_unmatched_rows=unmatched,
        mapping_success=unmatched == 0,
        bus_voltage_audit_rows=get(by_type, "bus_voltage", (0, 0))[1],
        bus_voltage_mapped_rows=get(by_type, "bus_voltage", (0, 0))[2],
        load_audit_rows=get(by_type, "load", (0, 0))[1],
        load_mapped_rows=get(by_type, "load", (0, 0))[2],
        gen_pgqg_audit_rows=get(by_type, "gen_pgqg", (0, 0))[1],
        gen_pgqg_mapped_rows=get(by_type, "gen_pgqg", (0, 0))[2],
        gen_vg_audit_rows=get(by_type, "gen_vg", (0, 0))[1],
        gen_vg_mapped_rows=get(by_type, "gen_vg", (0, 0))[2],
    )
end

function convergence_by_variant(ac_rows::DataFrame)
    ac_rows = copy(ac_rows)
    ac_rows.variant_order = [findfirst(==(name), AC_VARIANT_ORDER) === nothing ? length(AC_VARIANT_ORDER) + 1 : findfirst(==(name), AC_VARIANT_ORDER) for name in ac_rows.variant_name]
    grouped = groupby(ac_rows, [:variant_order, :variant_name])
    table = combine(
        grouped,
        nrow => :n_runs,
        :converged => (x -> count(identity, skipmissing(x))) => :n_converged,
    )
    sort!(table, :variant_order)
    select!(table, Not(:variant_order))
    table.convergence_rate = table.n_converged ./ table.n_runs
    return table
end

function large_problematic_rows(ac_rows::DataFrame)
    return ac_rows[in.(ac_rows.case_name, Ref(LARGE_PROBLEMATIC_CASES)), :]
end

function mean_or_missing(values)
    vals = numeric_values(values)
    return isempty(vals) ? missing : mean(vals)
end

function presentable_value(value)
    if value isa AbstractFloat
        return isfinite(value) ? round(value; digits=2) : value
    elseif value isa Integer || value isa Bool || value isa Missing || value === nothing
        return value
    elseif value isa Real
        return round(Float64(value); digits=2)
    end
    return value
end

function round_presentable!(table::DataFrame)
    for name in names(table)
        table[!, name] = [presentable_value(value) for value in table[!, name]]
    end
    return table
end

function dc_summary_by_variant(dc_rows::DataFrame)
    grouped = groupby(dc_rows, :variant_name)
    table = combine(
        grouped,
        nrow => :n_runs,
        :converged => (x -> count(identity, skipmissing(x))) => :n_converged,
        :variant_available => (x -> count(identity, skipmissing(x))) => :n_available,
        :solve_time_sec => mean_or_missing => :mean_solve_time_sec,
        :max_power_balance_residual => max_or_missing => :max_active_power_residual,
    )
    return table
end

function speedup_status(original_converged::Bool, variant_converged::Bool)
    if original_converged && variant_converged
        return "both_converged"
    elseif !original_converged && variant_converged
        return "convergence_gain"
    elseif original_converged && !variant_converged
        return "convergence_loss"
    end
    return "both_failed"
end

function ac_speedup_detail(ac_rows::DataFrame)
    rows = NamedTuple[]
    key_columns = [:case_name, :case_variant_family]
    for group in groupby(ac_rows, key_columns)
        original_rows = group[group.variant_name .== "original", :]
        nrow(original_rows) == 0 && continue
        original = original_rows[1, :]
        original_converged = Bool(original.converged)
        original_time = original.solve_time_sec
        for row in eachrow(group)
            row.variant_name == "original" && continue
            variant_converged = Bool(row.converged)
            variant_time = row.solve_time_sec
            status = speedup_status(original_converged, variant_converged)

            comparable = status == "both_converged" &&
                original_time isa Number &&
                variant_time isa Number &&
                isfinite(Float64(original_time)) &&
                isfinite(Float64(variant_time)) &&
                Float64(variant_time) > 0.0

            speedup = comparable ? Float64(original_time) / Float64(variant_time) : missing
            delta = comparable ? Float64(original_time) - Float64(variant_time) : missing

            push!(rows, (
                case_name = row.case_name,
                case_variant_family = row.case_variant_family,
                n_buses = row.n_buses,
                variant_name = row.variant_name,
                original_converged = original_converged,
                variant_converged = variant_converged,
                original_solve_time_sec = original_time,
                variant_solve_time_sec = variant_time,
                time_delta_sec = delta,
                speedup_vs_original = speedup,
                speedup_status = status,
            ))
        end
    end
    return DataFrame(rows)
end

function median_or_missing(values)
    vals = sort(numeric_values(values))
    n = length(vals)
    if n == 0
        return missing
    elseif isodd(n)
        return vals[(n + 1) ÷ 2]
    end
    return (vals[n ÷ 2] + vals[n ÷ 2 + 1]) / 2.0
end

function ac_speedup_summary(detail::DataFrame)
    nrow(detail) == 0 && return DataFrame()
    grouped = groupby(detail, :variant_name)
    return combine(
        grouped,
        nrow => :n_cases,
        :speedup_status => (x -> count(==("both_converged"), x)) => :n_both_converged,
        :speedup_status => (x -> count(==("convergence_gain"), x)) => :n_convergence_gain,
        :speedup_status => (x -> count(==("convergence_loss"), x)) => :n_convergence_loss,
        :speedup_status => (x -> count(==("both_failed"), x)) => :n_both_failed,
        :speedup_vs_original => mean_or_missing => :mean_speedup_when_both_converged,
        :speedup_vs_original => median_or_missing => :median_speedup_when_both_converged,
    )
end

function ac_large_speedup_summary(detail::DataFrame)
    nrow(detail) == 0 && return DataFrame()
    large_detail = detail[in.(detail.case_name, Ref(LARGE_PROBLEMATIC_CASES)), :]
    return ac_speedup_summary(large_detail)
end

function large_problematic_table(ac_rows::DataFrame)
    rows = large_problematic_rows(ac_rows)
    return rows[:, [
        :case_name, :variant_name, :converged, :termination_status, :solve_time_sec,
        :n_buses, :n_generators, :generator_to_bus_ratio, :total_pg, :total_pd,
    ]]
end

function plot_ac_convergence(convergence::DataFrame, figure_dir::String)
    labels = convergence.variant_name
    plt = bar(labels, convergence.n_converged; legend=false, xlabel="Variant", ylabel="Converged large/problematic cases", xrotation=35, title="Large/problematic AC PF convergence by variant")
    savefig(plt, joinpath(figure_dir, "ac_convergence_count_by_variant.png"))
end

function plot_large_heatmap(ac_rows::DataFrame, figure_dir::String)
    cases = [case for case in LARGE_PROBLEMATIC_CASES if case in Set(ac_rows.case_name)]
    values = fill(0.0, length(cases), length(AC_VARIANT_ORDER))
    for (i, case) in enumerate(cases), (j, variant) in enumerate(AC_VARIANT_ORDER)
        subset = ac_rows[(ac_rows.case_name .== case) .& (ac_rows.variant_name .== variant), :]
        values[i, j] = nrow(subset) == 0 ? 0.5 : (Bool(subset.converged[1]) ? 1.0 : 0.0)
    end
    plt = heatmap(
        AC_VARIANT_ORDER,
        cases,
        values;
        c=:RdYlGn,
        clims=(0.0, 1.0),
        xlabel="Variant",
        ylabel="Case",
        xrotation=35,
        title="Large/problematic AC PF convergence",
        colorbar=false,
    )
    savefig(plt, joinpath(figure_dir, "ac_large_problematic_convergence_heatmap.png"))
end

function plot_large_solve_time(ac_rows::DataFrame, figure_dir::String)
    rows = ac_rows[in.(ac_rows.case_name, Ref(LARGE_PROBLEMATIC_CASES)) .& ac_rows.converged, :]
    if nrow(rows) == 0
        return
    end
    labels = string.(rows.case_name, " | ", rows.variant_name)
    plt = bar(labels, rows.solve_time_sec; legend=false, yscale=:log10, xlabel="Case | variant", ylabel="solve_time_sec", xrotation=45, title="AC solve time for converged large/problematic cases")
    savefig(plt, joinpath(figure_dir, "ac_large_problematic_solve_time.png"))
end

function plot_runtime_vs_size(ac_rows::DataFrame, figure_dir::String)
    ac_rows = ac_rows[
        [row.solve_time_sec isa Number && isfinite(Float64(row.solve_time_sec)) && row.solve_time_sec > 0 for row in eachrow(ac_rows)],
        :,
    ]
    nrow(ac_rows) == 0 && return
    plt = scatter(
        ac_rows.n_buses,
        ac_rows.solve_time_sec;
        group=ac_rows.variant_name,
        xlabel="n_buses",
        ylabel="solve_time_sec",
        title="AC runtime versus system size",
        legend=:outerright,
        xscale=:log10,
        yscale=:log10,
    )
    savefig(plt, joinpath(figure_dir, "ac_runtime_vs_system_size.png"))
end

function plot_dc_summaries(dc_rows::DataFrame, figure_dir::String)
    status = dc_summary_by_variant(dc_rows)
    plt1 = bar(status.variant_name, status.n_converged; legend=false, xlabel="Variant", ylabel="Converged/available runs", xrotation=25, title="DC PF convergence by variant")
    savefig(plt1, joinpath(figure_dir, "dc_convergence_by_variant.png"))

    available = dc_rows[
        (dc_rows.variant_available .== true) .&
        [value isa Number && isfinite(Float64(value)) && value >= 0 for value in dc_rows.solve_time_sec],
        :,
    ]
    if nrow(available) > 0
        labels = string.(available.case_name, " | ", available.variant_name)
        plt2 = bar(labels, available.solve_time_sec; legend=false, xlabel="Case | variant", ylabel="solve_time_sec", xrotation=45, title="DC solve time by case and variant")
        savefig(plt2, joinpath(figure_dir, "dc_solve_time_by_case_variant.png"))
    end
end

function write_case300_table(ac_rows::DataFrame, output_dir::String)
    rows = ac_rows[ac_rows.case_name .== "case300_ieee", :]
    output = joinpath(output_dir, "powermodels_case300_diagnostic_table.csv")
    CSV.write(output, rows[:, [:variant_name, :converged, :termination_status, :solve_time_sec, :total_pg, :total_pd, :generator_to_bus_ratio]])
end

function write_report(ac_rows::DataFrame, dc_rows::DataFrame, ac_conv::DataFrame, dc_summary::DataFrame, audit_summary::DataFrame, doc_path::String, output_dir::String)
    case_count = length(unique(ac_rows.case_name))
    large_case_count = length(unique(large_problematic_rows(ac_rows).case_name))
    best = ac_conv[argmax(ac_conv.n_converged), :]
    case300 = ac_rows[ac_rows.case_name .== "case300_ieee", :]
    case300_ratio = nrow(case300) == 0 ? "not run" : string(round(case300.generator_to_bus_ratio[1]; digits=4))
    open(doc_path, "w") do io
        println(io, "# PowerModels LF Replacement-Note Sweep")
        println(io)
        println(io, "## Purpose")
        println(io)
        println(io, "This sweep evaluates how selected PGLib replacement-note variants affect PowerModels load-flow behavior. The primary result is AC load-flow convergence with `PowerModels.compute_ac_pf`; the DC sweep is a secondary active-power reference.")
        println(io)
        println(io, "## Methods")
        println(io)
        println(io, "AC LF uses exactly:")
        println(io)
        println(io, "```julia")
        println(io, "PowerModels.compute_ac_pf(data_variant; method=:newton, ftol=1e-8, xtol=1e-8, iterations=100)")
        println(io, "```")
        println(io)
        println(io, "DC LF uses exactly:")
        println(io)
        println(io, "```julia")
        println(io, "PowerModels.compute_dc_pf(data_variant)")
        println(io, "```")
        println(io)
        println(io, "Bus-voltage replacement notes are applied only to `vm_start` and `va_start`. They are initialization values, not final enforced voltage constraints. Generator PG/QG notes modify `gen[...][\"pg\"]` and `gen[...][\"qg\"]` after MW/Mvar to per-unit conversion. Generator VG notes modify `gen[...][\"vg\"]`. Load notes, where present, modify `load[...][\"pd\"]` and `load[...][\"qd\"]` after MW/Mvar to per-unit conversion.")
        println(io)
        println(io, "## Case Set")
        println(io)
        println(io, "Cases swept: $(case_count). The large/problematic subset is `$(join(LARGE_PROBLEMATIC_CASES, "`, `"))`. `case300_ieee` is included as a problematic medium-large benchmark; its generator-to-bus ratio in this sweep is $(case300_ratio). This is a diagnostic hypothesis only, not a causal claim.")
        println(io)
        println(io, "## Mapping Audit")
        println(io)
        println(io, "Mapping audit rows: $(sum(audit_summary.n_audit_rows)); unmatched rows: $(sum(audit_summary.n_unmatched_rows)).")
        println(io)
        println(io, "## Large/Problematic AC Convergence Summary")
        println(io)
        println(io, "The headline convergence rates are computed only over the $(large_case_count) large/problematic cases. Small systems are retained in the row-level CSV, but they are excluded from this summary because flat-start convergence is already expected there.")
        println(io)
        println(io, "The highest large/problematic AC convergence count in this run is `$(best.variant_name)` with $(best.n_converged) converged runs out of $(best.n_runs).")
        println(io)
        println(io, "```text")
        show(io, ac_conv; allrows=true, allcols=true)
        println(io)
        println(io, "```")
        println(io)
        println(io, "The effect is not monotonic; the CSVs should be used for case-by-case interpretation.")
        println(io)
        println(io, "## DC Sweep Summary")
        println(io)
        println(io, "```text")
        show(io, dc_summary; allrows=true, allcols=true)
        println(io)
        println(io, "```")
        println(io)
        println(io, "DC variants exclude bus-voltage, reactive-power, and generator-voltage notes because those quantities do not enter the DC formulation.")
        println(io)
        println(io, "## Outputs")
        println(io)
        rel_output = relpath(output_dir, ROOT)
        println(io, "- AC rows: `$(joinpath(rel_output, "powermodels_ac_pf_sweep_rows.csv"))`")
        println(io, "- DC rows: `$(joinpath(rel_output, "powermodels_dc_pf_sweep_rows.csv"))`")
        println(io, "- Figures: `$(joinpath(rel_output, "figures"))`")
        println(io)
        println(io, "## Cautions")
        println(io)
        println(io, "- This does not reproduce full PGLib replacement-note semantics; only the parsed load, bus-voltage, generator PG/QG, and generator VG note families are used.")
        println(io, "- Iteration counts are not available from `compute_ac_pf` in this workflow and are recorded as unavailable.")
        println(io, "- No PyPowSyBl/OpenLoadFlow comparison is made here. That is the next separate step.")
    end
end

function main()
    case_dir = normpath(joinpath(ROOT, arg_value("--case-dir", "data/raw/pglib/matpower_m")))
    case_glob = arg_value("--case-glob", "pglib_opf_case*.m")
    include_api_sad = arg_bool("--include-api-sad")
    large_only = arg_bool("--large-only")
    api_only = arg_bool("--api-only")
    sad_only = arg_bool("--sad-only")
    api_sad_only = arg_bool("--api-sad-only")
    selected_family_modes = count(identity, [api_only, sad_only, api_sad_only, include_api_sad])
    selected_family_modes > 1 && error("Use only one of --api-only, --sad-only, --api-sad-only, or --include-api-sad.")
    base_only = arg_bool("--base-only") || !(api_only || sad_only || api_sad_only || include_api_sad)
    mode_name = output_mode_name(
        base_only=base_only,
        api_sad_only=api_sad_only,
        api_only=api_only,
        sad_only=sad_only,
        include_api_sad=include_api_sad,
        large_only=large_only,
    )
    paths = output_paths(mode_name)

    if large_only
        case_glob = "pglib_opf_case*.m"
    end
    normalize_bus_types = !arg_bool("--no-normalize-bus-types")
    mkpath(paths.output_dir)
    mkpath(paths.figure_dir)

    case_entries = collect_case_entries(
        case_dir,
        case_glob;
        base_only=base_only,
        api_sad_only=api_sad_only,
        api_only=api_only,
        sad_only=sad_only,
        include_api_sad=include_api_sad,
    )
    if large_only
        case_entries = [
            entry for entry in case_entries
            if case_family_name(splitext(basename(entry.path))[1]) in LARGE_PROBLEMATIC_CASES
        ]
    end
    isempty(case_entries) && error("No cases matched $(joinpath(case_dir, case_glob))")

    ac_rows = Vector{Dict{Symbol,Any}}()
    dc_rows = Vector{Dict{Symbol,Any}}()
    audit_rows = Vector{NamedTuple}()

    for entry in case_entries
        case_path = entry.path
        source_family = source_family_for_entry(entry)
        case_stem = splitext(basename(case_path))[1]
        case_name = case_family_name(case_stem)
        println("Running PowerModels LF replacement sweep for ", case_name)
        base_data, _ = parse_case_for_experiment(case_path; quiet=true, normalize_bus_types=normalize_bus_types)
        stats = case_statistics(base_data)
        notes = parse_replacement_notes(case_path)
        audit = verify_replacement_mapping(base_data, notes)
        push!(audit_rows, audit_summary_row(case_name, notes, audit; source_family=source_family))

        for item in canonical_ac_variants()
            push!(ac_rows, run_one_pf(case_path, case_name, item.name, item.variant, notes, stats; lf_type="AC", source_family=source_family))
        end

        for item in canonical_dc_variants(notes)
            push!(dc_rows, run_one_pf(case_path, case_name, item.name, item.variant, notes, stats; lf_type="DC", available=item.available, source_family=source_family))
        end
    end

    ac = DataFrame(ac_rows)
    dc = DataFrame(dc_rows)
    audit_summary = DataFrame(audit_rows)
    ac_conv = convergence_by_variant(large_problematic_rows(ac))
    ac_large = large_problematic_table(ac)
    dc_summary = dc_summary_by_variant(dc)
    speedup_detail = ac_speedup_detail(ac)
    speedup_summary = ac_speedup_summary(speedup_detail)
    large_speedup_summary = ac_large_speedup_summary(speedup_detail)

    ac_out = round_presentable!(copy(ac))
    dc_out = round_presentable!(copy(dc))
    audit_summary_out = round_presentable!(copy(audit_summary))
    ac_conv_out = round_presentable!(copy(ac_conv))
    ac_large_out = round_presentable!(copy(ac_large))
    dc_summary_out = round_presentable!(copy(dc_summary))
    speedup_detail_out = round_presentable!(copy(speedup_detail))
    speedup_summary_out = round_presentable!(copy(speedup_summary))
    large_speedup_summary_out = round_presentable!(copy(large_speedup_summary))

    CSV.write(joinpath(paths.output_dir, "powermodels_ac_pf_sweep_rows.csv"), ac_out)
    CSV.write(joinpath(paths.output_dir, "powermodels_dc_pf_sweep_rows.csv"), dc_out)
    CSV.write(joinpath(paths.output_dir, "powermodels_ac_pf_convergence_by_variant.csv"), ac_conv_out)
    CSV.write(joinpath(paths.output_dir, "powermodels_ac_pf_large_problematic_cases.csv"), ac_large_out)
    CSV.write(joinpath(paths.output_dir, "powermodels_dc_pf_summary_by_variant.csv"), dc_summary_out)
    CSV.write(joinpath(paths.output_dir, "powermodels_mapping_audit_summary.csv"), audit_summary_out)
    CSV.write(joinpath(paths.output_dir, "powermodels_ac_pf_speedup_vs_original_detail.csv"), speedup_detail_out)
    CSV.write(joinpath(paths.output_dir, "powermodels_ac_pf_speedup_vs_original_summary.csv"), speedup_summary_out)
    CSV.write(joinpath(paths.output_dir, "powermodels_ac_pf_large_speedup_vs_original_summary.csv"), large_speedup_summary_out)
    write_case300_table(ac_out, paths.output_dir)

    plot_ac_convergence(ac_conv, paths.figure_dir)
    plot_large_heatmap(ac, paths.figure_dir)
    plot_large_solve_time(ac, paths.figure_dir)
    plot_runtime_vs_size(ac, paths.figure_dir)
    plot_dc_summaries(dc, paths.figure_dir)
    write_report(ac_out, dc_out, ac_conv_out, dc_summary_out, audit_summary_out, paths.doc_path, paths.output_dir)

    println("Wrote outputs to ", paths.output_dir)
    println("Wrote report to ", paths.doc_path)
end

if abspath(PROGRAM_FILE) == @__FILE__
    main()
end
