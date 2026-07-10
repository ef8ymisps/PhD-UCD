# Owns AC OPF constraints: nonlinear nodal P/Q balance, reference angle,
# branch angle limits, and apparent-power branch limits.

using JuMP

"""Fix the slack/reference bus angle when requested."""
function add_ac_reference_angle!(model::Model, case::PowerCase, va; fix_reference_angle::Bool=true)
    if fix_reference_angle
        @constraint(model, va[case.slack_bus_id] == 0.0)
    end
end

"""Add AC nodal balances using P = Re(V * conj(YV)), Q = Im(V * conj(YV))."""
function add_ac_power_balance!(model::Model, case::PowerCase, data::ACOPFData, pg, qg, vm, va)
    for bus in case.buses
        bus_id = bus.id
        pgen = sum((pg[g] for g in data.gens_by_bus[bus_id]); init=0.0)
        qgen = sum((qg[g] for g in data.gens_by_bus[bus_id]); init=0.0)

        @NLconstraint(
            model,
            pgen - data.pd_by_bus[bus_id] ==
                sum(
                    vm[bus_id] * vm[other_bus_id] *
                    (
                        conductance * cos(va[bus_id] - va[other_bus_id]) +
                        susceptance * sin(va[bus_id] - va[other_bus_id])
                    )
                    for (other_bus_id, conductance, susceptance) in data.  [bus_id]
                )
        )

        @NLconstraint(
            model,
            qgen - data.qd_by_bus[bus_id] ==
                sum(
                    vm[bus_id] * vm[other_bus_id] *
                    (
                        conductance * sin(va[bus_id] - va[other_bus_id]) -
                        susceptance * cos(va[bus_id] - va[other_bus_id])
                    )
                    for (other_bus_id, conductance, susceptance) in data.ybus_row_terms[bus_id]
                )
        )
    end
end

"""Return branch admittance coefficients for from/to apparent-power equations."""
function ac_branch_admittance_terms(branch::Branch)
    z = branch.r + im * branch.x
    y = 1.0 / z
    y_fr = branch.g_fr + im * branch.b_fr
    y_to = branch.g_to + im * branch.b_to
    tap_mag = branch.tap == 0.0 ? 1.0 : branch.tap
    tap = tap_mag * exp(im * branch.shift)

    return (
        Yff = (y + y_fr) / abs2(tap),
        Yft = -y / conj(tap),
        Ytf = -y / tap,
        Ytt = y + y_to
    )
end

"""Create nonlinear branch flow expressions and add optional branch limits."""
function add_ac_branch_constraints!(
    model::Model,
    data::ACOPFData,
    vm,
    va;
    include_branch_thermal::Bool=true,
    include_branch_angles::Bool=true
)
    pf_by_branch = Dict{String, Any}()
    qf_by_branch = Dict{String, Any}()
    pt_by_branch = Dict{String, Any}()
    qt_by_branch = Dict{String, Any}()

    for branch in data.active_branches
        f = branch.from_bus
        t = branch.to_bus

        if include_branch_angles
            @constraint(model, branch.angmin <= va[f] - va[t] <= branch.angmax)
        end

        terms = ac_branch_admittance_terms(branch)
        Gff, Bff = real(terms.Yff), imag(terms.Yff)
        Gft, Bft = real(terms.Yft), imag(terms.Yft)
        Gtf, Btf = real(terms.Ytf), imag(terms.Ytf)
        Gtt, Btt = real(terms.Ytt), imag(terms.Ytt)

        # Oriented flows use PowerModels/PGLib branch orientation f_bus -> t_bus.
        pf_by_branch[branch.id] = @NLexpression(
            model,
            vm[f]^2 * Gff +
            vm[f] * vm[t] * (Gft * cos(va[f] - va[t]) + Bft * sin(va[f] - va[t]))
        )
        qf_by_branch[branch.id] = @NLexpression(
            model,
            -vm[f]^2 * Bff +
            vm[f] * vm[t] * (Gft * sin(va[f] - va[t]) - Bft * cos(va[f] - va[t]))
        )
        pt_by_branch[branch.id] = @NLexpression(
            model,
            vm[t]^2 * Gtt +
            vm[t] * vm[f] * (Gtf * cos(va[t] - va[f]) + Btf * sin(va[t] - va[f]))
        )
        qt_by_branch[branch.id] = @NLexpression(
            model,
            -vm[t]^2 * Btt +
            vm[t] * vm[f] * (Gtf * sin(va[t] - va[f]) - Btf * cos(va[t] - va[f]))
        )

        if include_branch_thermal && branch.rate_a > 0.0
            @NLconstraint(model, pf_by_branch[branch.id]^2 + qf_by_branch[branch.id]^2 <= branch.rate_a^2)
            @NLconstraint(model, pt_by_branch[branch.id]^2 + qt_by_branch[branch.id]^2 <= branch.rate_a^2)
        end
    end

    return pf_by_branch, qf_by_branch, pt_by_branch, qt_by_branch
end
