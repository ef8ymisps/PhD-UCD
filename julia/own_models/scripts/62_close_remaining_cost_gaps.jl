# Closes the last known cost-objective gaps found while auditing the final
# validation_scenarios tables: these 5 branch-outage scenarios already have
# a PowerModels reference JSON (exported by an earlier Julia run) and a
# converged/infeasible Python cost-objective row, but no corresponding
# Julia own-model cost row in julia_pm_like/summary.csv at all -- i.e. some
# earlier full-sweep run solved and exported them, then never appended the
# summary row (run interrupted, or an error swallowed between the export
# and the summary write). Re-solving is cheap; export_reference=true is
# passed for idempotence even though the reference JSON already exists.

const TWENTYFIVE_SCRIPT_PATH_62 = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH_62, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH_62).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH_62)
end

const TARGETS_62 = [
    ("300_ieee", "pglib_opf_case300_ieee", "137"),
    ("1354_pegase", "pglib_opf_case1354_pegase", "829"),
    ("6470_rte", "pglib_opf_case6470_rte", "7060"),
    ("6470_rte", "pglib_opf_case6470_rte", "7138"),
    ("6470_rte", "pglib_opf_case6470_rte", "7174"),
]

function main()
    ac_optimizer = build_ac_optimizer()
    base_data_cache = Dict{String,Any}()

    for (label, case_file, branch_id) in TARGETS_62
        scenario_id = "$(label)_branch_$(branch_id)_out__branch_outage__branch__$(branch_id)__status__0p0"
        base_data = get!(base_data_cache, label) do
            d = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
            d["name"] = label
            d
        end
        print("$(scenario_id) (generation cost) ... ")
        t = @elapsed begin
            try
                data = deepcopy(base_data)
                apply_branch_outage!(data, branch_id)
                process_data(
                    label,
                    scenario_id,
                    data;
                    ac_optimizer=ac_optimizer,
                    export_reference=true,
                    own_variant=:pm_like_zero,
                )
            catch err
                println("ERROR: ", sprint(showerror, err))
            end
        end
        println("done ($(round(t, digits=1))s)")
        GC.gc()
    end

    println("\nRemaining cost-gap-closing run complete.")
end

main()
