# Solver Tolerances — Ipopt & HiGHS, Python & Julia & PowerModels

Date: 2026-07-10

Purpose: document the actual numerical tolerances every solver invocation in
this repo uses, verified directly against the installed binaries rather than
assumed from general documentation — and use that to explain the
binding-constraint classification mismatches seen in OPF validation (§2 of
`docs/model_development_and_validation_status.md`).

## Method

Rather than trusting generic Ipopt/HiGHS documentation (option defaults have
changed across versions historically), every value below was read directly
off the installed solver:

- Ipopt: `ipopt.exe --print_options_documentation` (via
  `opt.options["print_options_documentation"] = "yes"` in a real Pyomo
  solve), which dumps every option's live default value.
- HiGHS: `highspy.Highs().getOptionValue(name)` (Python) and
  `JuMP.get_optimizer_attribute(model, name)` (Julia), queried directly
  against a solved model.

## Verified values

Both languages use **identical solver binaries** — confirmed via version
string, not assumed: Ipopt 3.14.19 and HiGHS 1.15.1 in both the Python
(Pyomo) and Julia (JuMP) environments.

| Solver | Option | Verified default | Meaning |
| --- | --- | --- | --- |
| Ipopt 3.14.19 | `tol` | `1e-8` | Overall relative convergence tolerance. |
| Ipopt 3.14.19 | `acceptable_tol` | `1e-6` | Relaxed tolerance Ipopt will accept after `acceptable_iter` consecutive iterations if it can't hit `tol`. |
| Ipopt 3.14.19 | `dual_inf_tol` | `1` | Dual (stationarity) infeasibility threshold. |
| Ipopt 3.14.19 | `constr_viol_tol` | `1e-4` | Constraint/variable-bound violation threshold. |
| Ipopt 3.14.19 | `compl_inf_tol` | `1e-4` | Complementarity-condition threshold. |
| Ipopt 3.14.19 | `max_iter` | `3000` | See "The `max_iter=500` correction" below. |
| HiGHS 1.15.1 | `primal_feasibility_tolerance` | `1e-7` | Primal constraint feasibility. |
| HiGHS 1.15.1 | `dual_feasibility_tolerance` | `1e-7` | Dual feasibility (simplex). |
| HiGHS 1.15.1 | `ipm_optimality_tolerance` | `1e-8` | Interior-point optimality gap. |

**Neither Pyomo's Ipopt/HiGHS interface nor Julia's Ipopt.jl/HiGHS.jl
overrides any of these** — checked directly in both wrapper packages'
source (no matches for any of these option names outside of pass-through
plumbing). Both bindings call into the identical underlying C/C++ solver
library with the solver's own native defaults untouched.

## PowerModels.jl's tolerances

**PowerModels.jl sets no tolerances of its own** — checked directly in
`PowerModels/src/core/base.jl` and `core/solution.jl` (no matches for any
tolerance-related option or `MOI.RawOptimizerAttribute` calls).
`build_opf`/`optimize_model!` purely accept and use whatever optimizer
object (with whatever attributes) the caller constructs. Since the
validation pipeline (`09_run_compact_opf_validation_sweeps.jl`) passes
**the same `ac_optimizer`/`dc_optimizer` object to both the own-model solve
and the PowerModels reference solve** (see `run_one_model`), those two were
always using byte-identical solver configuration even before this
documentation pass — there is no PowerModels-specific tolerance to compare
against the own model's.

## Why this doesn't change any numbers

Since the pinned values (below) equal each language's already-verified
defaults, applying them is a no-op on solve behavior — confirmed via smoke
test: `case5_pjm` AC/DC objective and iteration counts (own and
PowerModels) are bit-identical before and after pinning. The value of
pinning is (a) making the shared assumption explicit and documented rather
than implicit, and (b) protecting against a future Ipopt/HiGHS upgrade
silently changing a default underneath the validation pipeline without
anyone noticing.

## Where tolerances are now pinned explicitly

