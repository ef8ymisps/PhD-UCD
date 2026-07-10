using PowerModels
using DataFrames
using CSV
using Logging

const SECTION_RE = r"^%\s+INFO\s+:\s+===\s+(.+?)\s+==="
const LOAD_RE = r"Bus\s+(\d+)\s*:\s*Pd=([-+.\deE]+),\s*Qd=([-+.\deE]+)\s*->\s*Pd=([-+.\deE]+),\s*Qd=([-+.\deE]+)"
const BUS_VOLTAGE_RE = r"Bus\s+(\d+)\s*:\s*V=([-+.\deE]+),\s*theta=([-+.\deE]+)\s*->\s*V=([-+.\deE]+),\s*theta=([-+.\deE]+)"
const GEN_PQ_RE = r"Gen at bus\s+(\d+)(?:\s+-\s+[^:\t]+)?\s*:\s*Pg=([-+.\deE]+),\s*Qg=([-+.\deE]+)\s*->\s*Pg=([-+.\deE]+),\s*Qg=([-+.\deE]+)"
const GEN_VG_RE = r"Gen at bus\s+(\d+)(?:\s+-\s+[^:\t]+)?\s*:\s*Vg=([-+.\deE]+)\s*->\s*Vg=([-+.\deE]+)"

function _empty_notes()
    return (
        load = DataFrame(line_no=Int[], section_id=Int[], bus=Int[], order_at_bus=Int[], Pd_from=Float64[], Qd_from=Float64[], Pd_to=Float64[], Qd_to=Float64[]),
        bus_voltage = DataFrame(line_no=Int[], section_id=Int[], bus=Int[], order_at_bus=Int[], V_from=Float64[], theta_from=Float64[], V_to=Float64[], theta_to=Float64[]),
        gen_pgqg = DataFrame(line_no=Int[], section_id=Int[], bus=Int[], order_at_bus=Int[], Pg_from=Float64[], Qg_from=Float64[], Pg_to=Float64[], Qg_to=Float64[]),
        gen_vg = DataFrame(line_no=Int[], section_id=Int[], bus=Int[], order_at_bus=Int[], Vg_from=Float64[], Vg_to=Float64[]),
    )
end

function _add_order_at_bus!(df::DataFrame)
    if nrow(df) == 0
        return df
    end

    counters = Dict{Tuple{Int, Int}, Int}()
    orders = Int[]
    for row in eachrow(df)
        key = (row.section_id, row.bus)
        counters[key] = get(counters, key, 0) + 1
        push!(orders, counters[key])
    end
    df.order_at_bus = orders
    return df
end

