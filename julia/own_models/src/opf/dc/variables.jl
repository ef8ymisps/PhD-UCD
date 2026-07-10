# Owns DC OPF JuMP decision variables and their bounds.

using JuMP

"""Create theta and Pg variables. Pg is in p.u.; theta is in radians."""
function add_dc_variables!(model::Model, case::PowerCase, data::DCOPFData)
    @variable(model, theta[data.bus_ids])
    @variable(model, pg[data.gen_ids])

    for gen in case.generators
        if gen.status == 1
            set_lower_bound(pg[gen.id], gen.pmin)
            set_upper_bound(pg[gen.id], gen.pmax)
            set_start_value(pg[gen.id], min(max(gen.pg0, gen.pmin), gen.pmax))
        else
            fix(pg[gen.id], 0.0; force=true)
        end
    end

    for bus in case.buses
        set_start_value(theta[bus.id], bus.va0)
    end

    return theta, pg
end
