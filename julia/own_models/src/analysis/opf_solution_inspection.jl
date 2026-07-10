# ============================================================
# OPF solution inspection utilities
# DataFrame-ready rows for OwnPowerModels and PowerModels.jl.
# ============================================================

using CSV
using DataFrames
using Statistics

function _opf_pm_solution(pm_result, section)
    return get(pm_result["solution"], section, Dict())
end

function _opf_absmax(values)
    return isempty(values) ? 0.0 : maximum(abs.(values))
end

function _opf_absmean(values)
    return isempty(values) ? 0.0 : sum(abs.(values)) / length(values)
end

function _opf_absstd(values)
    if length(values) < 2
        return 0.0
    end
    magnitudes = abs.(values)
    return Statistics.std(magnitudes)
end

function _opf_limit_flag(value, limit; tol=1e-6)
    return abs(value - limit) <= tol
end

function _opf_loading_abs(flow)
    return abs(flow)
end

function _opf_loading_pct(loading, rate_a)
    return rate_a > 0.0 ? 100.0 * loading / rate_a : NaN
end

function _opf_flow_margin(loading, rate_a)
    return rate_a > 0.0 ? rate_a - loading : NaN
end

function _opf_binding(loading, rate_a; tol=1e-6)
    return rate_a > 0.0 && abs(loading - rate_a) <= tol
end

function _opf_apparent_power(p, q)
    return sqrt(p^2 + q^2)
end

function _opf_pm_status(pm_result)
    return string(pm_result["termination_status"])
end

# ------------------------------------------------------------
# Binding-set classification (own vs PowerModels)
# ------------------------------------------------------------

"""Classify whether a constraint is binding in own model, PowerModels, both, or neither."""
function classify_binding(own_flag::Bool, pm_flag::Bool)
    if own_flag && pm_flag
        return "both"
    elseif own_flag
        return "only_own"
    elseif pm_flag
        return "only_pm"
    else
        return "neither"
    end
end

"""Classify an only_own/only_pm mismatch as numerical noise vs a real modelling difference.

`scale` should be the constraint's natural range (e.g. pmax - pmin); mismatches
within `tol` of the boundary are noise, mismatches beyond `rel_threshold` of the
natural scale are flagged as a modelling difference, anything else is "unclear".
"""
function classify_mismatch_reason(abs_diff::Float64, scale::Float64; tol::Float64=1e-6, rel_threshold::Float64=0.01)
    if abs_diff <= tol
        return "numerical_noise"
    elseif scale > 0.0 && abs_diff > rel_threshold * scale
        return "modelling_difference"
    else
        return "unclear"
    end
end

"""Count both/only_own/only_pm/neither occurrences of a classification vector."""
function _binding_counts(classes)
    return (
        both = count(==("both"), classes),
        only_own = count(==("only_own"), classes),
        only_pm = count(==("only_pm"), classes),
    )
end

"""Aggregate per-element binding classifications into one case/scenario row.

Accepts the per-element comparison rows produced by `compare_*_generator_rows`
and `compare_*_branch_rows` (and, for AC, `compare_ac_opf_bus_rows`) and returns
counts of both/only_own/only_pm for each constraint type.
"""
function binding_set_comparison(generator_rows; bus_rows=nothing, branch_rows=nothing)
    pmin_classes = [classify_binding(row.own_at_pmin, row.pm_at_pmin) for row in generator_rows]
    pmax_classes = [classify_binding(row.own_at_pmax, row.pm_at_pmax) for row in generator_rows]
    result = Dict{Symbol, Int}()
    for (name, classes) in [(:pmin, pmin_classes), (:pmax, pmax_classes)]
        counts = _binding_counts(classes)
        result[Symbol("n_$(name)_both")] = counts.both
        result[Symbol("n_$(name)_only_own")] = counts.only_own
        result[Symbol("n_$(name)_only_pm")] = counts.only_pm
    end

    if !isempty(generator_rows) && haskey(generator_rows[1], :own_at_qmin)
        qmin_classes = [classify_binding(row.own_at_qmin, row.pm_at_qmin) for row in generator_rows]
        qmax_classes = [classify_binding(row.own_at_qmax, row.pm_at_qmax) for row in generator_rows]
        for (name, classes) in [(:qmin, qmin_classes), (:qmax, qmax_classes)]
            counts = _binding_counts(classes)
            result[Symbol("n_$(name)_both")] = counts.both
            result[Symbol("n_$(name)_only_own")] = counts.only_own
            result[Symbol("n_$(name)_only_pm")] = counts.only_pm
        end
    end

    if bus_rows !== nothing
        vmin_classes = [classify_binding(row.own_at_vmin, row.pm_at_vmin) for row in bus_rows]
        vmax_classes = [classify_binding(row.own_at_vmax, row.pm_at_vmax) for row in bus_rows]
        for (name, classes) in [(:vmin, vmin_classes), (:vmax, vmax_classes)]
            counts = _binding_counts(classes)
            result[Symbol("n_$(name)_both")] = counts.both
            result[Symbol("n_$(name)_only_own")] = counts.only_own
            result[Symbol("n_$(name)_only_pm")] = counts.only_pm
        end
    end

    if branch_rows !== nothing
        thermal_classes = [classify_binding(row.own_binding, row.pm_binding) for row in branch_rows]
        angle_lower_classes = [classify_binding(row.own_angle_lower_binding, row.pm_angle_lower_binding) for row in branch_rows]
        angle_upper_classes = [classify_binding(row.own_angle_upper_binding, row.pm_angle_upper_binding) for row in branch_rows]
        for (name, classes) in [(:thermal, thermal_classes), (:angle_lower, angle_lower_classes), (:angle_upper, angle_upper_classes)]
            counts = _binding_counts(classes)
            result[Symbol("n_$(name)_both")] = counts.both
            result[Symbol("n_$(name)_only_own")] = counts.only_own
            result[Symbol("n_$(name)_only_pm")] = counts.only_pm
        end
    end

    return (; result...)
