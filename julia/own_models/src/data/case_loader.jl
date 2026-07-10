using PowerModels

@isdefined(PowerCase) || include(joinpath(@__DIR__, "case_structs.jl"))
@isdefined(powermodels_to_powercase) || include(joinpath(@__DIR__, "..", "adapters", "powermodels_adapter.jl"))

# Parse a PowerModels/PGLib case file and convert it to the own PowerCase type.
function load_powercase_from_powermodels(case_path)
    network_data = PowerModels.parse_file(case_path)
    return powermodels_to_powercase(network_data)
end
