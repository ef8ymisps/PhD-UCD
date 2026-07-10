# Implementation & Validation Status

A record of what's built, what's validated, and what's still open, across the
Python and Julia "own model" DC/AC OPF implementations and the security
analysis layer built on top of them. Written after a work session covering:
Julia-vs-PowerModels validation metrics, binding-constraint comparison,
slack-modelling policy, and N-1 security screening on case118.

---

## 1. AC OPF

### Python (`python/own_models/src/opf/ac/`)
- **Formulation**: nonconvex NLP via Pyomo, solved with Ipopt.
- **Files**: `data.py`, `variables.py`, `constraints.py`, `objective.py`,
  `model.py` (`build_ac_opf_pyomo_model`), `initialization.py` (warm-start
  sources), `results.py` (`ACOPFResult`), `solve.py` (`solve_ac_opf_pyomo`).
- **Status**: implemented and validated (see §3).
- **Solve-iteration count**: Ipopt's Pyomo result object does not expose
  iteration count as a field (verified empirically) — captured instead by
  redirecting solver stdout and regex-parsing `Number of Iterations....: NN`
  (`python/own_models/src/opf/solver_iterations.py::solve_with_iteration_capture`).
  Same helper handles HiGHS's `Simplex iterations: NN` line for DC.
- **Known issue found & fixed this session**: `run_python_ac_opf_against_powermodels`
  (`compare_ac_with_powermodels.py`) was solving with defaults
  (`cost_power_units="mw"`, `voltage_bounds=(0.95, 1.05)`) instead of matching
  PowerModels' convention (`"pu"`, case-native bounds), inflating the reported
  objective by ~100x even though dispatch itself matched. Fixed by passing
  `cost_power_units="pu", voltage_bounds=None` explicitly in that comparison
  path.

### Julia (`julia/own_models/src/opf/ac/`)
- **Formulation**: JuMP, solved with Ipopt.
- **Files**: `data.jl`, `variables.jl`, `constraints.jl`, `objective.jl`,
  `model.jl` (`build_ac_opf_model`), `solve.jl` (`solve_ac_opf_own`),
  `results.jl` (`ACOPFResult` struct).
- **Status**: implemented and validated at scale, up to the 6470-bus case.
- **Solve-iteration count**: unlike Python, JuMP exposes this generically —
  `barrier_iterations(model)` (Ipopt) works directly, no log-scraping needed.
  Added as `ACOPFResult.solve_iterations` (new field this session, wired via
  `_safe_barrier_iterations` in `results.jl`).

---

## 2. DC OPF

### Python (`python/own_models/src/opf/dc/`)
- **Formulation**: LP via Pyomo, solved with HiGHS.
- **Files**: `data.py`, `variables.py`, `constraints.py`, `objective.py`,
  `model.py`, `results.py` (`DCOPFResult`, now carries `solve_iterations`),
  `solve.py` (`solve_dc_opf_pyomo`), `validation.py`.
- **Status**: implemented and validated.
- **Solve-iteration count**: HiGHS's Pyomo interface (`"highs"`) does not
  populate a `Statistics` field either — same stdout-capture approach as AC,
  parsing `Simplex iterations: NN` from the HiGHS console log.

### Julia (`julia/own_models/src/opf/dc/`)
- **Formulation**: JuMP, solved with HiGHS.
- **Files**: `data.jl`, `variables.jl`, `constraints.jl`, `objective.jl`,
  `model.jl`, `solve.jl` (`solve_dc_opf_own`), `results.jl` (`DCOPFResult`,
  now carries `solve_iterations` via `_safe_simplex_iterations`).
- **Status**: implemented and validated at scale.

---

## 3. Validation progress (own model vs. PowerModels.jl)

### Methodology
Per case/scenario: objective abs/rel diff; per-variable mean/max/std absolute
diff (Pg, Qg, Vm, Va, branch Pf/Qf/Pt/Qt, branch loading %); binding-constraint
agreement (`both`/`only_own`/`only_pm`/`neither` per pmin/pmax/qmin/qmax/
vmin/vmax/thermal/angle); a `mismatch_reason` classifier
(`numerical_noise`/`modelling_difference`/`unclear`) for `only_own`/`only_pm`
disagreements; a branch-flow sign/magnitude convention check for branches near
their rating.

