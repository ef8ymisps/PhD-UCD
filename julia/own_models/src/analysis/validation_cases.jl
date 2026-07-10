# ============================================================
# Validation case builders
# Controlled perturbations for PF/OPF validation studies
# ============================================================

struct ValidationCaseSummary
    scenario::String
    case_name::String
    n_buses::Int
    n_branches::Int
    n_generators::Int
    n_loads::Int
    total_pd::Float64
    total_qd::Float64
    total_pmin::Float64
    total_pmax::Float64
    total_qmin::Float64
    total_qmax::Float64
    min_positive_rate_a::Float64
    max_rate_a::Float64
    min_vmin::Float64
    max_vmax::Float64
end

function _copy_powercase(
    case::PowerCase;
    name::String=case.name,
    buses::Vector{Bus}=case.buses,
    branches::Vector{Branch}=case.branches,
    generators::Vector{Generator}=case.generators,
    loads::Vector{Load}=case.loads,
    shunts::Vector{Shunt}=case.shunts
)
    return PowerCase(
        name,
        case.baseMVA,
        buses,
        branches,
        generators,
        loads,
        shunts,
        case.slack_bus_id,
        case.bus_id_to_index
    )
end

function _copy_bus(bus::Bus; vmin=bus.vmin, vmax=bus.vmax)
    return Bus(
        bus.id,
        bus.index,
        bus.bus_type,
        bus.vm0,
        bus.va0,
        Float64(vmin),
        Float64(vmax)
    )
end

function _copy_branch(branch::Branch; rate_a=branch.rate_a)
    return Branch(
        branch.id,
        branch.index,
        branch.from_bus,
        branch.to_bus,
        branch.r,
        branch.x,
        branch.b,
        branch.b_fr,
        branch.b_to,
        branch.g_fr,
        branch.g_to,
        Float64(rate_a),
        branch.tap,
        branch.shift,
        branch.angmin,
        branch.angmax,
        branch.status
    )
end

function _copy_generator(
    gen::Generator;
    pmin=gen.pmin,
    pmax=gen.pmax,
    qmin=gen.qmin,
    qmax=gen.qmax
)
    return Generator(
        gen.id,
        gen.index,
        gen.bus,
        gen.pg0,
        gen.qg0,
        Float64(pmin),
        Float64(pmax),
        Float64(qmin),
        Float64(qmax),
        gen.status,
        gen.cost_c2,
        gen.cost_c1,
        gen.cost_c0
    )
end

function _copy_load(load::Load; pd=load.pd, qd=load.qd)
    return Load(
        load.id,
        load.index,
        load.bus,
        Float64(pd),
        Float64(qd),
        load.status
    )
end

function make_relaxed_case(
    case::PowerCase;
    branch_rate_multiplier::Float64=10.0,
    generator_limit_multiplier::Float64=10.0,
    voltage_lower::Float64=0.5,
    voltage_upper::Float64=1.5,
    name_suffix::String="_relaxed"
)
    buses = [
        _copy_bus(
            bus;
            vmin=min(bus.vmin, voltage_lower),
            vmax=max(bus.vmax, voltage_upper)
        )
        for bus in case.buses
    ]

    branches = [
        branch.rate_a > 0.0 ?
            _copy_branch(branch; rate_a=branch.rate_a * branch_rate_multiplier) :
            branch
        for branch in case.branches
    ]

    generators = [
        gen.status == 1 ?
            _copy_generator(
                gen;
                pmax=gen.pmax * generator_limit_multiplier,
                qmin=gen.qmin * generator_limit_multiplier,
                qmax=gen.qmax * generator_limit_multiplier
            ) :
            gen
        for gen in case.generators
    ]

    return _copy_powercase(
        case;
        name=case.name * name_suffix,
        buses=buses,
        branches=branches,
        generators=generators
    )
end

function make_congested_case(
    case::PowerCase;
    branch_id::String,
    new_rate::Float64,
    name_suffix::String="_congested"
)
    if !any(branch.id == branch_id for branch in case.branches)
        error("Branch $(branch_id) does not exist in case $(case.name).")
    end

    branches = [
        branch.id == branch_id ?
            _copy_branch(branch; rate_a=new_rate) :
            branch
        for branch in case.branches
    ]

    return _copy_powercase(
        case;
        name=case.name * name_suffix,
        branches=branches
    )
end