| Site | Language / solver | What it covers |
| --- | --- | --- |
| `python/own_models/src/opf/ac/solve.py` (`DEFAULT_IPOPT_TOLERANCES`) | Pyomo / Ipopt | Every Python AC-OPF solve (own model) that doesn't explicitly override `solver_options`. |
| `python/own_models/src/opf/dc/solve.py` (`DEFAULT_HIGHS_TOLERANCES`) | Pyomo / HiGHS | Every Python DC-OPF solve (own model). This function had **no options mechanism at all** before this pass — added one, mirroring the AC side. |
| `julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl` (`ac_optimizer`/`dc_optimizer`) | JuMP / Ipopt + HiGHS | The primary 607-row validation sweep — covers **both** the own-model solve and the PowerModels reference solve, since `run_one_model` passes the same optimizer object to both. |
| `julia/tools/powermodels/scripts/07_export_ac_opf_reference.jl` | JuMP / Ipopt (PowerModels) | The AC reference JSON export Python's validation reads. |
| `julia/tools/powermodels/scripts/06_export_dc_opf_reference.jl` | JuMP / HiGHS (PowerModels) | The DC reference JSON export Python's validation reads. |

**Not pinned** (out of scope, per the earlier scoping decision — demo/
inspection scripts, N-1 screening, and superseded validation pathways like
`ac_opf_validation.jl`/`base_opf_cross_model.jl` don't feed the current
primary comparison numbers): `julia/own_models/src/opf/{ac,dc}/model.jl`'s
bare `optimizer=Ipopt.Optimizer`/`HiGHS.Optimizer` kwarg defaults, N-1
screening scripts (Python `08`/`09`/`19`, Julia `10`), and all
`julia/tools/powermodels/scripts/{03,04,05,08}_*.jl` demo/inspection
scripts.

## The `max_iter=500` correction

Prior comments/docs (`09_run_compact_opf_validation_sweeps.jl`,
`ac_opf_sweeps.py`, `docs/implementation_status.md`) claimed *"500 (Ipopt's
own default) is insufficient for large/stressed systems... raised to
3000."* **This is factually wrong for the installed Ipopt 3.14.19** —
verified directly, its real default `max_iter` is already 3000. Whatever
produced the originally-observed 500-iteration `ITERATION_LIMIT` failures
(`case6470_rte`'s base AC-OPF needing 564 iterations,
`case1888_rte`'s `reactive_generation_limits` scenario hitting a similar
wall) predates this session and wasn't Ipopt's native behavior on the
currently-installed version — most likely an explicit (mistaken)
`max_iter=500` setting in an earlier version of the code. The current
explicit `max_iter=>3000` is harmless either way (matches today's real
default) and is now pinned for the same future-proofing reason as the
other tolerances above. Comments correcting this claim were added at all
three sites.

## Why binding-constraint classification mismatches happen

**This is not a solver-tolerance mismatch between tools** — own model and
PowerModels use byte-identical solver tolerances (previous section). It's a
**downstream classification threshold** that's ~100x tighter than the
solver's own acceptance criteria.

`violations.py` (Python) and `opf_solution_inspection.jl` (Julia) both
classify a generator/bus/branch as "at its limit" using a hardcoded
**`1e-6`** absolute-distance threshold (`own_at_pmax = abs(own_pg - pmax) <=
1e-6`, applied identically to own and PowerModels values). But Ipopt only
guarantees convergence to within `constr_viol_tol`/`compl_inf_tol` = **1e-4**
— two orders of magnitude looser. Two independently-converged Ipopt runs
(own model's Ipopt instance and PowerModels' Ipopt instance, solving the
same problem) can each fully satisfy Ipopt's own 1e-4-scale convergence
criteria while landing at very slightly different points relative to a
bound — and if that difference straddles the classification code's much
tighter 1e-6 line, one gets called "binding" and the other doesn't, even
though both solutions are equally valid.

### Worked example (real data, not illustrative)