### Where it lives
- **Python**: `python/own_models/src/analysis/violations.py` (own-model
  element rows + `classify_*_rows` + `binding_set_comparison`),
  `compare_ac_with_powermodels.py` / `compare_with_powermodels.py` (own-vs-PM
  comparison + summary aggregation), driven by
  `python/own_models/scripts/18_plot_opf_variable_diffs.py`, which also
  persists long-format per-element CSVs to
  `results/own_python/opf_validation_sweeps/detail_rows/`.
- **Julia**: `julia/own_models/src/analysis/opf_solution_inspection.jl` (same
  role as Python's `violations.py` + `compare_*_with_powermodels.py`
  combined), driven by
  `julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl`
  (`--write-detail-rows` flag persists the same long-format CSVs to
  `results/own_julia/opf_validation_sweeps/detail_rows/`). Includes a live
  progress spinner (`ProgressMeter.jl`) added this session.

### Coverage
- **Python**: 3 cases (case5, case14, case30) × 5 scenarios (base, relaxed,
  branch_congested, generator_limited, load_scaled), via pre-exported
  PowerModels JSON references in `results/reference/powermodels/{ac,dc}_opf/`.
  Limited to this scope because Python has no PowerModels.jl access — it can
  only validate against pre-exported references, not solve PowerModels
  in-process.
- **Julia**: 8 PGLib cases (case5_pjm → case6470_rte), full ~40-scenario
  stress-sweep family on **all 8 cases**, including case6470_rte (initially
  scoped to base-only given runtime, later completed in full after raising
  the Ipopt iteration limit — see "Resolved this session" below). 607 rows
  total in
  `results/own_julia/opf_validation_sweeps/compact_opf_validation_summary.csv`.
  Julia solves PowerModels fresh in-process per scenario, so it carries the
  validation weight for anything beyond the small 3-case Python set.

### Results
- **Variable agreement**: solver-tolerance precision (~1e-8 to 1e-17 median
  absolute diff) across Pg/Qg/Vm/Va/flows/loading, consistent from 5 buses to
  6470 buses.
- **Binding-set agreement**: 52,221 / 52,253 checks agree (99.94%) across all
  constraint types.
