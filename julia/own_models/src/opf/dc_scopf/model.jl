# Owns assembly of the monolithic preventive DC-SCOPF JuMP model (see
# docs/formulations/dc_scopf.md for the full formulation).
#
# Reuses the base DC-OPF's own variable/constraint/objective builders
# (add_dc_variables!, add_dc_reference_angle!, add_dc_power_balance!,
# add_dc_branch_constraints!, add_dc_generation_cost_objective!) completely
# unchanged for the BASE case -- theta/pg/Pf are the only base-case decision
# variables, matching preventive SCOPF's defining property (no per-
# contingency REDISPATCH variables). Post-contingency flows are instead
# modeled EXACTLY: one extra angle vector theta^k AND one extra flow vector
# Pf^k per contingency k (Pf^k tied to theta^k by its own equality
# constraint, mirroring the base case's Pf/theta relationship and
# PowerModels.jl's DCPPowerModel), theta^k itself tied to the base-case pg
# (plus, for generator outages, the fixed automatic-response weights below)
# via a genuine Bbus_k * theta^k = injections^k equality constraint, using
# build_dc_scopf_contingency_data's
# already-scaffolded per-contingency DCOPFData (opf/dc_scopf/data.jl) --
# NOT PTDF/LODF (network/ptdf.jl). PTDF/LODF are mathematically exact for
# this same linear model (validated in docs/formulations/dc_scopf.md, error
# ~1e-15) and remain available for later work exploring cheaper
# approximations, but per an explicit scoping decision this monolithic
# model does not depend on them for now -- theta^k is a genuine decision
# variable, not eliminated via a closed-form sensitivity.
#
# For generator-outage contingencies, the lost generator's output is
# redistributed onto surviving generators via a fixed (non-optimized),
# CONTINGENCY-INDEPENDENT droop weight `gamma_i` (anchored to a separate
# plain-DC-OPF solve -- automatic_response.jl's generator_response_weights
# -- for why it can't be derived from this model's own still-unsolved pg;
# and see that function's own docstring for why it must NOT be
# renormalized per contingency, unlike the earlier headroom-proportional
# `alpha_i^g` this replaced). The SHARE each generator actually ends up
# absorbing is a saturating water-filling MILP (stage 3 -- the only stage
# now; see dc_scopf.md's "Automatic Generation Response"), not a rigid
# proportional split: every participating generator's response is tied to
# one shared per-contingency auxiliary variable lambda^k via Big-M/
# indicator-style linear constraints and a binary saturation flag z_i^k --
# a generator whose
# proportional share would exceed its own Pmax instead saturates AT Pmax,
# and lambda^k (solved jointly, not iteratively) automatically finds the
# level at which the remaining non-saturated generators' shares exactly
# absorb what's left. See docs/formulations/dc_scopf.md's "Automatic
# Generation Response" section for the full derivation and why this
# belongs inside the monolithic preventive model rather than being a
# separate corrective stage.
#
# Island handling: a branch outage that disconnects part of the network is
# NOT always skipped wholesale anymore. `_island_analysis` finds every
# connected component of the post-outage topology; if any component OTHER
# than the reference bus's own carries real load, the whole contingency is
# still skipped (a lossless DC model has no load-shedding mechanism to
# represent serving -- or deliberately dropping -- stranded load). But if
# every other component is load-FREE (empirically the overwhelming common
# case -- a lone generator's own radial tie tripping, stranding nothing but
# that one generator), the contingency is no longer thrown away: any
# generator in a stranded component is folded into the SAME automatic-
# response machinery as an ordinary generator outage (unioned with k's own
# outaged generator if k is itself a generator contingency), and
# power-balance/branch-flow constraints are still built and enforced over
# the reference-containing component -- so the REST of the network's
# security is no longer left unchecked just because one small appendage
# also tripped along with it.

using JuMP
using HiGHS

