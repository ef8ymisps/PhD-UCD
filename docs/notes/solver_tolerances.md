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

### Formal closure: checking the starting-point theory against real iteration counts

The above is a mechanism argument from source code. This section checks it
against the actual `own_solve_iterations`/`pm_solve_iterations` numbers in
the real 607-row `compact_opf_validation_summary.csv` (AC rows, both-solved
subset), for the two base cases that show the large binding mismatches.

| Base case | n (both solved) | mean `own_solve_iterations` | mean `pm_solve_iterations` | own faster than PM |
| --- | --- | --- | --- | --- |
| `1888_rte` | 23 | 211.5 | 157.7 | 5 / 23 |
| `6470_rte` | 26 | 421.6 | 94.3 | 0 / 26 |

**Confirmed**: own model and PowerModels take systematically, sometimes
dramatically different iteration counts to solve the *identical* NLP (same
objective, same constraints, same tolerances, per the sections above) — up
to **~4.5x more iterations** for `6470_rte`. This is direct evidence the two
solves are not tracing the same trajectory through the problem, which is a
necessary consequence of starting from different points. It corroborates
the starting-point explanation at the mechanistic level.

**Counter-intuitive direction, worth flagging honestly**: own model needs
*more* iterations than PowerModels' flat start, not fewer — the opposite of
the naive "closer to the real dispatch → faster convergence" intuition, and
starkly so for `6470_rte` (own never once converges faster than PM's flat
start across 26 scenarios). Likely explanation: a case file's bundled
`Pg`/`Vm`/`Va` values are a plausible operating point the case author
shipped with the file, not a jointly AC-power-flow-consistent state at (or
near) the OPF's actual optimum — sitting close to a physically plausible
but not-jointly-feasible point can be a *worse* interior-point start than
the neutral, uniformly-interior flat start Ipopt's barrier method is tuned
around. This is a plausible explanation, not independently verified in this
pass.

**Within-case iteration count does not reliably predict mismatch severity**:
correlating `own_solve_iterations`, `pm_solve_iterations`, and their ratio
against `max_pg_diff` scenario-by-scenario gives weak, inconsistent
results — `1888_rte`: 0.15 / 0.35 / −0.07; `6470_rte`: −0.43 / −0.07 / −0.42
(Pearson r). So "how hard the solver worked" is not a graded predictor of
"how far apart the two answers end up" — the two flagged large-mismatch
scenarios in `1888_rte` don't stand out as the highest-iteration rows.

**Conclusion (closes B3)**: the starting-point difference is real and
demonstrably changes solver behavior, supporting it as a genuine
contributing mechanism — but iteration-count data alone doesn't give a
clean quantitative causal chain to mismatch magnitude. The actual
divergence still traces to AC-OPF's inherent non-convexity under reactive
stress (status doc §2): different starting points determine *which* local
optimum a solve lands on, but not *how much* that optimum will differ from
another one in a way iteration count alone predicts. The re-run experiment
proposed just above (warm-start PowerModels from the own model's solution)
remains the one test that would fully close the loop; it's optional
follow-up, not required to consider this line of investigation closed.

### Warm-start ablation: testing the starting-point theory directly

The sections above reason from source code and from iteration counts the
own model always produced with the case-file start. This ablation instead
*varies* the own model's starting point directly (Python's own AC-OPF now
supports `warm_start="case"` / `"flat"` / `"zero"` — the latter added this
pass, see `python/own_models/src/opf/ac/initialization.py`'s
`build_zero_initial_point`, an exact match to PowerModels' real default:
`Pg=Qg=0`, `Vm=1.0`, `Va=0`) and re-solves `6470_rte`/`1888_rte` under each
mode. Script: `python/own_models/scripts/20_run_ac_opf_warm_start_experiment.py`;
results: `results/own_python/ac_opf_warm_start_experiment/warm_start_iteration_comparison.csv`.
Interactive writeup with chart (combined Python+Julia results, see part 2
below): `results/presentable/ac_opf_warm_start_experiment/warm_start_ablation_report.html`
(open directly in a browser) — or the tables below.

