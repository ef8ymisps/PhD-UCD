# Full-scenario Set A sweep: every scenario compact_scenarios generates, for
# all 8 default cases (the full ~313-scenario grid used throughout this
# session's warm-start ablation work), not just base + the 3 hand-picked
# 1888_rte spotlight scenarios. This is an overnight-scale job -- some
# scenarios on the largest cases (6470_rte) take 100+ seconds per solve, and
# there are ~313 scenarios x 2 solves (own + PM) total.
#
# Reuses 25_run_own_vs_powermodels_tables.jl's process_data (and everything
# it pulls in transitively: compact_scenarios via its own script-09
# inclusion, the constraints inventory/binding builders, the optional
# PowerModels-reference-JSON exporter) rather than duplicating any of it --
# include-and-strip-main, same pattern as every other multi-scenario script
# this session.
#
# Checkpointed/resumable: reads results/own_vs_powermodels/julia/summary.csv
# at startup and skips any (case, scenario) pair already present, so an
# interrupted overnight run can just be restarted rather than redone from
# scratch. Also exports PowerModels reference JSON for every scenario
# (export_reference=true) so Python's Set B can process the same full grid
# later without re-solving PowerModels a second time.
#
# max_iter capped at 1000, not the repo's usual 3000 -- see
# 16_run_ac_opf_full_scenario_warm_start_ablation.jl's docstring for why:
# one scenario previously ran 3.27h without converging at max_iter=3000 and
# had to be killed mid-overnight-run. 1000 is the same cap used there and in
# scripts 18/20/24 for the same reason.

const TWENTYFIVE_SCRIPT_PATH = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH)
end

# Scenarios that already have a friendly-named folder/summary row from
# earlier, targeted runs (25_run_own_vs_powermodels_tables.jl's "base" +
# SPOTLIGHT_SCENARIOS) -- reuse those exact names instead of the raw
# scenario_id, same reasoning as the "base" special-case below.
const KNOWN_LABELS = Dict(
    "1888_rte__base__none__none__none__none" => "base",
    "1888_rte_load_x0p8__load_scaling__none__none__load_multiplier__0p8" => "load_x0.8",
    "1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2" => "load_x1.2_reactive_stress",
    "1888_rte_qg_limits_x0p8__reactive_generation_limits__system__all_generators__qg_limit_multiplier__0p8" => "qg_limits_x0.8",
)

function full_sweep_ac_optimizer(max_iter::Int=1000)
    return optimizer_with_attributes(Ipopt.Optimizer, "print_level" => 0, "max_iter" => max_iter)
end

function parse_own_variant(text::String)
    variant = Symbol(lowercase(text))
    # "current" (bus-injection) removed -- that formulation was deleted;
    # pm_like_zero is the sole AC-OPF model now.
    variant == :pm_like_zero || error("Unsupported --own-variant=$(text). Use pm_like_zero.")
    return variant
end

function completed_pairs(summary_path::String)
    completed = Set{Tuple{String,String}}()
    isfile(summary_path) || return completed
    df = DataFrame(CSV.File(summary_path; select=[:case, :scenario]))
    for row in eachrow(df)
        push!(completed, (String(row.case), String(row.scenario)))
    end
    return completed
end

function main()
    own_variant = parse_own_variant(arg_value("--own-variant", "pm_like_zero"))
    max_scenarios = parse(Int, arg_value("--max-scenarios", "0"))
    max_iter = parse(Int, arg_value("--max-iter", "1000"))
    scenarios_seen = 0
    out_root, summary_path = own_variant_paths(own_variant)
    ac_optimizer = full_sweep_ac_optimizer(max_iter)
    completed = completed_pairs(summary_path)
    println(isempty(completed) ? "Starting fresh -- no prior progress found." : "Resuming -- $(length(completed)) (case, scenario) pairs already done.")
    println("Own variant: $(own_variant)")
    println("Output root: $(out_root)")
    println("Ipopt max_iter: $(max_iter)")

    for case_file in DEFAULT_CASES
        label = short_case_name(case_file)
        data = PowerModels.parse_file(joinpath(CASE_DIR, "$(case_file).m"))
        entries = compact_scenarios(data, label, ac_optimizer; include_no_reference=true)
        println("\n=== $(label): $(length(entries)) scenarios ===")

        for (i, entry) in enumerate(entries)
            scenarios_seen += 1
            if max_scenarios > 0 && scenarios_seen > max_scenarios
                println("Reached --max-scenarios=$(max_scenarios); stopping.")
                println("\nFull sweep stopped by scenario limit.")
                return
            end

            # Reuse existing friendly names (plain "base", and the 3
            # hand-named 1888_rte spotlight scenarios) instead of the raw
            # scenario_id, so this doesn't create confusing duplicate
            # folders for scenarios already run under a nicer name. Every
            # other scenario type is genuinely only unique via its full
            # scenario_id (e.g. multiple "branch_congestion" entries differ
            # only by target branch).
            raw_id = String(entry.scenario_id)
            scenario_id = if haskey(KNOWN_LABELS, raw_id)
                KNOWN_LABELS[raw_id]
            elseif entry.scenario == "base"
                "base"
            else
                raw_id
            end
            if (label, scenario_id) in completed
                println("  [$(i)/$(length(entries))] $(scenario_id) -- already done, skipping")
                continue
            end
            print("  [$(i)/$(length(entries))] $(scenario_id) ... ")
            t = @elapsed begin
                try
                    process_data(
                        label,
                        scenario_id,
                        entry.data;
                        ac_optimizer=ac_optimizer,
                        export_reference=true,
                        own_variant=own_variant,
                    )
                catch err
                    println("ERROR: ", sprint(showerror, err))
                end
            end
            println("done ($(round(t, digits=1))s)")
            push!(completed, (label, scenario_id))
            GC.gc()
        end
    end

    println("\nFull sweep complete.")
end

main()