function parse_replacement_notes(case_path::AbstractString)
    load = DataFrame(line_no=Int[], section_id=Int[], bus=Int[], order_at_bus=Int[], Pd_from=Float64[], Qd_from=Float64[], Pd_to=Float64[], Qd_to=Float64[])
    bus_voltage = DataFrame(line_no=Int[], section_id=Int[], bus=Int[], order_at_bus=Int[], V_from=Float64[], theta_from=Float64[], V_to=Float64[], theta_to=Float64[])
    gen_pgqg = DataFrame(line_no=Int[], section_id=Int[], bus=Int[], order_at_bus=Int[], Pg_from=Float64[], Qg_from=Float64[], Pg_to=Float64[], Qg_to=Float64[])
    gen_vg = DataFrame(line_no=Int[], section_id=Int[], bus=Int[], order_at_bus=Int[], Vg_from=Float64[], Vg_to=Float64[])

    section = ""
    section_id = 0

    for (line_no, line) in enumerate(eachline(case_path))
        section_match = match(SECTION_RE, line)
        if section_match !== nothing
            section = section_match.captures[1]
            section_id += 1
            continue
        end

        if section == "Load Replacement Notes"
            note = match(LOAD_RE, line)
            if note !== nothing
                push!(load, (
                    line_no, section_id, parse(Int, note.captures[1]), 0,
                    parse(Float64, note.captures[2]), parse(Float64, note.captures[3]),
                    parse(Float64, note.captures[4]), parse(Float64, note.captures[5]),
                ))
            end
        elseif section == "Voltage Setpoint Replacement Notes"
            note = match(BUS_VOLTAGE_RE, line)
            if note !== nothing
                push!(bus_voltage, (
                    line_no, section_id, parse(Int, note.captures[1]), 0,
                    parse(Float64, note.captures[2]), parse(Float64, note.captures[3]),
                    parse(Float64, note.captures[4]), parse(Float64, note.captures[5]),
                ))
            end
        elseif section == "Generator Setpoint Replacement Notes"
            pq_note = match(GEN_PQ_RE, line)
            vg_note = match(GEN_VG_RE, line)
            if pq_note !== nothing
                push!(gen_pgqg, (
                    line_no, section_id, parse(Int, pq_note.captures[1]), 0,
                    parse(Float64, pq_note.captures[2]), parse(Float64, pq_note.captures[3]),
                    parse(Float64, pq_note.captures[4]), parse(Float64, pq_note.captures[5]),
                ))
            elseif vg_note !== nothing
                push!(gen_vg, (
                    line_no, section_id, parse(Int, vg_note.captures[1]), 0,
                    parse(Float64, vg_note.captures[2]), parse(Float64, vg_note.captures[3]),
                ))
            end
        end
    end

    _add_order_at_bus!(load)
    _add_order_at_bus!(bus_voltage)
    _add_order_at_bus!(gen_pgqg)
    _add_order_at_bus!(gen_vg)

    return (load=load, bus_voltage=bus_voltage, gen_pgqg=gen_pgqg, gen_vg=gen_vg)
end

function bus_number(bus_key::AbstractString, bus::Dict)
    value = get(bus, "bus_i", nothing)
    if value !== nothing
        return Int(round(value))
    end
    return parse(Int, bus_key)
end

function build_bus_key_by_number(data::Dict)
    mapping = Dict{Int, String}()
    for (bus_key, bus) in data["bus"]
        mapping[bus_number(bus_key, bus)] = bus_key
    end
    return mapping
end

function build_powermodels_mapping(data::Dict)
    bus_key_by_number = build_bus_key_by_number(data)

    gen_rows = DataFrame(gen_id=String[], gen_bus=Int[], order_at_bus=Int[], status=Int[], pg=Float64[], qg=Float64[], vg=Float64[], pmin=Float64[], pmax=Float64[], qmin=Float64[], qmax=Float64[])
    gen_counters = Dict{Int, Int}()
    for gen_id in sort(collect(keys(data["gen"])); by=x -> parse(Int, x))
        gen = data["gen"][gen_id]
        bus = Int(round(gen["gen_bus"]))
        gen_counters[bus] = get(gen_counters, bus, 0) + 1
        push!(gen_rows, (
            gen_id,
            bus,
            gen_counters[bus],
            Int(round(get(gen, "gen_status", 1))),
            Float64(get(gen, "pg", NaN)),
            Float64(get(gen, "qg", NaN)),
            Float64(get(gen, "vg", NaN)),
            Float64(get(gen, "pmin", NaN)),
            Float64(get(gen, "pmax", NaN)),
            Float64(get(gen, "qmin", NaN)),
            Float64(get(gen, "qmax", NaN)),
        ))
    end

    load_rows = DataFrame(load_id=String[], load_bus=Int[], order_at_bus=Int[], status=Int[], pd=Float64[], qd=Float64[])
    load_counters = Dict{Int, Int}()
    if haskey(data, "load")
        for load_id in sort(collect(keys(data["load"])); by=x -> parse(Int, x))
            load = data["load"][load_id]
            bus = Int(round(load["load_bus"]))
            load_counters[bus] = get(load_counters, bus, 0) + 1
            push!(load_rows, (
                load_id,
                bus,
                load_counters[bus],
                Int(round(get(load, "status", 1))),
                Float64(get(load, "pd", NaN)),
                Float64(get(load, "qd", NaN)),
            ))
        end
    end

    return (bus_key_by_number=bus_key_by_number, generators=gen_rows, loads=load_rows)