end

# ------------------------------------------------------------
# Per-element binding_class / mismatch_reason columns
#
# Post-processing over already-built compare_*_rows (pure additions — the
# row builders above are untouched). Each check is (prefix, own_flag_key,
# pm_flag_key, diff_fn, scale): diff_fn(row) gives the magnitude to classify
# against `scale` (the constraint's natural range) when own/pm disagree.
# ------------------------------------------------------------

function _classify_row_constraint(row, own_key::Symbol, pm_key::Symbol, diff_fn, scale::Float64)
    own_flag = getproperty(row, own_key)
    pm_flag = getproperty(row, pm_key)
    cls = classify_binding(own_flag, pm_flag)
    reason = cls in ("only_own", "only_pm") ? classify_mismatch_reason(abs(diff_fn(row)), scale) : "n/a"
    return cls, reason
end

function _add_binding_classification(row, checks)
    pairs = Pair{Symbol, Any}[]
    for (prefix, own_key, pm_key, diff_fn, scale) in checks
        cls, reason = _classify_row_constraint(row, own_key, pm_key, diff_fn, scale)
        push!(pairs, Symbol("$(prefix)_binding_class") => cls)
        push!(pairs, Symbol("$(prefix)_mismatch_reason") => reason)
    end
    return merge(row, NamedTuple(pairs))
end

function classify_dc_opf_generator_rows(rows)
    return [
        _add_binding_classification(row, (
            ("pmin", :own_at_pmin, :pm_at_pmin, r -> r.pg_diff, row.pmax - row.pmin),
            ("pmax", :own_at_pmax, :pm_at_pmax, r -> r.pg_diff, row.pmax - row.pmin),
        ))
        for row in rows
    ]
end

function classify_dc_opf_branch_rows(rows)
    return [
        _add_binding_classification(row, (
            ("thermal", :own_binding, :pm_binding, r -> r.abs_loading_pct_diff, 100.0),
            ("angle_lower", :own_angle_lower_binding, :pm_angle_lower_binding, r -> r.own_angle_diff - r.pm_angle_diff, max(row.angmax - row.angmin, 1e-9)),
            ("angle_upper", :own_angle_upper_binding, :pm_angle_upper_binding, r -> r.own_angle_diff - r.pm_angle_diff, max(row.angmax - row.angmin, 1e-9)),
        ))
        for row in rows
    ]
end

function classify_ac_opf_generator_rows(rows)
    return [
        _add_binding_classification(row, (
            ("pmin", :own_at_pmin, :pm_at_pmin, r -> r.pg_diff, row.pmax - row.pmin),
            ("pmax", :own_at_pmax, :pm_at_pmax, r -> r.pg_diff, row.pmax - row.pmin),
            ("qmin", :own_at_qmin, :pm_at_qmin, r -> r.qg_diff, row.qmax - row.qmin),
            ("qmax", :own_at_qmax, :pm_at_qmax, r -> r.qg_diff, row.qmax - row.qmin),
        ))
        for row in rows
    ]
end

function classify_ac_opf_bus_rows(rows)
    return [
        _add_binding_classification(row, (
            ("vmin", :own_at_vmin, :pm_at_vmin, r -> r.vm_diff, row.vmax - row.vmin),
            ("vmax", :own_at_vmax, :pm_at_vmax, r -> r.vm_diff, row.vmax - row.vmin),
        ))
        for row in rows
    ]
end

function classify_ac_opf_branch_rows(rows)
    return [
        _add_binding_classification(row, (
            ("thermal", :own_binding, :pm_binding, r -> r.abs_loading_pct_diff, 100.0),
            ("angle_lower", :own_angle_lower_binding, :pm_angle_lower_binding, r -> r.own_angle_diff - r.pm_angle_diff, max(row.angmax - row.angmin, 1e-9)),
            ("angle_upper", :own_angle_upper_binding, :pm_angle_upper_binding, r -> r.own_angle_diff - r.pm_angle_diff, max(row.angmax - row.angmin, 1e-9)),
        ))
        for row in rows
    ]
end

# ------------------------------------------------------------
# Branch flow convention check
#
# AC only (needs qf/pt/qt, which DC branch rows don't carry). For every
# branch near its rating (own OR pm loading within `near_rating_pct` of
# 100%), compare pf/qf/pt/qt sign and magnitude between own and PowerModels.
# Large disagreement here is the signature of a flow/apparent-power
# convention mismatch rather than solver noise.
# ------------------------------------------------------------