"""One contingency this preventive model could not enforce at all -- either
the outage would strand real load in an unreachable island (a lossless DC
model has no load-shedding mechanism to represent that, so the model does
not attempt to; see `_island_analysis`) or a generator outage (including
one produced by trivial islanding, see below) had no other online generator
anywhere to absorb its lost output. Recorded rather than silently dropped,
so a caller can see exactly which contingencies were left unconstrained."""
struct DCSCOPFSkippedContingency
    contingency_id::String
    element_type::String
    element_id::String
    reason::String
end

"""Every connected component of `data`'s topology (via `data.active_branches`,
treated as an undirected graph), as a `Vector{Set{String}}` of bus ids --
plain BFS from an arbitrary unvisited bus, repeated until every bus is
assigned to some component. A fully-connected topology returns a single
component containing every bus in `data.bus_ids`."""
function _connected_components(data::DCOPFData)
    adjacency = Dict(bus_id => String[] for bus_id in data.bus_ids)
    for branch in data.active_branches
        push!(adjacency[branch.from_bus], branch.to_bus)
        push!(adjacency[branch.to_bus], branch.from_bus)
    end

    unvisited = Set{String}(data.bus_ids)
    components = Vector{Set{String}}()
    while !isempty(unvisited)
        start = first(unvisited)
        component = Set{String}([start])
        queue = [start]
        while !isempty(queue)
            bus_id = popfirst!(queue)
            for neighbor in adjacency[bus_id]
                if !(neighbor in component)
                    push!(component, neighbor)
                    push!(queue, neighbor)
                end
            end
        end
        push!(components, component)
        setdiff!(unvisited, component)
    end
    return components
end

"""Result of analyzing one contingency's post-outage topology for
islanding: `main_component` is the LARGEST connected component by bus
count (theta^k/power-balance/branch-flow constraints are only ever built
over this component -- see `build_dc_scopf_model`) -- **not** necessarily
the one containing `case.slack_bus_id`. That distinction matters: a fixed
reference bus is not guaranteed to sit in the electrically central part of
the network -- `case60nordic`'s own reference bus (52) turns out to be a
single-generator appendage reachable only via one transformer (72), so
"the component containing the reference bus" would be that ONE bus, and
everything else -- the entire rest of the 60-bus network -- would get
mislabeled as "the stranded part" purely because of where the reference
bus happens to sit, not because of any real islanding of the bulk network.
Picking the largest component instead avoids that failure mode generally,
not just for this one case. `reference_bus` is `case.slack_bus_id` when it
IS in `main_component`, or an arbitrary (lowest-sorting, for determinism)
bus of `main_component` otherwise -- any bus works as an angle reference
within a connected DC network; there is nothing physically special
required of it beyond breaking the angle nullspace for THIS contingency's
`theta^k`, unlike the base case's `theta`, which keeps `case.slack_bus_id`
always (see `add_dc_reference_angle!`, unrelated to per-contingency
`theta^k`). `stranded_load` is the total `pd_by_bus` demand sitting in
every OTHER component (if positive, the contingency gets skipped entirely
-- a lossless DC model has no way to represent serving, or deliberately
shedding, load trapped in an island with no path to `main_component`).
`stranded_generator_ids` is every online generator located in some OTHER
component (empty when `stranded_load` is positive, since that contingency
is skipped before those generators would ever matter) -- including
`case.slack_bus_id`'s own generator(s) when the reference bus itself ends
up on the smaller side -- treated exactly like an ADDITIONAL generator
outage triggered by this same contingency (folded into the same
automatic-response machinery as k's own outaged generator, if any -- see
`build_dc_scopf_model`), since a generator that lost its only path to the
network is unavailable regardless of why."""
struct IslandAnalysis
    main_component::Set{String}
    reference_bus::String
    stranded_load::Float64
    stranded_generator_ids::Vector{String}
end

function _island_analysis(case::PowerCase, data::DCOPFData)
    components = _connected_components(data)
    main_component = components[argmax(length.(components))]
    other_components = [c for c in components if c !== main_component]
    reference_bus = case.slack_bus_id in main_component ? case.slack_bus_id : minimum(main_component)

    stranded_load = sum(
        data.pd_by_bus[bus_id] for component in other_components for bus_id in component;
        init=0.0,
    )
    stranded_generator_ids = [
        gen_id
        for component in other_components
        for bus_id in component
        for gen_id in data.gens_by_bus[bus_id]
    ]

    return IslandAnalysis(main_component, reference_bus, stranded_load, stranded_generator_ids)