- **3 scenarios (of 532) show genuine dispatch divergence** at generator
  bounds — `case1888_rte`'s `load_scaling` and `reactive_power_stress`
  scenarios, several generators swapping between pmin/pmax between own model
  and PowerModels. Checked whether this was cost-tied-generator degeneracy
  (it isn't — costs differ, 2027–11963 $/MWh) — consistent instead with
  already-documented AC-OPF non-uniqueness under reactive stress
  (`docs/python_ac_opf_validation.md`'s ~0.304 pu Qg finding). Objective still
  agrees closely in these scenarios (~0.05% relative, vs. ~1e-9 baseline).
- **Engineered degeneracy test** (two same-bus generators, tied cost,
  generous headroom): both DC (HiGHS/simplex) and AC (Ipopt/interior-point)
  converged *identically* between own model and PowerModels despite genuine
  mathematical degeneracy — solver tie-breaking is deterministic enough that
  independently-built formulations land on the same point. The
  `mismatch_reason` classifier remains correctly implemented but rarely
  exercised in practice for this reason.
- **Known open issue — NOT yet addressed**: default Ipopt `max_iter=500` is
  insufficient for large/stressed systems. `case6470_rte`'s base AC-OPF hit
  `ITERATION_LIMIT` at 500, converged cleanly at `--max-iter=3000` (564
  iterations needed). Same pattern recurred on `case1888_rte`'s
  `reactive_generation_limits` scenario. **This is the explicit next task —
  raising the default iteration limit — deferred until after this
  documentation pass.** [**Correction, 2026-07-10**: "500 (Ipopt's own
  default)" is factually wrong for the currently-installed Ipopt 3.14.19 —
  verified directly via `ipopt --print_options_documentation`, its real
  default `max_iter` is 3000. The 500 figure was most likely an explicit
  (mistaken) setting in an earlier version of the code, not Ipopt's native
  behavior. See `docs/notes/solver_tolerances.md`.]
- **Solve-time**: own model ~6x slower than PowerModels on the largest case
  tested (317.8s vs 51.7s, case6470_rte AC base) — not a correctness issue,
  worth watching if scaling further.

### Reporting artifacts
`python/own_models/scripts/18_plot_opf_variable_diffs.py` produces, in
`results/presentable/opf_validation_findings/`: per-element diff distribution
box plots (`{python,julia}_{ac,dc}_element_diff_distributions.png`), solver
iteration comparison charts, `variable_diff_summary_table.csv` (case ×
scenario × variable → mean/max/std), `binding_set_comparison.csv`,
`binding_mismatch_elements.csv` (drillable list of exactly which elements
disagreed — currently only the 3 scenarios above), and
`{python,julia}_ac_branch_flow_convention_check.csv`.

---

## 4. Load-flow security analysis (N-1)

### Slack-modelling policy
Documented in `docs/formulations/security_constraints.md`: single fixed slack
bus for OPF, power flow, and branch-outage contingencies (100% imbalance
absorbed by the slack generator); for generator-outage contingencies, static
proportional-reserve redispatch (lost `Pg` split across surviving generators
in proportion to `pmax - base_pg` headroom) plus slack reassignment if the
outaged unit was the slack generator itself.

### Tool decision
- **Python**: pypowsybl + OpenLoadFlow, exclusively. The own model's role is
  limited to producing the base-case AC-OPF dispatch; the own
  Newton-Raphson PF solver (`solve_ac_power_flow_newton`) is **not used** for
  N-1 (retired from this role — it lacked PV→PQ switching, which caused a
  large, since-resolved discrepancy, see below).
- **Julia**: PowerModels.jl's native `compute_ac_pf` (Newton-Raphson), via
  the existing `run_powermodels_ac_pf_compute` wrapper
  (`ac_pf_compute_validation.jl`) — not the own model's PF solver, not Ipopt.

### Where it lives
- **Python**: `python/own_models/src/analysis/pypowsybl_security_validation.py`
  — `run_pypowsybl_n1_screening` (branches, via pypowsybl's batched
  `security.create_analysis()`) and `run_pypowsybl_generator_n1_with_redispatch`
  (generators, per-contingency loop since each outage needs a differently
  redispatched network state). Driver: `python/own_models/scripts/19_run_n1_case118.py`.
- **Julia**: new file `julia/own_models/src/analysis/n1_screening.jl` —
  contingency generation, proportional-reserve redispatch (ported from
  Python's `balancing_policy.py`), and `run_n1_screening` /
  `run_n1_branch_screening` / `run_n1_generator_screening`, calling
  `run_powermodels_ac_pf_compute` per contingency. Driver:
  `julia/own_models/scripts/10_run_n1_case118.jl`.
- Output schema is aligned column-for-column across both languages (and
  pypowsybl) for direct comparison: `case_name`, `contingency_id`,
  `element_type`, `outaged_element_id`, `status`/`pypowsybl_status`,
  `converged`, `max_loading_pct`, `n_overloaded_branches`,
  `total_overload_pct`, `total_overload_pct_sq`, `min_vm_pu`, `max_vm_pu`,
  `n_voltage_violations`, `total_voltage_violation_pu`,
  `total_voltage_violation_pu_sq`, `thermal_violation`, `voltage_violation`,
  `secure`, `severity_score`.

### Bugs found & fixed this session
1. **Contingency ID collision**: branch and generator IDs are independently
   numbered (both have e.g. id `"1"`), so `outage::1` was ambiguous —
   pypowsybl's `post_contingency_results` dict silently kept only one,
   dropping all 54 generator contingencies from the first run. Fixed by
   prefixing generator contingency IDs (`outage::gen::{id}`).
2. **Redispatch not actually applied**: `transfer_ac_opf_dispatch` (reused
   from the base-case validation path) reads `ac_opf_result.pg.get(gen.id,
   gen.pg0)` — since `ac_opf_result.pg` always contains an entry for
   surviving generators, it always won over the redispatched `pg0` computed
   by `apply_balancing_policy`, silently ignoring the redispatch. Fixed with a
   new `transfer_case_dispatch(network, case)` that reads `case`'s own
   `pg0`/`qg0` directly.
3. **Follow-on bug from the fix above**: the new `transfer_case_dispatch`
   read bus voltage targets from `case.buses[i].vm0` — but the raw case
   (before `case_with_base_dispatch`) only has file-native starting-guess
   voltages, not the OPF-solved ones, causing near-uniform spurious
   violations across every contingency. Fixed by calling
   `case_with_base_dispatch(case, ac_opf_result)` before generating
   contingencies, matching the pattern `post_contingency.py` already used.

### Results on case118 (186 branches + 54 generators = 240 contingencies)

| Tool | Branch secure | Branch thermal viol. | Branch voltage viol. | Gen secure | Gen thermal viol. | Gen voltage viol. |
|---|---|---|---|---|---|---|
| pypowsybl / OpenLoadFlow | 16/186 | 136 | 96 | 7/54 | 43 | 20 |
| Julia PowerModels.compute_ac_pf | 24/186 | 131 | 73 | 6/54 | 44 | 19 |
| ~~Own Python Newton (retired)~~ | 0/186 | ~0 (mislabeled) | 72 | 40/54 | 13 | 12 |

Two independent, established tools (OpenLoadFlow and PowerModels.jl) agree
closely with each other on both branch and generator contingencies — this is
a real, corroborated finding: **case118 in its stock PGLib form is genuinely
N-1 fragile** (only ~13–15% of contingencies are secure), not an artifact of
either tool. The now-retired own-model Newton-Raphson path was the outlier,
substantially under-counting thermal violations (13 vs. ~43-44 for
generators) — most likely due to its lack of PV→PQ switching during the PF
solve, a limitation that motivated retiring it from the N-1 role entirely
rather than patching it further.

Stale output from the retired own-model path
(`results/own_python/security/n1_branch_case118_*.csv`,
`n1_generator_case118_*.csv`) is still on disk for reference but should not be
treated as authoritative — `n1_pypowsybl_case118_contingencies.csv` and
`n1_julia_compute_pf_case118_contingencies.csv` are the current results.

---

## 5. Severity metrics

Built directly into the N-1 screening output (§4) — no separate task, since
task 8 was substantially covered by the per-contingency metrics needed for
task 7's table anyway:
- `max_loading_pct` — worst single branch loading.
- `n_overloaded_branches`, `total_overload_pct`, `total_overload_pct_sq` —
  count/sum/sum-of-squares of branch loading percentage above 100%.
- `min_vm_pu`, `max_vm_pu` — voltage extremes.
- `n_voltage_violations`, `total_voltage_violation_pu`,
  `total_voltage_violation_pu_sq` — count/sum/sum-of-squares of bus voltage
  deviation beyond `vmin`/`vmax`.
- `thermal_violation`, `voltage_violation`, `secure` — boolean flags.
- `severity_score` — `max(worst overload fraction, total voltage violation)`,
  a single scalar for ranking contingencies by severity.

Generator/slack-specific severity (task 9 — dropped per explicit instruction,
not pursued).

---

## Resolved this session

- **Ipopt `max_iter` raised 500 → 3000** in all three defaults
  (`python/own_models/src/analysis/ac_opf_sweeps.py`,
  `julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl`,
  `julia/own_models/scripts/08_run_ac_opf_powermodels_references.jl`).
  **case6470_rte's full ~40-scenario-family stress sweep (80 rows: AC+DC ×
  all families) now completes** — previously only base-case coverage was
  attempted due to the iteration limit and runtime. Full run took ~6 hours.
  Result: 100% `outcome_match` (own vs. PowerModels agree on solved/failed
  status for all 80 scenarios). Residual edge cases at the new limit: 2
  `ITERATION_LIMIT` and 3 `OTHER_ERROR` rows, all matched by an identical or
  consistent failure mode on the PowerModels side (e.g. Ipopt hitting the
  iteration limit while trying to certify a `LOCALLY_INFEASIBLE` scenario,
  or both tools erroring identically on the same DC scenario) — not own-model
  bugs, just genuinely hard/borderline stress scenarios where the reference
  tool struggles too.
- Julia's `compact_opf_validation_summary.csv` full stress sweep is now
  **complete for all 8 cases**, case5_pjm through case6470_rte, no scoping
  exceptions remaining.

## Open items (not yet done)

1. Own-model Python Newton-Raphson PF (`solve_ac_power_flow_newton`,
   `post_contingency.py`) is retired from the N-1 role but the code/scripts
   (08, 09, `post_contingency.py`) still exist and still work for smaller
   cases (case5/14/30) — not deleted, just no longer the source of truth for
   security analysis.
2. N-1 security screening has only been run on case118 so far — not
   extended to other case sizes.
