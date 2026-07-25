using LinearAlgebra
using SparseArrays

# Build the complex AC bus-admittance matrix Ybus.
#
# Built directly as a sparse matrix (triplet accumulation -> SparseArrays.sparse)
# rather than a dense n x n array filtered down afterward. Real power
# networks are extremely sparse here -- measured 0.054% nonzero density on
# case6470_rte (22,602 real entries out of 41.86 million), so the old
# `zeros(ComplexF64, nb, nb)` approach was allocating a ~670 MB matrix, of
# which 99.95% was wasted zeros, on every single solve. `sparse(I, J, V, n, n)`
# sums duplicate (i,j) triplets automatically (Julia's documented behavior),
# which is exactly the accumulation semantics needed here (e.g. two parallel
# branches between the same bus pair, or multiple shunts at one bus).
function build_ybus(case::PowerCase)
    nb = length(case.buses)
    entries = Dict{Tuple{Int,Int}, ComplexF64}()

    function accumulate!(i::Int, j::Int, v::ComplexF64)
        entries[(i, j)] = get(entries, (i, j), zero(ComplexF64)) + v
    end

    for branch in case.branches
        if branch.status == 0
            continue
        end

        if abs(branch.r) < 1e-12 && abs(branch.x) < 1e-12
            error("Branch $(branch.id) has near-zero impedance.")
        end

        i = case.bus_id_to_index[branch.from_bus]
        j = case.bus_id_to_index[branch.to_bus]

        z = branch.r + im * branch.x
        y = 1.0 / z

        y_fr = branch.g_fr + im * branch.b_fr
        y_to = branch.g_to + im * branch.b_to

        tap_mag = branch.tap == 0.0 ? 1.0 : branch.tap
        # Phase shift is in radians in the internal PowerCase convention.
        tap = tap_mag * exp(im * branch.shift)

        accumulate!(i, i, (y + y_fr) / abs2(tap))
        accumulate!(i, j, -y / conj(tap))
        accumulate!(j, i, -y / tap)
        accumulate!(j, j, y + y_to)
    end

    for shunt in case.shunts
        if shunt.status == 0
            continue
        end

        i = case.bus_id_to_index[shunt.bus]
        accumulate!(i, i, shunt.gs + im * shunt.bs)
    end

    n = length(entries)
    I = Vector{Int}(undef, n)
    J = Vector{Int}(undef, n)
    V = Vector{ComplexF64}(undef, n)
    for (idx, ((i, j), v)) in enumerate(entries)
        I[idx] = i
        J[idx] = j
        V[idx] = v
    end

    return sparse(I, J, V, nb, nb)
end

# Calculate AC active/reactive bus injections from Ybus and voltage state.
function calc_bus_power_injections(Ybus, vm, va)
    # Positive P/Q are injections into the network: S = V * conj(YV).
    V = vm .* exp.(im .* va)
    S = V .* conj.(Ybus * V)

    P = real.(S)
    Q = imag.(S)

    return P, Q
end

# Calculate AC branch from/to P/Q flows via Ohm's law, given vm/va per bus id.
#
# Pure arithmetic, no JuMP -- mirrors
# python/own_models/src/network/admittance.py's calc_ac_branch_flows, and
# reuses ac_branch_admittance_terms (opf/ac/constraints.jl) for the
# per-branch Yff/Yft/Ytf/Ytt so this can never drift from the formula the
# real solve actually uses.
function calc_ac_branch_flows(case::PowerCase, vm::Dict{String,Float64}, va::Dict{String,Float64})
    flows = Dict{String, NamedTuple{(:pf, :qf, :pt, :qt), NTuple{4,Float64}}}()
    for branch in case.branches
        if branch.status == 0
            continue
        end
        terms = ac_branch_admittance_terms(branch)
        f, t = branch.from_bus, branch.to_bus
        Vf = vm[f] * exp(im * va[f])
        Vt = vm[t] * exp(im * va[t])
        Sf = Vf * conj(terms.Yff * Vf + terms.Yft * Vt)
        St = Vt * conj(terms.Ytf * Vf + terms.Ytt * Vt)
        flows[branch.id] = (pf=real(Sf), qf=imag(Sf), pt=real(St), qt=imag(St))
    end
    return flows
end