function ac_branch_flow_convention_check(branch_rows; near_rating_pct::Float64=80.0)
    return [
        (
            scenario = row.scenario,
            branch_id = row.branch_id,
            own_loading_pct = row.own_loading_pct,
            pm_loading_pct = row.pm_loading_pct,
            own_pf = row.own_pf, pm_pf = row.pm_pf, pf_sign_match = sign(row.own_pf) == sign(row.pm_pf),
            own_qf = row.own_qf, pm_qf = row.pm_qf, qf_sign_match = sign(row.own_qf) == sign(row.pm_qf),
            own_pt = row.own_pt, pm_pt = row.pm_pt, pt_sign_match = sign(row.own_pt) == sign(row.pm_pt),
            own_qt = row.own_qt, pm_qt = row.pm_qt, qt_sign_match = sign(row.own_qt) == sign(row.pm_qt),
            abs_loading_pct_diff = row.abs_loading_pct_diff,
        )
        for row in branch_rows
        if max(row.own_loading_pct, row.pm_loading_pct) >= near_rating_pct
    ]
end

# ------------------------------------------------------------
# DC OPF generator rows
# ------------------------------------------------------------

function own_dc_opf_generator_rows(case::PowerCase, own_result; scenario="base", tol=1e-6)
    return [
        (
            scenario = scenario,
            source = "own",
            gen_id = gen.id,
            bus_id = gen.bus,
            pg = own_result.pg[gen.id],
            pmin = gen.pmin,
            pmax = gen.pmax,
            cost_c2 = gen.cost_c2,
            cost_c1 = gen.cost_c1,
            cost_c0 = gen.cost_c0,
            at_pmin = _opf_limit_flag(own_result.pg[gen.id], gen.pmin; tol=tol),
            at_pmax = _opf_limit_flag(own_result.pg[gen.id], gen.pmax; tol=tol),
            headroom_to_pmax = gen.pmax - own_result.pg[gen.id],
            margin_from_pmin = own_result.pg[gen.id] - gen.pmin
        )
        for gen in case.generators
        if haskey(own_result.pg, gen.id)
    ]
end

function powermodels_dc_opf_generator_rows(case::PowerCase, pm_result; scenario="base", tol=1e-6)
    pm_gen = _opf_pm_solution(pm_result, "gen")

    return [
        (
            scenario = scenario,
            source = "powermodels",
            gen_id = gen.id,
            bus_id = gen.bus,
            pg = pm_gen[gen.id]["pg"],
            pmin = gen.pmin,
            pmax = gen.pmax,
            cost_c2 = gen.cost_c2,
            cost_c1 = gen.cost_c1,
            cost_c0 = gen.cost_c0,
            at_pmin = _opf_limit_flag(pm_gen[gen.id]["pg"], gen.pmin; tol=tol),
            at_pmax = _opf_limit_flag(pm_gen[gen.id]["pg"], gen.pmax; tol=tol),
            headroom_to_pmax = gen.pmax - pm_gen[gen.id]["pg"],
            margin_from_pmin = pm_gen[gen.id]["pg"] - gen.pmin
        )
        for gen in case.generators
        if haskey(pm_gen, gen.id)
    ]
end

function compare_dc_opf_generator_rows(case::PowerCase, own_result, pm_result; scenario="base", tol=1e-6)
    pm_gen = _opf_pm_solution(pm_result, "gen")

    return [
        begin
            own_pg = own_result.pg[gen.id]
            pm_pg = pm_gen[gen.id]["pg"]

            (
                scenario = scenario,
                gen_id = gen.id,
                bus_id = gen.bus,
                own_pg = own_pg,
                pm_pg = pm_pg,
                pg_diff = own_pg - pm_pg,
                abs_pg_diff = abs(own_pg - pm_pg),
                pmin = gen.pmin,
                pmax = gen.pmax,
                cost_c2 = gen.cost_c2,
                cost_c1 = gen.cost_c1,
                cost_c0 = gen.cost_c0,
                own_at_pmin = _opf_limit_flag(own_pg, gen.pmin; tol=tol),
                own_at_pmax = _opf_limit_flag(own_pg, gen.pmax; tol=tol),
                pm_at_pmin = _opf_limit_flag(pm_pg, gen.pmin; tol=tol),
                pm_at_pmax = _opf_limit_flag(pm_pg, gen.pmax; tol=tol),
                own_headroom_to_pmax = gen.pmax - own_pg,
                pm_headroom_to_pmax = gen.pmax - pm_pg
            )
        end
        for gen in case.generators
        if haskey(own_result.pg, gen.id) && haskey(pm_gen, gen.id)
    ]
end

# ------------------------------------------------------------
# DC OPF branch rows
# ------------------------------------------------------------

function own_dc_opf_branch_rows(case::PowerCase, own_result; scenario="base", tol=1e-6)
    return [
        begin
            pf = own_result.branch_flows[branch.id]
            loading = _opf_loading_abs(pf)

            (
                scenario = scenario,
                source = "own",
                branch_id = branch.id,
                from_bus = branch.from_bus,
                to_bus = branch.to_bus,
                pf = pf,
                rate_a = branch.rate_a,
                loading_abs = loading,
                loading_pct = _opf_loading_pct(loading, branch.rate_a),
                binding = _opf_binding(loading, branch.rate_a; tol=tol),
                flow_margin = _opf_flow_margin(loading, branch.rate_a),
                angmin = branch.angmin,
                angmax = branch.angmax
            )
        end
        for branch in case.branches
        if haskey(own_result.branch_flows, branch.id)
    ]
end