end

function active_generator_buses(data::Dict)
    buses = Set{Int}()
    for gen in values(data["gen"])
        if Int(round(get(gen, "gen_status", 1))) != 0
            push!(buses, Int(round(gen["gen_bus"])))
        end
    end
    return buses
end

function normalize_bus_types!(data::Dict)
    active_buses = active_generator_buses(data)
    corrections = DataFrame(
        bus=Int[],
        old_bus_type=Int[],
        new_bus_type=Int[],
        reason=String[],
    )

    for (bus_key, bus) in data["bus"]
        mat_bus = bus_number(bus_key, bus)
        old_type = Int(round(get(bus, "bus_type", 1)))
        new_type = old_type
        reason = ""

        if mat_bus in active_buses && old_type == 1
            new_type = 2
            reason = "active generator at PQ bus"
        elseif !(mat_bus in active_buses) && old_type in (2, 3)
            new_type = 1
            reason = "PV/ref bus without active generator"
        end

        if new_type != old_type
            bus["bus_type"] = new_type
            push!(corrections, (mat_bus, old_type, new_type, reason))
        end
    end

    has_reference = any(Int(round(get(bus, "bus_type", 1))) == 3 for bus in values(data["bus"]))
    if !has_reference && !isempty(active_buses)
        gen_rows = [
            (
                bus = Int(round(gen["gen_bus"])),
                pmax = Float64(get(gen, "pmax", 0.0)),
            )
            for gen in values(data["gen"])
            if Int(round(get(gen, "gen_status", 1))) != 0
        ]
        ref_bus = sort(gen_rows; by=x -> x.pmax, rev=true)[1].bus
        bus_key_by_number = build_bus_key_by_number(data)
        ref_key = bus_key_by_number[ref_bus]
        old_type = Int(round(get(data["bus"][ref_key], "bus_type", 1)))
        data["bus"][ref_key]["bus_type"] = 3
        push!(corrections, (ref_bus, old_type, 3, "selected largest-generator reference bus"))
    end

    return corrections
end

function parse_case_for_experiment(case_path::AbstractString; quiet::Bool=true, normalize_bus_types::Bool=true)
    data = if quiet
        with_logger(NullLogger()) do
            PowerModels.parse_file(case_path)
        end
    else
        PowerModels.parse_file(case_path)
    end

    corrections = normalize_bus_types ? normalize_bus_types!(data) : DataFrame()
    return data, corrections
end

function inspect_bus_type_corrections(case_path::AbstractString; quiet::Bool=true)
    _, corrections = parse_case_for_experiment(case_path; quiet=quiet, normalize_bus_types=true)
    return corrections
end

function _match_generator(mapping, note_bus::Int, order_at_bus::Int)
    rows = mapping.generators[(mapping.generators.gen_bus .== note_bus) .& (mapping.generators.order_at_bus .== order_at_bus), :]
    if nrow(rows) == 1
        return rows[1, :], "bus_order", ""
    elseif nrow(rows) > 1
        return rows[1, :], "bus_order_ambiguous_first", "multiple generator candidates"
    end
    return nothing, "", "no PowerModels generator mapped"
end

function _match_load(mapping, note_bus::Int, order_at_bus::Int)
    rows = mapping.loads[(mapping.loads.load_bus .== note_bus) .& (mapping.loads.order_at_bus .== order_at_bus), :]
    if nrow(rows) == 1
        return rows[1, :], "bus_order", ""
    elseif nrow(rows) > 1
        return rows[1, :], "bus_order_ambiguous_first", "multiple load candidates"
    end
    return nothing, "", "no PowerModels load mapped"
end

