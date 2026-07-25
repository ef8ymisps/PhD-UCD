# Scaffold for the planned preventive, monolithic DC-SCOPF (see
# docs/formulations/dc_scopf.md for the full formulation this implements).
#
# STATUS: data structures and contingency enumeration only -- no model
# builder/solver yet. This mirrors julia/own_models/src/opf/dc/data.jl's
# DCOPFData/build_dc_opf_data exactly, once per contingency, deliberately
# reusing that function rather than duplicating the Bbus/Bf math: a branch
# outage is just build_dc_opf_data on a case copy with that branch's status
# zeroed (build_bbus/build_dc_opf_data already skip status==0 branches); a
# generator outage needs no topology change at all (Bbus/Bf are identical
# to the base case), only that generator's exclusion from the dispatchable
# set, which build_dc_opf_data already does via its own status==1 filter.

"""One contingency's DC network view: the `N1Contingency` it corresponds to
(reused from `julia/own_models/src/analysis/n1_screening.jl` -- NOT
re-enumerated here) and the `DCOPFData` rebuilt on that contingency's
topology."""
struct DCSCOPFContingencyData
    contingency::N1Contingency
    data::DCOPFData
end

"""Base case plus every N-1 contingency's own `DCOPFData` view. `data.Bbus`/
`data.Bf` per contingency are ready to use directly for the post-contingency
flow constraints in docs/formulations/dc_scopf.md -- `Pf_l^k = (Bf_k * theta)_l`,
`theta` still the single base-case decision variable, not re-solved per
contingency (see that doc's "Post-Contingency Flow Constraints" section for
why this is valid only because DC's Bf is a direct linear map, not a
recursive one)."""
struct DCSCOPFData
    base::DCOPFData
    contingencies::Vector{DCSCOPFContingencyData}
end

"""Build one contingency's `DCOPFData`, on the outaged topology.

Branch outage: `build_dc_opf_data` already filters to `branch.status == 1`
when building `active_branches`/`Bf`, and `build_bbus` (called internally)
already skips `branch.status == 0` -- so setting the outaged branch's status
to 0 on a case copy and calling `build_dc_opf_data` unchanged produces
exactly the contingency topology, no new matrix code needed.

Generator outage: topology (Bbus/Bf) is unchanged from the base case;
`build_dc_opf_data` already filters `active_generators`/`gens_by_bus` to
`gen.status == 1`, so the outaged generator is automatically excluded from
the dispatchable set on the same case-copy-with-status-zeroed pattern."""
# `Branch`/`Generator`/`PowerCase` are plain (non-`@kwdef`) structs, so
# there is no keyword-based copy constructor -- these two helpers do the
# field-by-field reconstruction explicitly, only ever changing `status`.
function _outaged_branch(b::Branch)
    return Branch(
        b.id, b.index, b.from_bus, b.to_bus, b.r, b.x, b.b, b.b_fr, b.b_to,
        b.g_fr, b.g_to, b.rate_a, b.tap, b.shift, b.angmin, b.angmax, 0,
    )
end

function _outaged_generator(g::Generator)
    return Generator(
        g.id, g.index, g.bus, g.pg0, g.qg0, g.pmin, g.pmax, g.qmin, g.qmax,
        0, g.cost_c2, g.cost_c1, g.cost_c0,
    )
end

function build_dc_scopf_contingency_data(case::PowerCase, contingency::N1Contingency)
    if contingency.element_type == "branch"
        outaged_branches = [
            b.id == contingency.element_id ? _outaged_branch(b) : b
            for b in case.branches
        ]
        contingency_case = PowerCase(
            case.name, case.baseMVA, case.buses, outaged_branches, case.generators,
            case.loads, case.shunts, case.slack_bus_id, case.bus_id_to_index,
        )
    elseif contingency.element_type == "generator"
        outaged_generators = [
            g.id == contingency.element_id ? _outaged_generator(g) : g
            for g in case.generators
        ]
        contingency_case = PowerCase(
            case.name, case.baseMVA, case.buses, case.branches, outaged_generators,
            case.loads, case.shunts, case.slack_bus_id, case.bus_id_to_index,
        )
    else
        error("Unsupported N1Contingency.element_type: $(contingency.element_type). Expected \"branch\" or \"generator\".")
    end

    return DCSCOPFContingencyData(contingency, build_dc_opf_data(contingency_case))
end

"""Build the full preventive DC-SCOPF data view: the base case's own
`DCOPFData` plus one `DCSCOPFContingencyData` per N-1 contingency, reusing
the existing enumeration (`generate_branch_n1_contingencies`,
`generate_generator_n1_contingencies` -- `n1_screening.jl`) rather than
building a new contingency list. `include_branch_outages`/
`include_generator_outages` let a caller scope down to one contingency
family at a time (useful while the eventual monolithic model builder is
still under development and being tested incrementally)."""
function build_dc_scopf_data(case::PowerCase; include_branch_outages::Bool=true, include_generator_outages::Bool=true)
    base = build_dc_opf_data(case)

    contingencies = N1Contingency[]
    include_branch_outages && append!(contingencies, generate_branch_n1_contingencies(case))
    include_generator_outages && append!(contingencies, generate_generator_n1_contingencies(case))

    contingency_data = [build_dc_scopf_contingency_data(case, k) for k in contingencies]

    return DCSCOPFData(base, contingency_data)
end

# --- Not yet implemented -- see docs/formulations/dc_scopf.md ---
#
# function build_dc_scopf_model(case::PowerCase; optimizer, silent, fix_reference_angle, ...)
#     Builds the single monolithic JuMP model: base-case `theta`/`Pg`
#     variables (no per-contingency variables, per the preventive
#     formulation), base-case DC power balance + branch flow constraints
#     (reuse add_dc_power_balance!/add_dc_branch_constraints! from
#     opf/dc/constraints.jl directly, unchanged), then one additional
#     branch-flow-limit constraint per (surviving branch, contingency) pair
#     using that contingency's own Bf_k row. The open modeling question
#     flagged in docs/formulations/dc_scopf.md (how a generator outage's lost
#     output is absorbed under a NO-corrective-response preventive model)
#     needs to be resolved before this can be written correctly -- not a
#     mechanical extension like the branch-outage case.
# end