function powermodels_dc_opf_branch_rows(case::PowerCase, pm_result; scenario="base", tol=1e-6)
    pm_branch = _opf_pm_solution(pm_result, "branch")

    return [
        begin
            pf = pm_branch[branch.id]["pf"]
            loading = _opf_loading_abs(pf)

            (
                scenario = scenario,
                source = "powermodels",
                branch_id = branch.id,
                from_bus = branch.from_bus,
                to_bus = branch.to_bus,
                pf = pf,
                rate_a = branch.rate_a,
                loading_abs = loading,
                loading_pct = _opf_loading_pct(loading, branch.rate_a),
                binding = _opf_binding(loading, branch.rate_a; tol=tol),
                flow_margin = _opf_flow_margin(loading, branch.rate_a),
                angmin = branch.angmin,
                angmax = branch.angmax
            )
        end
        for branch in case.branches
        if haskey(pm_branch, branch.id)
    ]
end

function compare_dc_opf_branch_rows(case::PowerCase, own_result, pm_result; scenario="base", tol=1e-6)
    pm_branch = _opf_pm_solution(pm_result, "branch")
    pm_bus = _opf_pm_solution(pm_result, "bus")

    return [
        begin
            own_pf = own_result.branch_flows[branch.id]
            pm_pf = pm_branch[branch.id]["pf"]
            own_loading = _opf_loading_abs(own_pf)
            pm_loading = _opf_loading_abs(pm_pf)
            own_loading_pct = _opf_loading_pct(own_loading, branch.rate_a)
            pm_loading_pct = _opf_loading_pct(pm_loading, branch.rate_a)
            own_angle_diff = own_result.theta[branch.from_bus] - own_result.theta[branch.to_bus]
            pm_angle_diff = pm_bus[branch.from_bus]["va"] - pm_bus[branch.to_bus]["va"]

            (
                scenario = scenario,
                branch_id = branch.id,
                from_bus = branch.from_bus,
                to_bus = branch.to_bus,
                own_pf = own_pf,
                pm_pf = pm_pf,
                pf_diff = own_pf - pm_pf,
                abs_pf_diff = abs(own_pf - pm_pf),
                rate_a = branch.rate_a,
                own_loading_abs = own_loading,
                pm_loading_abs = pm_loading,
                own_loading_pct = own_loading_pct,
                pm_loading_pct = pm_loading_pct,
                loading_pct_diff = own_loading_pct - pm_loading_pct,
                abs_loading_pct_diff = abs(own_loading_pct - pm_loading_pct),
                own_binding = _opf_binding(own_loading, branch.rate_a; tol=tol),
                pm_binding = _opf_binding(pm_loading, branch.rate_a; tol=tol),
                own_flow_margin = _opf_flow_margin(own_loading, branch.rate_a),
                pm_flow_margin = _opf_flow_margin(pm_loading, branch.rate_a),
                angmin = branch.angmin,
                angmax = branch.angmax,
                own_angle_diff = own_angle_diff,
                pm_angle_diff = pm_angle_diff,
                own_angle_lower_binding = abs(own_angle_diff - branch.angmin) <= tol,
                own_angle_upper_binding = abs(own_angle_diff - branch.angmax) <= tol,
                pm_angle_lower_binding = abs(pm_angle_diff - branch.angmin) <= tol,
                pm_angle_upper_binding = abs(pm_angle_diff - branch.angmax) <= tol
            )
        end
        for branch in case.branches
        if haskey(own_result.branch_flows, branch.id) && haskey(pm_branch, branch.id)
            && haskey(own_result.theta, branch.from_bus) && haskey(own_result.theta, branch.to_bus)
            && haskey(pm_bus, branch.from_bus) && haskey(pm_bus, branch.to_bus)
    ]
end

function dc_opf_summary_row(case::PowerCase, own_result, pm_result; scenario="base")
    pm_bus = _opf_pm_solution(pm_result, "bus")

    gen_rows = compare_dc_opf_generator_rows(case, own_result, pm_result; scenario=scenario)
    branch_rows = compare_dc_opf_branch_rows(case, own_result, pm_result; scenario=scenario)

    pg_diffs = [row.pg_diff for row in gen_rows]

    va_diffs = [
        own_result.theta[bus.id] - pm_bus[bus.id]["va"]
        for bus in case.buses
        if haskey(own_result.theta, bus.id) && haskey(pm_bus, bus.id)
    ]

    pf_diffs = [row.pf_diff for row in branch_rows]
    loading_pct_diffs = [row.loading_pct_diff for row in branch_rows]

    binding = binding_set_comparison(gen_rows; branch_rows=branch_rows)

    return (
        scenario = scenario,
        case_name = case.name,
        own_status = string(own_result.termination_status),
        pm_status = _opf_pm_status(pm_result),
        own_objective = own_result.objective,
        pm_objective = pm_result["objective"],
        objective_diff = own_result.objective - pm_result["objective"],
        max_pg_diff = _opf_absmax(pg_diffs),
        mean_pg_diff = _opf_absmean(pg_diffs),
        std_pg_diff = _opf_absstd(pg_diffs),
        max_va_diff = _opf_absmax(va_diffs),
        mean_va_diff = _opf_absmean(va_diffs),
        std_va_diff = _opf_absstd(va_diffs),
        max_pf_diff = _opf_absmax(pf_diffs),
        mean_pf_diff = _opf_absmean(pf_diffs),
        std_pf_diff = _opf_absstd(pf_diffs),
        max_loading_pct_diff = _opf_absmax(loading_pct_diffs),
        mean_loading_pct_diff = _opf_absmean(loading_pct_diffs),
        std_loading_pct_diff = _opf_absstd(loading_pct_diffs),
        own_solve_iterations = own_result.solve_iterations,
        pm_solve_iterations = NaN,
        binding...
    )