end

"""Build the monolithic preventive DC-SCOPF model without solving it.

`include_branch_outages`/`include_generator_outages` mirror
`build_dc_scopf_data`'s scoping kwargs, useful for testing one contingency
family at a time. `contingencies`, if given, OVERRIDES both of those and is
used verbatim instead of auto-generating from the case -- e.g. a single
`[k]` isolates exactly one contingency's own securability (is the system
N-1-secure against JUST this outage, independent of every other
contingency), the standard way to attribute an infeasible monolithic model
to its actual irreducible cause(s) rather than only knowing the whole
model failed. `exclude_contingency_ids`, if given, removes those ids
(matched against `N1Contingency.id`, e.g. `"outage::gen::5"`) from
whichever contingency list was otherwise going to be used -- for excluding
a contingency already proven, elsewhere (e.g. via
`74_diagnose_scopf_contingency_causes.jl`'s per-contingency isolation and
its `CAPACITY_INADEQUATE`/`NO_RESPONSE_CAPACITY` categories), to be
unfixable by ANY automatic-response policy sophistication, so the rest of
the model's securability can be assessed on its own rather than being
masked by an already-understood, already-documented cause. `anchor_pg`,
if given, is used AS-IS instead of solving the anchor plain-DC-OPF
internally -- lets a caller that already solved it once (e.g. when
building many single-contingency models back to back, as
`74_diagnose_scopf_contingency_causes.jl` does) avoid a redundant re-solve
per call; the automatic-response weights depend only on the CASE, not on
which contingencies are in scope, so the same anchor is valid for any
subset. Generator-outage automatic response always uses the Big-M/
indicator-constraint water-filling MILP (one binary per participating
generator per contingency) documented in "Automatic Generation Response"
(dc_scopf.md) -- the earlier plain-linear-bound shortcut (no binaries) is
GONE, not merely disabled: it relied on participating weights summing to
exactly 1, true only under the discarded per-contingency-renormalized
policy (see `generator_response_weights`' own docstring,
opf/dc_scopf/automatic_response.jl, for why that policy was replaced).
Under the current fixed,
contingency-independent weights, participating weights sum to LESS than 1
whenever the outaged generator itself had headroom, so the old shortcut
would silently under-replace lost generation (a real power-balance bug,
not just an unproven equivalence) -- the MILP's saturation/Big-M
structure (below) is the only formulation that still enforces the exact
total correctly regardless of normalization; unlike the old shortcut, it
doesn't need a SEPARATE conservation constraint to do this -- the
per-contingency power-balance equations already require it, verified by
testing (see `response_expr_by_gen`'s own use below). This is more
expensive at scale (observed directly: a `case118`-scale run
was still active after 30+ minutes and 55,000+ CPU-seconds before being
killed under the old policy's equivalent MILP path) -- a real cost, not
a hypothetical one, but no longer optional. Returns `(model, data, theta, pg, Pf, constraints,
skipped)`. `Pf_k[(k.id, branch.id)]` -- the per-contingency counterpart of
the base case's own `Pf` -- is a genuine decision variable (not a
substituted expression) tied to `theta_k` by its own equality constraint,
bounded by `branch.rate_a` directly (matches PowerModels.jl's DCPPowerModel
-- docs/formulations/dc_scopf.md); it is folded into `constraints` as
`scopf_branch_flow_eq` rather than returned as its own positional value,
since (unlike the base case's `Pf`, indexed over every branch up front) its
index set is only known once the per-contingency loop below decides which
branches survive in `island.main_component` for each `k`. `constraints`
extends the base DC-OPF's own named-constraint NamedTuple (now including
`branch_flow_eq` in place of the old `branch_flow_limits`) with
`scopf_branch_flow_eq` (a `Dict{Tuple{String,String}, ConstraintRef}` keyed
by `(contingency_id, surviving_branch_id)`, the equality tying `Pf_k` to
`theta_k`), `scopf_generator_response_bounds` (same key shape, keyed by
`(contingency_id, surviving_generator_id)` -- the Big-M constraint that
ties a saturated generator's response to its own Pmax; only present for
generator-outage contingencies; no separate conservation-constraint field
-- REMOVED, not merely never added, once confirmed redundant with the
per-contingency power-balance equations, see this function's own body);
`skipped` is a `Vector{DCSCOPFSkippedContingency}`.
"""
function build_dc_scopf_model(
    case::PowerCase;
    optimizer=default_optimizer(),
    silent::Bool=true,
    fix_reference_angle::Bool=true,
    include_branch_outages::Bool=true,
    include_generator_outages::Bool=true,
    contingencies::Union{Nothing, Vector{N1Contingency}}=nothing,
    exclude_contingency_ids::Union{Nothing, AbstractSet{String}}=nothing,
    anchor_pg::Union{Nothing, Dict{String, Float64}}=nothing,
)
    data = build_dc_opf_data(case)
    model = Model(optimizer)
    if silent
        set_silent(model)
    end

    theta, pg, Pf = add_dc_variables!(model, case, data)
    # Clear add_dc_variables!'s base-case primal starts (harmless, even
    # helpful, for plain DC-OPF -- but demonstrated to cause a real
    # numerical failure here). HiGHS treats a provided start as "a useful
    # basis" and skips presolve unconditionally; on the ADDITIONAL
    # per-contingency angle-difference constraints (introduced alongside
    # this fix), that combination has been observed to return
    # `OTHER_ERROR`/`Unknown` on a genuinely infeasible instance
    # (case118_ieee, outage::line::9) instead of a clean `INFEASIBLE` --
    # confirmed by reproducing it, then confirming presolve resolves it
    # cleanly once the provided starts are removed. Only theta/pg carry a
    # start at all (theta_k/response_i are fresh @variable calls with none).
    for v in all_variables(model)
        start_value(v) === nothing || set_start_value(v, nothing)
    end
    reference_angle = add_dc_reference_angle!(model, case, theta; fix_reference_angle=fix_reference_angle)
    power_balance = add_dc_power_balance!(model, case, data, theta, pg)
    branch_flow_eq, branch_angle_limits = add_dc_branch_constraints!(model, data, theta, Pf)
    add_dc_generation_cost_objective!(model, case, pg)

    # Anchor dispatch for the fixed primary-response weights
    # (generator_response_weights, opf/dc_scopf/automatic_response.jl) --
    # a fixed number from the plain DC-OPF's own solve, NOT re-derived
    # from this model's still-unsolved `pg`, since a literal headroom-vs-Pg
    # ratio would be
    # nonlinear. See dc_scopf.md's "Automatic Generation Response" section
    # for why this stays inside the monolithic model regardless.
    if anchor_pg === nothing
        anchor_result = solve_dc_opf_own(case; optimizer=optimizer, silent=silent, fix_reference_angle=fix_reference_angle)
        if string(anchor_result.termination_status) != "OPTIMAL"
            error("Anchor DC-OPF solve for automatic-response headroom weights did not reach OPTIMAL (status: $(anchor_result.termination_status)); cannot compute DC-SCOPF's generator primary-response weights.")
        end
        anchor_pg = anchor_result.pg
    end

    # Computed ONCE, not per contingency -- see generator_response_weights'
    # own docstring for why that matters (a weight recomputed to exclude
    # whichever generator is currently outaged would make every OTHER
    # generator's own coefficient depend on which unit tripped, exactly
    # the corrective-not-preventive objection this replaces). Each
    # contingency below just filters its own outaged id(s) out of this
    # SAME fixed dict.
    all_response_weights = generator_response_weights(case, anchor_pg)

    gen_by_id = Dict(g.id => g for g in case.generators)

    if contingencies === nothing
        contingencies = N1Contingency[]
        include_branch_outages && append!(contingencies, generate_branch_n1_contingencies(case))
        include_generator_outages && append!(contingencies, generate_generator_n1_contingencies(case))
    end
    if exclude_contingency_ids !== nothing
        contingencies = [k for k in contingencies if !(k.id in exclude_contingency_ids)]
    end

    scopf_branch_flow_eq = Dict{Tuple{String,String}, ConstraintRef}()
    scopf_branch_angle_limits = Dict{Tuple{String,String}, ConstraintRef}()
    scopf_generator_response_bounds = Dict{Tuple{String,String}, ConstraintRef}()
    skipped = DCSCOPFSkippedContingency[]
    # Pf_k[(k.id, branch.id)]: contingency k's own post-outage branch flow,
    # a genuine decision variable (not a substituted expression) tied to
    # theta_k by an equality constraint below -- matches the base case's Pf
    # and PowerModels.jl's DCPPowerModel (docs/formulations/dc_scopf.md).
    # Declared as a plain Dict (not a JuMP-indexed container) since the
    # (contingency, branch) index space is only fully known after the
    # per-contingency loop below decides which branches actually survive in
    # island.main_component for each k.
    Pf_k = Dict{Tuple{String,String}, VariableRef}()
    # theta_k[(k.id, bus_id)]: same accumulation reason as Pf_k above --
    # theta_k itself (below) is a FRESH JuMP array variable each loop
    # iteration, out of scope once the loop moves on, so this is the only
    # way to read a contingency's own post-outage bus angles back out
    # after solving (needed for binding-constraint/security-assessment
    # analysis -- see own_models/scripts/83_run_dc_scopf_monolithic_branches.jl).
    theta_k_all = Dict{Tuple{String,String}, VariableRef}()

    for k in contingencies
        contingency_data = build_dc_scopf_contingency_data(case, k)
        data_k = contingency_data.data

        island = _island_analysis(case, data_k)
        if island.stranded_load > 1e-9
            push!(
                skipped,
                DCSCOPFSkippedContingency(
                    k.id, k.element_type, k.element_id,
                    "outage would island part of the network with real load ($(round(island.stranded_load; digits=4)) p.u.) stranded in the disconnected part",
                ),
            )
            continue
        end

        # Every generator THIS contingency loses -- k's own outaged
        # generator (if k is a generator contingency) UNION any generator
        # trivially stranded by islanding (a zero-load island -- positive-
        # load islands were already skipped above; see _island_analysis
        # and the "Island Handling" section of dc_scopf.md). Both are fed
        # into the SAME automatic-response machinery below regardless of
        # which one (or both at once) put a generator on this list -- a
        # generator that lost its only path to the network is unavailable
        # either way.
        outaged_gen_ids = Set{String}()
        if k.element_type == "generator"
            push!(outaged_gen_ids, k.element_id)
        elseif k.element_type != "branch"
            error("Unsupported N1Contingency.element_type: $(k.element_type). Expected \"branch\" or \"generator\".")
        end
        union!(outaged_gen_ids, island.stranded_generator_ids)

        participation_by_gen = Dict{String, Float64}()
        if !isempty(outaged_gen_ids)
            # Filter the SAME precomputed, contingency-independent weights
            # -- not a recomputation -- down to this contingency's own
            # eligible responders (everyone except whoever this contingency
            # itself just lost; a unit that isn't running trivially cannot
            # respond, unlike "knowledge of which OTHER unit tripped," which
            # this policy no longer requires -- see generator_response_
            # weights' own docstring).
            participation_by_gen = Dict(gid => w for (gid, w) in all_response_weights if !(gid in outaged_gen_ids))
            if isempty(participation_by_gen)
                push!(skipped, DCSCOPFSkippedContingency(k.id, k.element_type, k.element_id, "no other online generator anywhere to absorb the lost output"))
                continue
            end
        end

        theta_k = @variable(model, [data_k.bus_ids], base_name = "theta[$(k.id)]")
        for bus_id in data_k.bus_ids
            theta_k_all[(k.id, bus_id)] = theta_k[bus_id]
        end
        if fix_reference_angle
            # island.reference_bus, NOT unconditionally case.slack_bus_id --
            # see IslandAnalysis's docstring for why (the fixed reference
            # bus is not guaranteed to sit in the larger/main component).
            @constraint(model, theta_k[island.reference_bus] == 0.0, base_name = "reference_angle[$(k.id)]")
        end

        # Generator-outage automatic response: the water-filling MILP
        # (see the docstring above and dc_scopf.md's "Automatic Generation
        # Response" for why the earlier plain-linear-bound shortcut was
        # removed, not merely disabled). Every participating generator
        # i's response is tied to ONE shared auxiliary variable lambda^k:
        #   desired_i = pg[i] + gamma_i * lambda^k      (its droop share)
        #   response_i = desired_i           if not saturated (z_i=0)
        #   response_i = gen.pmax             if saturated     (z_i=1)
        # with a Big-M linearization of that if/else, plus a "tightness"
        # pair of constraints so the solver can't pick z_i=1 when
        # generator i actually had headroom left (there is no cost term
        # to do this naturally, since automatic response isn't in the
        # objective). No explicit "response_i >= pg[i]" constraint -- it's
        # implied either way (see the per-generator loop below for the
        # full argument, confirmed by testing, not just derived). This
        # policy only ever asks a surviving generator to produce MORE,
        # Pmin never binds. Solved jointly (not iteratively), lambda^k
        # self-consistently finds the right level even when MULTIPLE
        # generators saturate at once -- no fixed-point iteration needed.
        # Expensive at scale (one binary per participating generator per
        # contingency, loose Big-M) -- a real cost (see the docstring),
        # but no longer optional.
        response_expr_by_gen = Dict{String, Any}()
        if !isempty(participation_by_gen)
            outaged_gens = [gen_by_id[gid] for gid in outaged_gen_ids]
            participating_gens = [gen_by_id[gid] for gid in keys(participation_by_gen)]
            # lambda^k's natural scale used to be bounded by total_lost
            # alone (participating weights summed to EXACTLY 1 under the
            # old per-contingency renormalization, so lambda^k == total_lost
            # exactly whenever nothing saturates). That is NO LONGER true --
            # participating weights now sum to (1 - the outaged generator's
            # OWN global share), which can be noticeably less than 1, so
            # lambda^k can need to go higher than total_lost to compensate.
            # Derived directly instead of reused: in the worst case (all of
            # total_lost funneled through the SINGLE smallest-weight
            # participant alone), lambda^k = total_lost / min(weight) is a
            # safe (if pessimistic) upper bound, since sum(weight) over
            # every participant is >= any one participant's own weight, so
            # total_lost / sum(weight) <= total_lost / min(weight) always.
            min_weight = minimum(w for w in values(participation_by_gen) if w > 0.0)
            lambda_ub = (sum(g.pmax for g in outaged_gens; init=0.0) + 1.0) / min_weight
            big_m = lambda_ub * maximum(values(participation_by_gen)) + maximum(g.pmax for g in participating_gens) + 1.0

            lambda_k = @variable(model, lower_bound = 0.0, upper_bound = lambda_ub, base_name = "lambda[$(k.id)]")

            for (gen_id, weight) in participation_by_gen
                weight <= 0.0 && continue
                gen = gen_by_id[gen_id]
                desired_i = @expression(model, pg[gen_id] + weight * lambda_k)
                response_i = @variable(model, lower_bound = gen.pmin, upper_bound = gen.pmax, base_name = "response[$(k.id),$(gen_id)]")
                z_i = @variable(model, binary = true, base_name = "saturated[$(k.id),$(gen_id)]")

                # No explicit "response_i >= pg[gen_id]" constraint -- it's
                # implied either way (desired_i = pg[gen_id] + weight*lambda_k
                # >= pg[gen_id] since weight, lambda_k >= 0; gen.pmax >=
                # pg[gen_id] since pg[gen_id]'s own variable bound caps it
                # there), confirmed by testing (Python port), not just
                # derived: deactivating an explicit version of this
                # constraint left the objective unchanged to solver
                # precision on a real saturating case.
                # z_i == 0 => response_i == desired_i (both directions, Big-M):
                @constraint(model, response_i <= desired_i + big_m * z_i, base_name = "scopf_generator_link_upper[$(k.id),$(gen_id)]")
                @constraint(model, response_i >= desired_i - big_m * z_i, base_name = "scopf_generator_link_lower[$(k.id),$(gen_id)]")
                # z_i == 1 => response_i == gen.pmax (upper bound already caps it there):
                scopf_generator_response_bounds[(k.id, gen_id)] = @constraint(
                    model, response_i >= gen.pmax - big_m * (1 - z_i),
                    base_name = "scopf_generator_response_bound[$(k.id),$(gen_id)]"
                )
                # Tightness: z_i is only allowed to be 1 when the UNCONSTRAINED
                # share actually would have exceeded Pmax, and only allowed to
                # be 0 when it would NOT have -- rules out a "fake" saturation
                # flag the solver would otherwise be free to pick arbitrarily.
                @constraint(model, desired_i >= gen.pmax - big_m * (1 - z_i), base_name = "scopf_generator_saturation_floor[$(k.id),$(gen_id)]")
                @constraint(model, desired_i <= gen.pmax + big_m * z_i, base_name = "scopf_generator_saturation_ceiling[$(k.id),$(gen_id)]")

                response_expr_by_gen[gen_id] = response_i
            end

            # No explicit "sum(response_i - pg_i) == total_lost" constraint
            # -- REMOVED, not merely never added: tested directly (Python
            # port) by deactivating an explicit version of it on a real
            # saturating case and re-solving -- objective and every single
            # response_i/lambda_k value came out identical to solver
            # precision, AND the sum of response deltas still equalled
            # total_lost on its own. It's implied by the per-bus power-
            # balance equations below: Bbus_k*theta_k = injections_k is a
            # graph-Laplacian system whose rows sum to zero by
            # construction, so a solution for theta_k only EXISTS when
            # total generation change equals total load change across the
            # island -- there is no freedom for conservation to fail,
            # constraint or not.
        end

        gen_response(gen_id) = haskey(response_expr_by_gen, gen_id) ? response_expr_by_gen[gen_id] : pg[gen_id]

        # Power balance and branch-flow limits are only built over
        # `island.main_component` -- the LARGEST post-outage component,
        # which contains `island.reference_bus` by construction (not
        # necessarily `case.slack_bus_id` itself -- see IslandAnalysis).
        # A bus in some OTHER (necessarily zero-load, or this contingency
        # would already have been skipped above) component has no
        # meaningful equation to write here: Bbus's block-diagonal
        # structure means the main component's own balance never actually
        # depends on a disconnected bus's theta^k value regardless, so
        # simply not constraining it is exact, not an approximation.
        for bus in case.buses
            bus.id in island.main_component || continue
            i = case.bus_id_to_index[bus.id]
            gen_sum = sum((gen_response(g) for g in data_k.gens_by_bus[bus.id]); init=0.0)
            @constraint(
                model,
                gen_sum - data_k.pd_by_bus[bus.id] ==
                    sum(data_k.Bbus[i, j] * theta_k[data_k.bus_ids[j]] for j in 1:length(data_k.bus_ids)),
                base_name = "scopf_power_balance[$(k.id),$(bus.id)]"
            )
        end

        for (row, branch) in enumerate(data_k.active_branches)
            branch.from_bus in island.main_component || continue

            pf_kl = @variable(model, base_name = "Pf[$(k.id),$(branch.id)]")
            if branch.rate_a > 0.0
                set_lower_bound(pf_kl, -branch.rate_a)
                set_upper_bound(pf_kl, branch.rate_a)
            end
            Pf_k[(k.id, branch.id)] = pf_kl
            scopf_branch_flow_eq[(k.id, branch.id)] = @constraint(
                model,
                pf_kl == sum(data_k.Bf[row, j] * theta_k[data_k.bus_ids[j]] for j in 1:length(data_k.bus_ids)),
                base_name = "scopf_branch_flow_eq[$(k.id),$(branch.id)]"
            )

            # Post-contingency angle-difference limit -- same filter
            # (_has_angle_limits) as the base case, enforced per
            # contingency too (previously base-case only, a real gap not
            # a deliberate scoping decision).
            if _has_angle_limits(branch)
                scopf_branch_angle_limits[(k.id, branch.id)] = @constraint(
                    model,
                    branch.angmin <= theta_k[branch.from_bus] - theta_k[branch.to_bus] <= branch.angmax,
                    base_name = "scopf_branch_angle[$(k.id),$(branch.id)]"
                )
            end
        end

    end

    # Named/indexed constraint references (A5) -- see docs/notation.md.
    constraints = (
        reference_angle=reference_angle,
        power_balance=power_balance,
        branch_flow_eq=branch_flow_eq,
        branch_angle_limits=branch_angle_limits,
        scopf_branch_flow_eq=scopf_branch_flow_eq,
        scopf_branch_angle_limits=scopf_branch_angle_limits,
        scopf_generator_response_bounds=scopf_generator_response_bounds,
        # Pf_k[(k.id, branch.id)] / theta_k_all[(k.id, bus_id)]: every
        # contingency's own post-outage decision variables, bundled in
        # here (rather than added to this function's own positional
        # return tuple, which every existing caller destructures
        # exactly-by-arity) so binding-constraint/security-assessment
        # code can read them back after solving without a signature
        # change touching every one of this function's ~12 existing
        # callers.
        Pf_k=Pf_k,
        theta_k=theta_k_all,
    )

    return model, data, theta, pg, Pf, constraints, skipped