function _empty_audit()
    return DataFrame(
        note_type=String[],
        matpower_bus=Int[],
        section_id=Int[],
        order_at_bus=Int[],
        field=String[],
        matched_object_kind=String[],
        matched_object_id=Union{Missing,String}[],
        old_value=Any[],
        note_from_value=Any[],
        note_to_value=Any[],
        applied_value=Any[],
        applied=Bool[],
        reason=String[],
        mapping_method=String[],
    )
end

function verify_replacement_mapping(data::Dict, notes)
    mapping = build_powermodels_mapping(data)
    audit = _empty_audit()

    for note in eachrow(notes.load)
        load, method, reason = _match_load(mapping, note.bus, note.order_at_bus)
        matched = load !== nothing
        for (field, from_value, to_value) in (
            ("pd", note.Pd_from, note.Pd_to),
            ("qd", note.Qd_from, note.Qd_to),
        )
            push!(audit, (
                "load", note.bus, note.section_id, note.order_at_bus, field,
                matched ? "load" : "", matched ? string(load.load_id) : missing,
                matched ? data["load"][string(load.load_id)][field] : missing,
                from_value, to_value, missing, false, reason, method,
            ))
        end
    end

    for note in eachrow(notes.bus_voltage)
        bus_key = get(mapping.bus_key_by_number, note.bus, nothing)
        matched = bus_key !== nothing
        for (field, from_value, to_value) in (
            ("vm", note.V_from, note.V_to),
            ("va", deg2rad(note.theta_from), deg2rad(note.theta_to)),
        )
            push!(audit, (
                "bus_voltage", note.bus, note.section_id, note.order_at_bus, field,
                matched ? "bus" : "", matched ? bus_key : missing,
                matched ? data["bus"][bus_key][field] : missing,
                from_value, to_value, missing, false,
                matched ? "" : "no PowerModels bus mapped", matched ? "bus_number" : "",
            ))
        end
    end

    for note in eachrow(notes.gen_pgqg)
        gen, method, reason = _match_generator(mapping, note.bus, note.order_at_bus)
        matched = gen !== nothing
        for (field, from_value, to_value) in (
            ("pg", note.Pg_from, note.Pg_to),
            ("qg", note.Qg_from, note.Qg_to),
        )
            push!(audit, (
                "gen_pgqg", note.bus, note.section_id, note.order_at_bus, field,
                matched ? "generator" : "", matched ? string(gen.gen_id) : missing,
                matched ? data["gen"][string(gen.gen_id)][field] : missing,
                from_value, to_value, missing, false, reason, method,
            ))
        end
    end

    for note in eachrow(notes.gen_vg)
        gen, method, reason = _match_generator(mapping, note.bus, note.order_at_bus)
        matched = gen !== nothing
        push!(audit, (
            "gen_vg", note.bus, note.section_id, note.order_at_bus, "vg",
            matched ? "generator" : "", matched ? string(gen.gen_id) : missing,
            matched ? data["gen"][string(gen.gen_id)]["vg"] : missing,
            note.Vg_from, note.Vg_to, missing, false, reason, method,
        ))
    end

    return audit
end

function make_replacement_variants()
    return Dict(
        "original" => (groups=String[], direction="from"),
        "BV_from_warmstart" => (groups=["BV"], direction="from"),
        "BV_to_warmstart" => (groups=["BV"], direction="to"),
        "G_PG_from" => (groups=["G_PG"], direction="from"),
        "G_PGQG_from" => (groups=["G_PG", "G_QG"], direction="from"),
        "G_PGQG_to" => (groups=["G_PG", "G_QG"], direction="to"),
        "G_PGQG_from_plus_GVG_from" => (groups=["G_PG", "G_QG", "G_VG"], direction="from"),
        "G_PGQG_from_plus_BV_from" => (groups=["G_PG", "G_QG", "BV"], direction="from"),
    )
end

function make_nonoverlapping_voltage_variants()
    variants = make_replacement_variants()
    return [
        variants["original"],
        variants["BV_from_warmstart"],
        variants["G_PGQG_from"],
        variants["G_PGQG_from_plus_BV_from"],
        variants["G_PGQG_from_plus_GVG_from"],
    ]