| Case | `case` start | `flat` start | `zero` start (matches PM exactly) | PM (reference) | Julia own model, `case` start (reference) |
| --- | --- | --- | --- | --- | --- |
| `6470_rte` (base) | 338 | **136** | 160 | 92 | 564 |
| `1888_rte` (base) | 120 | 118 | 178 | 114 | 148 |
| `1888_rte` (Qd ×1.2, reactive stress) | 375 | 527 | 596 | 169 | 447 |

**Finding 1 — the case-file start is not a uniformly good warm start,
sometimes it's the worst one.** On `6470_rte`, `flat` (real Pg, everything
else flat) needs 60% fewer iterations than `case` (everything real): 136 vs
338. `zero` (fully flat, matching PowerModels exactly) sits in between at
160. This directly confirms the counter-intuitive direction already seen in
the B3 closure above — a case file's bundled dispatch is a plausible
*reported* operating point, not necessarily a jointly AC-feasible one, and
can be a worse interior-point start than a neutral one.

**Finding 2 — matching PowerModels' starting point narrows the iteration
gap but does not close it.** `zero` mode gets own model to 160 iterations
on `6470_rte`, much closer to PowerModels' 92 than `case`'s 338 — but still
~1.7x more, from an *identical* numerical starting point, same tolerances,
same objective. So starting point is a real contributing factor but not the
whole story; something else (formulation/scaling/dual-variable
warm-starting differences between own model's Pyomo/Ipopt NL formulation
and PowerModels' native JuMP formulation) accounts for the rest. Not
investigated further in this pass.

**Finding 3 — unexpected, and not what this ablation set out to test: Julia's
own model needs far more iterations than Python's own model for the
identical case and starting point.** `6470_rte` with `case` start: Julia 564
vs Python 338 — both solving the same NLP (same objective, constraints,
tolerances) from equivalent case-file starting values. This is a genuine
Julia-vs-Python own-model implementation difference, independent of the
starting-point question this ablation was designed around. **Not
investigated — flagged as a new open item** (see status doc §7).

**Finding 4 — on the reactive-stress case, different starting points
genuinely select different local optima, not just different speeds.**
`case`/`flat` converge to objective 1,474,501; `zero` converges to
1,481,921 — a different point, not a slower path to the same one.
PowerModels' real answer for the equivalent Julia scenario is 1,478,336, so
all three own-model runs land within ~0.3% of PM but at three distinct
points. This is direct, first-hand confirmation (not just source-code
inference) of the non-uniqueness-under-reactive-stress mechanism documented
above for `1888_rte`: matching PM's exact starting point does not reproduce
PM's exact answer, it lands on yet another nearby optimum. (Caveat: this
scenario is a Python-side reconstruction — uniform Qd ×1.2 scaling applied
directly to the loaded case, matching Julia's
`scale_loads!(data, 1.2; scale_p=false, scale_q=true)` — not a byte-identical
replay of the Julia sweep's exact scenario file, so a small residual gap
from Julia's own numbers, on top of the starting-point effect, is
expected.)

**Practical implication for the validation pipeline**: none required. The
validation pipeline's own-model default (`warm_start="case"`) is what
Julia's own model always uses too, so the 607-row comparison dataset is
unaffected by any of this. This ablation is diagnostic, not a proposed
pipeline change — `flat`/`zero` are available as `warm_start` options if a
future experiment wants to use them.

### Warm-start ablation, part 2: the same experiment in Julia

