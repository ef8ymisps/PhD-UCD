# Re-runs just Q9 from 66_investigate_1888_rte_active_losses_improvement.jl,
# now that q9_no_thermal reports BOTH the original (structurally
# mismatched: own thermal REMOVED vs PM thermal RELAXED) and a true
# apples-to-apples version (own thermal RELAXED to the identical rate_a=1e6
# PM already used, keeping the constraint structurally present on both
# sides). Skips Q1-Q8 (unchanged from the last run) to keep this fast.

const SIXTYSIX_SCRIPT_PATH_67 = joinpath(@__DIR__, "66_investigate_1888_rte_active_losses_improvement.jl")
let
    src = read(SIXTYSIX_SCRIPT_PATH_67, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(SIXTYSIX_SCRIPT_PATH_67).")
    Base.include_string(Main, stripped, SIXTYSIX_SCRIPT_PATH_67)
end

function main()
    default_optimizer = build_ac_optimizer()

    for (label, match_substr) in [("1888_rte_qg_limits_x0.6", "qg_limits_x0p6"), ("1888_rte_qg_limits_x0.8", "qg_limits_x0p8")]
        println("\n" * "="^90)
        println("SCENARIO: $(label)")
        println("="^90)
        raw_data, case = scenario_data_66(match_substr)
        q9_no_thermal(raw_data, case, default_optimizer)
    end
end

main()
