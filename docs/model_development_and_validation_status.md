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
  `model.py` (`build_ac_opf_pyomo_model`), `initialization.py` (warm-start
  sources), `results.py` (`ACOPFResult`), `solve.py` (`solve_ac_opf_pyomo`).
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
  `model.jl` (`build_ac_opf_model`), `solve.jl` (`solve_ac_opf_own`),
  `results.jl` (`ACOPFResult` struct, `_safe_barrier_iterations`).
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
- Full spec: `docs/pv_pq_switching.md`. Validation detail:
  `docs/ac_pf_q_limit_cross_language_validation.md`.

### Artifact index (§1)

- `docs/python_own_models_fundamentals.md`, `docs/julia_own_models_fundamentals.md`
- `docs/implementation_ownership_audit.md`, `docs/julia_own_models_implementation_audit.md`
- `docs/pv_pq_switching.md`, `docs/ac_pf_q_limit_cross_language_validation.md`

---

## 2. OPF Validation (own model vs. PowerModels.jl)

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
- **Solver iterations, own vs. PowerModels (AC, backfilled this session)**:
  PowerModels needs *more* Ipopt iterations than the own model on most
  mid-size cases, but *fewer* on the two largest (1888_rte: 448 vs. 405;
  6470_rte: 265 vs. 559 own). Real signal worth a closer look, not a
  plotting artifact — flagged as an open item (§6).
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

- `docs/powermodels_lf_replacement_sweep.md` (and its 4 near-duplicate
  siblings `_api.md`, `_base_large.md`, `_mixed_full.md`, `_sad.md` — same
  narrative, different case-set/run numbers; worth consolidating into one
  doc with a comparison table rather than 5 separate files).
- `results/presentable/powermodels_lf_replacement_sweep/powermodels_ac_pf_sweep_rows.csv`,
  `powermodels_dc_pf_sweep_rows.csv`.
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

- **PowerModels' AC solver-iteration comparison currently AC-only.** Julia
  DC (`compute_ac_pf`'s sibling code path via `optimize_model!`) could
  capture PowerModels' `simplex_iterations` the same way AC captures
  `barrier_iterations` (§1.2) — the mechanism already exists in the
  codebase (`_safe_simplex_iterations`, confirmed generic MOI-based, same
  pattern as the AC backfill done this session) and just hasn't been wired
  into the DC branch of `09_run_compact_opf_validation_sweeps.jl` yet.
- **Python never captures PowerModels' solver-iteration count**, for either
  AC or DC — Python's comparison reads pre-exported static reference JSON
  rather than solving PowerModels in-process, and that JSON doesn't carry
  iteration metadata. Would require a Julia-side change to the reference
  export script (`08_run_ac_opf_powermodels_references.jl`) to include it.
- **`compute_ac_pf`'s own iteration count is unavailable** in the LF
  replacement-note sweep (§4.2) — a different limitation from the two above,
  specific to bypassing the JuMP layer.
- **`python_dc` has no large-scale objective/outcome/solve-time-vs-
  PowerModels comparison plot set** — only the small 3-case × 5-scenario
  comparison (used for §2's element-level tables) exists; there's no DC
  equivalent of the 607-row Julia sweep or a `compact_dc_opf_sweeps_with_
  powermodels_comparison.csv` for Python.
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
- **The 5 `docs/powermodels_lf_replacement_sweep*.md` files are near-
  duplicates** of each other (same narrative template, different case-
  set/run numbers) and could be consolidated into one document with a
  results-comparison table.
- **`docs/notes/pypowsybl_data_format.md`** is currently an empty stub; if
  network-table/result-structure notes are written up, that's the natural
  home for them (distinct from this file's experiment-level summary).

---

## 7. Next steps / open decisions

Living checklist — prune items once done, add new ones as they come up,
rather than letting this grow unbounded.

1. Decide whether the PowerModels-DC iteration-count backfill (§6, item 1)
   is worth doing now, given the mechanism is already proven for AC.
2. Decide whether Python needs its own PowerModels-iteration capture, or
   whether that's low-value given Python's validation scope is already
   limited to 3 small cases.
3. Decide whether `python_dc` needs objective/outcome/solve-time plots at
   Julia-DC scale, or whether the existing small-scale comparison is
   sufficient for Python's intended scope.
4. Decide whether the 1888_rte iteration-count finding (§2 — PowerModels
   needs *fewer* iterations than the own model on the two largest cases,
   opposite of the mid-size trend) warrants its own investigation.
5. Decide whether operational-control metadata for PGLib cases is worth
   building, or whether native-network demonstrations (§4.1) are sufficient
   evidence for the thesis/presentation purpose.
6. Consolidate the 5 near-duplicate `powermodels_lf_replacement_sweep*.md`
   files (§6) when there's a natural opportunity.
7. Extend N-1 security screening beyond case118 if broader coverage is
   needed.

---

## Document index (everything this file summarizes or points to)

**Model development**: `docs/python_own_models_fundamentals.md`,
`docs/julia_own_models_fundamentals.md`, `docs/implementation_ownership_audit.md`,
`docs/julia_own_models_implementation_audit.md`, `docs/pv_pq_switching.md`

**OPF validation**: `docs/python_ac_opf_validation.md`,
`docs/ac_pf_q_limit_cross_language_validation.md`, `docs/implementation_status.md`,
`results/presentable/opf_validation_findings/`

**Security / N-1**: `docs/formulations/security_constraints.md`,
`results/own_python/security/`, `results/own_julia/security/`

**Load-flow experiments**: `results/presentable/pypowsybl_lf_replacement_sweep/`
(esp. `presentation_findings/pypowsybl_lf_convergence_findings_for_presentation.md`
and `baseline_problem_case_analysis/advanced_configuration_plan.md`),
`docs/powermodels_lf_replacement_sweep.md` (+ 4 near-duplicate siblings),
`results/presentable/powermodels_lf_replacement_sweep/`

**Formulation background**: `docs/formulations/pf_vs_opf_taxonomy.md`,
`docs/formulations/validation_plan.md`