Bus 4482, `vmax` constraint, `1354_pegase_qg_limits_x0.8` scenario (Julia
AC validation sweep, one of the "31 confirmed binding-constraint
mismatches" in `docs/model_development_and_validation_status.md` §2):

| | Value | Distance to `vmax = 1.1` | Inside 1e-6 classification window? | Inside Ipopt's 1e-4 `constr_viol_tol`? |
| --- | --- | --- | --- | --- |
| Own model | 1.0999989192... | `1.081e-6` | **No** (just outside) | Yes, by ~90x margin |
| PowerModels | 1.0999996877... | `3.123e-7` | **Yes** | Yes, by ~300x margin |

Both solutions are fully, legitimately converged — the own-vs-PM gap in the
*actual* voltage values is only ~7.7e-7 pu, physically negligible. But
because they land on opposite sides of the 1e-6 classification cutoff, this
element gets logged as an `only_pm` binding mismatch. The classifier
already labels this correctly as `"numerical_noise"` (via
`classify_mismatch_reason`'s own `<=1e-6` check on the *own-vs-pm diff*,
not the *distance-to-bound* — same order of magnitude, same mechanism).

**This mechanism explains the small mismatch counts** (`1354_pegase`: 1,
`6470_rte`: 4) **but not the large ones** (`1888_rte`: 31, concentrated in
2 stress scenarios). Those involve own-vs-PM differences of up to 4.3 pu —
six orders of magnitude larger than anything a 1e-6-vs-1e-4 classification
gap could produce. That remains genuine AC-OPF solution non-uniqueness
under reactive stress (see the status doc §2), a completely different
phenomenon that happens to also surface as a "binding mismatch" in the same
summary table.

### Why the large (1888_rte) mismatches happen despite identical tolerances: different starting points

Same solver, same tolerances, but **not the same starting point** —
confirmed directly in both codebases' variable-creation source, not
inferred:

- **Own model** (`julia/own_models/src/opf/ac/variables.jl:16-27`)
  initializes every variable from the PGLib case file's own baseline
  values: `set_start_value(vm[bus.id], bus.vm0)`,
  `set_start_value(pg[gen.id], gen.pg0)`, etc. For `case5_pjm`, the raw
  `.m` file's `mpc.gen` table has genuinely nonzero baseline dispatch
  (`Pg` column: 20, 85, 260, 100, 300 MW) — confirmed by reading the file
  directly.
- **PowerModels.jl** (`PowerModels/src/core/variable.jl`) initializes from
  `comp_start_value(ref(...), "pg_start")`, which is a **different key**
  (`"pg_start"`, not `"pg"`) with signature `get(comp, key, default=0.0)`.
  Standard PGLib parsing never populates `"pg_start"`/`"qg_start"`
  (confirmed — that only happens via the explicit, separately-callable
  `set_ac_pf_start_values!` helper in `PowerModels/src/core/data.jl`,
  which neither `09_run_compact_opf_validation_sweeps.jl` nor
  `07_export_ac_opf_reference.jl` ever calls). So PowerModels **flat-starts
  every scenario**: `Pg = Qg = 0`, `Vm = 1.0`, `Va = 0`, regardless of the
  case file's own baseline dispatch.

For the overwhelming majority of scenarios (604 of 607), this difference
doesn't matter — AC-OPF is well-conditioned enough that both starting
points converge to the same, effectively-unique global optimum (hence the
~1e-8-to-1e-17 median agreement). It only matters in scenarios where the
problem becomes **weakly-determined** — multiple generators can each supply
similar reactive power at similar cost, so the feasible/optimal region has
a near-flat direction. There, which of several near-equally-good local
optima a nonconvex NLP solver converges to genuinely depends on where it
started. `1888_rte`'s `reactive_power_stress`/`load_scaling` scenarios are
exactly this case — real generator redispatch differences up to 4.3 pu,
objective agreeing to 1e-5–1e-4 relative. This is consistent with (and now
gives a concrete mechanism for) the non-uniqueness already documented in
the status doc §2 and `docs/python_ac_opf_validation.md`.

**Not yet done, worth doing if this needs to be nailed down further**:
directly test the hypothesis by re-running just `1888_rte`'s two flagged
scenarios with PowerModels warm-started from the own model's solution
(via `set_ac_pf_start_values!` or manually setting `pg_start`/`qg_start`/
`vm_start`/`va_start` before calling `instantiate_model`) — if that makes
PowerModels converge to the *same* point as the own model, it confirms
starting-point sensitivity as the full explanation rather than a residual
formulation difference.

## An unrelated bug found while verifying this work

While smoke-testing the tolerance pins, `solve_ac_opf_own` started failing
with `MethodError: no method matching getproperty(::ACOPFData, ::Expr)` —
traced to a literal missing field name on
`julia/own_models/src/opf/ac/constraints.jl:29`
(`data.  [bus_id]` instead of `data.ybus_row_terms[bus_id]`). Confirmed via
a minimal repro that this reproduced identically with the *original,
unmodified* optimizer (2 attributes, no tolerance pins) — so it was not
caused by this session's tolerance work. It was already present in the
initial git commit, yet the own-model AC-OPF solve clearly worked many
times earlier in this same session (the entire PowerModels-iteration
backfill, all 15 AC/DC reference re-exports, etc.) — meaning the file
changed on disk mid-session, most likely via an out-of-band edit (e.g. in
an editor open on the source machine) that OneDrive then synced in. Fixed
by restoring the field name, confirmed against the parallel (correct) `qgen`
constraint 13 lines below, which uses the identical loop pattern.
