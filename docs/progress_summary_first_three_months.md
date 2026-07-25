# Progress Summary — First Three Months

Date: 2026-07-25

This is a technical progress summary drafted for the PhD's first-three-months
review, built from what is already tracked in this repository (primarily
`docs/model_development_and_validation_status.md`,
`docs/implementation_ownership_audit.md`, and this session's own work). It
covers what was built, what was found, and where direction changed — not the
September-December research plan, which is being written separately.

## 1. What was built

**Two independent, from-scratch power-flow/OPF implementations** (Julia/JuMP
and Python/Pyomo, both on Ipopt for AC and HiGHS for DC), covering:

- DC power flow (linear), AC power flow (Newton-Raphson, polar coordinates,
  with one-way generator Q-limit/PV→PQ switching).
- DC OPF (single-period, LP/QP).
- AC OPF (single-period, nonconvex NLP) — originally a bus-injection
  (implicit Ybus-substitution) formulation; as of 2026-07-24, replaced
  entirely by an arc-based (PowerModels-style) formulation in both
  languages, after the arc-based model reached validated objective parity
  with PowerModels.jl. See `docs/formulations/ac_opf.md` for the current
  formulation and §4 below for why the switch happened.

**A full own-vs-reference validation pipeline**, not just spot checks:

- Own AC/DC OPF vs. PowerModels.jl: per-case/scenario objective, per-variable
  (Pg/Qg/Vm/Va/branch flow) diffs, and binding-constraint agreement across
  9 inequality-constraint families, run across 8 default PGLib cases (5_pjm
  through 6470_rte) and a ~313-scenario perturbation grid (branch congestion,
  voltage tightening, generator capacity changes, load scaling, angle limits,
  reactive-power stress, Qg-limit tightening, slack-bus relocation, branch
  and generator outages).
  A **three-way** (PowerModels/Julia/Python) version of this table exists
  for both the generation-cost objective and an active-losses
  (loss-minimization) objective — the latter required building a custom
  PowerModels reference, since PowerModels.jl has no built-in
  loss-minimization `build_opf` variant. Both are now finalized as flat,
  self-explanatory deliverables:
  `results/own_vs_powermodels/submission_tables/{generation_cost,
  active_losses}/{objective}_validation_scenarios.csv`, 311 rows each,
  identical scenario sets between the two objectives, zero unexplained
  gaps, clean per-scenario names (`clean_scenario_name` in
  `python/own_models/scripts/57_build_submission_tables.py`) instead of the
  raw `compact_scenarios` id grammar. Closing the last handful of gaps
  surfaced and fixed two unrelated bugs along the way: a Windows MAX_PATH
  failure in a shared JSON-writer helper, and a ragged-CSV loader silently
  dropping rows whose column count didn't match an older header schema.
- Own AC power flow vs. PyPowSyBl/OpenLoadFlow, as an independent
  cross-tool check separate from the OPF-vs-PowerModels comparison.
- N-1 security screening (single branch/generator outages): own AC-OPF base
  case, then a sequential re-solve per outage (static proportional-reserve
  redispatch for generator outages, full AC power-flow re-solve per
  contingency) — explicitly a screening pass, not a co-optimized SCOPF model
  (see §5).

**A deviation-minimization diagnostic**, purpose-built to distinguish "the
own model has a bug" from "the own model found a different, also-valid local
optimum": substitutes `x = x^{target} + Delta^+ - Delta^-` for chosen
variable groups and minimizes `sum(Delta^+ + Delta^-)` subject to the full
AC-OPF physics, testing whether a suspected local-optimum gap is actually
reachable from the own model's own constraint set. Validated (true
substitution, not the earlier linking-equality-constraint formulation that
had a real bug) across both languages, both warm-start-source axes
(seeded at the reference point vs. at the own model's own prior solution),
and an equality-only mode (physics only, no inequality bounds, to separate
"can the equalities alone reach this point" from "are the bounds also
compatible").

## 2. What was found

- **Own-vs-PowerModels objective mismatches are local-optimum behavior, not
  missing constraints — now backed by a formal, reusable 9-step diagnostic
  ladder plus a 10th step that closes the loop numerically.** Both
  documented in full in `docs/formulations/ac_opf.md`
  ("Diagnostic Ladder"/"Worked Example") and `docs/formulations/comments.md`.
  Applied to all 5 scenarios (across both objectives, both directions of
  the gap) where a nonzero own-vs-PowerModels gap remains, all concentrated
  on `case1888_rte`: the own model's equality constraints are exactly
  satisfied at PowerModels' solution (`g^e(x^{p*})=0`, residual ~1e-15);
  PowerModels' point is fully feasible under the own model's inequality
  formulas; Newton-Raphson load flow converges cleanly from both reference
  points; and — the new closing step — a formal linking-LP reconstruction
  shows the *entire* objective gap is accounted for by the identified
  dispatch/flow deviation, to residuals of 1e-10 to 1e-15, with zero
  unexplained remainder. A methodological correction along the way is worth
  keeping in mind for any future use of this ladder: an early attempt at the
  thermal-ablation step compared the own model with thermal *removed*
  against PowerModels with thermal merely *relaxed* — a structurally
  different NLP on each side — which overstated the ablation's effect by
  more than 10x on one scenario; redone with the identical relaxed bound on
  both sides, thermal congestion turned out to fully explain all three
  generation-cost gaps (including a dramatic 7.22% one, which collapsed to
  exact machine-precision agreement) and partially explain one of the two
  active-losses gaps.
- **A single Julia-only Ipopt convergence failure was root-caused and fixed
  as a targeted exception, not a sweep-wide change.** One scenario
  (`1888_rte_qg_limits_x0.6`, active-losses objective) got stuck for 1000+
  iterations under Ipopt's default `mu_strategy="monotone"`, even with 4x
  the normal time budget, while the identical constraint set converges
  cleanly under generation-cost — `mu_strategy="adaptive"` converges in 280
  iterations to the same value Python's independent implementation already
  found (6 significant figures). This is precisely the class of solver-
  trajectory sensitivity documented in the PowerModels iteration-gap
  investigation below, now with a concrete fix for the one case it actually
  blocked a result.
- **PowerModels.jl needs far fewer Ipopt iterations than either own
  implementation on large cases** (e.g. `case6470_rte`'s base case: ~92
  iterations for PowerModels vs. 300+ for the own models at the time of
  investigation). Investigated at length — problem scaling, dual
  warm-starting, and angle-constraint deduplication were each tested and
  ruled out as the explanation; PowerModels' sparse per-arc internal
  structure is the leading remaining explanation, not resolved to a fix.
  Independently, a genuinely sparse Ybus construction (both languages had
  been building it densely then filtering) was found and fixed — a real,
  language-independent performance win with zero correctness cost, though
  it does not close the PowerModels iteration gap on its own.
- **A branch-flow AC-OPF variant helps Julia's iteration count but hurts
  Python's**, and had a real structural bug (degree-1, generator-less,
  load-only buses not correctly served under one internal implementation) —
  this finding is what originally motivated evaluating the separate,
  independently-built arc-based/PowerModels-style formulation properly
  rather than patching the bus-injection module's own internal variant, and
  ultimately led to that arc-based formulation becoming the sole model (§4).

## 3. Repository/tooling investment

Beyond the physics/optimization work: a resumable, checkpointed sweep runner
(so an interrupted overnight run restarts rather than redoing work), named/
indexed JuMP constraints with duals reachable by symbol (not just container
position) for shadow-price extraction, solver-tolerance pinning verified
against actual installed-binary defaults in both languages (not left to
whatever a future solver upgrade might silently change), and a documented
notation/symbol mapping (`docs/notation.md`) tying the math to the code.

## 4. Direction change: bus-injection deleted, arc-based formulation now sole model

Following the 16/23 July supervisor meetings, the bus-injection AC-OPF
formulation was hard-deleted (not archived) in both languages, and the
arc-based (PowerModels-style) formulation — previously an experimental,
opt-in variant — was promoted to the plain model names as the sole AC-OPF
model going forward. This was verified, not assumed: the full own-vs-
PowerModels validation sweep was re-run after the deletion/rename, and every
scenario that previously agreed with PowerModels still does, to the same
precision; the only nonzero gaps are the two already-characterized
`case1888_rte` local-optimum scenarios from §2, unchanged in magnitude and
identical between languages. `docs/formulations/ac_opf.md` was rewritten to
describe this (now sole) formulation.

## 5. What's next (in-repo scope; the Sept-Dec research plan is separate)

- **DC-SCOPF, preventive/monolithic, as the first step toward AC-SCOPF** —
  chosen because it is convex (a genuine global optimum, no local-optimum
  ambiguity to diagnose) and can reuse existing infrastructure directly: the
  N-1 contingency enumeration already built for the sequential screening
  pass, and the existing DC-OPF `Bbus`/`Bf` matrix construction, applied once
  per contingency instead of once for the base case. Formulation written up
  (`docs/formulations/dc_scopf.md`) and a data-structure scaffold built and
  smoke-tested (`julia/own_models/src/opf/dc_scopf/data.jl`) — the model
  builder/solver itself is not yet implemented; one open modeling question
  (how a generator outage's lost output is absorbed under a strictly
  preventive, no-corrective-response model) needs resolving first.
- The deviation-minimization diagnostic remains available, unchanged, for
  any future own-vs-PowerModels gap that needs explaining — its scope is
  now narrower (only PowerModels-vs-arc-based comparisons are possible,
  since bus-injection no longer exists to compare against).
- Repository cleanup: an earlier pass during the bus-injection deletion
  removed the dead formulation's own code and scripts, but the repo as a
  whole still has ~150+ files never committed to git (spanning many past
  sessions' worth of scripts and notes) — a fuller curation-and-commit pass
  is in progress separately from this summary. Historical
  `results/own_vs_powermodels/julia/` and `.../python/` result folders stay
  as frozen, non-regenerable snapshots (a couple of still-live diagnostic
  scripts read them by path for historical comparison), not deleted.