end

function _direction_value(note, base::String, direction::String)
    return getproperty(note, Symbol(base * "_" * direction))
end

function base_mva(data::Dict)
    return Float64(get(data, "baseMVA", get(data, "base_mva", 1.0)))
end

function power_note_to_pu(data::Dict, value)
    return Float64(value) / base_mva(data)
end

function apply_replacement_variant!(data::Dict, notes, variant)
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

    if "BV" in groups
        for note in eachrow(notes.bus_voltage)
            bus_key = get(mapping.bus_key_by_number, note.bus, nothing)
            if bus_key === nothing
                continue
            end
            data["bus"][bus_key]["vm"] = _direction_value(note, "V", direction)
            data["bus"][bus_key]["va"] = deg2rad(_direction_value(note, "theta", direction))
            counts["n_applied_bus_voltage"] += 1
        end
    end

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

    return audit, counts
end

function call_set_ac_pf_start_values!(data::Dict)
    if isdefined(PowerModels, Symbol("set_ac_pf_start_values!"))
        getfield(PowerModels, Symbol("set_ac_pf_start_values!"))(data)
    elseif isdefined(PowerModels, :set_ac_pf_start_values)
        result = PowerModels.set_ac_pf_start_values(data)
        if result !== nothing
            data = result
        end
    else
        error("PowerModels does not expose set_ac_pf_start_values! or set_ac_pf_start_values")
    end

    for bus in values(data["bus"])
        if haskey(bus, "vm")
            bus["vm_start"] = bus["vm"]
        end
        if haskey(bus, "va")
            bus["va_start"] = bus["va"]
        end
    end

    for gen in values(data["gen"])
        if haskey(gen, "pg")
            gen["pg_start"] = gen["pg"]
        end
        if haskey(gen, "qg")
            gen["qg_start"] = gen["qg"]
        end
        if haskey(gen, "vg")
            gen["vg_start"] = gen["vg"]
        end
    end

    return data
end

function run_nr_ac_pf(data::Dict; max_iter::Int=100, tolerance::Float64=1e-8, show_trace::Bool=false)
    if !isdefined(PowerModels, :compute_ac_pf)
        error("PowerModels.compute_ac_pf is not available")
    end
    compute_ac_pf = PowerModels.compute_ac_pf
    attempts = (
        (max_iter=max_iter, tolerance=tolerance, show_trace=show_trace),
        (max_iter=max_iter, tol=tolerance, show_trace=show_trace),
        (max_iteration=max_iter, tolerance=tolerance, show_trace=show_trace),
        (show_trace=show_trace,),
        NamedTuple(),
    )
    last_error = nothing
    for kwargs in attempts
        try
            return compute_ac_pf(data; kwargs...)
        catch err
            last_error = err
        end
    end
    error("compute_ac_pf failed for all known keyword signatures. Last error: $(last_error)")
end

function balance(data::Dict)
    total_gen = sum(Float64(get(gen, "pg", 0.0)) for gen in values(data["gen"]) if get(gen, "gen_status", 1) != 0)
    total_load = haskey(data, "load") ? sum(Float64(get(load, "pd", 0.0)) for load in values(data["load"]) if get(load, "status", 1) != 0) : 0.0
    base = base_mva(data)
    return (
        total_generation=total_gen,
        total_load=total_load,
        gen_minus_load=total_gen - total_load,
        total_generation_mw=total_gen * base,
        total_load_mw=total_load * base,
        gen_minus_load_mw=(total_gen - total_load) * base,
    )
end

function start_value_summary(data::Dict)
    buses = values(data["bus"])
    generators = values(data["gen"])
    return (
        n_bus_vm_start = count(bus -> haskey(bus, "vm_start"), buses),
        n_bus_va_start = count(bus -> haskey(bus, "va_start"), buses),
        n_gen_pg_start = count(gen -> haskey(gen, "pg_start"), generators),
        n_gen_qg_start = count(gen -> haskey(gen, "qg_start"), generators),
        n_gen_vg_start = count(gen -> haskey(gen, "vg_start"), generators),
    )