end

# ------------------------------------------------------------
# AC OPF generator rows
# ------------------------------------------------------------

function own_ac_opf_generator_rows(case::PowerCase, own_result; scenario="base", tol=1e-6)
    return [
        (
            scenario = scenario,
            source = "own",
            gen_id = gen.id,
            bus_id = gen.bus,
            pg = own_result.pg[gen.id],
            qg = own_result.qg[gen.id],
            pmin = gen.pmin,
            pmax = gen.pmax,
            qmin = gen.qmin,
            qmax = gen.qmax,
            cost_c2 = gen.cost_c2,
            cost_c1 = gen.cost_c1,
            cost_c0 = gen.cost_c0,
            at_pmin = _opf_limit_flag(own_result.pg[gen.id], gen.pmin; tol=tol),
            at_pmax = _opf_limit_flag(own_result.pg[gen.id], gen.pmax; tol=tol),
            at_qmin = _opf_limit_flag(own_result.qg[gen.id], gen.qmin; tol=tol),
            at_qmax = _opf_limit_flag(own_result.qg[gen.id], gen.qmax; tol=tol),
            headroom_to_pmax = gen.pmax - own_result.pg[gen.id],
            margin_from_pmin = own_result.pg[gen.id] - gen.pmin
        )
        for gen in case.generators
        if haskey(own_result.pg, gen.id) && haskey(own_result.qg, gen.id)
    ]
end

function powermodels_ac_opf_generator_rows(case::PowerCase, pm_result; scenario="base", tol=1e-6)
    pm_gen = _opf_pm_solution(pm_result, "gen")

    return [
        (
            scenario = scenario,
            source = "powermodels",
            gen_id = gen.id,
            bus_id = gen.bus,
            pg = pm_gen[gen.id]["pg"],
            qg = pm_gen[gen.id]["qg"],
            pmin = gen.pmin,
            pmax = gen.pmax,
            qmin = gen.qmin,
            qmax = gen.qmax,
            cost_c2 = gen.cost_c2,
            cost_c1 = gen.cost_c1,
            cost_c0 = gen.cost_c0,
            at_pmin = _opf_limit_flag(pm_gen[gen.id]["pg"], gen.pmin; tol=tol),
            at_pmax = _opf_limit_flag(pm_gen[gen.id]["pg"], gen.pmax; tol=tol),
            at_qmin = _opf_limit_flag(pm_gen[gen.id]["qg"], gen.qmin; tol=tol),
            at_qmax = _opf_limit_flag(pm_gen[gen.id]["qg"], gen.qmax; tol=tol),
            headroom_to_pmax = gen.pmax - pm_gen[gen.id]["pg"],
            margin_from_pmin = pm_gen[gen.id]["pg"] - gen.pmin
        )
        for gen in case.generators
        if haskey(pm_gen, gen.id)
    ]
end

function compare_ac_opf_generator_rows(case::PowerCase, own_result, pm_result; scenario="base", tol=1e-6)
    pm_gen = _opf_pm_solution(pm_result, "gen")

    return [
        begin
            own_pg = own_result.pg[gen.id]
            own_qg = own_result.qg[gen.id]
            pm_pg = pm_gen[gen.id]["pg"]
            pm_qg = pm_gen[gen.id]["qg"]

            (
                scenario = scenario,
                gen_id = gen.id,
                bus_id = gen.bus,
                own_pg = own_pg,
                pm_pg = pm_pg,
                pg_diff = own_pg - pm_pg,
                abs_pg_diff = abs(own_pg - pm_pg),
                own_qg = own_qg,
                pm_qg = pm_qg,
                qg_diff = own_qg - pm_qg,
                abs_qg_diff = abs(own_qg - pm_qg),
                pmin = gen.pmin,
                pmax = gen.pmax,
                qmin = gen.qmin,
                qmax = gen.qmax,
                cost_c2 = gen.cost_c2,
                cost_c1 = gen.cost_c1,
                cost_c0 = gen.cost_c0,
                own_at_pmin = _opf_limit_flag(own_pg, gen.pmin; tol=tol),
                own_at_pmax = _opf_limit_flag(own_pg, gen.pmax; tol=tol),
                own_at_qmin = _opf_limit_flag(own_qg, gen.qmin; tol=tol),
                own_at_qmax = _opf_limit_flag(own_qg, gen.qmax; tol=tol),
                pm_at_pmin = _opf_limit_flag(pm_pg, gen.pmin; tol=tol),
                pm_at_pmax = _opf_limit_flag(pm_pg, gen.pmax; tol=tol),
                pm_at_qmin = _opf_limit_flag(pm_qg, gen.qmin; tol=tol),
                pm_at_qmax = _opf_limit_flag(pm_qg, gen.qmax; tol=tol),
                own_headroom_to_pmax = gen.pmax - own_pg,
                pm_headroom_to_pmax = gen.pmax - pm_pg
            )
        end
        for gen in case.generators
        if haskey(own_result.pg, gen.id) && haskey(own_result.qg, gen.id) && haskey(pm_gen, gen.id)
    ]
