# Model Development & Validation Status

Date: 2026-07-09 (living document — update this date, and the relevant
section, whenever new development, validation, or experimentation work
lands)

Purpose: single entry point summarizing (1) the Python and Julia "own model"
AC/DC OPF development, (2) their validation against PowerModels.jl, and (3)
the load-flow experiments run with PyPowSyBl (`loadflow.run_ac`) and
PowerModels.jl (`compute_ac_pf`) — including exactly which parameters were
exercised in each. This is written to be returned to when deciding next
steps, not just read once. It links to the deep-dive docs for full detail
rather than duplicating them; treat those as the source of truth for
implementation specifics, and this file as the status/decision layer on top.

## How to use this document

- Each major section ends with an **Artifact index** pointing at the CSVs,
  scripts, and plots backing its claims.
- The **Known limitations / open items** and **Next steps** sections (§6, §7)
  are the parts most worth re-reading before planning a new session — they're
  written as living checklists, not history.
- When something changes (a bug fixed, a sweep re-run, a new parameter
  tested), update the relevant section in place rather than appending a new
  dated addendum, so this stays a current snapshot, not a session log. (For a
  session-by-session log with more granular bug-fix detail, see
  `docs/implementation_status.md`.)

---

## 1. OPF Model Development

Both languages implement DC OPF and AC OPF from scratch (not by calling
PowerModels.jl or another package to build the model), sharing a common
`PowerCase` data model in each language, and are validated against
PowerModels.jl (§2).

| Model | Language | Formulation | Solver | Status |
| --- | --- | --- | --- | --- |
| DC OPF | Python | LP via Pyomo | HiGHS | Implemented, validated |
| DC OPF | Julia | JuMP | HiGHS | Implemented, validated at scale (up to 6470 buses) |
| AC OPF | Python | Nonconvex NLP via Pyomo | Ipopt | Implemented, validated |
| AC OPF | Julia | JuMP | Ipopt | Implemented, validated at scale (up to 6470 buses) |
| AC PF (own, with Q-limit / PV→PQ switching) | Python + Julia | Newton-Raphson, polar coordinates | own Newton core | Implemented (one-way switching only; see §1.3) |

### 1.1 Python (`python/own_models/src/opf/{ac,dc}/`)

- **AC**: `data.py`, `variables.py`, `constraints.py`, `objective.py`,
  `model.py` (arc-based/PowerModels-style formulation --
  `build_ac_opf_pyomo_model` + `solve_ac_opf_pyomo`, also owns solver
  execution -- the sole AC-OPF formulation; the earlier bus-injection
  formulation and its separate `solve.py` were deleted 2026-07-24 once this
  formulation reached validated parity with PowerModels.jl), `initialization.py`
  (warm-start sources), `results.py` (`ACOPFResult`).
- **DC**: `data.py`, `variables.py`, `constraints.py`, `objective.py`,
  `model.py`, `results.py` (`DCOPFResult`), `solve.py`
  (`solve_dc_opf_pyomo`), `validation.py`.
- **Solve-iteration count**: neither Ipopt's nor HiGHS's Pyomo result object
  exposes iteration count as a field — captured instead by redirecting
  solver stdout and regex-parsing (`Number of Iterations....: NN` for Ipopt,
  `Simplex iterations: NN` for HiGHS) via
  `python/own_models/src/opf/solver_iterations.py::solve_with_iteration_capture`.
- Full architecture writeup, code evidence, and "defensible statements" for
  a technical review: `docs/python_own_models_fundamentals.md`,
  `docs/implementation_ownership_audit.md`.

### 1.2 Julia (`julia/own_models/src/opf/{ac,dc}/`)

- **AC**: `data.jl`, `variables.jl`, `constraints.jl`, `objective.jl`,
  `model.jl` (arc-based/PowerModels-style formulation -- `build_ac_opf_model`
  + `solve_ac_opf_own` -- the sole AC-OPF formulation; the earlier
  bus-injection formulation and its separate `solve.jl` were deleted
  2026-07-24 once this formulation reached validated parity with
  PowerModels.jl), `results.jl` (`ACOPFResult` struct,
  `_safe_barrier_iterations`).
- **DC**: `data.jl`, `variables.jl`, `constraints.jl`, `objective.jl`,
  `model.jl`, `solve.jl` (`solve_dc_opf_own`), `results.jl` (`DCOPFResult`
  struct, `_safe_simplex_iterations`).
- **Solve-iteration count**: unlike Python, JuMP exposes this generically —
  `barrier_iterations(model)` (Ipopt, AC) and `simplex_iterations(model)`
  (HiGHS, DC) work directly against any JuMP model via `MOI.BarrierIterations`
  / `MOI.SimplexIterations`, no log-scraping needed. This generality is what
  made it straightforward to also backfill PowerModels' own iteration count
  for AC OPF validation this session (§2).
- Full architecture writeup: `docs/julia_own_models_fundamentals.md`,
  `docs/julia_own_models_implementation_audit.md`.

### 1.3 Shared: AC PF with reactive-limit (PV→PQ) enforcement

Both languages also implement a Newton-Raphson AC **power flow** (not OPF)
core with optional one-way PV→PQ switching when a generator bus's aggregate
Qg exceeds its limits — used as an input to N-1 screening in earlier
iterations (now retired from that role on the Python side, §3) and validated
cross-language.

- Implemented: fixed-type Newton core, one-way PV→PQ switching, aggregate
  bus reactive-limit checks, deterministic per-generator Q allocation, slack
  Q-limit reporting.
- **Not implemented**: reversible switching (a bus that switches to PQ never
  switches back), cycle-detection logic for reversible switching. AC OPF is
  intentionally unchanged — it already treats Qg as a bounded decision
  variable, so it doesn't need a separate switching layer.
- Cross-language validation (12 case/scenario combinations: 3 PGLib cases ×
  4 Q-limit scenarios): Python and Julia converged identically — same
  switching sequence, same final bus types, differences at numerical
  roundoff (~1e-13–1e-14).
- **2026-07-25 decision: this own Newton-Raphson power-flow implementation is
  no longer used for any validation or analysis going forward.** It stays in
  `src/` (not deleted), but PowerModels' own `compute_ac_pf` and
  PyPowSyBl/OpenLoadFlow are the load-flow engines used everywhere now,
  including in the AC-OPF diagnostic ladder (`docs/formulations/ac_opf.md`).
  Full spec (archived): `archive/docs/pv_pq_switching.md`. Validation detail
  (archived): `archive/docs/ac_pf_q_limit_cross_language_validation.md`.

### Artifact index (§1)

- `docs/python_own_models_fundamentals.md`, `docs/julia_own_models_fundamentals.md`
- `docs/implementation_ownership_audit.md`, `docs/julia_own_models_implementation_audit.md`
- `archive/docs/pv_pq_switching.md`, `archive/docs/ac_pf_q_limit_cross_language_validation.md`
  (archived 2026-07-25 -- own Newton-Raphson PF retired from active use, see above)

---

## 2. OPF Validation (own model vs. PowerModels.jl)

**2026-07-24 note**: the bus-injection AC-OPF formulation was deleted in
both languages (see §1.1/§1.2 above) once the arc-based/PowerModels-style
formulation reached validated objective parity with PowerModels.jl across
the full sweep. `results/own_vs_powermodels/julia/` and `.../python/`
(non-`_pm_like` folders) are frozen pre-deletion snapshots -- they can no
longer be regenerated (the code that produced them is gone), and are kept
only because a couple of diagnostic scripts (Julia 41/46, Python
equivalents) still read them by path for historical comparison. All new
validation output goes to the `_pm_like`-suffixed folders (e.g.
`julia_pm_like/`, `python_pm_like/`), which are now simply "the" own-model
output, not an alternate variant.

### Methodology

Per case/scenario: objective abs/rel diff; per-variable mean/max/std absolute
diff (Pg, Qg, Vm, Va, branch Pf/Qf, branch loading %); binding-constraint
agreement (`both`/`only_own`/`only_pm` per pmin/pmax/qmin/qmax/vmin/vmax/
thermal/angle); a `mismatch_reason` classifier for `only_own`/`only_pm`
disagreements; a branch-flow sign/magnitude convention check.

- **Python**: `python/own_models/src/analysis/violations.py` +
  `compare_ac_with_powermodels.py` / `compare_with_powermodels.py`, driven by
  `python/own_models/scripts/18_plot_opf_variable_diffs.py`.
- **Julia**: `julia/own_models/src/analysis/opf_solution_inspection.jl`,
  driven by `julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl`
  (checkpointed, resumable, `--write-detail-rows` flag persists per-element
  CSVs).

