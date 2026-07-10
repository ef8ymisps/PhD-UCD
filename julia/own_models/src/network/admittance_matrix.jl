using LinearAlgebra

# Build the complex AC bus-admittance matrix Ybus.
function build_ybus(case::PowerCase)
    # Full AC admittance matrix with branch-side shunts and complex taps.
    nb = length(case.buses)
    Y = zeros(ComplexF64, nb, nb)

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

        Y[i, i] += (y + y_fr) / abs2(tap)
        Y[i, j] += -y / conj(tap)
        Y[j, i] += -y / tap
        Y[j, j] += y + y_to
    end

    for shunt in case.shunts
        if shunt.status == 0
            continue
        end

        i = case.bus_id_to_index[shunt.bus]
        Y[i, i] += shunt.gs + im * shunt.bs
    end

    return Y
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