end

# ------------------------------------------------------------
# AC OPF bus rows
# ------------------------------------------------------------

function own_ac_opf_bus_rows(case::PowerCase, own_result; scenario="base", tol=1e-6)
    return [
        (
            scenario = scenario,
            source = "own",
            bus_id = bus.id,
            vm = own_result.vm[bus.id],
            va = own_result.va[bus.id],
            vmin = bus.vmin,
            vmax = bus.vmax,
            at_vmin = _opf_limit_flag(own_result.vm[bus.id], bus.vmin; tol=tol),
            at_vmax = _opf_limit_flag(own_result.vm[bus.id], bus.vmax; tol=tol)
        )
        for bus in case.buses
        if haskey(own_result.vm, bus.id) && haskey(own_result.va, bus.id)
    ]
end

function powermodels_ac_opf_bus_rows(case::PowerCase, pm_result; scenario="base", tol=1e-6)
    pm_bus = _opf_pm_solution(pm_result, "bus")

    return [
        (
            scenario = scenario,
            source = "powermodels",
            bus_id = bus.id,
            vm = pm_bus[bus.id]["vm"],
            va = pm_bus[bus.id]["va"],
            vmin = bus.vmin,
            vmax = bus.vmax,
            at_vmin = _opf_limit_flag(pm_bus[bus.id]["vm"], bus.vmin; tol=tol),
            at_vmax = _opf_limit_flag(pm_bus[bus.id]["vm"], bus.vmax; tol=tol)
        )
        for bus in case.buses
        if haskey(pm_bus, bus.id)
    ]
end

function compare_ac_opf_bus_rows(case::PowerCase, own_result, pm_result; scenario="base", tol=1e-6)
    pm_bus = _opf_pm_solution(pm_result, "bus")

    return [
        begin
            own_vm = own_result.vm[bus.id]
            own_va = own_result.va[bus.id]
            pm_vm = pm_bus[bus.id]["vm"]
            pm_va = pm_bus[bus.id]["va"]

            (
                scenario = scenario,
                bus_id = bus.id,
                own_vm = own_vm,
                pm_vm = pm_vm,
                vm_diff = own_vm - pm_vm,
                abs_vm_diff = abs(own_vm - pm_vm),
                own_va = own_va,
                pm_va = pm_va,
                va_diff = own_va - pm_va,
                abs_va_diff = abs(own_va - pm_va),
                vmin = bus.vmin,
                vmax = bus.vmax,
                own_at_vmin = _opf_limit_flag(own_vm, bus.vmin; tol=tol),
                own_at_vmax = _opf_limit_flag(own_vm, bus.vmax; tol=tol),
                pm_at_vmin = _opf_limit_flag(pm_vm, bus.vmin; tol=tol),
                pm_at_vmax = _opf_limit_flag(pm_vm, bus.vmax; tol=tol)
            )
        end
        for bus in case.buses
        if haskey(own_result.vm, bus.id) && haskey(own_result.va, bus.id) && haskey(pm_bus, bus.id)
    ]
end

# ------------------------------------------------------------
# AC OPF branch rows
# ------------------------------------------------------------

function own_ac_opf_branch_rows(case::PowerCase, own_result; scenario="base", tol=1e-6)
    return [
        begin
            flow = own_result.branch_flows[branch.id]
            pf = flow["pf"]
            qf = flow["qf"]
            pt = flow["pt"]
            qt = flow["qt"]
            sf = _opf_apparent_power(pf, qf)
            st = _opf_apparent_power(pt, qt)
            loading = max(sf, st)

            (
                scenario = scenario,
                source = "own",
                branch_id = branch.id,
                from_bus = branch.from_bus,
                to_bus = branch.to_bus,
                pf = pf,
                qf = qf,
                pt = pt,
                qt = qt,
                sf = sf,
                st = st,
                loading_abs = loading,
                loading_pct = _opf_loading_pct(loading, branch.rate_a),
                binding = _opf_binding(loading, branch.rate_a; tol=tol),
                flow_margin = _opf_flow_margin(loading, branch.rate_a),
                rate_a = branch.rate_a,
                angmin = branch.angmin,
                angmax = branch.angmax
            )
        end
        for branch in case.branches
        if haskey(own_result.branch_flows, branch.id)
    ]
end

function powermodels_ac_opf_branch_rows(case::PowerCase, pm_result; scenario="base", tol=1e-6)
    pm_branch = _opf_pm_solution(pm_result, "branch")

    return [
        begin
            flow = pm_branch[branch.id]
            pf = flow["pf"]
            qf = flow["qf"]
            pt = flow["pt"]
            qt = flow["qt"]
            sf = _opf_apparent_power(pf, qf)
            st = _opf_apparent_power(pt, qt)
            loading = max(sf, st)

            (
                scenario = scenario,
                source = "powermodels",
                branch_id = branch.id,
                from_bus = branch.from_bus,
                to_bus = branch.to_bus,
                pf = pf,
                qf = qf,
                pt = pt,
                qt = qt,
                sf = sf,
                st = st,
                loading_abs = loading,
                loading_pct = _opf_loading_pct(loading, branch.rate_a),
                binding = _opf_binding(loading, branch.rate_a; tol=tol),
                flow_margin = _opf_flow_margin(loading, branch.rate_a),
                rate_a = branch.rate_a,
                angmin = branch.angmin,
                angmax = branch.angmax
            )
        end
        for branch in case.branches
        if haskey(pm_branch, branch.id)
    ]