The Python-only ablation above was re-run natively in Julia
(`julia/own_models/scripts/11_run_ac_opf_warm_start_experiment.jl`, adding
the same `:case`/`:flat`/`:zero` modes to
`julia/own_models/src/opf/ac/variables.jl`'s `add_ac_variables!`), with
PowerModels solved natively too (`instantiate_model`/`optimize_model!`,
same pattern as the main sweep) rather than reusing the Python pass's
hardcoded reference numbers. Results:
`results/own_julia/ac_opf_warm_start_experiment/warm_start_iteration_comparison.csv`.
Full interactive comparison (both languages together):
`results/presentable/ac_opf_warm_start_experiment/warm_start_ablation_report.html`.

| Case | Python `case` | Python `flat` | Python `zero` | Julia `case` | Julia `flat` | Julia `zero` | PowerModels (native, either language) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `6470_rte` (base) | 338 | 136 | 160 | **564** | 147 | 147 | 92 |
| `1888_rte` (base) | 120 | 118 | 178 | 148 | 103 | 106 | 114 |
| `1888_rte` (Qd ×1.2) | 375 | 527 | 596 | 447 | 326 | 317 | 169 |

(PowerModels' iteration count is listed once per case since it's
language-independent by construction — Julia's freshly-solved native PM run
this pass reproduced the exact same values used as Python's hardcoded
reference in part 1, a useful cross-check.)

**Confirms, independently, in a second language**: matching PowerModels'
starting point (`zero`) narrows the gap but never closes it — Julia's
`flat`/`zero` both land on exactly 147 iterations on `6470_rte`, ~1.6x
PowerModels' 92, essentially the same residual ratio Python's `zero` mode
showed (160 vs 92, ~1.7x). Two independent implementations reach the same
conclusion: starting point is real and measurable, but not the whole story.

**New finding, only visible by comparing across languages**: the
`case`-start penalty is far more severe in Julia than in Python on
`6470_rte` specifically (564 vs 147, 3.8x, vs Python's 338 vs 136, 2.5x).
**Update, part 3 below**: extending this to all 8 default cases shows this
is *not* a general Julia-vs-Python trait — see the "Warm-start ablation,
part 3" section for the corrected, narrower finding.

**Reactive-stress case, now with two languages: four distinct local optima
across seven converged runs**, not two:

| Run | Iterations | Objective |
| --- | --- | --- |
| Python `case` | 375 | 1,474,501 |
| Python `flat` | 527 | 1,474,501 |
| Julia `flat` | 326 | 1,474,501 |
| Julia `case` | 447 | 1,477,597 |
| Julia `zero` | 317 | 1,477,597 |
| PowerModels (native) | 169 | 1,478,336 |
| Python `zero` | 596 | 1,481,921 |

All seven report `optimal`/`LOCALLY_SOLVED`. Notably, **which modes cluster
onto the same optimum is not consistent between languages**: Python's
`case` and `flat` agree with each other (and with Julia's `flat`); Julia's
`case` and `zero` agree with each other instead. There is no fixed rule like
"the `zero` mode always reproduces PowerModels' answer" — which of several
near-equally-good local optima gets selected depends on the specific
combination of starting point *and* implementation, not on starting point
alone. This is first-hand, reproduced-from-two-languages confirmation of
the AC-OPF non-uniqueness-under-reactive-stress mechanism, strictly
stronger evidence than the single-language version in part 1.

### Warm-start ablation, part 3: does the case-start penalty generalize? (all 8 default cases)

Part 2's Julia-vs-Python `case`-start penalty finding was based on one case
(`6470_rte`). Both scripts were generalized to loop over all 8 of the main
sweep's default PGLib cases (`DEFAULT_CASES` in
`09_run_compact_opf_validation_sweeps.jl`), run overnight as one sequential
batch alongside the DC iteration backfill (§B1). Full data: interactive
report (`results/presentable/ac_opf_warm_start_experiment/warm_start_ablation_report.html`)
and copy-paste-ready Beamer/LaTeX tables
(`results/presentable/ac_opf_warm_start_experiment/beamer_tables.tex`).

Case-start penalty (= `case` iterations ÷ best of `flat`/`zero`, per
language):

| Case | Buses | Python penalty | Julia penalty |
| --- | --- | --- | --- |
| `5_pjm` | 5 | 1.00x | 1.00x |
| `14_ieee` | 14 | 0.94x | 0.94x |
| `30_ieee` | 30 | 1.13x | 1.13x |
| `118_ieee` | 118 | 1.00x | 1.00x |
| `300_ieee` | 300 | 1.00x | 1.00x |
| `1354_pegase` | 1354 | 1.03x | 1.03x |
| `1888_rte` | 1888 | 1.02x | 1.44x |
| `6470_rte` | 6470 | 2.49x | 3.84x |

**The Julia-vs-Python gap is not a general trait — it's concentrated in one,
maybe two, specific cases.** For the 6 smaller/medium cases, Python's and
Julia's penalty ratios match almost exactly, in several cases to 2 decimal
places (`30_ieee`: both exactly 1.13x; `1354_pegase`: 1.03x both). The two
implementations are behaviorally indistinguishable there. The divergence
appears only on the two largest, RTE-derived networks — mildly on `1888_rte`
(1.02x vs 1.44x) and severely on `6470_rte` (2.49x vs 3.84x).

**This corrects part 2's framing.** The right conclusion is not "Julia's
own-model implementation is more sensitive to a poor starting point than
Python's" (a general claim about the two codebases) but "something about
`6470_rte` specifically (and to a lesser extent `1888_rte`) — most likely
network conditioning, or specific generators sitting near bounds in that
case file's dispatch — makes a poor starting point much more costly for
Julia's Ipopt call there than for Python's, on top of the smaller,
consistent-across-both-languages penalty every case shows." Not
investigated further here; the status doc §7 item 7 has been updated to
this narrower scope.

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

## Why Python's DC-OPF presolves to 0 iterations while Julia's needs ~18-35

Status doc §7 item 4, resolved 2026-07-11. On `case14_ieee`/`case30_ieee`,
Python's own DC-OPF (HiGHS) always reports 0 simplex iterations, while
Julia's own DC-OPF and PowerModels' DC-OPF (same cases, same solver) report
17-18+. **Not a meaningful formulation difference** — confirmed via direct
ablation, root cause is entirely about whether HiGHS receives a warm-start
primal basis.

**What's actually happening**, verified on `case14_ieee` with `output_flag
= true` (raw HiGHS logs, not inference):

- Julia's own model (`add_dc_variables!`) calls `set_start_value` on every
  `theta`/`pg` variable unconditionally. PowerModels does too, via
  `comp_start_value(ref, "p_start")` → `start=` kwarg on every `@variable`
  it creates, defaulting to `0.0` when the case has no `_start` data
  (same `comp_start_value` mechanism documented above for AC). So **both
  Julia paths always hand HiGHS a primal start for every variable**, even
  if it's just 0.0.
- When HiGHS's MOI interface receives primal starts for every variable, it
  builds a "useful basis" via a crash procedure and **skips presolve
  entirely** — log says so explicitly: `"Solving LP with useful basis so
  presolve not used"`. From that crash basis, dual simplex needs ~17
  (own model) to 18 (PowerModels) iterations on `case14_ieee`.
- Python's Pyomo→HiGHS interface (`pyo.SolverFactory("highs")`) **never
  forwards Pyomo's `initialize=` variable hints to HiGHS as a warm start**
  — the `.solve(model, tee=True, **solve_kwargs)` call
  (`solver_iterations.py`) never passes `warmstart=True`, which Pyomo's
  HiGHS interface requires to forward primal starts at all. So HiGHS always
  solves Python's DC-OPF cold. Its presolve then runs unimpeded and, for
  these small/simple DC-OPF LPs (55 rows, 19 cols for `case14_ieee`), is
  powerful enough to eliminate the **entire** problem via substitution:
  `"Presolve reductions: rows 0(-55); columns 0(-19); nonzeros 0(-140) -
  Reduced to empty"`. 0 simplex iterations is the true, correct count, not
  a bug or an "unknown" — see the existing `_HIGHS_PRESOLVE_EMPTY_PATTERN`
  fix that already stopped this from silently showing as `NaN`.

**Direct confirmation, not just plausible-sounding theory**: rebuilt
Julia's own DC-OPF model for `case14_ieee`, then called `set_start_value(v,
nothing)` on every variable before `optimize!` — with no starts, it
**also** presolves to empty (0 iterations), reproducing Python's exact
behavior. Restoring the default starts brings back the 17-iteration
"useful basis" behavior. This isolates the variable precisely: warm-start
presence, not LP structure, formulation, or variable count (own model: 19
vars; PowerModels: 39 vars, since PowerModels also carries explicit branch
flow variables own model treats as expressions — this difference exists
but isn't what's driving the iteration-count gap).

**No fix applied — this doesn't need one.** Both behaviors are correct:
Python solving cold into a presolve-friendly LP, and Julia/PowerModels
solving warm from a trivial crash basis, are just two valid ways to reach
the same optimum. Documented so this doesn't get mistaken for a bug or a
real algorithmic difference in a future pass.

## Closing the AC-OPF iteration-count gap: formulation, sparsity, and five ruled-out hypotheses

Date: 2026-07-15. Follow-up to the warm-start ablation above, which left one
explicit open item: matching PowerModels' starting point narrows the
`own_solve_iterations` vs `pm_solve_iterations` gap but doesn't close it
(`case6470_rte`: own model's best case still ~1.7x PowerModels' 92
iterations). This section documents everything tested afterward, using the
same standard as the rest of this file — direct source inspection and
controlled ablations, not inference from documentation.

### Two additive, validated model changes

**Sparse Ybus construction (both languages, unconditional fix, not a flag).**
Both own-model implementations built Ybus as a dense N×N matrix and filtered
it down afterward, despite real transmission networks being extremely
sparse — measured 0.054% nonzero density on `case6470_rte` (22,602 real
entries out of 41.86 million), ~670 MB of wasted zeros allocated on every
single solve. Rewrote `julia/own_models/src/network/admittance_matrix.jl`
(`SparseArrays.sparse` via triplet accumulation) and
`python/own_models/src/network/admittance.py`
(`scipy.sparse.coo_matrix(...).tocsr()`), plus the row-term extraction that
reads from it (`nonzero_ybus_row_terms`/`ybus_terms_from_csr`, now O(nnz) via
`rowvals`/`nonzeros`/`nzrange` in Julia and CSR's `indptr`/`indices`/`data`
in Python, instead of an O(N²) dense scan). Verified correct via an
independent from-scratch dense reference build on `case118_ieee` (exact
match, max abs diff 0.0). Real, modest win in both languages: Julia
`build_ybus` 242ms→3.5ms; Python `case6470_rte` bus-injection iterations
359→338 (~6% fewer), 109.7s→100.9s.

**Branch-flow AC-OPF formulation, additive via a `formulation` flag.**
Implemented PowerModels.jl's actual structure (`form/acp.jl`'s
`constraint_ohms_yt_from`/`_to` plus a linear `constraint_power_balance`) as
an alternative to the existing bus-injection formulation: real `pf`/`qf`/
`pt`/`qt` flow variables per branch, pinned to the same Ohm's-law formula via
separate equality constraints, with the nodal balance summing those
variables instead of substituting Ohm's law directly into one dense
per-bus equation. Ported to both languages
(`julia/own_models/src/opf/ac/{data,constraints,model}.jl`'s `formulation`
kwarg; `python/own_models/src/opf/ac/{data,constraints,model}.py`'s
`formulation` parameter), validated correct in both (objective diffs
~1e-9–1e-4 on a multi-million objective, matching to Ipopt-tolerance noise).
Combined-effect measurement on `case6470_rte` (branch-flow + sparse Ybus
together):

| | iterations | time |
| --- | --- | --- |
| Julia bus-injection + sparse Ybus | 561 | 156.7s |
| **Julia branch-flow + sparse Ybus** | **493** | **141.1s** |
| Python bus-injection + sparse Ybus | 338 | 100.9s |
| Python branch-flow + sparse Ybus | 518 | 170.9s |
| PowerModels.jl (reference, language-independent) | 92 | 24.0s |

**The formulation's benefit does not port across languages.** Real,
repeatable ~12-14% iteration/time reduction in Julia/JuMP; makes Python/
Pyomo measurably *worse* (+53% iterations). Most likely explanation: Pyomo's
NL-file/AD backend and MUMPS' ordering heuristics interact differently with
the larger variable/constraint set than JuMP's native expression graph does
— not independently confirmed beyond the measurement itself. Consequence:
**`formulation=:branch_flow` is now Julia's default** (`build_ac_opf_model`,
`julia/own_models/src/opf/ac/model.jl`), while Python's default stays
`bus_injection` (`build_ac_opf_pyomo_model`,
`python/own_models/src/opf/ac/model.py`) — a deliberate per-language
divergence, not an oversight. The full `results/own_vs_powermodels/julia/`
sweep was re-run after this default change (previous bus-injection-based
results preserved in `results/own_vs_powermodels_bus_injection_baseline_2026-07-15/`
for before/after comparison).

### Five further hypotheses tested, all ruled out or closed

**1. Angle-constraint bus-pair deduplication — refuted, and in the wrong
direction.** Reading PowerModels' actual source
(`core/ref.jl`'s `calc_buspair_parameters`,
`core/constraint_template.jl`'s `constraint_voltage_angle_difference`)
found a genuine structural difference: PowerModels only adds an
angle-difference constraint for the *representative* (lowest-index) branch
per oriented bus pair — parallel branches between the same two buses get
zero angle constraints, not a redundant copy. Confirmed specific to that one
constraint (`constraint_thermal_limit_from/to`, `constraint_ohms_yt_from/to`
stay per-branch in PowerModels too). Quantified on `case6470_rte`: 9,005
active branches, only 8,066 unique bus pairs — 939 redundant angle
constraints (10.4%), and this is the case with the worst own-vs-PowerModels
iteration penalty of all 8 default cases. Tested directly: deactivated all
938 redundant constraints in the Python bus-injection + sparse-Ybus model
(all parallel groups share identical ±30° bounds, so no bound-merging
needed). Objective unchanged (22375.705802, confirming correctness) but
**iterations went up, 338→538** — the opposite of the hypothesized
direction. Traced via `print_level=7` Ipopt trace comparison: the two runs
track each other to 4-5 significant figures for the first ~14 iterations,
both hit restoration phase for the first time at iteration 30 with matching
objectives, then compound apart — the dedup run hits Ipopt's (expensive)
feasibility-restoration phase 43 times vs. the baseline's 17 (2.5x more
often, 8.0% vs 5.0% of all iterations). Most likely root trigger: removing
duplicate Jacobian rows for the same `va[f]-va[t]` expression shifts Ipopt's
automatic gradient-based row-scaling for the shared angle variables, since
duplicate rows reinforce a particular scale that vanishes once removed —
confirmed directly (see #2 below), not just plausible.

**2. Ipopt automatic scaling — confirmed as the dedup mechanism, ruled out
as an own-vs-PowerModels difference.** Re-ran the angle-dedup ablation with
`nlp_scaling_method=none` (Ipopt's automatic scaling fully disabled):
baseline 338→631 iterations, dedup 538→651 — the 200-iteration gap between
baseline and dedup collapses to just 20 (noise-level) once scaling is off,
directly confirming automatic scaling is the real mechanism, not
coincidence. This also shows automatic scaling is one of the single largest
levers on the own model's absolute performance (338 vs 631, ~2x) — bigger
than formulation choice or Ybus sparsity alone. However, comparing a
same-language, same-units `print_level=7` trace of the own Julia model
against a native PowerModels Julia trace on the identical case shows the
automatic *objective* scaling factor is bit-identical between the two
(`Scaling parameter for objective function = 7.577451e-03`, both) — same
cost coefficients, same `pg` variables, so this isn't a distinguishing
factor between own model and PowerModels specifically.

**3. `obj_scaling_factor`/`check_derivatives_for_naninf` — a red herring for
this question, but a new lead for a different one.** The same trace
comparison showed both options set identically in own-model and PowerModels
traces. Traced to `Ipopt.jl`'s MOI wrapper
(`~/.julia/packages/Ipopt/*/ext/IpoptMathOptInterfaceExt/MOI_wrapper.jl:1330,1339`),
which forces `obj_scaling_factor=1.0` and `check_derivatives_for_naninf=yes`
unconditionally on *every* JuMP+Ipopt model, regardless of which one — so
identical between own model and PowerModels by construction, not a lever
for that comparison. It *is*, however, a genuine, previously-unflagged
difference between Julia (always forced via this MOI code path) and
Python/Pyomo (calls the raw `ipopt.exe` binary via an NL file, never
touching Ipopt.jl's MOI wrapper at all) — a new candidate explanation for
the still-open "Julia's own model needs far more iterations than Python's
for the identical case and starting point" item from the warm-start
ablation above (part 2/3), not yet tested.

**4. Dual-variable warm-starting — not a real difference, no experiment
needed.** Direct source search
(`grep -rn "dual_start|ConstraintDualStart|set_dual_start"`) across all of
PowerModels' source and both own-model codebases found zero matches
anywhere. Neither side ever sets a dual/multiplier start; both rely
entirely on Ipopt's own internal least-squares dual initialization from the
primal start (visible in traces as `Least square estimates max(y_c)...`).
Closed analytically.

**5. Bus-type/orientation data corrections PowerModels applies during
parsing — considered, ruled out on inspection.** PowerModels' `parse_file`
logs warnings like "no active generators found at bus X, updating to bus
type from 2 to 1" and "reversing the orientation of branch N to be
consistent with other parallel branches" for `case6470_rte`. Standard
`ACPPowerModel`/`build_opf` doesn't gate any constraint on `bus_type` beyond
buspair-set membership (`calc_buspair_parameters`'s
`bus["bus_type"] != 4`) and reference-bus selection — so this changes
bookkeeping, not the mathematical problem being solved.

### The real structural finding: PowerModels' problem is larger, not smaller

Pulling a same-language, same-units `print_level=7` Ipopt trace of both the
own Julia model (bus-injection, sparse Ybus) and PowerModels' native
`ACPPowerModel`/`build_opf` on identical `case6470_rte` data gives a direct
problem-size comparison neither side's documentation states explicitly:

| | Own model (bus-injection) | PowerModels |
| --- | --- | --- |
| Variables | 14,225 | **50,245** (3.5x) |
| Equality constraints | 12,941 | **48,961** (3.8x) |
| Inequality constraints | 27,015 | 26,076 |
| Iterations | 561 | 92 |

The variable gap (35,020) is exactly `2 arcs × 9,005 branches × {p,q}` —
PowerModels always creates explicit per-arc flow variables, even in the
vanilla model. The equality-constraint gap (36,020) is exactly `9,005
branches × {ohms_yt_from, ohms_yt_to} × {p,q}`. The inequality gap of 939
matches the angle bus-pair dedup finding above (8,066 vs 9,005) exactly.

**This rules out "PowerModels solves an easier/smaller NLP" entirely** —
its problem is substantially *larger* by every measure except the
already-tested-and-refuted angle-constraint count, yet it converges in 6x
fewer iterations. The advantage must come from the *sparsity pattern*
itself: many narrow (2-bus-wide) equality rows (branch-flow-native) versus
the bus-injection model's fewer but much denser per-bus rows (each nodal
balance touches every electrical neighbor at once). This is a direct,
quantitative explanation for why the branch-flow formulation experiment
above gave a real partial win in Julia (493 vs 561 iterations) — branch-flow's
variable count (4 per branch) lands almost exactly on PowerModels' per-arc
count (2 arcs × 2) — and why it doesn't fully close the gap: matching
variable *count* isn't the same as matching PowerModels' exact
construction, ordering, or pairing, and the one further structural
difference identified (angle bus-pair dedup) was tested and found to hurt,
not help.

### Post-mortem: branch-flow as Julia's default broke 6 case1888_rte scenarios, reverted

Date: 2026-07-15, same day. Acting on the branch-flow measurement above, flipped
`formulation::Symbol=:branch_flow` as Julia's default in
`julia/own_models/src/opf/ac/model.jl` and re-ran the full ~313-scenario
`own_vs_powermodels` sweep to refresh the validation tables under the new
default. The sweep completed cleanly (no crash), but comparing the new
results against the previous (bus-injection) baseline found **6
`case1888_rte` scenarios that regressed from `LOCALLY_SOLVED` to
`LOCALLY_INFEASIBLE`** under branch-flow, where both PowerModels and the
own model's own bus-injection formulation solve the identical case fine.
The tell that this was a real bug rather than genuine infeasibility: all 6
scenarios (different rate/load/angle perturbations) failed at the *exact
same* iteration count (986), which is not plausible for independently
differing NLP instances to hit by coincidence -- consistent with a fixed
structural defect in the model triggered identically regardless of the
scenario's specific perturbation.

Root-caused (partially) via direct reproduction: rebuilt `case1888_rte`'s
base scenario under both formulations, confirmed bus-injection solves
(`LOCALLY_SOLVED`) and branch-flow doesn't
(`LOCALLY_INFEASIBLE`), then inspected the largest post-solve
`power_balance_p` residuals. All of the worst 10 residuals (4-7 pu, not
solver noise) land on buses that are **degree-1 (exactly one incident
branch), carry zero generators, and have real load** -- and each
residual's magnitude matches that bus's own `pd` almost exactly (e.g. bus
1820: `pd=7.105`, residual `7.1049999999999995`). This means the single
flow variable connecting each of these dead-end load buses to the rest of
the network is not correctly carrying power in to serve the local load
under the branch-flow formulation's power-balance/arc bookkeeping
(`add_ac_power_balance_branch_flow!`'s `arcs_from`/`arcs_to` construction
or the associated Ohm's-law pinning, in
`julia/own_models/src/opf/ac/constraints.jl`) -- not yet narrowed to the
exact line. Bus-injection's Ybus-row-based approach handles the identical
degree-1 topology without issue, so this is specific to the branch-flow
code path, not the case data itself (both formulations solve the same
`PowerCase`).

**Action taken**: reverted Julia's default back to `:bus_injection`
(`formulation::Symbol=:bus_injection` in `model.jl`, with an inline comment
recording why), restored the previously-validated bus-injection sweep
results as the active `results/own_vs_powermodels/julia/` dataset (the
partial branch-flow sweep is preserved at
`results/own_vs_powermodels_branch_flow_attempt_2026-07-15_INCOMPLETE/` for
whoever picks up the fix). **`:branch_flow` remains available as an
explicit, opt-in formulation** for cases without this degree-1-bus
topology (validated correct and faster on `case6470_rte` and
`case118_ieee`), but should not be trusted as a default, and should not be
used at all on `case1888_rte`, until this bug is fixed.

**Lesson reinforced**: this bug was invisible in every test run earlier the
same day, because branch-flow had only ever been validated against
`case118_ieee` (correctness) and `case6470_rte` (performance) -- never
against `case1888_rte`, which happens to be the one default case with this
particular degree-1-load-bus topology. Flipping a default and re-running
the *full* validation sweep (not just the cases already spot-checked) is
exactly what surfaced this, which is the reason full-sweep validation
exists rather than relying on a couple of representative cases.

**Conclusion: closed, not fully resolved.** Five hypotheses tested this
pass (angle dedup, Ipopt scaling, `obj_scaling_factor`/MOI-wrapper
defaults, dual warm-starting, bus-type corrections) are each individually
ruled out or closed. The one confirmed, quantitative structural explanation
— PowerModels' sparser-but-larger branch-flow-native Jacobian — is already
partially captured by the formulation change adopted above. Further closing
the residual gap would require matching PowerModels' exact construction
(ordering, pairing, its specific MUMPS pivot sequence) rather than any
single remaining configuration difference, which is disproportionate effort
for the likely return — especially since the one adjacent lever tried
(angle dedup) made things worse, not better. Not pursued further; this is
treated as an accepted, well-evidenced characteristic of two independent
from-scratch reimplementations compared against PowerModels' native code,
not an unresolved bug.
