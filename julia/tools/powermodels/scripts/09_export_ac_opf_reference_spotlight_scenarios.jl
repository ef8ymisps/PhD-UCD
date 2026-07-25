# Extends 08_export_ac_opf_reference_all_cases.jl (base scenario, all 8
# cases) to 1888_rte's two spotlight non-uniqueness scenarios (load_x0.8,
# load_x1.2 reactive-power-stress), so Set B (Python own-model vs
# PowerModels) has a reference to compare against for these too, matching
# julia/own_models/scripts/25_run_own_vs_powermodels_tables.jl's Set A
# spotlight coverage.
#
# These two scenarios scale only Q_load (reactive-power-stress) or both P/Q
# together in a way 07_export_ac_opf_reference.jl's apply_scenario doesn't
# support (its "load_scaled" type only offers "both" or "P only", not the
# exact q_load_multiplier-only stress used here) -- so this reuses
# 09_run_compact_opf_validation_sweeps.jl's compact_scenarios generator
# directly (same source of truth as scripts 16/18/20/24/25) instead of
# re-deriving the perturbation.

const SEVEN_SCRIPT_PATH = joinpath(@__DIR__, "07_export_ac_opf_reference.jl")
let
    src = read(SEVEN_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(SEVEN_SCRIPT_PATH).")
    Base.include_string(Main, stripped, SEVEN_SCRIPT_PATH)
end

const NINE_SCRIPT_PATH = joinpath(@__DIR__, "..", "..", "..", "own_models", "scripts", "09_run_compact_opf_validation_sweeps.jl")
let
    src = read(NINE_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(NINE_SCRIPT_PATH).")
    Base.include_string(Main, stripped, NINE_SCRIPT_PATH)
end

const SPOTLIGHT_SCENARIOS = [
    ("1888_rte", "load_x0p8__load_scaling", "load_x0.8"),
    ("1888_rte", "load_x1p2__reactive_power_stress", "load_x1.2_reactive_stress"),
    ("1888_rte", "qg_limits_x0p8__reactive_generation_limits", "qg_limits_x0.8"),
]

function main()
    root = project_root()
    case_dir = joinpath(root, "data", "raw", "pglib", "matpower_m", "base_case")
    output_dir = joinpath(root, "results", "reference", "powermodels", "ac_opf")
    ac_optimizer = optimizer_with_attributes(
        Ipopt.Optimizer, "print_level" => 0,
        "tol" => 1e-8, "acceptable_tol" => 1e-6, "constr_viol_tol" => 1e-4,
        "compl_inf_tol" => 1e-4, "dual_inf_tol" => 1.0,
    )

    for (case_label, scenario_id_substring, out_label) in SPOTLIGHT_SCENARIOS
        case_file = "pglib_opf_case$(case_label).m"
        data = PowerModels.parse_file(joinpath(case_dir, case_file))
        entries = compact_scenarios(data, case_label, ac_optimizer; include_no_reference=true)
        entry = only(filter(e -> occursin(scenario_id_substring, e.scenario_id), entries))

        solve_start = time()
        pm = PowerModels.instantiate_model(deepcopy(entry.data), ACPPowerModel, PowerModels.build_opf)
        result = PowerModels.optimize_model!(pm, optimizer=ac_optimizer)
        solve_time_sec = time() - solve_start
        solve_iterations = safe_barrier_iterations(pm.model)

        stem = replace(case_file, ".m" => "")
        case_json_path = joinpath(output_dir, stem * "_" * out_label * "_case.json")
        reference_json_path = joinpath(output_dir, stem * "_" * out_label * "_reference.json")
        write_json(case_json_path, entry.data)
        write_json(reference_json_path, reference_payload(case_file, out_label, entry.data, result, solve_iterations, solve_time_sec))
        println("Exported ", stem, " / ", out_label, " -> ", reference_json_path)
    end
end

main()