end

function compare_ac_opf_branch_rows(case::PowerCase, own_result, pm_result; scenario="base", tol=1e-6)
    pm_branch = _opf_pm_solution(pm_result, "branch")
    pm_bus = _opf_pm_solution(pm_result, "bus")

    return [
        begin
            own_flow = own_result.branch_flows[branch.id]
            pm_flow = pm_branch[branch.id]

            own_pf = own_flow["pf"]
            own_qf = own_flow["qf"]
            own_pt = own_flow["pt"]
            own_qt = own_flow["qt"]

            pm_pf = pm_flow["pf"]
            pm_qf = pm_flow["qf"]
            pm_pt = pm_flow["pt"]
            pm_qt = pm_flow["qt"]

            own_sf = _opf_apparent_power(own_pf, own_qf)
            own_st = _opf_apparent_power(own_pt, own_qt)
            pm_sf = _opf_apparent_power(pm_pf, pm_qf)
            pm_st = _opf_apparent_power(pm_pt, pm_qt)
            own_loading = max(own_sf, own_st)
            pm_loading = max(pm_sf, pm_st)
            own_loading_pct = _opf_loading_pct(own_loading, branch.rate_a)
            pm_loading_pct = _opf_loading_pct(pm_loading, branch.rate_a)
            own_angle_diff = own_result.va[branch.from_bus] - own_result.va[branch.to_bus]
            pm_angle_diff = pm_bus[branch.from_bus]["va"] - pm_bus[branch.to_bus]["va"]

            (
                scenario = scenario,
                branch_id = branch.id,
                from_bus = branch.from_bus,
                to_bus = branch.to_bus,
                own_pf = own_pf,
                pm_pf = pm_pf,
                pf_diff = own_pf - pm_pf,
                abs_pf_diff = abs(own_pf - pm_pf),
                own_qf = own_qf,
                pm_qf = pm_qf,
                qf_diff = own_qf - pm_qf,
                abs_qf_diff = abs(own_qf - pm_qf),
                own_pt = own_pt,
                pm_pt = pm_pt,
                pt_diff = own_pt - pm_pt,
                abs_pt_diff = abs(own_pt - pm_pt),
                own_qt = own_qt,
                pm_qt = pm_qt,
                qt_diff = own_qt - pm_qt,
                abs_qt_diff = abs(own_qt - pm_qt),
                own_sf = own_sf,
                pm_sf = pm_sf,
                own_st = own_st,
                pm_st = pm_st,
                own_loading_abs = own_loading,
                pm_loading_abs = pm_loading,
                own_loading_pct = own_loading_pct,
                pm_loading_pct = pm_loading_pct,
                loading_pct_diff = own_loading_pct - pm_loading_pct,
                abs_loading_pct_diff = abs(own_loading_pct - pm_loading_pct),
                own_binding = _opf_binding(own_loading, branch.rate_a; tol=tol),
                pm_binding = _opf_binding(pm_loading, branch.rate_a; tol=tol),
                own_flow_margin = _opf_flow_margin(own_loading, branch.rate_a),
                pm_flow_margin = _opf_flow_margin(pm_loading, branch.rate_a),
                rate_a = branch.rate_a,
                angmin = branch.angmin,
                angmax = branch.angmax,
                own_angle_diff = own_angle_diff,
                pm_angle_diff = pm_angle_diff,
                own_angle_lower_binding = abs(own_angle_diff - branch.angmin) <= tol,
                own_angle_upper_binding = abs(own_angle_diff - branch.angmax) <= tol,
                pm_angle_lower_binding = abs(pm_angle_diff - branch.angmin) <= tol,
                pm_angle_upper_binding = abs(pm_angle_diff - branch.angmax) <= tol
            )
        end
        for branch in case.branches
        if haskey(own_result.branch_flows, branch.id) && haskey(pm_branch, branch.id)
            && haskey(own_result.va, branch.from_bus) && haskey(own_result.va, branch.to_bus)
            && haskey(pm_bus, branch.from_bus) && haskey(pm_bus, branch.to_bus)
    ]
end

function inspect_branch_angle_limits(case::PowerCase, opf_result; scenario="base", tol=1e-6)
    rows = NamedTuple[]

    for branch in case.branches
        if branch.status == 0
            continue
        end

        f = branch.from_bus
        t = branch.to_bus

        if !haskey(opf_result.va, f) || !haskey(opf_result.va, t)
            continue
        end

        angle_diff = opf_result.va[f] - opf_result.va[t]
        lower_slack = angle_diff - branch.angmin
        upper_slack = branch.angmax - angle_diff

        push!(rows, (
            scenario = scenario,
            branch_id = branch.id,
            from_bus = f,
            to_bus = t,
            angle_diff = angle_diff,
            angmin = branch.angmin,
            angmax = branch.angmax,
            lower_slack = lower_slack,
            upper_slack = upper_slack,
            binding_lower = abs(lower_slack) <= tol,
            binding_upper = abs(upper_slack) <= tol,
            violated = angle_diff < branch.angmin - tol || angle_diff > branch.angmax + tol
        ))
    end

    return rows
