# ============================================================
# Public AC power-flow interface
# ============================================================

include("ac_power_flow_newton.jl")

# Public AC PF dispatcher; chooses Newton or NLP implementation.
function solve_ac_power_flow_own(case::PowerCase; method::Symbol=:newton, kwargs...)
    if method == :nlp
        error("AC PF NLP implementation is not available in this checkout. Use method=:newton.")
    elseif method == :newton
        return solve_ac_power_flow_newton(case; kwargs...)
    elseif method == :newton_q_limits
        return solve_ac_power_flow_with_q_limits(case; kwargs...)
    else
        error("Unknown AC PF method: $(method). Use :newton or :newton_q_limits.")
    end
end
