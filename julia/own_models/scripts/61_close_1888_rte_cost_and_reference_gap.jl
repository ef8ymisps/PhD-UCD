# Closes a third gap found while rebuilding the final validation_scenarios
# tables: case1888_rte's branches 2284, 2306, 2307 are the CURRENT top-6
# branch-outage selection a fresh compact_scenarios call makes today (used
# by scripts 48/58's active-losses sweep), but neither language's
# generation-cost sweep (julia_pm_like/summary.csv, python_pm_like/summary.csv)
# has ever solved them, and no PowerModels reference JSON was ever exported
# for them either -- the mirror image of the previously-known
# 6470_rte/1888_rte gaps (which were solved-for-cost-but-not-losses; these
# are solved-for-losses-but-not-cost-at-all).
#
# Reuses process_data(...; export_reference=true) directly (the same
# per-scenario worker 27_run_own_vs_powermodels_full_sweep.jl calls), which
# in one call: solves own model + PowerModels under generation-cost,
# appends to julia_pm_like/summary.csv, and exports the reference JSON pair
# -- so Python's own cost sweep (script 34) can discover and solve these 3
# scenarios afterward exactly like any other.

const TWENTYFIVE_SCRIPT_PATH_61 = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH_61, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH_61).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH_61)
end

const TARGET_BRANCH_IDS_61 = ["2284", "2306", "2307"]

function main()
    ac_optimizer = build_ac_optimizer()
    label = "1888_rte"
    case_file = "pglib_opf_case1888_rte"
    base_data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
    base_data["name"] = label

    for branch_id in TARGET_BRANCH_IDS_61
        scenario_id = "$(label)_branch_$(branch_id)_out__branch_outage__branch__$(branch_id)__status__0p0"
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

    println("\n1888_rte cost+reference gap-closing run complete.")
end

main()