function make_generator_limited_case(
    case::PowerCase;
    gen_id::String,
    new_pmax::Float64,
    new_pmin::Union{Nothing, Float64}=nothing,
    name_suffix::String="_generator_limited"
)
    if !any(gen.id == gen_id for gen in case.generators)
        error("Generator $(gen_id) does not exist in case $(case.name).")
    end

    generators = [
        gen.id == gen_id ?
            _copy_generator(
                gen;
                pmin=isnothing(new_pmin) ? gen.pmin : new_pmin,
                pmax=new_pmax
            ) :
            gen
        for gen in case.generators
    ]

    return _copy_powercase(
        case;
        name=case.name * name_suffix,
        generators=generators
    )
end

function make_infeasible_load_case(
    case::PowerCase;
    load_multiplier::Float64=10.0,
    name_suffix::String="_infeasible_load"
)
    loads = [
        load.status == 1 ?
            _copy_load(
                load;
                pd=load.pd * load_multiplier,
                qd=load.qd * load_multiplier
            ) :
            load
        for load in case.loads
    ]

    return _copy_powercase(
        case;
        name=case.name * name_suffix,
        loads=loads
    )
end

function first_active_branch_id(case::PowerCase)
    for branch in case.branches
        if branch.status == 1
            return branch.id
        end
    end

    error("Case $(case.name) has no active branches.")
end

function first_active_generator_id(case::PowerCase)
    for gen in case.generators
        if gen.status == 1
            return gen.id
        end
    end

    error("Case $(case.name) has no active generators.")
end

function build_default_validation_cases(
    case::PowerCase;
    congested_branch_id::String=first_active_branch_id(case),
    congested_rate_multiplier::Float64=0.5,
    generator_limited_gen_id::String=first_active_generator_id(case),
    generator_pmax_multiplier::Float64=0.5,
    load_multiplier::Float64=10.0
)
    congested_branch = only([
        branch for branch in case.branches
        if branch.id == congested_branch_id
    ])

    limited_generator = only([
        gen for gen in case.generators
        if gen.id == generator_limited_gen_id
    ])

    return (
        base = case,
        relaxed = make_relaxed_case(case),
        congested = make_congested_case(
            case;
            branch_id=congested_branch_id,
            new_rate=congested_branch.rate_a * congested_rate_multiplier
        ),
        generator_limited = make_generator_limited_case(
            case;
            gen_id=generator_limited_gen_id,
            new_pmax=limited_generator.pmax * generator_pmax_multiplier
        ),
        infeasible_load = make_infeasible_load_case(
            case;
            load_multiplier=load_multiplier
        )
    )
end

function summarize_validation_case(scenario::String, case::PowerCase)
    active_loads = [load for load in case.loads if load.status == 1]
    active_gens = [gen for gen in case.generators if gen.status == 1]
    positive_rates = [
        branch.rate_a for branch in case.branches
        if branch.status == 1 && branch.rate_a > 0.0
    ]

    return ValidationCaseSummary(
        scenario,
        case.name,
        length(case.buses),
        length(case.branches),
        length(case.generators),
        length(case.loads),
        sum(load.pd for load in active_loads; init=0.0),
        sum(load.qd for load in active_loads; init=0.0),
        sum(gen.pmin for gen in active_gens; init=0.0),
        sum(gen.pmax for gen in active_gens; init=0.0),
        sum(gen.qmin for gen in active_gens; init=0.0),
        sum(gen.qmax for gen in active_gens; init=0.0),
        isempty(positive_rates) ? 0.0 : minimum(positive_rates),
        isempty(positive_rates) ? 0.0 : maximum(positive_rates),
        minimum(bus.vmin for bus in case.buses),
        maximum(bus.vmax for bus in case.buses)
    )
end

function validation_case_summary_as_named_tuple(summary::ValidationCaseSummary)
    return (
        scenario = summary.scenario,
        case_name = summary.case_name,
        n_buses = summary.n_buses,
        n_branches = summary.n_branches,
        n_generators = summary.n_generators,
        n_loads = summary.n_loads,
        total_pd = summary.total_pd,
        total_qd = summary.total_qd,
        total_pmin = summary.total_pmin,
        total_pmax = summary.total_pmax,
        total_qmin = summary.total_qmin,
        total_qmax = summary.total_qmax,
        min_positive_rate_a = summary.min_positive_rate_a,
        max_rate_a = summary.max_rate_a,
        min_vmin = summary.min_vmin,
        max_vmax = summary.max_vmax
    )
end

function validation_cases_as_named_tuples(validation_cases)
    return [
        validation_case_summary_as_named_tuple(
            summarize_validation_case(string(scenario), case)
        )
        for (scenario, case) in pairs(validation_cases)
    ]
end
