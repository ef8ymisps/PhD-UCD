# Backfill the known 6470_rte outage scenarios for the Julia PM-like model.
#
# These scenarios were selected in the earlier full validation plan, but a
# max_iter=500 resume cannot regenerate them because the selection OPF itself
# stops at ITERATION_LIMIT. This script applies the known outage mutations
# directly and reuses script 25's process_data exporter.

const TWENTYFIVE_SCRIPT_PATH = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH)
end

function backfill_optimizer(max_iter::Int)
    return optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => max_iter)
end

function completed_pairs_for(summary_path::String)
    completed = Set{Tuple{String,String}}()
    isfile(summary_path) || return completed
    df = DataFrame(CSV.File(summary_path; select=[:case, :scenario]))
    for row in eachrow(df)
        push!(completed, (String(row.case), String(row.scenario)))
    end
    return completed
end

const TARGET_BRANCH_OUTAGES = ["2170", "7043", "7119", "7261", "8846", "8932"]
const TARGET_GENERATOR_OUTAGES = ["59", "651", "1171", "1172"]

function main()
    max_iter = parse(Int, arg_value("--max-iter", "500"))
    own_variant = :pm_like_zero
    label = "6470_rte"
    _, summary_path = own_variant_paths(own_variant)
    completed = completed_pairs_for(summary_path)
    ac_optimizer = backfill_optimizer(max_iter)
    base = PowerModels.parse_file(joinpath(CASE_DIR, "pglib_opf_case6470_rte.m"))
    base["name"] = label

    println("Backfilling Julia PM-like 6470 outage scenarios")
    println("Ipopt max_iter: $(max_iter)")
    println("Already completed pairs: $(length(completed))")

    for branch_id in TARGET_BRANCH_OUTAGES
        scenario_id = "$(label)_branch_$(branch_id)_out__branch_outage__branch__$(branch_id)__status__0p0"
        if (label, scenario_id) in completed
            println("  $(scenario_id) -- already done, skipping")
            continue
        end
        data = deepcopy(base)
        apply_branch_outage!(data, branch_id)
        print("  $(scenario_id) ... ")
        t = @elapsed begin
            try
                process_data(label, scenario_id, data; ac_optimizer=ac_optimizer, export_reference=false, own_variant=own_variant)
            catch err
                println("ERROR: ", sprint(showerror, err))
            end
        end
        println("done ($(round(t, digits=1))s)")
        push!(completed, (label, scenario_id))
        GC.gc()
    end

    for gen_id in TARGET_GENERATOR_OUTAGES
        scenario_id = "$(label)_gen_$(gen_id)_out__generator_outage__generator__$(gen_id)__status__0p0"
        if (label, scenario_id) in completed
            println("  $(scenario_id) -- already done, skipping")
            continue
        end
        data = deepcopy(base)
        apply_generator_outage!(data, gen_id)
        print("  $(scenario_id) ... ")
        t = @elapsed begin
            try
                process_data(label, scenario_id, data; ac_optimizer=ac_optimizer, export_reference=false, own_variant=own_variant)
            catch err
                println("ERROR: ", sprint(showerror, err))
            end
        end
        println("done ($(round(t, digits=1))s)")
        push!(completed, (label, scenario_id))
        GC.gc()
    end

    println("Backfill complete.")
end

main()
