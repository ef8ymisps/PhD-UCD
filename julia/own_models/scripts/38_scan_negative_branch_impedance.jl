# Data-quality scan for negative branch resistance/reactance values in the
# MATPOWER/PGLib case library. Uses PowerModels.parse_file so the scan sees
# the same parsed data structure as the OPF validation scripts.

using CSV
using DataFrames
using PowerModels

const ROOT = normpath(joinpath(@__DIR__, "..", "..", ".."))
const CASE_DIR = joinpath(ROOT, "data", "raw", "pglib", "matpower_m", "base_case")
const OUTPUT_DIR = joinpath(ROOT, "results", "own_julia", "data_quality")
const DETAIL_PATH = joinpath(OUTPUT_DIR, "negative_branch_impedance_scan.csv")
const SUMMARY_PATH = joinpath(OUTPUT_DIR, "negative_branch_impedance_summary.csv")

short_case_name(path::String) = replace(replace(basename(path), ".m" => ""), "pglib_opf_case" => "")

function sorted_branch_pairs(branches)
    pairs = collect(branches)
    return sort(pairs; by = pair -> tryparse(Int, String(pair.first)) === nothing ? typemax(Int) : parse(Int, String(pair.first)))
end

function float_value(dict, key::String)
    value = get(dict, key, NaN)
    return value isa Number ? Float64(value) : NaN
end

function string_value(dict, key::String)
    return string(get(dict, key, ""))
end

function int_value(dict, key::String, default::Int)
    value = get(dict, key, default)
    if value isa Number
        return round(Int, value)
    end
    parsed = tryparse(Int, string(value))
    return parsed === nothing ? default : parsed
end

function scan_case(path::String)
    data = PowerModels.parse_file(path)
    case_file = basename(path)
    case_label = short_case_name(path)
    details = NamedTuple[]
    n_negative_r = 0
    n_negative_x = 0
    n_negative_any = 0

    for (branch_id, branch) in sorted_branch_pairs(data["branch"])
        r = float_value(branch, "br_r")
        x = float_value(branch, "br_x")
        neg_r = !isnan(r) && r < 0.0
        neg_x = !isnan(x) && x < 0.0
        if !(neg_r || neg_x)
            continue
        end
        n_negative_r += neg_r ? 1 : 0
        n_negative_x += neg_x ? 1 : 0
        n_negative_any += 1
        push!(details, (
            case_file=case_file,
            case_label=case_label,
            branch_id=String(branch_id),
            branch_index=int_value(branch, "index", tryparse(Int, String(branch_id)) === nothing ? 0 : parse(Int, String(branch_id))),
            f_bus=string_value(branch, "f_bus"),
            t_bus=string_value(branch, "t_bus"),
            br_status=int_value(branch, "br_status", 1),
            r=r,
            x=x,
            has_negative_r=neg_r,
            has_negative_x=neg_x,
        ))
    end

    summary = (
        case_file=case_file,
        case_label=case_label,
        n_branches=length(data["branch"]),
        negative_r_count=n_negative_r,
        negative_x_count=n_negative_x,
        negative_branch_count=n_negative_any,
        has_negative_branch_r=n_negative_r > 0,
        has_negative_branch_x=n_negative_x > 0,
    )
    return details, summary
end

function main()
    mkpath(OUTPUT_DIR)
    case_paths = sort(filter(path -> endswith(path, ".m"), joinpath.(CASE_DIR, readdir(CASE_DIR))))
    all_details = NamedTuple[]
    summaries = NamedTuple[]

    for path in case_paths
        println("Scanning $(basename(path)) ...")
        details, summary = scan_case(path)
        append!(all_details, details)
        push!(summaries, summary)
        if summary.negative_branch_count > 0
            println("  -> negative branches=$(summary.negative_branch_count) r=$(summary.negative_r_count) x=$(summary.negative_x_count)")
        end
    end

    detail_df = isempty(all_details) ? DataFrame(
        case_file=String[], case_label=String[], branch_id=String[], branch_index=Int[],
        f_bus=String[], t_bus=String[], br_status=Int[], r=Float64[], x=Float64[],
        has_negative_r=Bool[], has_negative_x=Bool[],
    ) : DataFrame(all_details)
    summary_df = DataFrame(summaries)

    CSV.write(DETAIL_PATH, detail_df)
    CSV.write(SUMMARY_PATH, summary_df)
    println("\nSaved detail: $(DETAIL_PATH)")
    println("Saved summary: $(SUMMARY_PATH)")
    affected = summary_df[summary_df.negative_branch_count .> 0, :case_label]
    println("Affected cases: $(isempty(affected) ? "none" : join(affected, ", "))")
end

main()
