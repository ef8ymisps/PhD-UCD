using Test

include("../src/OwnPowerModels.jl")
using .OwnPowerModels

const CASE14_PATH = joinpath(@__DIR__, "..", "..", "..", "data", "raw", "pglib-opf", "pglib_opf_case14_ieee.m")

function _case14()
    return load_powercase_from_powermodels(CASE14_PATH)
end

function _pv_generator(case)
    pv_buses = Set(bus.id for bus in case.buses if bus.bus_type == 2)
    return first(gen for gen in case.generators if gen.status == 1 && gen.bus in pv_buses)
end

function _replace_generator(case, target_id, replacement)
    generators = [gen.id == target_id ? replacement : gen for gen in case.generators]
    return PowerCase(
        case.name,
        case.baseMVA,
        case.buses,
        case.branches,
        generators,
        case.loads,
        case.shunts,
        case.slack_bus_id,
        case.bus_id_to_index,
    )
end

@testset "AC PF one-way PV-PQ switching" begin
    case = _case14()
    fixed = solve_ac_power_flow_own(case)
    default = solve_ac_power_flow_own(case; method=:newton)
    @test fixed.converged
    @test default.converged
    @test isempty(default.switched_buses)
    @test default.termination_reason == "fixed_type_converged"
    @test abs(fixed.mismatch_norm - default.mismatch_norm) < 1e-12

    wide_gens = [
        gen.status == 1 ? OwnPowerModels.Generator(gen.id, gen.index, gen.bus, gen.pg0, gen.qg0, gen.pmin, gen.pmax, -10.0, 10.0, gen.status, gen.cost_c2, gen.cost_c1, gen.cost_c0) : gen
        for gen in case.generators
    ]
    wide = PowerCase(case.name, case.baseMVA, case.buses, case.branches, wide_gens, case.loads, case.shunts, case.slack_bus_id, case.bus_id_to_index)
    no_switch = solve_ac_power_flow_with_q_limits(wide)
    @test no_switch.converged
    @test no_switch.q_limit_converged
    @test isempty(no_switch.switched_buses)

    gen = _pv_generator(case)
    required = fixed.qg_by_bus[gen.bus]
    tight_gen = OwnPowerModels.Generator(gen.id, gen.index, gen.bus, gen.pg0, gen.qg0, gen.pmin, gen.pmax, required - 1.0, required - 1e-3, gen.status, gen.cost_c2, gen.cost_c1, gen.cost_c0)
    tight = _replace_generator(case, gen.id, tight_gen)
    switched = solve_ac_power_flow_with_q_limits(tight)
    @test switched.converged
    @test gen.bus in switched.switched_buses
    @test switched.final_bus_types[gen.bus] == 1
    @test any(event.bus_id == gen.bus && event.violation_direction == "upper" for event in switched.switching_events)
    @test first(bus.bus_type for bus in tight.buses if bus.id == gen.bus) == 2

    alloc = OwnPowerModels.allocate_qg_to_generators(
        [
            OwnPowerModels.Generator("g1", 1, "b", 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 1, 0.0, 0.0, 0.0),
            OwnPowerModels.Generator("g2", 2, "b", 0.0, 0.0, 0.0, 1.0, -1.0, 3.0, 1, 0.0, 0.0, 0.0),
        ],
        2.0,
    )
    @test alloc.aggregate_feasible
    @test abs(sum(values(alloc.individual_allocation)) - 2.0) < 1e-8
end