end

"""Solve the monolithic preventive DC-SCOPF and return the base-case
dispatch/angles/branch-flows (same `DCOPFResult` shape as plain DC-OPF --
the per-contingency theta^k variables are internal to enforcing security,
not part of what's reported), plus the list of skipped contingencies this
preventive model could not enforce at all."""
function solve_dc_scopf_own(case::PowerCase; kwargs...)
    model, data, theta, pg, Pf, _constraints, skipped = build_dc_scopf_model(case; kwargs...)
    optimize!(model)
    return collect_dc_opf_result(model, case, data, theta, pg, Pf), skipped
end

"""Line contingencies that do NOT disconnect the network AT ALL (any split,
trivial or load-bearing) -- for scoping `K` down to exactly what
Capitanescu's own contingency lists describe ("the outage of any single
transmission line that does not create islands"). This is a STRICTER,
simpler exclusion than `build_dc_scopf_model`'s own tiered trivial-vs-
load-bearing handling (`_island_analysis`): here, ANY topology split
removes the contingency from the list entirely, before the model is even
built, rather than being enforced (trivial islands) or skipped-but-
tracked (load-bearing islands) at model-build time. Use this
`contingencies` list with `build_dc_scopf_model`/`solve_dc_scopf_own` to
build a preventive DC-SCOPF whose contingency set matches this simpler,
literature-style scoping."""
function generate_non_islanding_line_n1_contingencies(case::PowerCase)
    lines = generate_line_n1_contingencies(case)
    return [
        k for k in lines
        if length(_connected_components(build_dc_scopf_contingency_data(case, k).data)) == 1
    ]
end

"""Same non-islanding exclusion as `generate_non_islanding_line_n1_contingencies`,
but over EVERY branch (lines + transformers), not just lines --
`K = K^line ∪ K^trans`. A transformer is not excluded here for any DC-
modeling deficiency: PowerModels.jl's own DCPPowerModel (the reference
this project validates own-vs-PowerModels DC-OPF against) treats a
transformer exactly like a line, tap ratio dropped by convention
(confirmed directly from its source, `constraint_ohms_yt_from` in
`form/dcp.jl` -- `tm`/`tr`/`ti` are accepted parameters, never used).
There is therefore no DC-specific reason a transformer outage needs
different treatment from a line outage; only islanding (this function's
own exclusion, identical mechanism for both element types) and the usual
per-contingency securability question apply."""
function generate_non_islanding_branch_n1_contingencies(case::PowerCase)
    branches = generate_branch_n1_contingencies(case)
    return [
        k for k in branches
        if length(_connected_components(build_dc_scopf_contingency_data(case, k).data)) == 1
    ]
end
