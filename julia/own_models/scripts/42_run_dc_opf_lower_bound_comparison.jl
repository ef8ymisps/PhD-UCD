# Q9: DC-OPF lower-bound comparison for julia_current's objective-gap scenarios.
#
# The user cited a PGLib-referenced paper (Josz, Fliscounakis, Maeght,
# Panciatici, "AC Power Flow Data in MATPOWER and QCQP Format", arXiv:1603.01533)
# figure for case1888rte: a "losses minimization without flow constraints"
# table row showing DCOPF=59110.5 vs OPF=59769.9, a 1.12% gap. That figure is
# a different objective (losses, no flow limits) than this project's
# generation-cost objective with full constraints, so it isn't directly
# reproduced here. Instead, this script builds the analogous DC-vs-AC
# optimality-gap statistic for THIS project's own results: for the two
# julia_current scenarios where the own model's AC-OPF is CHEAPER than
# PowerModels' AC-OPF (load_x0.8: own=1,105,148.24 vs pm=1,105,160.70;
# load_x1.2_reactive_stress: own=1,474,500.69 vs pm=1,478,335.90 -- both
# confirmed from results/own_vs_powermodels/julia/summary.csv), solve this
# project's own DC-OPF model (same generation-cost objective, same branch
# flow/angle limits) on the identical scenario data, and check that it sits
# below (i.e. is a sound lower bound for) both AC objectives.

const TWENTYFIVE_SCRIPT_PATH_42 = joinpath(@__DIR__, "25_run_own_vs_powermodels_tables.jl")
let
    src = read(TWENTYFIVE_SCRIPT_PATH_42, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(TWENTYFIVE_SCRIPT_PATH_42).")
    Base.include_string(Main, stripped, TWENTYFIVE_SCRIPT_PATH_42)
end

const OUT_ROOT_42 = joinpath(ROOT, "results", "own_vs_powermodels", "_organized_for_meeting", "10_opf_solution_inspection", "1888_rte_objective_gap")
const AC_SUMMARY_PATH_42 = joinpath(ROOT, "results", "own_vs_powermodels", "julia", "summary.csv")

const DC_OPF_SCENARIOS = [
    (scenario="load_x0.8", match_text="load_x0p8__load_scaling"),
    (scenario="load_x1.2_reactive_stress", match_text="load_x1p2__reactive_power_stress"),
]

function find_entry_dc(entries, match_text::String)
    matches = filter(e -> occursin(match_text, String(e.scenario_id)), entries)
    isempty(matches) && error("No 1888_rte scenario matched substring: $(match_text)")
    length(matches) > 1 && error("Multiple 1888_rte scenarios matched substring $(match_text)")
    return only(matches)
end

function read_ac_objectives(scenario::String)
    df = CSV.read(AC_SUMMARY_PATH_42, DataFrame)
    row = only(filter(r -> r.case == "1888_rte" && r.scenario == scenario, eachrow(df)))
    return (
        own_ac_objective=Float64(row.own_objective),
        own_ac_status=String(row.own_status),
        pm_ac_objective=Float64(row.pm_objective),
        pm_ac_status=String(row.pm_status),
    )
end

function main()
    ac_optimizer = build_ac_optimizer()
    case_label = "1888_rte"
    base_data = PowerModels.parse_file(joinpath(CASE_DIR, "pglib_opf_case$(case_label).m"))
    entries = compact_scenarios(base_data, case_label, ac_optimizer; include_no_reference=true)

    println("Q9: DC-OPF lower-bound comparison (julia_current, objective-gap scenarios)")
    rows = NamedTuple[]

    for spec in DC_OPF_SCENARIOS
        entry = find_entry_dc(entries, spec.match_text)
        case = OwnPowerModels.powermodels_to_powercase(deepcopy(entry.data))

        dc_result = OwnPowerModels.solve_dc_opf_own(case)
        ac = read_ac_objectives(spec.scenario)

        dc_vs_own_gap_abs = ac.own_ac_objective - dc_result.objective
        dc_vs_own_gap_pct = 100.0 * dc_vs_own_gap_abs / ac.own_ac_objective
        dc_vs_pm_gap_abs = ac.pm_ac_objective - dc_result.objective
        dc_vs_pm_gap_pct = 100.0 * dc_vs_pm_gap_abs / ac.pm_ac_objective

        row = (
            scenario=spec.scenario,
            dc_objective=dc_result.objective,
            dc_status=string(dc_result.termination_status),
            own_ac_objective=ac.own_ac_objective,
            own_ac_status=ac.own_ac_status,
            pm_ac_objective=ac.pm_ac_objective,
            pm_ac_status=ac.pm_ac_status,
            dc_vs_own_gap_abs=dc_vs_own_gap_abs,
            dc_vs_own_gap_pct=dc_vs_own_gap_pct,
            dc_vs_pm_gap_abs=dc_vs_pm_gap_abs,
            dc_vs_pm_gap_pct=dc_vs_pm_gap_pct,
            dc_lower_bound_respected=dc_result.objective <= ac.own_ac_objective,
        )
        push!(rows, row)
        println("  $(spec.scenario): dc=$(dc_result.objective) own_ac=$(ac.own_ac_objective) pm_ac=$(ac.pm_ac_objective) dc_vs_own_gap_pct=$(dc_vs_own_gap_pct) lower_bound_respected=$(row.dc_lower_bound_respected)")
    end

    out_path = joinpath(OUT_ROOT_42, "dc_opf_optimality_gap_julia_current.csv")
    mkpath(OUT_ROOT_42)
    CSV.write(out_path, DataFrame(rows))
    println("Wrote: $(out_path)")
end

main()