### Coverage

- **Python**: 3 cases (case5, case14, case30) × 5 scenarios (base, relaxed,
  branch_congested, generator_limited, load_scaled), against pre-exported
  PowerModels JSON references (`results/reference/powermodels/{ac,dc}_opf/`).
  Limited to this scope because Python has no PowerModels.jl access — it can
  only validate against pre-exported references, not solve PowerModels
  in-process.
- **Julia**: 8 PGLib cases (case5_pjm → case6470_rte), full ~40-scenario
  stress-sweep family on **all 8 cases** including case6470_rte. 607 rows
  total (`results/own_julia/opf_validation_sweeps/compact_opf_validation_summary.csv`:
  305 AC + 302 DC). Julia solves PowerModels fresh in-process per scenario,
  so it carries the validation weight for anything beyond Python's small
  3-case set.

### Results

- **Variable agreement**: solver-tolerance precision (~1e-8 to 1e-17 median
  absolute diff) across Pg/Qg/Vm/Va/flows/loading, consistent from 5 buses to
  6470 buses, for the large majority of scenarios.
- **Binding-set agreement**: 52,221 / 52,253 checks agree (99.94%) across all
  constraint types (Julia AC+DC combined, full sweep). At the DC level
  specifically: **0 binding-constraint mismatches across all 302 DC
  scenarios** — own DC-OPF and PowerModels agree on every binding constraint
  in the whole sweep, with no exceptions. Python's small-case AC and DC sets
  (10–11 valid instances each after excluding doubly-infeasible scenarios,
  see below) also show 0 mismatches.
- **AC-OPF non-uniqueness under stress — isolated to 2 of 1888_rte's 40
  scenarios**: `load_scaling` (0.8× load) and `reactive_power_stress` (1.2×
  reactive load). Root-caused this session:
  - Objective agrees to within **1.1e-5** and **5.0e-4 relative**
    respectively — near-identical total cost.
  - Yet individual generator dispatch differs by up to **4.3 pu** (≈430 MW on
    a 100 MVA base) — e.g. gen 79 sits at its `pmin` in the own model but not
    in PowerModels, with gen 167 compensating by dispatching ~4.3 pu higher.
  - Only ~10–14 of 290 generators actually diverge meaningfully per scenario;
    the rest agree to near machine precision.
  - **Bus voltage magnitudes (Vm) barely diverge at all** — own and
    PowerModels essentially agree on the voltage profile even while Pg/Qg/
    branch flows redistribute substantially. This is the clearest visual
    signature that it's a *dispatch* non-uniqueness (multiple near-equal-cost
    ways to serve the same load under the same voltage profile), not a
    modelling disagreement.
  - Checked and ruled out cost-tied-generator degeneracy (costs differ,
    $2027–11963/MWh across the involved generators) — consistent instead
    with the AC-OPF non-uniqueness already characterized in
    `docs/python_ac_opf_validation.md`'s ~0.304 pu Qg finding (relaxed
    reactive limits create a weakly-determined Qg allocation; here it's a
    reactive-stress scenario producing an analogous weakly-determined Pg
    allocation).
  - An **engineered degeneracy test** (two same-bus generators, tied cost,
    generous headroom) found both DC and AC solvers converged *identically*
    between own model and PowerModels despite genuine mathematical
    degeneracy — solver tie-breaking is deterministic enough that
    independently-built formulations usually land on the same point. The
    1888_rte case is a genuine exception, not the norm.
  - Binding-constraint mismatches concentrate in exactly these same two
    scenarios: 31 of 1888_rte's total (21 in `reactive_power_stress`, 10 in
    `load_scaling`), vs. 1354_pegase's 1 and 6470_rte's 4 spread across other
    scenario families, and 0 everywhere else across all 8 cases.
  - **Root-caused (2026-07-10) why binding-constraint classification
    mismatches happen at all, distinct from the 1888_rte non-uniqueness
    above**: own model and PowerModels use byte-identical, verified solver
    tolerances (`docs/notes/solver_tolerances.md`) — Ipopt
    `constr_viol_tol`/`compl_inf_tol` = 1e-4 — but the binding
    classification code (`violations.py`/`opf_solution_inspection.jl`) uses
    a hardcoded 1e-6 distance-to-bound cutoff, ~100x tighter. Two
    independently-converged solves that agree to ~1e-6 pu can land on
    opposite sides of that cutoff. Worked real example in the tolerances
    doc: bus 4482's `vmax` mismatch is a 7.7e-7 pu own-vs-PM gap, both sides
    well within Ipopt's own convergence criteria. This explains the small
    mismatch counts (1354_pegase's 1, 6470_rte's 4) but *not* the large
    1888_rte ones (31, multi-pu gaps — six orders of magnitude too big for
    this mechanism).
- **Solver iterations, own vs. PowerModels (Julia AC, backfilled this
  session)**: PowerModels needs *more* Ipopt iterations than the own model
  on most mid-size cases, but *fewer* on the two largest (1888_rte: 448 vs.
  405; 6470_rte: 265 vs. 559 own). Real signal worth a closer look, not a
  plotting artifact — flagged as an open item (§7).
- **Solver iterations, own vs. PowerModels (Python AC+DC, implemented
  2026-07-10)**: now capturing real PowerModels iteration counts for
  Python's 3-case set too (previously always `NaN`, §6). AC: own and
  PowerModels track closely on all 3 cases (e.g. `case5_pjm`: 20 vs. 22
  mean). DC: PowerModels needs 5/18/34 iterations on
  `case5_pjm`/`case14_ieee`/`case30_ieee` respectively, while the *own*
  DC-OPF formulation solves `case14_ieee`/`case30_ieee` via HiGHS presolve
  alone (0 simplex iterations, confirmed genuine — not missing data) —
  flagged as an open item (§7) whether that's a meaningful formulation
  difference worth investigating.
- **Solve-time**: own model ~6x slower than PowerModels on the largest case
  tested (317.8s vs 51.7s, case6470_rte AC base) — not a correctness issue,
  worth watching if scaling further.
- **Known convergence issue, resolved**: default Ipopt `max_iter=500` was
  insufficient for large/stressed systems (case6470_rte's base AC-OPF needed
  564 iterations). Raised to 3000 across all three defaults
  (`python/own_models/src/analysis/ac_opf_sweeps.py`,
  `julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl`,
  `julia/own_models/scripts/08_run_ac_opf_powermodels_references.jl`).
  case6470_rte's full stress sweep now completes with 100% `outcome_match`
  (own vs. PowerModels agree on solved/failed status for every scenario);
  residual `ITERATION_LIMIT`/`OTHER_ERROR` rows at the new cap are matched by
  an identical or consistent failure mode on the PowerModels side, not own-
  model bugs.
- **Data-hygiene bugs found & fixed this session** (worth recording because
  they affected what earlier plots/tables appeared to show):
  1. Python's per-element detail-row CSVs accumulated duplicate rows across
     repeated script runs (no reset between runs) — a case seen 7x looked
     like 7x the actual mismatch volume. Fixed: detail files are now cleared
     at the start of each run.
  2. The per-element diff table grouped by `case_name` alone, which silently
     pooled all 5 of Python's scenarios per case into one row (Julia's
     `case_name` already encodes the scenario, e.g. `"1888_rte_load_x0.8"`,
     so this only affected Python). Fixed: now keys on `(case_name,
     scenario)`.
  3. Per-element detail rows were persisted even when **both** own and
     PowerModels failed to solve (doubly-infeasible scenarios), producing
     meaningless "diffs" between two non-converged solver states — confirmed
     on case30_ieee's `generator_limited` scenario (`own_status="infeasible"`,
     `pm_status="LOCALLY_INFEASIBLE"`, yet a 10.7-percentage-point
     `loading_pct` "diff" was recorded). Fixed: detail rows now only persist
     when both sides actually solved, matching a guard Julia's write path
     already had.

### Python own AC-OPF model — full-parity validation (2026-07-14)

