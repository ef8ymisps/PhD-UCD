# Selected 1888_rte objective-gap export runner.
#
# Regenerates only the raw own-vs-PowerModels comparison tables needed for
# detailed inspection of the objective-mismatch scenarios discussed on
# 2026-07-21. Reuses script 25's process_data worker so the table schema stays
# identical to the existing validation exports.

const TWENTYFIVE_SCRIPT_PATH = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH)
end

const SELECTED_1888_SCENARIOS = [
    (
        out_label="load_x0.8",
        match_text="load_x0p8__load_scaling",
        variants=[:pm_like_zero],
        export_reference=true,
    ),
    (
        out_label="load_x1.2_reactive_stress",
        match_text="load_x1p2__reactive_power_stress",
        variants=[:pm_like_zero],
        export_reference=true,
    ),
    (
        out_label="qg_limits_x0.6",
        match_text="qg_limits_x0p6__reactive_generation_limits",
        variants=[:pm_like_zero],
        export_reference=false,
    ),
]

function find_entry(entries, match_text::String)
    matches = filter(e -> occursin(match_text, String(e.scenario_id)), entries)
    isempty(matches) && error("No 1888_rte scenario matched substring: $(match_text)")
    length(matches) > 1 && error("Multiple 1888_rte scenarios matched substring $(match_text): $(String.(getfield.(matches, :scenario_id)))")
    return only(matches)
end

function main()
    max_iter = parse(Int, get(ENV, "AC_OPF_SELECTED_1888_MAX_ITER", "1000"))
    ac_optimizer = optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => max_iter)
    case_label = "1888_rte"
    data = PowerModels.parse_file(joinpath(CASE_DIR, "pglib_opf_case$(case_label).m"))
    entries = compact_scenarios(data, case_label, ac_optimizer; include_no_reference=true)

    println("Selected 1888_rte objective-gap export")
    println("Ipopt max_iter: $(max_iter)")
    println("Scenario entries available: $(length(entries))")

    for spec in SELECTED_1888_SCENARIOS
        entry = find_entry(entries, spec.match_text)
        for variant in spec.variants
            export_pm_reference = spec.export_reference
            try
                process_data(
                    case_label,
                    spec.out_label,
                    entry.data;
                    ac_optimizer=ac_optimizer,
                    export_reference=export_pm_reference,
                    own_variant=variant,
                    write_summary=false,
                )
            catch err
                println("ERROR on $(case_label)/$(spec.out_label)/$(variant): ", sprint(showerror, err))
            end
            GC.gc()
        end
    end
end

main()
