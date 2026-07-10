# Owns AC OPF JuMP decision variables and initial values.

using JuMP

"""Create Pg, Qg, Vm, and Va variables with case-based bounds."""
function add_ac_variables!(model::Model, case::PowerCase, data::ACOPFData; voltage_bounds=nothing)
    @variable(model, pg[data.gen_ids])
    @variable(model, qg[data.gen_ids])
    @variable(model, vm[data.bus_ids])
    @variable(model, va[data.bus_ids])

    for bus in case.buses
        vmin, vmax = voltage_bounds_for_bus(bus, voltage_bounds)
        set_lower_bound(vm[bus.id], vmin)
        set_upper_bound(vm[bus.id], vmax)
        set_start_value(vm[bus.id], min(max(bus.vm0, vmin), vmax))
        set_start_value(va[bus.id], bus.va0)
    end

    for gen in case.generators
        if gen.status == 1
            set_lower_bound(pg[gen.id], gen.pmin)
            set_upper_bound(pg[gen.id], gen.pmax)
            set_lower_bound(qg[gen.id], gen.qmin)
            set_upper_bound(qg[gen.id], gen.qmax)
            set_start_value(pg[gen.id], min(max(gen.pg0, gen.pmin), gen.pmax))
            set_start_value(qg[gen.id], min(max(gen.qg0, gen.qmin), gen.qmax))
        else
            fix(pg[gen.id], 0.0; force=true)
            fix(qg[gen.id], 0.0; force=true)
        end
    end

    return pg, qg, vm, va
end

"""Return valid voltage bounds for one bus under an optional scenario override."""
function voltage_bounds_for_bus(bus::Bus, voltage_bounds)
    case_lower = Float64(bus.vmin)
    case_upper = Float64(bus.vmax)
    validate_voltage_bounds(bus.id, case_lower, case_upper)

    if voltage_bounds === nothing
        return case_lower, case_upper
    end

    if voltage_bounds isa AbstractDict
        lower, upper = get(voltage_bounds, bus.id, (case_lower, case_upper))
        lower = Float64(lower)
        upper = Float64(upper)
        validate_voltage_bounds(bus.id, lower, upper)
        return lower, upper
    end

    lower = Float64(voltage_bounds[1])
    upper = Float64(voltage_bounds[2])
    validate_voltage_bounds("all buses", lower, upper)

    target_width = upper - lower
    case_width = case_upper - case_lower
    if target_width >= case_width
        return min(case_lower, lower), max(case_upper, upper)
    end

    half_width = target_width / 2.0
    center = min(max(Float64(bus.vm0), case_lower), case_upper)
    return max(case_lower, center - half_width), min(case_upper, center + half_width)
end

"""Fail before JuMP receives an invalid voltage-magnitude interval."""
function validate_voltage_bounds(bus_id, lower::Float64, upper::Float64)
    if lower > upper
        error("Invalid voltage bounds for $(bus_id): $(lower) > $(upper).")
    end
end
