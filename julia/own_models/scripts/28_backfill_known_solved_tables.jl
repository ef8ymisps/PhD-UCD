# Backfill per-scenario comparison tables only for scenarios that are already
# known to solve in both the PM-like own model and PowerModels.
#
# This intentionally avoids rerunning scenarios previously classified as
# LOCALLY_INFEASIBLE or ITERATION_LIMIT. It is for filling missing
# buses/generators/branches/binding CSVs, not for discovering new statuses.

const TWENTYFIVE_SCRIPT_PATH = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH)
end

const KNOWN_LABELS = Dict(
    "1888_rte__base__none__none__none__none" => "base",
    "1888_rte_load_x0p8__load_scaling__none__none__load_multiplier__0p8" => "load_x0.8",
    "1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2" => "load_x1.2_reactive_stress",
    "1888_rte_qg_limits_x0p8__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p8" => "qg_limits_x0.8",
)

const REQUIRED_TABLES = (
    "branches.csv",
    "buses.csv",
    "constraints_binding.csv",
    "constraints_inventory.csv",
    "generators.csv",
)

function solved_status(text)
    return text in ("OPTIMAL", "LOCALLY_SOLVED", "ALMOST_OPTIMAL", "ALMOST_LOCALLY_SOLVED")
end

function selected_cases()
    raw = arg_value("--cases", "1354_pegase,1888_rte,6470_rte")
    return Set(strip.(split(raw, ",")))
end

function full_sweep_ac_optimizer()
    return optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => 1000)
end

function expected_scenario_label(entry)
    raw_id = String(entry.scenario_id)
    if haskey(KNOWN_LABELS, raw_id)
        return KNOWN_LABELS[raw_id]
    elseif entry.scenario == "base"
        return "base"
    else
        return raw_id
    end
end

function solved_targets(summary_path, cases)
    targets = Set{Tuple{String,String}}()
    df = DataFrame(CSV.File(summary_path))
    for row in eachrow(df)
        base_case = String(row.base_case)
        if base_case in cases &&
           solved_status(String(row.pmlike_status)) &&
           solved_status(String(row.pm_status_pmlike_run))
            push!(targets, (base_case, String(row.scenario_id)))
        end
    end
    return targets
end

function has_required_tables(out_root, label, scenario_label)
    dir = joinpath(out_root, label, safe_scenario_folder(scenario_label))
    return all(name -> isfile(joinpath(dir, name)), REQUIRED_TABLES)
end

function main()
    cases = selected_cases()
    summary_path = joinpath(ROOT, "results", "own_julia", "ac_opf_pm_like_validation_summary", "scenario_side_by_side.csv")
    targets = solved_targets(summary_path, cases)
    out_root, _ = own_variant_paths(:pm_like_zero)
    ac_optimizer = full_sweep_ac_optimizer()

    println("Known solved PM-like targets: $(length(targets))")
    println("Cases: $(join(sort(collect(cases)), ", "))")
    println("Output root: $(out_root)")

    attempted = 0
    skipped_complete = 0
    skipped_not_known_solved = 0

    for case_file in DEFAULT_CASES
        label = short_case_name(case_file)
        label in cases || continue

        data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
        entries = compact_scenarios(data, label, ac_optimizer; include_no_reference=true)
        println("\n=== $(label): $(length(entries)) scenarios ===")

        for (i, entry) in enumerate(entries)
            raw_id = String(entry.scenario_id)
            scenario_label = expected_scenario_label(entry)
            if !((label, raw_id) in targets)
                skipped_not_known_solved += 1
                continue
            end
            if has_required_tables(out_root, label, scenario_label)
                skipped_complete += 1
                println("  [$(i)/$(length(entries))] $(scenario_label) -- tables already present")
                continue
            end

            attempted += 1
            print("  [$(i)/$(length(entries))] $(scenario_label) -- backfilling ... ")
            t = @elapsed begin
                process_data(
                    label,
                    scenario_label,
                    entry.data;
                    ac_optimizer=ac_optimizer,
                    own_variant=:pm_like_zero,
                    write_summary=false,
                )
            end
            println("done ($(round(t, digits=1))s)")
            GC.gc()
        end
    end

    println("\nBackfill complete.")
    println("  attempted=$(attempted)")
    println("  skipped_complete=$(skipped_complete)")
    println("  skipped_not_known_solved=$(skipped_not_known_solved)")
end

main()
