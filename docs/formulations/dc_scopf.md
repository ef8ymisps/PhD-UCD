# DC SCOPF Formulation (planned — preventive, monolithic, first step)

Date: 2026-07-24

This is the repository-level formulation note for the planned **monolithic
DC security-constrained OPF (DC-SCOPF)** — the first step toward AC SCOPF,
chosen because DC-SCOPF is convex (a single global optimum) and easier to
analyze/debug than an AC security-constrained model. Nothing in this
document is implemented yet beyond the code scaffold described at the
bottom; it exists to fix the target formulation before building it.

## Relationship to what already exists

- `docs/formulations/dc_opf.md` describes the current single-period,
  base-case-only DC-OPF (`julia/own_models/src/opf/dc/`,
  `python/own_models/src/opf/dc/`). DC-SCOPF extends that formulation with a
  contingency set, not a new physics model.
- `docs/formulations/security_constraints.md` describes the current N-1
  handling: a **sequential screening pass** (solve one base-case AC-OPF,
  then for each outage apply a static proportional-reserve redispatch and
  re-solve a plain AC power flow) — explicitly not a co-optimized model
  (see that doc's "No 're-solve as OPF' redispatch policy exists" note).
  DC-SCOPF replaces that sequential pattern, for the DC case, with one
  monolithic optimization that sees every contingency at once.
- Contingency enumeration is already implemented and directly reusable:
  `N1Contingency`, `generate_branch_n1_contingencies`,
  `generate_generator_n1_contingencies`
  (`julia/own_models/src/analysis/n1_screening.jl:20-40`). DC-SCOPF's
  contingency set `K` is exactly the output of these two functions, not a
  new enumeration.
- The linear network matrices already exist: `DCOPFData.Bbus`/`Bf`
  (`julia/own_models/src/opf/dc/data.jl`) map bus angles to nodal
  injections and oriented branch flows respectively, for the base topology.
  DC-SCOPF needs one such matrix pair **per contingency topology** (outaged
  branch removed, or outaged generator's column zeroed) — a mechanical
  extension of the same construction, not new math.

## Variant chosen: preventive DC-SCOPF

Two standard SCOPF variants exist: **preventive** (base-case dispatch must
already be feasible post-contingency, no post-contingency corrective
action) and **corrective** (base-case dispatch may be adjusted by bounded
corrective response after a contingency, e.g. generator ramping within a
time window). This document specifies **preventive** first — it has fewer
decision variables (no per-contingency redispatch variables) and is the
natural first target; corrective DC-SCOPF is a documented extension, not
built here.

## Sets

- `B`: buses; `G`: generators; `L`: active branches; `G_i`: generators at
  bus `i`
- `K`: contingency set, `K = K_branch ∪ K_generator ∪ {0}`, where `0` is the
  base case and each `k` is one `N1Contingency` (single branch or single
  generator outage, matching the existing N-1 screening's k=1 scope — no
  N-2 or higher)

## Decision Variables

- `theta_i`: base-case voltage angle at bus `i` (preventive: no
  post-contingency angle variable — post-contingency flows are a *linear
  function* of the base-case dispatch, evaluated via each contingency's own
  `Bbus_k`/`Bf_k`, not re-optimized)
- `Pg_g`: base-case active output of generator `g` — the **only** dispatch
  variable; preventive SCOPF has no per-contingency `Pg_g^k`

## Objective

Same as base-case DC-OPF — minimize base-case generation cost only (the
contingency constraints below change what dispatch is *feasible*, not what
is being minimized):

```text
min sum_g c2_g Pg_g^2 + c1_g Pg_g + c0_g
```

## Base-Case DC Power Balance And Branch Flow

Identical to `docs/formulations/dc_opf.md`'s existing DC-OPF:

```text
sum_{g in G_i} Pg_g - Pd_i - Gs_i = sum_{l incident to i} Pf_l        (per bus i)
Pf_l = b_l (theta_i - theta_j - shift_l)                              (per branch l = (i,j))
```

## Post-Contingency Flow Constraints (the SCOPF-specific part)

For each contingency `k in K \ {0}` and each **surviving** branch `l` (i.e.
`l` is not the outaged element when `k` is a branch outage):

```text
Pf_l^k = sum_j (Bf_k)_{l,j} theta_j
-rate_l <= Pf_l^k <= rate_l
```

`Bf_k` is the from-bus/to-bus flow matrix built on contingency `k`'s
topology (branch outage: drop that branch's row from `Bf`, and drop it from
the network entirely when rebuilding `Bbus_k` for that contingency;
generator outage: `Bf_k = Bf`, unchanged, but the corresponding generator's
column is excluded from the base-case power balance's generation sum for
feasibility-checking purposes only — the dispatch itself, `Pg_g`, is still
the preventive base-case value, not re-optimized).

The key structural point: `theta` is **not** re-solved per contingency.
Since preventive DC-SCOPF has no post-contingency angle variable, the
post-contingency flow `Pf_l^k` must be expressed as a linear function of
the **same** base-case `theta` that the base-case power balance already
determines — this only works cleanly because `Bf_k` acts as a direct,
non-recursive linear map from `theta` to flows (the defining convenience of
staying in DC). This is the reason a preventive DC-SCOPF is a genuinely
simple monolithic model (one QP/LP, `theta`/`Pg` still the only variables,
just many more linear inequality rows) rather than requiring nested
optimization or a re-solve per contingency.

A generator outage's effect on post-contingency balance is enforced by
requiring the **remaining online generators alone** cover the load under
contingency `k` — i.e. an implicit constraint that `Pg` values for
non-outaged generators, together with the base-case `theta`, still satisfy
`Bbus`-consistent balance once the outaged generator's output is zeroed.
Working out the exact bus-balance-vs-slack-redistribution convention for
generator outages (which bus absorbs the lost generation under a pure
preventive model, since there is no corrective redispatch variable to do it
explicitly) is flagged as the first open modeling question for whoever
implements this — the existing sequential screening's "proportional
reserve" policy (`docs/formulations/security_constraints.md`) does NOT
transfer directly, since that policy is itself a redispatch action, which
preventive SCOPF (by definition) does not have.

## Bounds And Limits

Same as base-case DC-OPF (`Pmin_g <= Pg_g <= Pmax_g`, `theta_ref = 0`,
base-case branch/angle limits) plus the post-contingency branch-flow limits
above, for every `(l, k)` pair where `l` survives contingency `k`.

## Modeling Assumptions

- Single time period, no ramping/corrective response (preventive only, see
  above) — a corrective variant is a documented future extension, not
  scoped here.
- N-1 only (`|k| <= 1` element removed per contingency), matching the
  existing N-1 screening's scope — no N-2 or cascading outages.
- Linear/quadratic generation cost only, consistent with the existing
  DC-OPF.
- Convex: unlike AC-SCOPF, this is a single QP/LP with a unique global
  optimum (given a feasible region) — no local-optimum ambiguity to
  diagnose, which is exactly why this is the chosen first step.

## Validation Implication

Once implemented, the natural validation check is: does the preventive
DC-SCOPF's base-case dispatch, evaluated contingency-by-contingency through
the *existing* sequential N-1 screening pipeline
(`run_n1_branch_screening`/`run_n1_generator_screening`,
`julia/own_models/src/analysis/n1_screening.jl`), actually clear every
branch-flow limit the monolithic model enforced? A dispatch that passes the
monolithic model's own constraints but fails the independent sequential
screening's flow check would indicate a `Bf_k`-construction bug (e.g. wrong
topology per contingency), not a genuine formulation issue — the same
"cross-check by a differently-implemented, independent path" discipline
already used throughout this project's AC-OPF-vs-PowerModels validation
work.
