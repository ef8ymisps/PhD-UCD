# Owns AC OPF constraints shared across the model: reference angle and
# branch admittance-term helpers. Nodal power balance and the per-branch
# Ohm's-law/thermal/angle constraints live in model.jl (arc-based, the sole
# AC-OPF formulation).

using JuMP

"""Fix the slack/reference bus angle when requested.

Returns the `ConstraintRef`, or `nothing` if `fix_reference_angle` is false.
"""
function add_ac_reference_angle!(model::Model, case::PowerCase, va; fix_reference_angle::Bool=true)
    if fix_reference_angle
        return @constraint(model, va[case.slack_bus_id] == 0.0, base_name = "reference_angle")
    end
    return nothing
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