Replicated the entire Julia validation body of work for Python's own AC-OPF
model: a 313-scenario compact sweep with per-variable diffs/binding-set
agreement/iteration counts (matching Julia's schema exactly), an optimality
gap against PowerModels.jl's own OBBT/one-shot-SOC lower bounds, and the
deviation-minimization formulation-equivalence test (C2). Full report:
`results/presentable/ac_opf_python_validation/python_ac_opf_validation_report.html`.
Raw results: `results/own_python/ac_opf_validation/compact_ac_opf_full_comparison.csv`,
`results/own_python/ac_opf_validation/ac_opf_optimality_gap.csv`,
`results/own_python/deviation_minimization/deviation_minimization_results.csv`.

- **Full sweep (313 scenarios)**: 215 both solve and agree to near machine
  precision (median objective diff ~1.4e-8, median max |Pg| diff ~3.3e-9 pu);
  97 both correctly report infeasibility on the same intentionally-stressed
  scenarios; **exactly 1 genuine disagreement** —
  `1888_rte_generator_114_outage`, where own model's Ipopt needs 771
  iterations/683s (vs. PM's 210/17.7s) and misses the post-hoc feasibility
  tolerance. Isolated solver-robustness edge case on the single hardest
  contingency tested, not a formulation defect (every other generator-outage
  scenario, on every case, matches cleanly).
- **4 additional rows among the 215** (all `1888_rte`-family, under heavy
  generator/reactive/angle stress) show a real local-optimum-quality gap:
  own model lands on a different optimum, worse by up to 92,020 (6.5%
  relative) in 3/4 cases and marginally better (−3,835) in the 4th.
- **Iteration/solve-time parity**: close on 6/8 cases; own model needs
  notably more on `1888_rte` (248.7 vs. 148.4 mean iters, 20.3s vs. 12.2s)
  and `6470_rte` (379.3 vs. 94.3 mean iters, 104.5s vs. 30.4s) — same two
  cases flagged throughout.
- **Binding-set agreement**: small mismatch counts across all constraint
  types except branch-thermal (30 own-only vs. 1 PM-only, summed across 215
  scenarios), concentrated entirely in `1888_rte`.
- **Optimality gap**: 0.003%–9.24% on the 5 cases with a rigorous OBBT bound
  (`5_pjm`'s 9.24% is a known real residual, already documented on Julia's
  side too — not Python-specific); 1.57%–6.82% on the 3 large/2 stressed
  cases using the looser one-shot SOC bound. Since own model's objective
  already matches PowerModels' to ~1e-8 on every base case, these numbers
  are effectively the cross-language consistency check, not new information
  about Python's solution quality per se.
- **Deviation-minimization (C2, Python)**: PowerModels' exact solved point
  is directly reachable/feasible under own model's formulation to
  ~1.4e-7–2.8e-5 L1 deviation on 6/8 base cases (`5_pjm` through
  `1354_pegase`) — direct proof of formulation equivalence, not just
  coincidentally-matching objectives. On `1888_rte`/`6470_rte` and their 2
  stressed variants, real deviation persists (382–502 L1, up to 10.5 pu max
  Pg) even when Ipopt is seeded exactly at the target point — the same
  AC-OPF non-uniqueness on large meshed networks already documented above
  for Julia's C2 test, not an infeasibility.
- **Methodological bug found & fixed**: `solve_ac_opf_pyomo`'s
  function-signature default `voltage_bounds=(0.95, 1.05)` silently clips
  PowerModels' actual solution out of reach for this experiment (e.g.
  `5_pjm`'s solved bus voltages run up to 1.10 pu) — produced a spurious
  ~0.33 pu deviation on an otherwise-correct formulation. Root-caused by
  comparing a manually-built model (no override, ~1.6e-7 deviation) against
  the default API call (identical seeding, ~0.33 deviation). Fix: the
  deviation-minimization script (`python/own_models/scripts/
  22_run_ac_opf_deviation_minimization_experiment.py`) passes
  `voltage_bounds=None` explicitly, matching Julia's own
  `voltage_bounds=nothing` default. Section A's compact-sweep numbers were
  never affected — `iter_compact_ac_validation` already passed
  `voltage_bounds=None` internally for the base scenario.
- **Overall verdict**: Python's own AC-OPF model is validated as
  formulation-equivalent to PowerModels.jl's `ACPPowerModel`, unambiguously
  on 6 of 8 benchmark systems (all three lines of evidence agree) and with a
  well-characterized, expected non-uniqueness caveat (not a formulation gap)
  on the 2 largest, most densely-meshed systems — matching what Julia's own
  C2 test already found on the same two cases.

### Reporting artifacts

`python/own_models/scripts/18_plot_opf_variable_diffs.py` (per-variable/
per-element diff tables, solver-iteration comparison, spotlight drill-downs,
binding-constraint mismatch bar/table/heatmap) and
`17_plot_opf_validation_findings.py` (outcome heatmap, objective scatter,
solve-time-by-case) write to `results/presentable/opf_validation_findings/`.
Notable outputs:

- `{python,julia}_{ac,dc}_variable_diffs_table.png` / `_max_table.png` — case
  × variable mean / worst-case diff.
- `{python,julia}_{ac,dc}_element_diff_table.png` — per-element diff for the
  sparse spot-check scenarios with detail-row data (explicitly labelled as a
  spot-check sample, not full-case coverage).
- `julia_ac_spotlight_1888_rte_load_x0.8__load_scaling.png`,
  `julia_ac_spotlight_1888_rte_load_x1.2__reactive_power_stress.png` —
  auto-flagged (not hand-picked) drill-down bar charts (Pg/Qg/branch flow/Vm,
  own vs. PowerModels) for the two non-uniqueness scenarios above.
- `{python,julia}_{ac,dc}_binding_mismatch_by_case.png` / `_table.png` /
  `_heatmap.png` — mismatch counts by case and by case×scenario; renders an
  explicit "0 binding-constraint mismatches confirmed" artifact rather than
  silently emitting nothing when a source (all of Python, and Julia DC) has
  zero mismatches.
- `{python,julia}_{ac,dc}_solver_iterations.png` — own vs. PowerModels
  iteration count by case, mean bar + min/max whiskers, non-converged runs
  excluded.
- `variable_diff_summary_table.csv`, `binding_set_comparison.csv`,
  `binding_mismatch_elements.csv` (element-level drill list).

### Artifact index (§2)

- `docs/python_ac_opf_validation.md` — Python AC OPF validation milestone
  freeze (relaxed-case Qg non-uniqueness finding).
- `docs/implementation_status.md` — session-log-style record with additional
  bug-fix detail.
- `results/presentable/opf_validation_findings/` — all current plots/tables.
- `results/own_julia/opf_validation_sweeps/compact_opf_validation_summary.csv`
  — 607-row Julia sweep (source of truth for Julia AC/DC validation numbers).

---

## 3. N-1 Security Screening

Built on top of the OPF base-case dispatch, using AC power flow (not the own
model's) as the contingency solver in both languages.

### Tool decision

- **Python**: PyPowSyBl + OpenLoadFlow, exclusively. The own model's role is
  limited to producing the base-case AC-OPF dispatch; the own Newton-Raphson
  PF solver is **retired** from the N-1 role — it lacked PV→PQ switching at
  the time and substantially under-counted thermal violations (13 vs. ~43-44
  for generator contingencies) relative to the two independent tools below.
- **Julia**: PowerModels.jl's native `compute_ac_pf` (Newton-Raphson) via
  `run_powermodels_ac_pf_compute` — not the own model's PF solver, not Ipopt.
  See §4.2 for what `compute_ac_pf` is and why it's the right tool here.

### Slack-modelling policy (shared across OPF, PF, and N-1)

Single fixed slack bus everywhere — no distributed-slack/participation-factor
mechanism anywhere in the own-model codebase. Branch outages: slack absorbs
100% of the imbalance. Generator outages: two-step static policy — slack
reassignment to the largest-headroom generator if the outaged unit *was* the
slack, then proportional-reserve redispatch (lost Pg split across surviving
generators by headroom). Full rationale and code pointers:
`docs/formulations/security_constraints.md`.

### Results on case118 (186 branches + 54 generators = 240 contingencies)

| Tool | Branch secure | Branch thermal viol. | Branch voltage viol. | Gen secure | Gen thermal viol. | Gen voltage viol. |
| --- | --- | --- | --- | --- | --- | --- |
| PyPowSyBl / OpenLoadFlow | 16/186 | 136 | 96 | 7/54 | 43 | 20 |
| Julia PowerModels.compute_ac_pf | 24/186 | 131 | 73 | 6/54 | 44 | 19 |
| ~~Own Python Newton (retired)~~ | 0/186 | ~0 (mislabeled) | 72 | 40/54 | 13 | 12 |

Two independent, established tools (OpenLoadFlow and PowerModels.jl) agree
closely with each other — a corroborated finding that **case118 in its stock
PGLib form is genuinely N-1 fragile** (only ~13–15% of contingencies secure),
not a tool artifact. This cross-tool agreement is the strongest evidence in
the repo that the two load-flow engines (§4) are trustworthy for downstream
security work despite their very different parameter surfaces.

### Artifact index (§3)

- `python/own_models/src/analysis/pypowsybl_security_validation.py`,
  `python/own_models/scripts/19_run_n1_case118.py`
- `julia/own_models/src/analysis/n1_screening.jl`,
  `julia/own_models/scripts/10_run_n1_case118.jl`
- `results/own_python/security/n1_pypowsybl_case118_contingencies.csv`,
  `results/own_julia/security/n1_julia_compute_pf_case118_contingencies.csv`
  (current, authoritative — do not use the stale retired-Newton outputs
  under the same directories)

---

## 4. Load-Flow Experiments: PyPowSyBl vs. PowerModels.jl

This is the part of the repo where the two languages' tooling diverges most:
Python's pypowsybl exposes a large, genuinely-exercised parameter surface;
Julia's `compute_ac_pf` is a narrower, faster, black-box Newton-Raphson
solve. Both are used deliberately for what they're good at (§3), and this
section documents what was actually tested in each, not just what's
theoretically available.

### 4.1 Python: PyPowSyBl `loadflow.run_ac` / `LoadFlowParameters`

```python
import pypowsybl as pp
from pypowsybl.loadflow import LoadFlowParameters, run

network = pp.network.create_ieee14()
params = LoadFlowParameters(
    voltage_init_mode='UNIFORM_VALUES',
    transformer_voltage_control_on=True,
    phase_shifter_regulation_on=True,
    shunt_compensator_regulation_on=True,
    distributed_slack=True,
    balance_type='PROPORTIONAL_TO_GENERATION_P_MAX',
    max_iterations=20,
    tolerance=1e-6,
    voltage_remote_control_on=True,
    provider_parameters={},
)
result = run(network, parameters=params)
```

**What was actually varied, staged one mechanism at a time** (not a single
flat sweep — see `advanced_configuration_plan.md` for the full staged design):

| Stage | Parameter(s) exercised | Finding |
| --- | --- | --- |
| Numerical robustness | `acSolverType` (`NEWTON_RAPHSON` vs `NEWTON_KRYLOV`), `stateVectorScalingMode` (`LINE_SEARCH`, `MAX_VOLTAGE_CHANGE`), `newtonRaphsonStoppingCriteriaType` | Tests whether non-convergence is a Newton-path issue independent of the model itself. |
| **Initialization** | `voltage_init_mode`: `UNIFORM_VALUES` vs `DC_VALUES` | **Strongest single finding in this whole area.** `DC_VALUES` recovers cases that fail from a uniform start: `case1888_rte` goes from 0/20 to **20/20** converged; `case6470_rte` from 0/20 to 12/20; `case13659_pegase` from 4/20 to only 2/20 (not universally beneficial). |
| Slack modelling | `distributed_slack`, `balance_type` (`PROPORTIONAL_TO_GENERATION_P_MAX`, `PROPORTIONAL_TO_GENERATION_REMAINING_MARGIN`), `slackBusSelectionMode` (`NAME`, `LARGEST_GENERATOR`) | **Not the dominant recovery mechanism** on PGLib-imported cases — in the factorial sweep, slack modelling alone did not newly recover any of the tested failing variants. |
| Reactive limits | `use_reactive_limits` (PV→PQ switching), `reactiveLimitsMaxPqPvSwitch`, `forceTargetQInReactiveLimits`, `reactivePowerDispatchMode` | **Not automatically beneficial** — in several cases enabling PV→PQ switching worsens convergence or leaves it unchanged (e.g. case1888_rte's `Gen P/Q` variant: converged under matched baseline, `False` under several factorial configs). |
| **Operational controls** | `transformer_voltage_control_on`, `shunt_compensator_voltage_control_on` (also seen as `shunt_compensator_regulation_on`), `phase_shifter_regulation_on` | **Do not activate meaningfully on PGLib/MATPOWER-imported data**, confirmed by a direct metadata audit: 0 regulating phase tap changers and 0 voltage-regulating shunts across all 4 audited large cases, despite up to thousands of generators. This is a *data* limitation, not a PyPowSyBl limitation — demonstrated working correctly on **native** PyPowSyBl/IIDM networks instead (e.g. `create_ieee300`: 48/50 ratio tap changers regulating, 69/69 generators voltage-regulating). |

**Advanced utilities also identified (not all exercised yet)**:
`pypowsybl.loadflow.run_validation` (post-hoc consistency check),
`pypowsybl.voltage_initializer.run` (improved starting voltages),
`pypowsybl.security.create_analysis().run_ac` (the N-1 tool actually used in
§3), `pypowsybl.sensitivity.create_ac_analysis` (not yet used anywhere in
this repo).

**Matched-baseline anchor** (the fair, PowerModels-comparable configuration,
used before any of the above is layered on): single slack, reactive limits
off, distributed slack off, all operational controls off, provider
`OpenLoadFlow`, solver `NEWTON_RAPHSON`, `maxNewtonRaphsonIterations=1000`.

**Explicit callout, since it's easy to state this wrong**: the correct
framing is *"PGLib/MATPOWER data doesn't support meaningful operational-
control activation,"* not *"operational controls fail in PyPowSyBl."* If
operational controls (transformer/shunt/phase-shifter regulation) are needed
on PGLib-derived cases going forward, the regulating-bus/target-voltage/
deadband/tap-step metadata would have to be **implemented ourselves** — it
doesn't exist in the imported data, matching what the user flagged when this
document was requested.

### Artifact index (§4.1)

- `results/presentable/pypowsybl_lf_replacement_sweep/presentation_findings/pypowsybl_lf_convergence_findings_for_presentation.md`
  — the presentation-ready synthesis this section summarizes (figures,
  full result tables, recommended slide order).
- `results/presentable/pypowsybl_lf_replacement_sweep/baseline_problem_case_analysis/advanced_configuration_plan.md`
  — the staged experiment design (Stages 1–6) with exact parameter names.
- `results/interactive/pypowsybl_lf_four_config_playground/base_case_four_config_lf_summary.csv`
  — the DC-vs-uniform-initialization interactive sweep data.
- `results/presentable/pypowsybl_lf_replacement_sweep/factorial_control_analysis/`,
  `native_operational_control_analysis/` — factorial-control and native-
  network CSVs/figures.
- `docs/notes/pypowsybl_data_format.md` (currently a stub — a good home for
  network-table/result-structure notes if/when that's written up).

### 4.2 Julia: PowerModels.jl `compute_ac_pf`

`compute_ac_pf` is PowerModels.jl's native AC power-flow solver: it works
directly in polar voltage coordinates via Newton-Raphson, **bypassing the
JuMP model-building layer entirely** — no JuMP variables/constraints are
built, so there's no generic-solver overhead. This is what the repo uses:

```julia
result = PowerModels.compute_ac_pf(netdata)

# As actually invoked in the LF replacement-note sweep:
PowerModels.compute_ac_pf(data_variant; method=:newton, ftol=1e-8, xtol=1e-8, iterations=100)
```

**Why `compute_ac_pf` over `solve_pf`/`solve_ac_pf`**: `solve_pf` is
generic/formulation-agnostic — it builds a JuMP model and can use any JuMP
solver, which is more flexible but slower for pure AC power-flow solves.
`compute_ac_pf` is used wherever the repo needs *fast, repeated* AC PF
solves rather than a one-off OPF-style optimization: the N-1 screening
contingency loop (§3, one solve per contingency, up to 240 for case118) and
the LF replacement-note convergence sweep (§ below). It is **not** used for
OPF (Ipopt/JuMP handles that, §1) — `compute_ac_pf` only solves the
power-flow equations for a *given* dispatch, it doesn't optimize one.

**Required inputs**: a PowerModels-format network dict with bus
voltage/type, branch admittances, and load/generator injections, same as any
PowerModels.jl workflow. **Initial guess**: via `_start`-suffixed fields in
the network data (e.g. `vm_start`, `va_start`) for warm-starting repeated
solves — this is the general PowerModels.jl convention, and it's what the
LF replacement-note sweep uses to inject PGLib "replacement note" values as
*initialization* rather than enforced constraints.

**What was actually exercised — the PGLib replacement-note convergence
sweep** (12 cases; headline numbers over the 7 large/problematic cases:
case300_ieee, case1354_pegase, case1888_rte, case6470_rte, case9241_pegase,
case13659_pegase, case78484_epigrids):

| Variant | What it changes | n converged / 7 (large cases) | Convergence rate |
| --- | --- | --- | --- |
| `original` | No replacement-note values applied | 2 | 29% |
| `BV_from_warmstart` | `vm_start`/`va_start` from replacement notes | 3 | 43% |
| `G_PGQG_from` | + generator Pg/Qg from replacement notes | 4 | 57% |
| `G_PGQG_from_plus_BV_from` | + bus voltage init | 4 | 57% |
| `G_PGQG_from_plus_GVG_from` | + generator voltage setpoint (`vg`) | 4 | 57% |

The effect is **not monotonic across individual cases** — the per-case CSV
should be used for case-by-case interpretation, not just the aggregate rate.
A parallel DC sweep (`compute_dc_pf`) found `original` and `G_PG_from`
converge 12/12; variants touching load values failed entirely (0/12) — noted
as needing further investigation, not yet root-caused.

**Known limitation, confirmed in the sweep methodology itself**: *iteration
counts are not available from `compute_ac_pf` in this workflow* and are
recorded as unavailable in the sweep's row-level CSV. This is a different
code path from the OPF-validation iteration capture in §2/§1.2 — that uses
`barrier_iterations`/`simplex_iterations` against a JuMP model built via
`instantiate_model`/`optimize_model!`, which `compute_ac_pf` deliberately
bypasses. If `compute_ac_pf`'s iteration count is ever needed, it would
require either patching the call to request it explicitly (if the
underlying native solver exposes it at all) or switching that specific
workflow to the JuMP-based path, trading away the speed advantage.

### Artifact index (§4.2)

- `docs/powermodels_lf_replacement_sweep.md` — consolidated (2026-07-10) from
  5 near-duplicate per-run write-ups into one doc with a per-run comparison
  table (`base`, `api`, `base_large`, `mixed_full`, `sad` — the last two
  turned out to be a reproducibility check that passed, not a leftover
  duplicate).
- `results/presentable/powermodels_lf_replacement_sweep/<run>/powermodels_ac_pf_sweep_rows.csv`,
  `powermodels_dc_pf_sweep_rows.csv` (one set per run, see the doc above).
- `julia/own_models/src/analysis/ac_pf_compute_validation.jl`
  (`run_powermodels_ac_pf_compute` — the wrapper used for N-1, §3).

### 4.3 Cross-tool comparison: PyPowSyBl vs. `compute_ac_pf`

A matched single-slack baseline comparison was run across 6 cases
(case300_ieee, case1354_pegase, case1888_rte, case6470_rte, case9241_pegase,
case13659_pegase) to check whether the two tools agree when configured as
comparably as their different APIs allow (PyPowSyBl: single slack, reactive
limits off, all operational controls off; PowerModels: plain `compute_ac_pf`
Newton-Raphson).

| Case | PowerModels converged / 5 variants | PyPowSyBl converged / 5 variants | Agreement |
| --- | --- | --- | --- |
| case300_ieee | 3 | 3 | Full agreement |
| case1354_pegase | 5 | 5 | Full agreement |
| case1888_rte | 2 | 2 | Full agreement |
| case9241_pegase | 5 | 5 | Full agreement |
| case6470_rte | 0 | 1 | PyPowSyBl recovers 1 shared variant PowerModels doesn't |
| case13659_pegase | 0 | 1 | PyPowSyBl recovers 1 shared variant PowerModels doesn't |

**Takeaway, stated explicitly to avoid over-claiming**: compare the two
tools only under this matched baseline. PyPowSyBl's Q-limit/operational-
control modes are *not* equivalent to plain `compute_ac_pf` and shouldn't be
read as "PyPowSyBl beats PowerModels" — they're different, richer
configurations being asked to solve a harder version of the same problem.
Where both tools are held to the same matched setup, they agree closely,
which is the same conclusion §3's N-1 case118 cross-check reached
independently — this is the second, separate piece of evidence that the two
engines are consistent with each other when given a like-for-like problem.

---

## 5. Model Development vs. Load-Flow Experiments — how they relate

To avoid conflating two different things that share vocabulary:

- **§1's own AC/DC OPF and own AC PF-with-switching** are code this project
  built from scratch, validated against PowerModels.jl's OPF (§2) and, for
  the PF-with-switching layer, cross-validated Python-vs-Julia directly
  (§1.3).
- **§4's PyPowSyBl and `compute_ac_pf`** are *not* own code — they're
  established third-party tools used as (a) an N-1 contingency solver (§3)
  and (b) a convergence-behavior/parameter-sensitivity experiment in their
  own right. The own model's PF solver is explicitly **not** used for N-1
  anymore (§3) — `compute_ac_pf` and PyPowSyBl replaced it.
- The two areas connect at exactly one point: the own AC-OPF's solved
  dispatch is the *input* that both PyPowSyBl and `compute_ac_pf` then run
  N-1 contingencies against (§3). Everything else in §4 (the parameter
  sweeps) is evaluating the third-party tools' own behavior, not validating
  the own model.

---

## 6. Known limitations / open items

- **PowerModels' DC solver-iteration capture: code fixed, dataset not yet
  backfilled.** `09_run_compact_opf_validation_sweeps.jl`'s DC branch was
  silently reusing `_safe_barrier_iterations` (Ipopt-specific) on a
  HiGHS-solved model, always returning `NaN`. Fixed to use
  `_safe_simplex_iterations` (mirroring the AC pattern, §1.2) — verified via
  a 2-scenario smoke test on `case5_pjm`, real values returned (5, 4
  iterations). **Not yet re-run against the real dataset**: the 302-row DC
  portion of `compact_opf_validation_summary.csv` still has `pm_solve_
  iterations = NaN` until the DC sweep is next run with `--models=dc`.
- ~~Python never captures PowerModels' solver-iteration count~~ **Implemented
  (2026-07-10).** The actual export scripts Python's 3-case validation reads
  are `julia/tools/powermodels/scripts/07_export_ac_opf_reference.jl` (AC)
  and `06_export_dc_opf_reference.jl` (DC) — a different, smaller pipeline
  from the "compact sweep" exporter (`08_run_ac_opf_powermodels_references.jl`)
  previously (incorrectly) named here. Both now use `instantiate_model`/
  `optimize_model!` (same pattern as §1.2/§6 item 1 above) with a local
  `safe_barrier_iterations`/`safe_simplex_iterations` helper, adding a
  `"solve_iterations"` field to the exported `*_reference.json`. Python's
  `compare_ac_with_powermodels.py`/`compare_with_powermodels.py` now read
  that field instead of hardcoding `NaN`. Re-exported all 15 case/scenario
  JSON pairs for both AC and DC; verified end-to-end via
  `python_{ac,dc}_solver_iterations.png` now showing real PowerModels bars.
  Two bugs found and fixed in the process, both worth remembering:
  1. The non-converged-run filter in `plot_iteration_comparison` (§2) only
     checked Julia's `own_solved`/`pm_solved` column names, silently not
     applying to Python (which uses `own_success`/`pm_success`) — a
     `case14_ieee` scenario with `pm_solve_iterations=3000` (iteration-cap
     timeout, both sides failed) was contaminating the mean/whiskers exactly
     like the Julia AC case did before that fix. Now checks both spellings.
  2. Python's own DC-OPF `own_solve_iterations` was `NaN` for every
     `case14_ieee`/`case30_ieee` scenario, even successfully-solved ones.
     Root cause: HiGHS's presolve fully reduces these small DC-OPF LPs to
     nothing before simplex ever runs ("Reduced to empty" in the solver
     log), so the regex-based iteration-count parser
     (`solver_iterations.py`) never found a "Simplex iterations" line to
     match and fell back to `NaN` — but the true count is 0, not unknown.
     Fixed to detect the presolve-to-empty case and return `0.0`. The
     resulting zero-height bars are now labeled `"0"` explicitly in the
     plot so they don't read as missing data.
- **`compute_ac_pf`'s own iteration count is unavailable** in the LF
  replacement-note sweep (§4.2) — a different limitation from the two above,
  specific to bypassing the JuMP layer.
- ~~`python_dc` has no large-scale objective/outcome/solve-time-vs-
  PowerModels comparison plot set~~ **Implemented (2026-07-10), at small
  scale.** There's still no DC equivalent of the 607-row Julia sweep or a
  `compact_dc_opf_sweeps_with_powermodels_comparison.csv` for Python — but
  `python_dc_vs_powermodels_{outcome_heatmap,objective_scatter,
  solve_time_by_case}.png` now exist, built by reusing
  `17_plot_opf_validation_findings.py`'s `classify_rows`/`make_plot_set`
  (loaded into `18_plot_opf_variable_diffs.py` via `importlib`, since a
  normal `import` of a digit-prefixed filename isn't possible) against the
  existing small 3-case × 5-scenario comparison, normalized to that schema
  via the new `normalize_python_dc_small_scale`. Titled "(3-case validation
  set)" so it doesn't read as Julia-sweep-scale coverage. This also required
  adding solve-time capture that didn't exist anywhere in this pipeline
  before: own Python DC-OPF wall-clock time (`compare_with_powermodels.py`)
  and PowerModels' DC wall-clock time (new `solve_time_sec` field in
  `06_export_dc_opf_reference.jl`'s exported reference JSON, mirrored into
  `07_export_ac_opf_reference.jl` for schema symmetry even though AC's
  existing plot trio doesn't consume it). All 15 AC + 15 DC reference JSON
  pairs re-exported to pick up both `solve_iterations` and `solve_time_sec`.
- **PyPowSyBl operational controls (transformer/shunt/phase-shifter
  regulation) have no working demonstration on PGLib-derived cases** — the
  imported data structurally lacks the control metadata (§4.1). If this is
  needed, the metadata has to be constructed/implemented, not just enabled
  via a flag.
- **Reversible PV→PQ switching deferred** (§1.3) — a bus that switches to PQ
  never switches back in the current implementation.
- **Own Python Newton-Raphson PF is retired from the N-1 role** but its code
  (`post_contingency.py`, scripts `08`/`09`) still exists and still runs for
  small cases — not deleted, just not the source of truth for security
  analysis anymore. Its stale output should not be confused with the current
  authoritative pypowsybl/compute_ac_pf N-1 results (§3).
- **N-1 security screening has only been run on case118** — not extended to
  other case sizes.
- If PyPowSyBl network-table/result-structure notes are ever written up,
  `docs/notes/` (see `neutral_data_format.md`, `powermodels_data_format.md`
  for the existing sibling docs' style) is the natural home — distinct from
  this file's experiment-level summary. (The old empty-stub placeholder for
  this, `docs/notes/pypowsybl_data_format.md`, was removed when this file
  replaced it and `docs/roadmap.md`, 2026-07-10.)

---

## 7. Next steps / open decisions

Living checklist — prune items once done, add new ones as they come up,
rather than letting this grow unbounded.

1. ~~Re-run the DC sweep to populate `pm_solve_iterations` for DC.~~
   **Closed (2026-07-11)** — backed up the summary CSV, stripped the 302 DC
   rows (kept all 305 AC rows), reran `--models=dc` (not `--overwrite`,
   which would have deleted the whole file including the AC rows).
   `compact_opf_validation_summary.csv` now has 305 AC + 305 DC = 610 rows,
   **all** with `pm_solve_iterations` populated. Bonus: the rerun produced 3
   more DC rows than the original run had (302→305) — the original DC sweep
   was apparently incomplete, not just missing the iteration column; the 3
   new scenarios are on `1354_pegase` and `1888_rte`, no rows were lost
   (diffed against the backup to confirm).
2. ~~Decide whether `python_dc` needs objective/outcome/solve-time plots at
   Julia-DC scale.~~ **Decided against, 2026-07-11** — the scenario-sweep
   infrastructure already exists
   (`python/own_models/scripts/13_run_compact_dc_opf_sweeps.py`, all 8
   cases), but it has no PowerModels-comparison layer at all; building one
   means a new DC reference-export pipeline matching (or reconciling with)
   Julia's scenario grid, a multi-hour build. Every DC comparison run this
   session already shows Python DC tracking PowerModels closely — a bigger
   sweep would mostly add confirmatory volume, not new insight. Not
   pursued; revisit if a specific need comes up.
3. ~~Decide whether the 1888_rte iteration-count finding warrants its own
   investigation.~~ **Closed** — formally checked against the real
   `own_solve_iterations`/`pm_solve_iterations` data in
   `docs/notes/solver_tolerances.md` ("Formal closure" subsection). Own
   model needs up to ~4.5x *more* iterations than PowerModels on
   `6470_rte` (mean 421.6 vs 94.3, own never faster across 26 scenarios),
   confirming the two solves take genuinely different trajectories
   (consistent with the confirmed different starting points), but
   within-case correlation between iteration count and mismatch magnitude
   is weak/inconsistent (Pearson r between −0.43 and 0.35) — iteration
   count is not a reliable predictor of which scenarios show large binding
   mismatches. The one remaining optional follow-up (warm-starting
   PowerModels from the own model's solution to see if it converges to the
   same point) is not required to close this out.
4. ~~Investigate why the own Python DC-OPF formulation presolves to
   nothing on `case14_ieee`/`case30_ieee` while PowerModels needs
   18-35.~~ **Closed (2026-07-11)** — not a formulation difference, purely
   a warm-start-plumbing one: Julia's own model and PowerModels always hand
   HiGHS a primal start for every variable (own model via `set_start_value`,
   PowerModels via `comp_start_value(...)` defaulting to 0.0), which makes
   HiGHS build a "useful basis" and skip presolve (~17-18 iterations from
   that crash basis). Python's Pyomo→HiGHS call never passes
   `warmstart=True`, so HiGHS always solves cold — presolve then runs and,
   for these small DC-OPF LPs, fully eliminates the problem (0 iterations,
   correctly). Confirmed directly: clearing Julia's own-model start values
   reproduces Python's exact presolve-to-empty behavior. Full writeup:
   `docs/notes/solver_tolerances.md`. No fix needed — both are correct.
5. Decide whether operational-control metadata for PGLib cases is worth
   building, or whether native-network demonstrations (§4.1) are sufficient
   evidence for the thesis/presentation purpose.
6. ~~Extend N-1 security screening beyond case118.~~ **Done (2026-07-11)** —
   generalized `python/own_models/scripts/19_run_n1_case118.py` and
   `julia/own_models/scripts/10_run_n1_case118.jl` (previously hardcoded to
   `case118`) to loop over cases; default list is now `case118`/`case300`
   for both languages. `case1354_pegase` was tried and dropped: its 2,251
   contingencies took over 5.7 hours on the Julia side (confirmed still
   actively computing via growing CPU time, not hung) with no sign of
   finishing — dramatically slower per-contingency than `case118`/`case300`,
   most likely one or a few pathological contingencies rather than uniform
   slowness. Not investigated further; killed and excluded from both
   languages' default lists rather than sinking more time chasing it.
   Separately, Python's pypowsybl-based screening has a real, pre-existing
   gap that would have also blocked `case1354_pegase` (and still applies to
   `case300_ieee`, which is fast enough to keep trying): non-zero
   phase-shifting transformers, which `pypowsybl_security_validation.py`'s
   network builder can't represent (it only ever builds plain two-winding
   transformers, never pypowsybl's `PhaseTapChanger`) — it deliberately
   raises rather than silently producing a wrong network. The script now
   detects and cleanly skips phase-shifted cases with a clear log message
   instead of crashing; Julia's PowerModels-based screening has no such
   gap. `case1888_rte`/`case6470_rte` deliberately excluded from both
   languages' default lists too — 2,828/10,335 contingencies each, more
   than the added insight over what the warm-start ablation already found
   about those two cases is worth right now.
7. **New, D1 closed (2026-07-11)**: N-1 contingency load-flow
   dispatch-source/warm-start consistency experiment
   (`julia/own_models/scripts/12_run_n1_load_flow_warmstart_experiment.jl`).
   Question: does post-contingency power-flow security classification
   (secure/insecure) depend on whether the pre-contingency dispatch comes
   from own model's AC-OPF or PowerModels', or on whether the post-outage
   Newton-Raphson starts warm (from that dispatch's voltages) or flat? On
   `case118`: **100% agreement** across all 4 combinations (own/PM ×
   warm/flat) — 240/240 contingencies agree on both convergence and
   secure/insecure classification. At the time, 9 contingencies failed to
   converge in every variant (same 9 each time) — consistent, not a
   warm-start artifact, flagged for a closer look. Clean confirmation of the
   hypothesis that the two OPF solutions are practically interchangeable as
   N-1 screening inputs, at least for this case. Not yet run on `case300`
   (only currently scoped to `case118`).

   **Update, 2026-07-12**: that closer look (item 9 below) found those 9
   "failures" were never a warm-start issue — they're case118's radial-spur
   branches, which island a load/generator-carrying bus, mathematically
   singular for Julia's `compute_ac_pf` without island detection. Fixed;
   D1 rerun now reports **240/240 converged, 32/240 secure**, still 100%
   agreement across all 4 combinations.
8. **Updated 2026-07-11 — narrower than originally stated.** The warm-start
   ablation (`docs/notes/solver_tolerances.md` "Warm-start ablation, part 3")
   was extended from 1 case to all 8 default PGLib cases, in both languages.
   **This overturned the original framing**: a Julia-vs-Python `case`-start
   iteration penalty is *not* a general own-model implementation trait — for
   6 of 8 cases (`5_pjm` through `1354_pegase`), Python's and Julia's
   case-start penalty ratios match almost exactly (often to 2 decimal
   places, e.g. `30_ieee`: both 1.13x). The gap is concentrated specifically
   in `6470_rte` (2.49x Python vs 3.84x Julia) and mildly `1888_rte` (1.02x
   vs 1.44x). Correct framing: something about these one or two
   RTE-derived networks specifically — not a JuMP-vs-Pyomo interface
   difference in general — makes a poor starting point much costlier for
   Julia's Ipopt call there. Not yet investigated — candidates worth
   checking: network conditioning on `6470_rte` specifically, generators
   sitting near bounds in that case's dispatch, constraint/variable scaling,
   or Ipopt's `nlp_scaling_method`.
8. ~~Named/indexed JuMP constraints + notation doc.~~ **Closed
   (2026-07-11)**. Python's Pyomo models were already named/indexed by
   construction; Julia's `@constraint`/`@NLconstraint` calls were anonymous
   (bare, inside `for` loops, refs discarded) — refactored every
   constraint-adding function in
   `julia/own_models/src/opf/{ac,dc}/constraints.jl` to return named,
   id-indexed `Dict`s/NamedTuples of `ConstraintRef`s, threaded through
   `build_{ac,dc}_opf_model` (now exported), which returns them as a
   `constraints` NamedTuple. **Two-pass fix**: the first pass only indexed
   (stored refs) without naming the AC power-balance/thermal constraints,
   since those used the legacy `@NLconstraint`/`@NLexpression` macros,
   which don't support `base_name`/`set_name` at all (verified — both
   error). Fixed by switching those to JuMP's modern, unified
   `@constraint`/`@expression` macros (JuMP 1.30.1 handles nonlinear
   expressions with these natively). Solve behavior confirmed unchanged:
   same solution to full float precision on a nonlinear test problem, and
   real-model objectives agree to ~1e-11 relative before/after (float-noise
   level from a different internal expression-graph representation, well
   inside Ipopt's own `1e-8` convergence tolerance — not a real change).
   Verified `dual(...)` extraction works end-to-end on both languages, all
   constraint types, both before and after the macro switch. Full
   symbol-to-identifier mapping (including the pre-existing Python names):
   `docs/notation.md`. This unblocks C2
   (deviation-minimization validation), which needs per-constraint duals.
9. **Julia/Python N-1 disagreement — root-caused AND fixed, 2026-07-12.**
   Julia (`compute_ac_pf`) and Python (pypowsybl/OpenLoadFlow) agreed on
   only 225/240 (93.8%) of `case118`'s N-1 secure/insecure verdicts.
   Cross-referenced the 15 disagreements' raw
   `max_loading_pct`/`min_vm_pu`/`max_vm_pu` values (both tools' per-
   contingency CSVs) against the pre-contingency base-case loading/voltage
   from D2's `n1_case118_variable_impact.csv` — no new solves needed for
   the root-cause pass. Two distinct mechanisms found, one root-cause-only,
   one fixed outright.

   **13 (now 15, see below) of 15 are a classification-tolerance artifact,
   not a real security disagreement.** Both tools apply the *identical*
   violation tolerance (thermal `loading% > 100 + 1e-6`, voltage
   `|Vm − limit| > 1e-6` pu — verified directly in both `n1_screening.jl`
   and `pypowsybl_security_validation.py`). The base-case optimal AC-OPF
   dispatch pins several branches/buses essentially exactly at their
   thermal/voltage bound (normal, cost-optimal OPF behavior — e.g. branch
   163 at 100.0000002% loading, branch 106 at 100.0000006%, over a dozen
   buses at ~1.0600000–1.0600001 pu against `vmax=1.06`). For contingencies
   that barely perturb those specific elements, the post-contingency value
   stays razor-close to the bound, and the two independently-implemented
   Newton-Raphson solvers never agree better than ~1e-4–2e-3 pu/% —
   3-4 orders of magnitude coarser than the 1e-6 classification tolerance —
   so whichever side of the true bound each engine's own numerical noise
   lands on decides its secure/insecure call. Not a bug in either tool;
   practical implication: a hard binary classification at 1e-6 tolerance is
   tighter than any single AC PF engine's own precision, and a "marginal"
   third bucket (or a ~1e-3 tolerance) would remove this disagreement
   without hiding the genuinely thin physical margins several `case118`
   constraints run under at N-1.

   **The other 2 of 15 turned out to be a bigger finding: not an NR
   robustness gap, an island-handling gap — and it was fixable.** All 9 of
   Julia's always-non-convergent contingencies (same 9 in every D1 variant)
   turned out to be structurally identical: a union-find connectivity check
   against the case file confirmed each one fully disconnects a leaf bus
   (degree 0 in the surviving graph) that carries real load and/or
   generation (bus 10: gen; 73: 0.06pu load + gen; 87: gen; 111: gen; 112:
   0.68pu load + gen; 116: 1.84pu load + gen; 117: 0.2pu load, no gen). A
   bus with real injection and no path to any slack bus is mathematically
   singular for a single-slack Newton-Raphson solve — Julia's
   `compute_ac_pf` had no island-detection step, so it correctly failed to
   converge on all 9, every time, regardless of warm/flat start (explaining
   D1's "same 9 fail in every variant" finding too — it was never a
   warm-start-sensitivity result). pypowsybl/OpenLoadFlow, by contrast,
   detects and drops de-energized islands before solving (standard
   industrial-PF-engine practice) — it converged on all 9 all along,
   correctly finding real overloads on 7 (`outage::7`: 159.8% loading,
   `outage::9`: 159.9% — genuine, not noise) and no violation on the
   remaining 2 (`outage::134`/`outage::176`).

   **Fix, implemented 2026-07-12**: `run_powermodels_ac_pf_compute`
   (`julia/own_models/src/analysis/ac_pf_compute_validation.jl`) now takes
   a `drop_islands::Bool=true` kwarg that calls PowerModels.jl's own
   `select_largest_component!` before solving, plus a small
   `_deactivate_equipment_on_isolated_buses!` helper that also turns off
   generators/loads/shunts on the newly-isolated buses (needed because
   `select_largest_component!` only flips `bus_type`, and
   `compute_ac_pf`'s `instantiate_pf_data` asserts if an in-service
   generator is still attached to a bus type it doesn't recognize).
   Verified as a true no-op on every non-islanding contingency and on the
   fully-connected base case before rerunning anything. Threaded through
   `run_n1_branch_screening`/`run_n1_generator_screening`/`run_n1_screening`
   as a passthrough kwarg, same pattern as `start_mode`.

   **Result, full rerun (`10_run_n1_case118.jl`, `12_...warmstart...jl`,
   `14_...variable_impact...jl`)**: `case118` converged 231/240 → **240/240**,
   secure 30/240 → **32/240** (both the main screen and D1, which stays at
   100% cross-variant agreement). `case300` converged 375/480 → **459/480**
   (secure stays 0/480 — the 84 newly-resolved contingencies are all
   genuinely insecure too). D2 rerun: 122,145 → 126,868 delta rows; the 9
   newly-resolved branch outages turn out to behave like generator outages
   for Pg (two of them, `outage::7`/`outage::9`, average 0.113 pu |ΔPg| —
   comparable to genuine generator-outage redispatch, since islanding a
   generation-carrying bus removes that generation from the surviving
   network's balance, same mechanism as a generator trip), pulling the
   overall branch-outage mean |ΔPg| up 2.7x and narrowing the
   branch-vs-generator contrast from 18x to ~6.8x.

   **Bonus, unplanned**: this is the exact same bug behind `case300`'s
   `outage::243` unphysical `min_vm_pu = -0.58` result (a singular-system
   Newton-Raphson artifact, not a data bug) — now resolved too
   (`+0.94` pu, physically normal; case300's max severity score dropped
   from a spurious 9.60 to a real 2.42 at `outage::137`).

   **Net effect on the Julia-vs-Python count**: still 225/240 (93.8%) — one
   disagreement resolved (`outage::176`, now agrees), one newly exposed
   (`outage::133`, previously "agreed" with Python only because Julia
   failed to converge; now that it genuinely converges it turns out to be
   the same boundary-tolerance pattern as the other 13). The *count* didn't
   change, but the *composition* did: all 15 remaining disagreements are
   now explained by the single classification-tolerance mechanism above,
   zero by non-convergence. Updated artifacts:
   `results/presentable/n1_screening/n1_screening_report.html`,
   `results/presentable/n1_screening/n1_variable_impact_report.html`.
10. **Archived 2026-07-25** (closed 2026-07-12): C2, the deviation-
    minimization formulation-equivalence test (signed-slack `x = target +
    dx_plus - dx_minus` objective, confirming own model's feasible region
    matches PowerModels' at the solution point). This whole diagnostic
    technique -- across all its later generations (PM-like constrained/
    equality-only variants, and the final Q1-Q10 ladder's linking-LP
    deviation reconstruction) -- was archived per an explicit decision that
    it has no further/future application; the final ladder's conclusions
    remain live in `docs/formulations/ac_opf.md`/`comments.md`. Full
    verbatim writeup of this original C2 test preserved in
    `archive/docs/superseded_status_doc_sections.md`.
11. **D2 closed (2026-07-12), rerun same day after the island-handling
    fix**: post-contingency variable-impact analysis
    (`julia/own_models/scripts/14_run_n1_variable_impact_analysis.jl`).
    Captures full per-element post-contingency state (every generator's
    Pg/Qg, every bus's Vm/Va, every branch's loading %) for all 240
    `case118` N-1 contingencies and compares against the pre-contingency
    AC-OPF solution — 126,868 (contingency × element × variable) delta rows
    (up from 122,145 pre-fix, once the 9 previously-non-convergent branch
    outages resolve — see item 9), reusing `n1_screening.jl`'s internals via
    module-qualified access rather than duplicating logic. Report:
    `results/presentable/n1_screening/n1_variable_impact_report.html`.

    Key findings: **branch loading is where the real severity lives** —
    up to 285 percentage points of change on a single branch (`outage::104`
    pushes branch 106 from 100% to 385% loaded), vs. sub-1-pu changes for
    every other variable; voltage magnitude barely moves at all (max 0.09
    pu) — `case118` is thermally constrained under N-1, not
    voltage-constrained. **Pg's behavior is mostly contingency-type
    dependent, and the physics explains why**: a power flow treats Pg as a
    fixed PV-bus input (only the slack bus's Pg is actually solved for), so
    a *typical* branch outage barely moves Pg anywhere (median |ΔPg|=0, mean
    0.0009 pu); generator outages are different by construction —
    `apply_proportional_reserve_balancing` explicitly redistributes the
    lost output across remaining generators' headroom before the power
    flow runs, so mean |ΔPg| is 18x a typical branch outage's (0.016 pu)
    even though the median stays 0 (redispatch concentrates on whichever
    generators have room, leaves most of the other ~50 generators
    untouched). **Exception, found in the 2026-07-12 rerun**: the 9
    previously-non-convergent branch outages (item 9) behave like generator
    outages for Pg, not branch outages — islanding a generation-carrying bus
    removes that generation from the surviving network, the same mechanism
    as a generator trip. Including them pulls the overall branch-outage mean
    |ΔPg| up to 0.0023 pu (2.7x the typical-only figure), narrowing the
    branch-vs-generator contrast from 18x to ~6.8x once they're in the
    dataset. **Generator outages cause branch overloads too, sometimes
    nearly as severe as a direct branch trip** — `outage::gen::45` pushes
    branch 119 from 27% to 123% loaded (96.7pp), comparable to several of
    `outage::104`'s branch-outage cascades — worth remembering before
    assuming branch outages are inherently the more dangerous contingency
    type.
12. **Archived 2026-07-25** (closed 2026-07-12, corrected 2026-07-13): the
    AC-OPF optimality-gap / OBBT bound-tightening study (SOC relaxation and
    PowerModels' `solve_obbt_opf!` used as certified lower bounds on
    AC-OPF's true global optimum). Its own script was already gone from
    disk; full verbatim writeup (gap-by-case table, the `5_pjm`/`30_ieee`
    findings, the OBBT-infeasible-on-large-cases finding) preserved in
    `archive/docs/superseded_status_doc_sections.md`.
13. **Closed (2026-07-12)**: full-scenario Julia AC-OPF warm-start ablation
    (`julia/own_models/scripts/16_run_ac_opf_full_scenario_warm_start_ablation.jl`).
    Widens item 12's warm-start ablation from 8 base cases + 2 stressed
    scenarios to the *entire* scenario grid the main compact validation
    sweep uses (09's `compact_scenarios`: 13 scenario types × 8 cases, 313
    distinct scenarios, `include_no_reference=true` to match the full
    historical dataset). Reuses `compact_scenarios` and its ~20 dependency
    functions directly by `include_string`-ing
    `09_run_compact_opf_validation_sweeps.jl`'s source with its trailing
    unconditional `main()` call stripped out, rather than reimplementing 13
    scenario generators — guarantees identical scenario definitions, zero
    drift risk. Checkpointed (one CSV row appended per (case, scenario,
    source) solve, resumable on rerun). 1,252 solves total (313 × 4
    solvers).

    **Mid-run correction**: the original `max_iter=3000` (the repo's usual
    pin) let `1888_rte_load_x1.4`'s own-model `case`-start mode run 3.27h
    without converging. Killed, purged the 4 rows that had hit
    `ITERATION_LIMIT`, dropped `max_iter` to 1000 for this script
    specifically (every scenario that converges normally in this dataset
    needs well under 600 iterations even under stress), resumed from
    checkpoint — user explicitly chose this over accepting the open-ended
    runtime or dropping `6470_rte` from scope. Worst single scenario after
    the fix: ~34 min (`6470_rte_branch_2170_out`), fully bounded.

    **Findings**: results in
    `results/presentable/ac_opf_warm_start_experiment/full_scenario_ablation_report.html`.
    (1) **Convergence is 100% scenario-determined, not solver-determined**
    — all 4 solvers succeed/fail on the exact same 313/313 scenarios; the
    31% non-convergence rate is by design (`tight_voltage_limits` 29%,
    `branch_congestion` 44%, `branch_outage` 46% — deliberately adversarial
    scenario types). (2) **The `6470_rte`/`1888_rte` case-start penalty
    finding from the original 2-case pilot is now confirmed as a real,
    general property, not a single-scenario artifact**: `6470_rte` averages
    a 3.01× case-start penalty across 27 converged scenarios (median
    3.24×, max 4.64×), `1888_rte` averages 1.59× across 24 (max 4.45×, on
    `angle_20deg` — not the load-scaling scenario one might expect), while
    all 6 smaller/medium cases stay at ~1.0× (no penalty) across every
    scenario type. (3) Several `6470_rte` scenarios (the "×1.0"/non-binding
    variant of each parameter sweep) land on the exact same 561 case-start
    iterations as the base case — confirms this is a stable property of
    the network's conditioning, not scenario-generation noise. (4)
    `1888_rte_load_x1.4` (reactive_power_stress) hits the iteration cap on
    *all four* solvers, not just own model's — a genuinely hard/near-
    infeasible operating point, not a warm-start artifact (PowerModels
    fails much faster, ~99s vs. Julia's 1,100-2,150s, but still fails).

---

## Document index (everything this file summarizes or points to)

**Model development**: `docs/python_own_models_fundamentals.md`,
`docs/julia_own_models_fundamentals.md`, `docs/implementation_ownership_audit.md`,
`docs/julia_own_models_implementation_audit.md`, `archive/docs/pv_pq_switching.md`
(archived -- own Newton-Raphson PF retired from active use, §1),
`docs/notation.md` (math symbol → Python/Julia code identifier mapping,
including how to pull constraint duals/shadow prices in each language)

**OPF validation**: `docs/python_ac_opf_validation.md`,
`archive/docs/ac_pf_q_limit_cross_language_validation.md` (archived), `docs/implementation_status.md`,
`docs/notes/solver_tolerances.md` (verified Ipopt/HiGHS/PowerModels tolerances
and the binding-constraint classification mismatch root cause),
`results/presentable/opf_validation_findings/`

**Security / N-1**: `docs/formulations/security_constraints.md`,
`results/own_python/security/`, `results/own_julia/security/`

**Load-flow experiments**: `results/presentable/pypowsybl_lf_replacement_sweep/`
(esp. `presentation_findings/pypowsybl_lf_convergence_findings_for_presentation.md`
and `baseline_problem_case_analysis/advanced_configuration_plan.md`),
`docs/powermodels_lf_replacement_sweep.md`,
`results/presentable/powermodels_lf_replacement_sweep/`

**Formulation background**: `docs/formulations/pf_vs_opf_taxonomy.md`,
`docs/formulations/validation_plan.md`
