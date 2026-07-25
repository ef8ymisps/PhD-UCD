# Closes a known gap: case1354_pegase's 3 qg_limits scenarios
# (reactive_generation_limits, multipliers 1.0/0.8/0.6) were already solved
# for both objectives by Julia's own model (present with matching own/PM
# values in both julia_pm_like/summary.csv and
# three_way_active_losses/julia_full_sweep.csv), but no PowerModels
# reference JSON was ever exported for them under
# results/reference/powermodels/ac_opf/ -- so Python's reference-JSON-driven
# discovery (discover_scenarios in scripts 23/34/55) never sees these 3
# scenarios at all and can't solve either objective for them.
#
# Only the missing artifact -- the PowerModels reference JSON pair
# (*_case.json / *_reference.json) -- is produced here; the already-correct
# Julia summary rows are left untouched.

const TWENTYFIVE_SCRIPT_PATH_59 = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH_59, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH_59).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH_59)
end

function main()
    label = "1354_pegase"
    case_file = "pglib_opf_case1354_pegase"
    ac_optimizer = build_ac_optimizer()

    data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
    entries = compact_scenarios(data, label, ac_optimizer; include_no_reference=true)
    targets = filter(e -> occursin("qg_limits", String(e.scenario_id)), entries)
    println("Found $(length(targets)) qg_limits scenario(s) for $(label).")

    for entry in targets
        raw_id = String(entry.scenario_id)
        stem = "pglib_opf_case$(label)_$(raw_id)"
        case_path = joinpath(REFERENCE_DIR, "$(stem)_case.json")
        if isfile(case_path)
            println("  $(raw_id) -- reference already present, skipping")
            continue
        end
        print("  $(raw_id) -- solving PowerModels for reference export ... ")
        t = @elapsed begin
            pm_model = PowerModels.instantiate_model(deepcopy(entry.data), PowerModels.ACPPowerModel, PowerModels.build_opf)
            pm_start = time()
            pm_result = PowerModels.optimize_model!(pm_model, optimizer=ac_optimizer)
            pm_solve_time_sec = time() - pm_start
            pm_iterations = OwnPowerModels._safe_barrier_iterations(pm_model.model)
            export_reference_json(label, raw_id, entry.data, pm_result, pm_iterations, pm_solve_time_sec)
        end
        println("done ($(round(t, digits=1))s) status=$(get(pm_result, "termination_status", "UNKNOWN"))")
    end

    println("\n1354_pegase qg_limits reference export complete.")
end

main()