end

function ac_opf_summary_row(case::PowerCase, own_result, pm_result; scenario="base")
    gen_rows = compare_ac_opf_generator_rows(case, own_result, pm_result; scenario=scenario)
    bus_rows = compare_ac_opf_bus_rows(case, own_result, pm_result; scenario=scenario)
    branch_rows = compare_ac_opf_branch_rows(case, own_result, pm_result; scenario=scenario)

    pg_diffs = [row.pg_diff for row in gen_rows]
    qg_diffs = [row.qg_diff for row in gen_rows]
    vm_diffs = [row.vm_diff for row in bus_rows]
    va_diffs = [row.va_diff for row in bus_rows]
    pf_diffs = [row.pf_diff for row in branch_rows]
    qf_diffs = [row.qf_diff for row in branch_rows]
    loading_pct_diffs = [row.loading_pct_diff for row in branch_rows]

    binding = binding_set_comparison(gen_rows; bus_rows=bus_rows, branch_rows=branch_rows)

    return (
        scenario = scenario,
        case_name = case.name,
        own_status = string(own_result.termination_status),
        pm_status = _opf_pm_status(pm_result),
        own_objective = own_result.objective,
        pm_objective = pm_result["objective"],
        objective_diff = own_result.objective - pm_result["objective"],
        max_pg_diff = _opf_absmax(pg_diffs),
        mean_pg_diff = _opf_absmean(pg_diffs),
        std_pg_diff = _opf_absstd(pg_diffs),
        max_qg_diff = _opf_absmax(qg_diffs),
        mean_qg_diff = _opf_absmean(qg_diffs),
        std_qg_diff = _opf_absstd(qg_diffs),
        max_vm_diff = _opf_absmax(vm_diffs),
        mean_vm_diff = _opf_absmean(vm_diffs),
        std_vm_diff = _opf_absstd(vm_diffs),
        max_va_diff = _opf_absmax(va_diffs),
        mean_va_diff = _opf_absmean(va_diffs),
        std_va_diff = _opf_absstd(va_diffs),
        max_pf_diff = _opf_absmax(pf_diffs),
        mean_pf_diff = _opf_absmean(pf_diffs),
        std_pf_diff = _opf_absstd(pf_diffs),
        max_qf_diff = _opf_absmax(qf_diffs),
        mean_qf_diff = _opf_absmean(qf_diffs),
        std_qf_diff = _opf_absstd(qf_diffs),
        max_loading_pct_diff = _opf_absmax(loading_pct_diffs),
        mean_loading_pct_diff = _opf_absmean(loading_pct_diffs),
        std_loading_pct_diff = _opf_absstd(loading_pct_diffs),
        own_solve_iterations = own_result.solve_iterations,
        pm_solve_iterations = NaN,
        binding...
    )
end

# ------------------------------------------------------------
# Long-format per-element CSV persistence
#
# Appends one row per element (generator/bus/branch) per scenario, so the
# full distribution of diffs is available for plotting, not just the
# per-scenario mean/max/std captured in the summary row. Off by default in
# the sweep script (opt-in via --write-detail-rows) since this is a lot of
# data across the full stress matrix.
# ------------------------------------------------------------

function append_rows_csv!(path::String, rows)
    if isempty(rows)
        return
    end
    mkpath(dirname(path))
    frame = DataFrame(rows)
    CSV.write(path, frame; append=isfile(path), writeheader=!isfile(path))
end

_with_case_name(rows, case_name::String) = [merge((case_name = case_name,), row) for row in rows]

function write_dc_opf_detail_rows!(output_dir::String, case::PowerCase, own_result, pm_result; scenario="base")
    gen_rows = _with_case_name(classify_dc_opf_generator_rows(compare_dc_opf_generator_rows(case, own_result, pm_result; scenario=scenario)), case.name)
    branch_rows = _with_case_name(classify_dc_opf_branch_rows(compare_dc_opf_branch_rows(case, own_result, pm_result; scenario=scenario)), case.name)
    append_rows_csv!(joinpath(output_dir, "dc_generators.csv"), gen_rows)
    append_rows_csv!(joinpath(output_dir, "dc_branches.csv"), branch_rows)
end

function write_ac_opf_detail_rows!(output_dir::String, case::PowerCase, own_result, pm_result; scenario="base")
    gen_rows = _with_case_name(classify_ac_opf_generator_rows(compare_ac_opf_generator_rows(case, own_result, pm_result; scenario=scenario)), case.name)
    bus_rows = _with_case_name(classify_ac_opf_bus_rows(compare_ac_opf_bus_rows(case, own_result, pm_result; scenario=scenario)), case.name)
    branch_rows = _with_case_name(classify_ac_opf_branch_rows(compare_ac_opf_branch_rows(case, own_result, pm_result; scenario=scenario)), case.name)
    append_rows_csv!(joinpath(output_dir, "ac_generators.csv"), gen_rows)
    append_rows_csv!(joinpath(output_dir, "ac_buses.csv"), bus_rows)
    append_rows_csv!(joinpath(output_dir, "ac_branches.csv"), branch_rows)

    convention_rows = _with_case_name(ac_branch_flow_convention_check(branch_rows), case.name)
    append_rows_csv!(joinpath(output_dir, "ac_branch_flow_convention_check.csv"), convention_rows)
end