end

function replacement_variant_name(variant)
    for (name, value) in make_replacement_variants()
        if value == variant
            return name
        end
    end
    return "custom"
end

function run_case_experiment_matrix(
    case_path::AbstractString;
    replacement_variants = [make_replacement_variants()["original"]],
    max_iter::Int = 100,
    tolerance::Float64 = 1e-8,
    show_trace::Bool = false,
    quiet_parse::Bool = true,
    normalize_bus_types::Bool = true,
    write_csv::Bool = false,
    output_csv::AbstractString = "powermodels_replacement_matrix_summary.csv",
)
    notes = parse_replacement_notes(case_path)
    rows = DataFrame()
    audits = DataFrame()

    for variant in replacement_variants
        data, bus_type_corrections = parse_case_for_experiment(
            case_path;
            quiet=quiet_parse,
            normalize_bus_types=normalize_bus_types,
        )
        before = balance(data)
        audit, counts = apply_replacement_variant!(data, notes, variant)
        after = balance(data)
        append!(audits, audit, cols=:union)

        status = "NOT_RUN"
        solve_time = missing
        exception = missing
        starts = (n_bus_vm_start=0, n_bus_va_start=0, n_gen_pg_start=0, n_gen_qg_start=0, n_gen_vg_start=0)
        try
            call_set_ac_pf_start_values!(data)
            starts = start_value_summary(data)
            result = run_nr_ac_pf(data; max_iter=max_iter, tolerance=tolerance, show_trace=show_trace)
            status = string(get(result, "termination_status", "missing"))
            solve_time = get(result, "solve_time", missing)
        catch err
            status = "EXCEPTION"
            exception = sprint(showerror, err)
            starts = start_value_summary(data)
        end

        unmatched = audit[ismissing.(audit.matched_object_id), :]
        append!(
            rows,
            DataFrame([(
                case_name = splitext(basename(case_path))[1],
                variant_name = replacement_variant_name(variant),
                groups = join(variant.groups, "+"),
                direction = variant.direction,
                n_bus_voltage_notes = nrow(notes.bus_voltage),
                n_load_notes = nrow(notes.load),
                n_gen_pgqg_notes = nrow(notes.gen_pgqg),
                n_gen_vg_notes = nrow(notes.gen_vg),
                n_unmatched_rows = nrow(unmatched),
                mapping_success = nrow(unmatched) == 0,
                total_generation_before = before.total_generation,
                total_load_before = before.total_load,
                gen_minus_load_before = before.gen_minus_load,
                gen_minus_load_mw_before = before.gen_minus_load_mw,
                total_generation_after = after.total_generation,
                total_load_after = after.total_load,
                gen_minus_load_after = after.gen_minus_load,
                gen_minus_load_mw_after = after.gen_minus_load_mw,
                n_applied_bus_voltage = counts["n_applied_bus_voltage"],
                n_applied_gen_pg = counts["n_applied_gen_pg"],
                n_applied_gen_qg = counts["n_applied_gen_qg"],
                n_applied_gen_vg = counts["n_applied_gen_vg"],
                n_applied_load = counts["n_applied_load"],
                n_bus_type_corrections = nrow(bus_type_corrections),
                n_bus_vm_start = starts.n_bus_vm_start,
                n_bus_va_start = starts.n_bus_va_start,
                n_gen_pg_start = starts.n_gen_pg_start,
                n_gen_qg_start = starts.n_gen_qg_start,
                n_gen_vg_start = starts.n_gen_vg_start,
                termination_status = status,
                solve_time = solve_time,
                exception = exception,
            )]),
            cols=:union,
        )
    end

    if write_csv
        CSV.write(output_csv, rows)
    end

    return rows, audits
end
