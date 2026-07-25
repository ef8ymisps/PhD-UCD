# Extends 07_export_ac_opf_reference.jl's reference-export machinery (which
# only ever covered 5_pjm/14_ieee/30_ieee) to all 8 default cases' base
# scenario, so Set B (Python own-model vs PowerModels) has a reference to
# compare against for every case Set A (Julia own-model vs PowerModels)
# already covers. Reuses export_reference/reference_payload/json_ready/
# write_json/safe_barrier_iterations from the original script unmodified
# (include-and-strip-main, same pattern as scripts 16/18/20/24) rather than
# duplicating the reference-payload schema Python's
# compare_ac_with_powermodels.py already depends on.

const SEVEN_SCRIPT_PATH = joinpath(@__DIR__, "07_export_ac_opf_reference.jl")
let
    src = read(SEVEN_SCRIPT_PATH, String)
    stripped = replace(src, r"\nmain\(\)\s*\z" => "\n")
    stripped == src && error("Could not find trailing `main()` call to strip in $(SEVEN_SCRIPT_PATH).")
    Base.include_string(Main, stripped, SEVEN_SCRIPT_PATH)
end

const ALL_CASES = [
    "pglib_opf_case5_pjm.m", "pglib_opf_case14_ieee.m", "pglib_opf_case30_ieee.m",
    "pglib_opf_case118_ieee.m", "pglib_opf_case300_ieee.m", "pglib_opf_case1354_pegase.m",
    "pglib_opf_case1888_rte.m", "pglib_opf_case6470_rte.m",
]

function main()
    root = project_root()
    case_dir = joinpath(root, "data", "raw", "pglib", "matpower_m", "base_case")
    output_dir = joinpath(root, "results", "reference", "powermodels", "ac_opf")
    base_scenario = Dict("name" => "base", "type" => "base")

    for case_name in ALL_CASES
        try
            export_reference(case_name, base_scenario, case_dir, output_dir)
        catch err
            println("  ERROR on $(case_name): ", sprint(showerror, err))
        end
    end
end

main()
