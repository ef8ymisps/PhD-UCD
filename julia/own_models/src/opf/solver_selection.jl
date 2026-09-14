# Shared default-optimizer selection for DC OPF / DC-SCOPF.
#
# Python's counterpart is own_models.src.opf.dc.solve.make_solver -- appsi
# isn't a JuMP concept (JuMP already talks to solvers in-process; there is
# no separate "persistent interface" to opt into, `Model(HiGHS.Optimizer)`
# IS the direct/native path), so the Julia-side equivalent of "apply appsi"
# is simply preferring Gurobi over HiGHS by default, with a graceful
# fallback -- mirroring Python's preference order (appsi-Gurobi ->
# appsi-HiGHS -> classic HiGHS) minus the appsi distinction, which doesn't
# apply here. Confirmed directly (session notes): Gurobi is faster than
# HiGHS specifically on the generator-outage automatic-response MILP (real
# binary variables), and comparable on the LP-only branch-outage
# DC-SCOPF.
#
# Gurobi.jl (unlike gurobipy) has no size-limited-license fallback -- it
# requires a real license file (GRB_LICENSE_FILE) or WLS credentials, or
# `Gurobi.Optimizer()` throws. Caught below and treated as "not available",
# not a hard failure, so this module (and every DC OPF / DC-SCOPF script)
# still works on a machine without a Gurobi license.

using JuMP
using HiGHS
using Gurobi

# Cached after the first check so every DC-OPF/DC-SCOPF model build doesn't
# each pay for a fresh Gurobi environment/license round trip just to
# rediscover the same answer -- Julia model-building happens far more
# often per script than Python's (once per N-1 screening contingency, for
# example), so memoizing matters here in a way it didn't for `make_solver`.
const _gurobi_available_cache = Ref{Union{Bool,Nothing}}(nothing)

"""Return whether Gurobi is currently licensed/usable. Checked once per
Julia session (see `_gurobi_available_cache`); catches ANY error from
constructing a throwaway optimizer instance -- no license file, expired
license, no network for a named-user license at activation time, etc. --
since all of those should fall back to HiGHS the same way, not crash
model building."""
function gurobi_available()::Bool
    cached = _gurobi_available_cache[]
    if cached !== nothing
        return cached
    end

    available = try
        Gurobi.Optimizer()
        true
    catch
        false
    end

    _gurobi_available_cache[] = available
    return available
end

"""Default JuMP optimizer factory for DC OPF / DC-SCOPF model building:
Gurobi if licensed, HiGHS otherwise. Every DC OPF / DC-SCOPF `optimizer=`
keyword in this codebase defaults to this function (called fresh at each
model build, not memoized as a factory itself -- only the availability
check is memoized) rather than a hardcoded `HiGHS.Optimizer`, so scripts
get the faster solver automatically without needing to pass anything;
explicitly passing `optimizer=...` still overrides it completely,
unchanged from before."""
function default_optimizer()
    return gurobi_available() ? Gurobi.Optimizer : HiGHS.Optimizer
end
