# Current Open Tasks

Date: 2026-07-17

This note tracks the remaining OPF validation tasks before the next meeting.

## 2026-07-24 update: bus-injection formulation deleted, superseding prior entries below

Per the 16/23 July meeting outcomes, the bus-injection (implicit
Ybus-substitution) AC-OPF formulation was **hard-deleted** in both Julia and
Python (not archived), and the arc-based/PowerModels-style formulation
(previously called "PM-like"/"pm_like_zero" below) was renamed to the plain
model names (`model.jl`/`build_ac_opf_model`/`solve_ac_opf_own`,
`model.py`/`build_ac_opf_pyomo_model`/`solve_ac_opf_pyomo`) as the sole
AC-OPF model going forward. This directly **supersedes** two specific
decisions recorded further down in this file:

- The "Keep Python formulation default as `bus_injection`; do not switch
  Python to branch-flow" line (§ "Remaining Before Python Flat-Start Sweep")
  — superseded; there is no `bus_injection` option left in either language.
- The "Python default should stay `formulation = "bus_injection"`" /
  "only promote PM-like to the default if it has equal-or-better success
  rate... Keep the current model available for reproducibility" replacement
  rule (§4, Julia AC OPF vs PowerModels Speed Gap) — superseded; the
  replacement already happened and bus-injection is gone entirely, not kept
  alongside for reproducibility.

The historical entries below are left as-is (this is a dated journal, not a
living spec) — read them as describing the state of the repo before this
change. See `docs/formulations/ac_opf.md` for the current (sole) formulation,
and `docs/model_development_and_validation_status.md` §1.1/§1.2/§2 for the
updated file/architecture references.

Meeting-ready Julia conclusion note:

- `docs/notes/julia_ac_opf_validation_conclusion_for_meeting.md`

Presentation table source:

- `results/own_vs_powermodels/_organized_for_meeting`

## Near-Term Coding Tasks Added After 16 July Meeting

These are the coding tasks to prioritize now. The goal is to separate four
possible explanations for the objective/binding mismatches:

- missing or differently handled bus-level terms;
- inequality constraints imposed differently;
- equality-feasible but different local optima;
- solver convergence/status interpretation.

### A. Bus-Shunt Toggle Experiment

Status: implemented for the first objective-mismatch target set.

Run the same objective-mismatch scenarios under three Julia model families:

- PowerModels ACP;
- own current Julia AC OPF;
- own PM-like Julia AC OPF.

For each model family, run two data variants:

- `bus_shunts_on`: original data;
- `bus_shunts_off`: explicit bus shunt conductance/susceptance set to zero
  (`gs = 0`, `bs = 0` at buses/shunts), while leaving branch charging unchanged.

Purpose:

- quantify whether bus shunts materially move the objective, `Pg/Qg`, `Vm/Va`,
  branch flows, and binding constraints;
- check whether the own current and PM-like formulations respond to shunt
  removal in the same direction/magnitude as PowerModels;
- avoid arguing abstractly about whether the shunts are present by producing a
  direct on/off sensitivity table.

Expected outputs:

- `results/own_vs_powermodels/shunt_toggle_solution_summary_julia.csv`;
- `results/own_vs_powermodels/shunt_toggle_variable_differences_julia.csv`;
- `results/own_vs_powermodels/shunt_toggle_binding_differences_julia.csv`;
- `results/own_vs_powermodels/shunt_toggle_binding_summary_julia.csv`;
- optional per-scenario folders only if the summary shows material movement.

Minimum columns:

- `case`, `scenario`, `model_family`, `bus_shunt_mode`;
- status, objective, solve time, iterations;
- objective change vs original mode;
- max absolute changes in `Pg`, `Qg`, `Vm`, `Va`, `Pf`, `Qf`;
- binding counts by family and binding mismatches vs PowerModels.

Implementation:

- script:
  `julia/own_models/scripts/33_run_bus_shunt_toggle_experiment.jl`;
- first run scope:
  `1888_rte/load_x1.2_reactive_stress`,
  `1888_rte/load_x0.8`, and `1888_rte/qg_limits_x0.6`;
- model families:
  PowerModels ACP, own current Julia AC OPF, and own PM-like Julia AC OPF;
- shunt modes:
  original explicit bus shunts vs explicit bus shunts zeroed.

First-run finding:

- with bus shunts on, all three model families solve all three scenarios;
- with bus shunts off, all three model families return `LOCALLY_INFEASIBLE`
  for all three scenarios;
- therefore these `1888_rte` scenarios are highly sensitive to the explicit
  bus shunt susceptance terms (`total_bs = -31.18` in the tested data);
- because the shunts-off runs are infeasible, there is no accepted feasible
  off-vs-on solution movement table yet. The empty variable-difference table is
  expected for this first run.

Parallel-line follow-up:

- status: completed;
- audit table:
  `results/own_vs_powermodels/_organized_for_meeting/05_shunt_and_formulation/julia_parallel_branch_ybus_audit.csv`;
- conclusion: parallel branch admittances are summed in `Ybus` for nodal
  balance, while thermal/angle/branch-flow constraints remain per physical
  branch row. For `1888_rte` base, 2,531 branch rows produce 2,531 thermal
  constraint instances, even though 197 bus-pair groups contain parallel
  branches.

### B. Endpoint Load-Flow Inequality Validation

Status: corrected to use PowerModels' own Newton AC PF implementation.

Existing script:

- `julia/own_models/scripts/31_run_opf_endpoint_loadflow_inequality_comparison.jl`

Existing outputs:

- `results/own_vs_powermodels/opf_endpoint_loadflow_validation_julia.csv`;
- `results/own_vs_powermodels/opf_endpoint_pf_variable_comparison_julia.csv`;
- `results/own_vs_powermodels/opf_endpoint_pf_inequality_levels_julia.csv`;
- `results/own_vs_powermodels/opf_endpoint_pf_inequality_summary_julia.csv`.

Interpretation to keep:

- this diagnostic writes each OPF endpoint into a PowerModels data dictionary:
  bus `vm/va`, generator `pg/qg`, generator `vg`, and corresponding
  `*_start` fields;
- it then calls
  `PowerModels.compute_ac_pf(data; method=:newton, flat_start=false,
  ftol=1e-8, xtol=1e-8, iterations=100)`;
- it does this twice per scenario: once warm-started from the own OPF solution,
  and once warm-started from the PowerModels OPF solution;
- after PF convergence, branch flows are recomputed with
  `PowerModels.calc_branch_flow_ac`;
- after PF convergence, compare inequality levels/binding margins at the two
  PF endpoints;
- if independently re-derived PF inequality levels remain feasible, then the
  cheaper objective is probably not explained by a simple post-OPF physical
  infeasibility in thermal/voltage/reactive-generator/angle/PQ-bus voltage
  bounds.

Important caveat:

- `Pg` limits are only partly independent in any AC PF diagnostic. Non-slack
  generator active powers are specified dispatch inputs; slack-bus active
  power may be rebalanced by `PowerModels.compute_ac_pf`.
- Voltage magnitude at PV/slack buses is pinned by the PF setpoint convention.
  The independently re-solved voltage checks are the exact `PQ` bus rows.
- Generator-level `Qg` rows now come from PowerModels' own PF result rather
  than our custom PF allocation. This is stronger evidence than the old
  repository-PF version, but it should still be interpreted as PF reactive
  output under the PowerModels PF convention, not as a new OPF inequality solve.
- The refreshed endpoint-PF summaries now expose this distinction with
  `independently_rederived_after_pf`, `pf_check_independence`, and separate VM
  bus-class summaries.

Corrected rerun finding:

- all ten endpoint PF runs converge with `pf_engine =
  PowerModels.compute_ac_pf`, `pf_method = newton`, and `flat_start=false`;
- branch flows are recalculated for all runs;
- each tested scenario has the same PF violation on both endpoints:
  `qg_min` for generator `12`, where `PowerModels.compute_ac_pf` returns
  `Qg = 0.0` against `qg_min = 0.01` or `0.018`;
- because this appears for both the own-start and PowerModels-start PF
  endpoints, it is not an own-vs-PowerModels disagreement by itself. It is a
  PF-convention/Q-limit-enforcement caveat.

Updated helper outputs:

- `results/own_vs_powermodels/opf_endpoint_pf_inequality_levels_classified_julia.csv`;
- `results/own_vs_powermodels/opf_endpoint_pf_inequality_feasibility_by_independence_julia.csv`;
- `results/own_vs_powermodels/opf_endpoint_pf_inequality_vm_by_bus_class_julia.csv`;
- `results/own_vs_powermodels/opf_endpoint_pf_inequality_not_independently_rederived_rows_julia.csv`;
- `results/own_vs_powermodels/opf_endpoint_pf_inequality_specified_or_pinned_rows_julia.csv`.

This does not close the case. It only rules down one explanation, and less
strongly than the equality-only deviation diagnostic because some endpoint-PF
families are specified or pinned by construction.

### C. Equality-Only Deviation-Minimization Diagnostic

Status: implemented for current and PM-like objective-mismatch scenarios.

For objective-mismatch scenarios, take:

- the PowerModels OPF solution as the reference point `(xP*, uP*)`;
- the own OPF solution as the suspicious point `(xE*, uE*)`.

Then solve a new own-model diagnostic NLP with decision variables `x, u` and
objective:

```text
minimize ||x - xP*||_1 + ||u - uP*||_1
```

where:

- `x` includes voltage magnitudes and voltage angles;
- `u` includes active and reactive generator outputs;
- branch-flow variables, if present in the formulation, are allowed to be
  determined by the equality constraints.

The key modelling choice for this diagnostic:

- impose only the AC equality constraints `g(x, u) = 0`;
- do not impose inequality constraints `h(x, u) <= 0` as constraints in the
  deviation-minimization solve;
- after solving, evaluate the inequality levels `h(xE**, uE**)` externally and
  compare them against the PowerModels binding levels `h(xP*, uP*)`.

Purpose:

- test whether the own formulation can move toward the PowerModels point using
  only AC physics/equality feasibility;
- identify whether the suspicious objective difference is associated with
  inequality-level disagreement after the equality-feasible projection;
- distinguish "wrong inequality enforcement" from "different equality-feasible
  local operating point."

Expected outputs:

- `results/own_vs_powermodels/equality_only_deviation_minimization_julia.csv`;
- `results/own_vs_powermodels/equality_only_deviation_variable_differences_julia.csv`;
- `results/own_vs_powermodels/equality_only_deviation_inequality_levels_julia.csv`;
- `results/own_vs_powermodels/equality_only_deviation_binding_summary_julia.csv`.

Minimum columns:

- `case`, `scenario`, `variant` (`current`, `pm_like`);
- target source (`powermodels_reference`);
- termination status and Ipopt diagnostics;
- total L1 deviation and max absolute deviations for `Pg`, `Qg`, `Vm`, `Va`;
- evaluated inequality margins for generator, voltage, thermal, and angle
  constraints at `(xE**, uE**)`;
- same margins at `(xP*, uP*)`;
- flags for whether the equality-only solution violates limits or changes the
  binding structure.

Run scope:

- start with cheaper-objective or material-objective-mismatch scenarios;
- prioritize current Julia first;
- repeat for PM-like where PM-like objective differs materially from
  PowerModels.

Implementation:

- script:
  `julia/own_models/scripts/36_run_equality_only_deviation_minimization.jl`;
- organized copies:
  `results/own_vs_powermodels/_organized_for_meeting/07_deviation_minimization`.

Run scope:

- current:
  `1888_rte/load_x1.2_reactive_stress`,
  `1888_rte/load_x0.8`;
- PM-like:
  `1888_rte/load_x0.8`,
  `1888_rte/load_x1.2_reactive_stress`,
  `1888_rte/qg_limits_x0.6`.

First-run finding:

- all equality-only projections solve locally;
- all reach the PowerModels OPF point to numerical tolerance;
- all have zero evaluated inequality violations after the equality-only solve;
- all have zero binding mismatches against PowerModels after evaluating the
  removed generator, voltage, thermal, and angle inequalities externally.

Presentation helper tables:

- `results/own_vs_powermodels/_organized_for_meeting/07_deviation_minimization/equality_only_deviation_inequality_level_by_family_julia.csv`;
- `results/own_vs_powermodels/_organized_for_meeting/07_deviation_minimization/equality_only_deviation_inequality_level_top_rows_julia.csv`.

Interpretation:

- the objective-mismatch cases are not explained by missing AC equality physics;
- they are also not explained by the PowerModels point violating own-model
  inequality levels after equality-only projection;
- together with the all-constraints deviation result, this points to local
  optimum / solver trajectory behaviour under the normal generation-cost
  objective.

### D. Solver Diagnostics For Objective Mismatches

Status: implemented in the revised Ipopt-output-aligned form.

Script:

- `julia/own_models/scripts/32_run_objective_mismatch_solver_diagnostics.jl`

Outputs:

- `results/own_vs_powermodels/solver_diagnostics_objective_mismatch_julia.csv`;
- `results/own_vs_powermodels/solver_diagnostics_objective_mismatch_ipopt_iterations_julia.csv`;
- `results/own_vs_powermodels/solver_diagnostics_ipopt_notes_julia.csv`.

Use this table before trusting any objective comparison. It now records:

- Ipopt termination status and raw status;
- final `inf_pr` as original unscaled constraint violation;
- final `inf_du` as scaled internal dual infeasibility;
- barrier parameter `mu` separately from complementarity;
- relevant Ipopt tolerance/options used in the diagnostic run.

Current interpretation:

- the suspicious objective-mismatch rows solved to Ipopt's desired tolerance
  according to the visible Ipopt callback diagnostics;
- therefore the objective mismatch is less likely to be explained by obvious
  solver nonconvergence alone;
- remaining explanations are formulation/objective convention differences,
  inequality-level differences, missing equality/bus terms, or different local
  optimum behaviour.

### E. Organize Own-vs-PowerModels Result Tables

Status: implemented as copied meeting folders, with a source CSV archive.

Script:

- `scripts/organize_own_vs_powermodels_tables.ps1`

Organized folder:

- `results/own_vs_powermodels/_organized_for_meeting`

The script copies top-level result CSVs into category folders while leaving the
original files in place. This avoids breaking existing scripts, open IDE tabs,
or result paths used by downstream diagnostics.

Folders:

- `00_overview`: global Julia/Python summaries and significant discrepancies;
- `01_objective_and_solver`: objective mismatch tables and Ipopt diagnostics;
- `02_binding_constraints`: binding mismatch details and tolerance summaries;
- `03_performance`: solve time and iteration comparisons;
- `04_loadflow_validation`: OPF endpoint load-flow validation and inequality
  levels;
- `05_shunt_and_formulation`: bus-shunt location/toggle diagnostics;
- `06_discrepancy_diagnosis`: automated Julia discrepancy diagnosis tables.

Use:

- `results/own_vs_powermodels/_organized_for_meeting/manifest.csv`

to see exactly which source file was copied into each category.

Source archive:

- `scripts/archive_own_vs_powermodels_top_level_csvs.ps1`
- `results/own_vs_powermodels/_source_top_level_archive/20260717_143151`

This archive is a timestamped copy of the top-level `*.csv` files. The original
top-level CSVs remain in place because diagnostic scripts still use those paths
as active inputs.

### F. PM-like Constrained Deviation-Minimization

Status: implemented for PM-like objective-mismatch scenarios.

This is the all-constraints deviation diagnostic, not the equality-only
diagnostic in task C.

Script:

- `julia/own_models/scripts/34_run_pm_like_constrained_deviation_minimization.jl`

Outputs:

- `results/own_vs_powermodels/pm_like_constrained_deviation_minimization_julia.csv`;
- `results/own_vs_powermodels/pm_like_constrained_deviation_variable_differences_julia.csv`;
- `results/own_vs_powermodels/pm_like_constrained_deviation_binding_differences_julia.csv`;
- `results/own_vs_powermodels/pm_like_constrained_deviation_binding_summary_julia.csv`.

Organized copies:

- `results/own_vs_powermodels/_organized_for_meeting/07_deviation_minimization`

Run scope:

- PM-like objective-mismatch scenarios:
  `1888_rte/load_x0.8`,
  `1888_rte/load_x1.2_reactive_stress`, and
  `1888_rte/qg_limits_x0.6`.

Variants:

- `full`: target `Pg`, `Qg`, `Vm`, and `Va`;
- `no_qg`: target `Pg`, `Vm`, and `Va`, leaving `Qg` free;
- `no_v`: target `Pg` and `Qg`, leaving `Vm` and `Va` free;
- `pg_only`: target only `Pg`.

First-run finding:

- the `full` variant reaches the PowerModels OPF point to numerical tolerance
  in all three scenarios;
- the `full` variant has zero binding mismatches against PowerModels in all
  three scenarios;
- therefore the PowerModels solution is feasible in the own PM-like
  formulation with all normal OPF constraints active;
- the PM-like objective mismatches are therefore best interpreted as local
  optimum / solver basin behaviour, not as a missing PM-like constraint in this
  constrained-deviation diagnostic.

### G. Current Julia Constrained Deviation-Minimization

Status: implemented for current-model objective-mismatch scenarios.

This mirrors task F, but uses the current Julia AC OPF model instead of the
PM-like model. It is still the all-constraints deviation diagnostic, not the
equality-only diagnostic in task C.

Script:

- `julia/own_models/scripts/35_run_current_constrained_deviation_minimization.jl`

Outputs:

- `results/own_vs_powermodels/current_constrained_deviation_minimization_julia.csv`;
- `results/own_vs_powermodels/current_constrained_deviation_variable_differences_julia.csv`;
- `results/own_vs_powermodels/current_constrained_deviation_binding_differences_julia.csv`;
- `results/own_vs_powermodels/current_constrained_deviation_binding_summary_julia.csv`.

Organized copies:

- `results/own_vs_powermodels/_organized_for_meeting/07_deviation_minimization`

Run scope:

- current-model objective-mismatch scenarios:
  `1888_rte/load_x1.2_reactive_stress` and `1888_rte/load_x0.8`.

First-run finding:

- the `full` variant reaches the PowerModels OPF point to numerical tolerance
  in both scenarios;
- the `full` variant has zero binding mismatches against PowerModels in both
  scenarios;
- therefore the PowerModels solution is feasible in the current Julia
  formulation with all normal OPF constraints active;
- the current-model cheaper objective rows are therefore not explained by an
  inability to represent the PowerModels point. They point toward local
  optimum / solver trajectory behaviour under the normal cost objective.

## Remaining Before Python Flat-Start Sweep

Do these before launching the long Python AC OPF compact sweep:

1. Freeze the slide formulation wording for shunts/Ybus.
2. Keep the `case1888_rte` different-optimum story as evidence-backed but
   caveated by data-version differences.
3. Treat Python gen-114 as diagnosed: rerun evidence supports a warm-start
   artifact, not an equation bug.
4. Keep Python formulation default as `bus_injection`; do not switch Python to
   branch-flow.
5. Do not export/upload results yet.

## 1. Slides: Add Bus Shunt Terms

Status: ready to apply to slides.

The code already includes bus shunts. The formulation slides should explicitly
show that shunts are included in the diagonal of `Ybus`, not added a second
time in the bus-injection balance. Use the compact slide form:

```latex
\[
\begin{aligned}
Y_{ii}
&=
\sum_{\ell \in \delta(i)} Y^{\ell}_{ii}
+
g_i^{sh}
+
j b_i^{sh},
&
Y_{ij}
&=
\sum_{\ell \in \mathcal{L}_{ij}} Y^{\ell}_{ij},
\quad i \neq j.
\end{aligned}
\]
```

Then state:

```text
Shunts and branch charging are already absorbed into G_ij and B_ij through
Ybus. Do not add them again in the AC balance constraints.
```

Use `docs/formulations/ac_opf.md` as the formulation source of truth. No code
change is needed.

## 2. case1888_rte Pg/Pf Discrepancy

Status: evidence-backed, with one data-version caveat. A focused Julia
self-inspection note now lives at
`docs/notes/julia_1888_different_optimum_binding_inspection.md`, with
machine-readable tables in
`results/own_julia/deviation_minimization/1888_significant_scenario_summary.csv`,
`results/own_julia/deviation_minimization/1888_top_variable_difference_elements.csv`,
and
`results/own_julia/deviation_minimization/1888_binding_mismatch_elements.csv`.

Initial inspection of `results/own_vs_powermodels/python/summary.csv` shows the
largest `Pg` and `Pf` discrepancies coincide in the same `case1888_rte`
scenarios, especially:

- `qg_limits_x0.8`
- `load_x1.2_reactive_stress`
- `load_x0.8`

The current hypothesis is different local optimum / different binding pattern,
with branch-flow differences caused by changed active dispatch near the same
buses.

Inspection evidence from `results/own_vs_powermodels/python/1888_rte/`:

| Scenario | Top generator discrepancy | Adjacent/top branch discrepancy | Interpretation |
| --- | --- | --- | --- |
| `qg_limits_x0.8` | gen `264`, bus `1995`, `abs_pg_diff = 10.5`; own at `pmin`, PM at `pmax` | branch `2466`, buses `1194-1995`, `abs_pf_diff = 10.5` | Pf gap is physically adjacent to the Pg gap |
| `load_x1.2_reactive_stress` | gen `79`, bus `1649`, `abs_pg_diff = 6.7`; own at `pmax`, PM at `pmin` | branch `2281`, buses `185-1649`, `abs_pf_diff = 6.7` | Pf gap is physically adjacent to the Pg gap |
| `load_x0.8` | gen `167`, bus `1713`, `abs_pg_diff = 4.3248` | branch `2369`, buses `577-1713`, `abs_pf_diff = 4.3248` | Same adjacency pattern, but see caveat below |

Binding evidence:

- `qg_limits_x0.8` has many active-set differences: 78 `pg_min`, 71
  `pg_max`, 30 `qg_min`, 27 `qg_max`, 28 `vm_min`, 3 `vm_max`, and 9
  `thermal` binding mismatches.
- `load_x1.2_reactive_stress` has active-power bound mismatches around the
  same top discrepant generators, including gen `79` at `pmax` in the own
  solution and at `pmin` in PowerModels.
- `load_x0.8` also shows the same generator/branch adjacency pattern in the
  own-vs-PowerModels spotlight table.

Numerical-tolerance check:

- Binding classification uses a tight `1e-6` threshold, so small mismatches can
  be classifier noise. These spotlight mismatches are not just that.
- Re-checking the mismatched constraints by the larger of the own/PowerModels
  distance-to-bound margins:
  - `qg_limits_x0.8`: `246/246` mismatches have max margin `> 1e-4`; `211/246`
    have max margin `> 1e-2`; `89/246` have max margin `> 1`.
  - `load_x1.2_reactive_stress`: `39/39` mismatches have max margin `> 1e-4`;
    `34/39` have max margin `> 1e-2`; `11/39` have max margin `> 1`.
  - `load_x0.8`: `12/12` mismatches have max margin `> 1e-4`; `7/12` have max
    margin `> 1e-2`; `5/12` have max margin `> 1`.
- Therefore the main `Pg`/`Pf` evidence cannot be dismissed as tolerance-only
  binding-label noise. Some voltage-bound mismatches may be small, but the
  generator active-power bound flips are materially different active sets.

Topology evidence:

- The top discrepant generator buses are pendant/radial in the inspected case
  data: degree `1`, online degree `1`.
- Examples:
  - bus `1995` has gen `264` and only branch `2466`;
  - bus `1772` has gen `263` and only branch `2465`;
  - bus `1744` has gen `227` and only branch `2429`;
  - bus `1649` has gen `79`, load `916`, and only branch `2281`;
  - bus `1713` has gen `167` and only branch `2369`.
- This explains why the largest `Pg` and `Pf` differences are numerically the
  same: at a pendant generator bus, any active dispatch difference must leave
  through the single incident branch, aside from fixed local load/shunt terms
  and small loss/sign-convention effects.

Caveat to keep in the meeting narrative: `load_x0.8` is discrepant in
`results/own_vs_powermodels/python/summary.csv`, but the fuller validation table
`results/own_python/ac_opf_validation/compact_ac_opf_full_comparison.csv` shows
the current `1888_rte_load_x0.8` load-scaling row with near-zero `Pg/Pf`
differences. Treat this as a data-version or scenario-definition hygiene item
before presenting it as a current unresolved model issue.

Julia comparison:

- Julia does not reproduce the Python `qg_limits_x0.8` discrepancy. In
  `results/own_vs_powermodels/julia/1888_rte/qg_limits_x0.8`, the largest
  generator and branch differences are about `1e-8`, with zero binding
  mismatches. This is a useful distinction: the Python `qg_limits_x0.8` result
  should not be generalized to the Julia model.
- Julia does show the same kind of different-active-set behaviour in other
  `1888_rte` scenarios:
  - `1888_rte_angle_20p0deg...`: `objective_rel_diff = 0.04284`; top gen
    `264` at bus `1995` has `abs_pg_diff = 10.5`, and branch `2466`
    (`1194-1995`) has `abs_pf_diff = 10.5`.
  - `load_x1.2_reactive_stress`: `objective_rel_diff = 0.00182`; top gen
    `147` at bus `1699` has `abs_pg_diff = 6.5621`, and branch `2349`
    (`453-1699`) has `abs_pf_diff = 6.5621`.
  - `load_x0.8`: `objective_rel_diff = 3.38e-5`; top gen `146` at bus `1698`
    has `abs_pg_diff = 2.0481`, and branch `2348` (`451-1698`) has
    `abs_pf_diff = 2.0481`.
- The same radial-bus explanation holds for the Julia top cases:
  - bus `1995` degree/on-degree `1/1`, branch `2466`;
  - bus `1699` degree/on-degree `1/1`, branch `2349`;
  - bus `1698` degree/on-degree `1/1`, branch `2348`.
- Julia binding-mismatch tolerance check:
  - `qg_limits_x0.8`: `0` mismatches.
  - `1888_rte_angle_20p0deg...`: `217` mismatches, `214` with max
    distance-to-bound margin `> 1e-4`, `183` with margin `> 1e-2`, `78` with
    margin `> 1`.
  - `load_x1.2_reactive_stress`: `40` mismatches, `38` with margin `> 1e-4`,
    `34` with margin `> 1e-2`, `8` with margin `> 1`.
  - `load_x0.8`: `9` mismatches, `7` with margin `> 1e-4`, `6` with margin
    `> 1e-2`, `4` with margin `> 1`.
- Conclusion for Julia: the `angle_20deg` and `load_x1.2_reactive_stress`
  differences are real active-set/local-optimum differences, not binding
  classifier tolerance noise. The `qg_limits_x0.8` discrepancy is Python-side
  only in the current own-vs-PowerModels tables.

Automated Julia diagnosis pass:

- Script added:
  `python/own_models/scripts/28_diagnose_julia_own_vs_powermodels_discrepancies.py`.
- Current generated outputs:
  - `results/own_vs_powermodels/julia_discrepancy_diagnosis.csv`
  - `results/own_vs_powermodels/julia_discrepancy_diagnosis_top_cases.csv`
- The Julia pass currently classifies:
  - `169` scenarios as `no_material_difference`;
  - `86` scenarios as `data_missing_or_unclassified` because solved detail
    tables are not available or the scenario is not suitable for variable-level
    comparison;
  - `5` scenarios as `radial_dispatch_active_set_difference`.
- The top five Julia material cases are all `1888_rte`:
  - `qg_limits_x0p6`;
  - `angle_20p0deg`;
  - `qg_limits_x1p0`;
  - `load_x1.2_reactive_stress`;
  - `load_x0.8`.
- The diagnostic now distinguishes the global largest `Pf` branch from the
  largest branch incident on the top discrepant generator bus. This matters
  because a neighboring upstream branch can tie or exceed the incident branch,
  while the radial explanation is still confirmed by the incident branch.

To verify this, inspect each scenario as follows:

1. Sort `generators.csv` by `abs_pg_diff`.
2. Sort `branches.csv` by `abs_pf_diff`.
3. Map the top generator bus to the top discrepant branch endpoints.
4. Check whether the top generator is at opposite active-power bounds in the
   two models.
5. Check `constraints_binding.csv` for nearby voltage, reactive, thermal, and
   angle binding mismatches.
6. Confirm both solutions satisfy AC balance and engineering limits to solver
   tolerance.
7. Record whether the `Pf` discrepancy is physically adjacent to the `Pg`
   discrepancy.

If those checks hold, the evidence supports the different-optimum explanation.
If not, investigate branch-flow equations, data mapping, or sign/orientation.

## 3. Python AC OPF Debugging

Status: first debugging pass completed; gen-114 root cause diagnosed.

The professor-facing debugging target is not the whole Python AC OPF
formulation. The current evidence points to large/stressed edge cases and to
pipeline/result-version hygiene.

Broad status from
`results/own_python/ac_opf_validation/compact_ac_opf_full_comparison.csv`:

- `215` rows are `own_status = optimal` and `pm_status = LOCALLY_SOLVED`.
- `92` rows are `own_status = infeasible` and
  `pm_status = LOCALLY_INFEASIBLE`, so they are expected stress/infeasibility
  matches rather than model bugs.
- The sharp Python-specific mismatch is one row:
  `1888_rte_generator_114_outage`, where own Python reports infeasible while
  PowerModels reports `LOCALLY_SOLVED`.
- Other abnormal rows are iteration-limit/error rows, especially branch
  outages and one `1888_rte_load_x1.4` reactive stress case.

Priority case:

```text
1888_rte_generator_114_outage
```

Current evidence:

- In `compact_ac_opf_full_comparison.csv`, the row is:
  - own status `infeasible`, PM status `LOCALLY_SOLVED`;
  - objective relative difference `0.07545`;
  - own iterations `771`, PM iterations `210`;
  - own time `683 s`, PM time `17.7 s`;
  - max `Pg` diff `10.5`, max `Pf` diff `14.92`, max `Qg` diff `6.905`,
    max `Vm` diff `0.2`.
- In the targeted diagnostic rerun
  `compact_ac_opf_sweeps_targeted_diagnostics_maxiter2000.csv`, the same case
  reaches Ipopt status `infeasible` / "Converged to a locally infeasible
  point", with:
  - max active balance violation `0.0`;
  - max reactive balance violation `0.019` p.u.;
  - voltage, thermal, and angle violations `0.0`;
  - solve time about `1102 s`.
- In the older compact sweep
  `compact_ac_opf_sweeps.csv`, the same case hit `maxIterations` after about
  `50 s`, with large balance/thermal residuals. Treat this file as stale for
  this row.
- In the JSON-based own-vs-PowerModels spotlight table
  `results/own_vs_powermodels/python/summary.csv`, the similarly named
  `1888_rte_gen_114_out...` row solves and matches PowerModels almost exactly:
  objective relative difference `2.6e-14`, max `Pg/Pf` differences about
  `7e-9`, and zero binding mismatches.

Interpretation:

- This does not look like a general AC balance/branch-flow formulation bug,
  because the JSON-based Python solve matches PowerModels for the same
  generator-outage concept.
- The compact MAT-based pipeline and the JSON/reference-table pipeline are not
  giving the same practical outcome under the default `case` start. The
  controlled rerun below shows this is a warm-start / local-basin issue, not an
  AC-equation issue.
- The targeted diagnostic suggests the compact Python `case` start gets very
  close except for a reactive balance residual around `0.019` p.u., then Ipopt
  declares a locally infeasible point.

Targeted rerun:

- Script added:
  `python/own_models/scripts/29_debug_python_ac_opf_gen114.py`.
- Output:
  `results/own_python/ac_opf_validation/debug_gen114_outage_warm_starts.csv`.

Current rerun evidence:

| Source | Warm start | Status | Objective | Iterations | Time (s) | Max Q balance violation |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| compact MAT | `case` | `infeasible` | `1613138.7527` | `1061` | `1059.4` | `1.896e-2` |
| compact MAT | `flat` | `optimal` | `1500311.1296` | `192` | `13.7` | `4.25e-9` |
| compact MAT | `zero` | `optimal` | `1500311.1296` | `742` | `344.9` | `4.25e-9` |
| JSON | `case` | `optimal` | `1500311.1296` | `575` | `45.5` | `4.25e-9` |

Data comparison:

- Compact MAT and JSON cases have the same active counts:
  `1888` buses, `2531` active branches, and `289` active generators.
- At bus `1875` / generator `114`, both cases have gen `114` offline,
  no local load/shunt, one online incident branch, and the same voltage and
  generator limits.
- Active generator and bus initial values match between compact MAT and JSON.
- The only obvious local-data representation difference is that compact MAT
  zeros the offline gen `114` `pg0/qg0`, while JSON retains its source
  `pg0/qg0`; this inactive generator is filtered out of the AC OPF variables.

Conclusion:

- The Python compact `1888_rte_generator_114_outage` mismatch is now best
  described as a warm-start-sensitive local infeasibility in Ipopt.
- The own formulation can reproduce the PowerModels solution for the compact
  MAT case when started from `flat` or `zero`.
- The compact Python AC validation default has been changed from
  `warm_start = "case"` to `warm_start = "flat"` in
  `python/own_models/src/analysis/ac_opf_sweeps.py`.

Effect on other systems:

- Existing base-case warm-start ablation
  `results/own_python/ac_opf_warm_start_experiment/warm_start_iteration_comparison.csv`
  shows that `flat` is not universally fastest, but it is generally safe:
  - `5_pjm`: `case/flat/zero = 20/20/20` iterations.
  - `14_ieee`: `15/16/16`.
  - `30_ieee`: `17/17/15`.
  - `118_ieee`: `25/25/27`.
  - `300_ieee`: `31/31/33`.
  - `1354_pegase`: `41/40/54`.
  - `1888_rte` base: `120/118/178`.
  - `6470_rte` base: `338/136/160`, so `flat` is much better.
  - `1888_rte` reactive stress `Qd x1.2`: `375/527/596`, so `case` remains
    better there.
- Interpretation: `flat` is a better compact-validation default because it
  prevents the gen-114 false infeasibility and improves some large systems,
  but scenario-level warm-start sensitivity remains real. For performance
  studies, continue reporting the selected warm-start mode.

Formulation generalization:

- Script added:
  `python/own_models/scripts/30_run_ac_opf_formulation_warm_start_matrix.py`.
- Current output:
  `results/own_python/ac_opf_formulation_warm_start_matrix/formulation_warm_start_matrix.csv`.
- Scope run so far: all 8 default PGLib base systems with
  `warm_start = "flat"` and both Python formulations.

Result:

| Case | Bus-injection iters | Branch-flow iters | Branch-flow / bus time | Objective note |
| --- | ---: | ---: | ---: | --- |
| `5_pjm` | 20 | 17 | `0.82x` | same objective |
| `14_ieee` | 16 | 17 | `1.10x` | same objective |
| `30_ieee` | 17 | 17 | `0.93x` | same objective |
| `118_ieee` | 25 | 25 | `1.12x` | same objective |
| `300_ieee` | 31 | 33 | `1.22x` | same objective |
| `1354_pegase` | 40 | 40 | `1.12x` | same objective |
| `1888_rte` | 118 | 772 | `7.66x` | branch-flow reaches a higher local optimum by `60082.8` |
| `6470_rte` | 136 | 421 | `3.27x` | same objective |

Conclusion from the generalized Python run:

- Sparse Ybus is an unconditional Python improvement and remains implemented.
- Branch-flow is implemented in Python, but the base-case matrix shows it is
  not a safe Python default. It is neutral/slightly worse on most systems,
  much worse on the two large RTE systems, and changes the local optimum on
  `1888_rte`.
- Therefore the Python default should stay `formulation = "bus_injection"`.
  Julia can still default to branch-flow because its JuMP/Ipopt path benefits
  from it; Python/Pyomo does not.

Next debugging steps:

1. Rerun the Python compact AC sweep with the new `warm_start = "flat"` default
   before deciding whether remaining old abnormal rows are true failures.
2. Optional: inspect exact model/variable ordering differences between MAT and
   JSON paths. This may explain why JSON `case` solves while compact MAT `case`
   fails even with matching active starts.
3. Do not change the AC balance or branch-flow equations based on gen-114.
   This case is now explained by solver initialization/local basin behaviour.

Secondary cases:

- `1888_rte_qg_limits_x0.6`
- `1888_rte_qg_limits_x0.8`
- `1888_rte_angle_20deg`
- `1888_rte_load_x1.2` reactive stress

## 4. Julia AC OPF vs PowerModels Speed Gap

Status: first-pass explanation exists; frozen while Julia validation is running.

Already investigated:

- Ipopt tolerances are aligned.
- Starting-point differences do not fully explain the gap.
- Sparse Ybus and branch-flow formulation were tested.
- Several solver-option and formulation hypotheses were ruled out.

PowerModels implementation audit:

- Installed source inspected locally:
  `C:\Users\Efthymis\.julia\packages\PowerModels\9nxfE\src`.
- The standard ACP OPF builder in `src/prob/opf.jl` calls:
  - `variable_bus_voltage(pm)`;
  - `variable_gen_power(pm)`;
  - `variable_branch_power(pm)`;
  - `constraint_power_balance(pm, i)`;
  - `constraint_ohms_yt_from(pm, i)` and `constraint_ohms_yt_to(pm, i)`;
  - `constraint_voltage_angle_difference(pm, i)`;
  - branch thermal limits and generation cost objective.
- In `src/form/acp.jl`, PowerModels' AC balance is branch-flow-native:
  nodal balance sums explicit per-arc `p/q` branch-flow variables and adds
  explicit bus shunt terms as `-gs*vm^2` and `+bs*vm^2`.
- In `src/core/variable.jl`, PowerModels creates `p` and `q` variables for
  every directed branch arc, i.e. both `(branch, from, to)` and
  `(branch, to, from)`. It does not substitute branch flows as expressions
  inside the bus balance.
- In `src/form/acp.jl`, Ohm's law is imposed as separate nonlinear equality
  constraints for each branch end: active/reactive from-end and active/reactive
  to-end.
- In `src/core/ref.jl` and `src/core/constraint_template.jl`, angle-difference
  limits are deduplicated by oriented bus pair. Parallel branches share one
  representative angle bound, while thermal and Ohm constraints remain
  per-branch.
- PowerModels variable starts default to `pg=qg=0`, `vm=1`, `va=0` unless
  explicit `*_start` fields are present. This differs from our old Julia
  default of case-file starts.

What we can reuse from PowerModels without turning our model into a wrapper:

- Keep the branch-flow formulation as the Julia default. This is the most
  important structural idea copied from PowerModels.
- Keep bus shunts separate in the branch-flow balance, while keeping them
  absorbed into `Ybus` for the bus-injection formulation. This now matches the
  physics of both formulations.
- Keep the sparse-network data structures and avoid dense Ybus scans.
- Use PowerModels' reference-structure ideas as design guidance:
  `arcs`, `arcs_from`, `arcs_to`, `bus_arcs`, `bus_gens`, `bus_shunts`, and
  `buspairs`.
- Optionally add a future inspection script that builds own Julia and
  PowerModels for the same case and reports variable/constraint counts,
  starts, angle-pair counts, and Ipopt trace metadata.

Experimental implementation added:

- New opt-in builder/solver:
  - `build_ac_opf_model_powermodels_like`
  - `solve_ac_opf_own_powermodels_like`
- New source file:
  `julia/own_models/src/opf/ac/powermodels_like.jl`.
- New experiment script:
  `julia/own_models/scripts/28_run_ac_opf_powermodels_like_experiment.jl`.
- This path does not change `build_ac_opf_model`, `solve_ac_opf_own`, or the
  validation sweep.
- Features captured in the experimental path:
  - directed `(branch, from, to)` and `(branch, to, from)` arc variables;
  - PowerModels-like voltage/generation/branch-flow variable ordering;
  - simple `[-rate_a, rate_a]` bounds on active and reactive arc-flow
    variables, in addition to nonlinear MVA thermal limits;
  - PowerModels-style branch-flow KCL with explicit bus shunt terms;
  - optional bus-pair angle deduplication via
    `deduplicate_angle_constraints=true`, defaulting to `false` because prior
    ablation showed dedup can hurt Ipopt in our implementation.
- Smoke test on `pglib_opf_case5_pjm` passed:
  - current own branch-flow model: objective `17551.9`, `17` iterations;
  - experimental PM-like model: objective `17551.9`, `21` iterations;
  - PowerModels: objective `17551.9`, `21` iterations.
- Interpretation: the new path is structurally runnable and objective-consistent
  on a tiny case. The equal iteration count with PowerModels on case5 is
  encouraging but not evidence of large-case improvement yet; test
  `1888_rte`/`6470_rte` later after the current Julia validation run finishes.

Extended PM-like experiment:

- Script extended to support larger/stressed scenario matrices:
  - profiles via `--profile=bigger`;
  - custom scenarios via `--scenarios=base,qstress_x1.2,...`;
  - custom output names via `--output-name=...`;
  - PM-relative metrics: objective diff, iteration/time ratio, max `Pg/Qg/Vm/Va`
    and branch-flow differences vs PowerModels.
- PowerModels logging is now silenced in this experiment script to avoid huge
  parser/cost-cleanup logs.
- Result files produced:
  - `results/own_julia/ac_opf_powermodels_like_experiment/powermodels_like_experiment.csv`
    for `case118_ieee` and `case300_ieee`;
  - `results/own_julia/ac_opf_powermodels_like_experiment/powermodels_like_experiment_1354_1888_stress.csv`
    for `case1354_pegase` and `case1888_rte`.
  - `results/own_julia/ac_opf_powermodels_like_experiment/powermodels_like_experiment_6470_speed.csv`
    for `case6470_rte` base and `angle_20deg` speed checks.
- Summary files produced:
  - `powermodels_like_experiment_all_rows.csv`;
  - `powermodels_like_experiment_approach_summary.csv`;
  - `powermodels_like_experiment_case_scenario_summary.csv`.

Key measured results:

| Case/scenario | Current own | PM-like zero | PM-like zero + angle dedup | PowerModels | Interpretation |
| --- | ---: | ---: | ---: | ---: | --- |
| `118_ieee` solved scenarios | `21-25` iters | exactly PM iters (`25-29`) | exactly PM iters (`25-29`) | `25-29` iters | PM-like resembles PM; current is slightly fewer iterations |
| `300_ieee` solved scenarios | `32` iters | exactly PM iters (`33`) | exactly PM iters (`33`) | `33` iters | same pattern; stressed infeasible rows also broadly align |
| `1354_pegase` solved scenarios | `39-40` iters | exactly PM iters (`48-49`) | exactly PM iters (`48-49`) | `48-49` iters | PM-like is almost structurally identical to PM, but current is faster here |
| `1888_rte` base | infeasible, `986` iters | solved, `80` iters | solved, `80` iters | solved, `114` iters | PM-like fixes the bad current case-start basin and is faster than PM |
| `1888_rte qstress_x1.2` | solved, `649` iters, different optimum | solved, `404` iters, different optimum | solved, `355` iters, PM objective/variables | solved, `169` iters | angle dedup changes basin here and recovers PM-like solution, but still slower |
| `1888_rte qg_limits_x0.8` | solved, `446` iters | solved, `323` iters | solved, `401` iters | solved, `89` iters | PM-like matches PM solution but remains much slower |
| `1888_rte angle_20deg` | solved, `888` iters, different optimum | solved, `80` iters | solved, `80` iters | solved, `127` iters | PM-like removes the large local-optimum discrepancy and is faster than PM |
| `6470_rte base` | solved, `493` iters, `152s` | solved, `92` iters, `29.0s` | solved, `92` iters, `28.8s` | solved, `92` iters, `31.0s` | PM-like essentially closes the 6470 speed gap |
| `6470_rte angle_20deg` | solved, `482` iters, `145s` | solved, `92` iters, `28.4s` | solved, `92` iters, `28.9s` | solved, `91` iters, `27.6s` | PM-like again matches PM-scale speed |

Resemblance evidence:

- On `118_ieee`, `300_ieee`, and `1354_pegase`, PM-like rows match
  PowerModels to numerical noise: objective relative differences around
  `1e-15` to `1e-14`, and max variable/flow differences typically
  `1e-8` or smaller.
- On `1888_rte`, PM-like strongly resembles PowerModels in base,
  `qg_limits_x0.8`, and `angle_20deg` scenarios. The large current-own local
  optimum discrepancy in `angle_20deg` disappears.
- On `1888_rte qstress_x1.2`, plain PM-like zero-start still lands in a
  different local optimum (`objective_rel_diff_vs_pm = 0.00242`, max `Pg/Pf`
  diff about `6.9`). Enabling angle dedup for this experimental variant lands
  on the PowerModels solution to numerical noise.

Current interpretation:

- The experimental path is valuable: it reproduces PowerModels' solution much
  more often than the current own branch-flow model on difficult `1888_rte`
  cases, and it can avoid bad local basins.
- It is not a universal speed fix. For `118/300/1354`, the current own model is
  often fewer iterations, while PM-like mirrors PowerModels' iteration count.
- For `1888_rte`, PM-like is clearly promising: it fixes the base infeasibility
  under case start and removes the `angle_20deg` local-optimum gap, but still
  has a substantial iteration gap in `qg_limits_x0.8` and `qstress_x1.2`.
- For `6470_rte`, PM-like is the strongest speed result so far: current own
  needs about `5.3x` PowerModels iterations/time, while PM-like zero-start
  matches PowerModels' iteration count and is slightly faster in wall-clock
  time in the two tested scenarios.
- Angle dedup should remain experimental, not default. It is decisive for
  `1888_rte qstress_x1.2`, neutral for many cases, and worse for
  `1888_rte qg_limits_x0.8`.

Approach-level comparison from the aggregate summary:

| Variant | Success rows | Median iterations | Median iteration ratio vs PM | Median time ratio vs PM | Max objective rel diff vs PM |
| --- | ---: | ---: | ---: | ---: | ---: |
| current own branch-flow, case start | `16/20` | `40` | `0.891x` | `1.026x` | `1.69e-1` |
| PM-like zero start | `17/20` | `49` | `1.000x` | `1.018x` | `2.42e-3` |
| PM-like zero start + angle dedup | `17/20` | `49` | `1.000x` | `1.041x` | `8.59e-4` |
| PowerModels | `17/20` | `49` | `1.000x` | `1.000x` | `0` |

How to read this:

- The current own branch-flow model can be faster on easy solved cases, but it
  has the largest failure/local-optimum exposure.
- PM-like zero start is the best candidate for a robust experimental model:
  it matches PM-style iteration behaviour and closes the major `6470_rte`
  speed gap.
- Angle dedup gives the closest aggregate resemblance, mostly because it fixes
  `1888_rte qstress_x1.2`, but it is not uniformly faster and should stay as a
  switch for experiments.

Full-validation integration plan:

- The compact Julia OPF validation sweep now accepts a third AC model label:
  `ac_pm_like`.
- This label uses `solve_ac_opf_own_powermodels_like(...; warm_start=:zero)`
  and compares against the same `PowerModels.ACPPowerModel` reference using the
  existing objective, variable-difference, binding-count, runtime, and
  iteration columns.
- The current model remains `ac`; no replacement has been made.
- Smoke command used:
  `julia --project=julia julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl --cases=pglib_opf_case5_pjm --models=ac_pm_like --max-scenarios=1 --output-dir=results/own_julia/opf_validation_sweeps_pm_like_smoke --overwrite --quiet`.
- Smoke result: `case5_pjm/base` solved with `21` iterations for both
  `ac_pm_like` and PowerModels, identical objective, and numerical-noise-level
  variable differences.
- Full compact validation command to run when ready:
  `julia --project=julia julia/own_models/scripts/09_run_compact_opf_validation_sweeps.jl --models=ac,ac_pm_like --output-dir=results/own_julia/opf_validation_sweeps_with_pm_like`.
- Replacement rule: only promote PM-like to the default if it has equal-or-better
  success rate, objective agreement, variable agreement, binding agreement, and
  materially better runtime/iteration behavior across the full sweep. Keep the
  current model available for reproducibility even if PM-like becomes default.

What not to assume/copy blindly:

- Angle deduplication is real in PowerModels, but previous ablation showed it
  did not improve our model and can make Ipopt behave worse. Do not make it a
  default "optimization" without a fresh controlled test.
- Matching PowerModels' flat/zero start narrows some gaps but does not close
  them. Warm start is part of the story, not the whole story.
- PowerModels is not faster because it solves a smaller model. Prior trace
  evidence showed its large-case ACP model is larger: more flow variables and
  more Ohm equality constraints, but with a friendlier sparse Jacobian pattern.

Current explanation:

- For Python, a large part of the comparison is not like-for-like:
  Pyomo/Ipopt through the Python stack can have different model-generation,
  NL-writer, AD, and solver-interface overheads from PowerModels/JuMP. So the
  Python speed gap is plausibly partly environment/framework.
- For Julia, that excuse is weak: both are JuMP/Ipopt-side implementations.
  The remaining gap is best explained by formulation and construction details:
  PowerModels builds a mature branch-flow-native ACP model with explicit
  directed arc variables and narrow Ohm-law rows. Our Julia branch-flow model
  now captures the main idea and improves performance, but it still may differ
  in expression ordering, container/indexing structure, variable/constraint
  ordering, flow bounds/starts, and the exact Jacobian/MUMPS pivot sequence.
- Therefore the professor-facing answer should be: we have identified and
  adopted the main useful PowerModels component, the branch-flow-native sparse
  structure. The residual Julia speed gap is not a tolerance issue or a missing
  shunt term; it is an implementation-structure/Jacobian-sparsity issue in a
  nonconvex NLP, and closing it further would mean mimicking PowerModels'
  construction much more exactly.

Remaining optional experiment after the current Julia run finishes: replicate
PowerModels' exact construction more closely, or compare detailed
Ipopt/Jacobian traces for one large case.

## Python AC OPF Debugging

Status: deviation diagnostics implemented, first compact-reference batch completed,
full-sweep Python PM-like equality-only diagnostic completed for material
objective/variable mismatch rows, and Python endpoint load-flow validation
implemented through PyPowSyBl/OpenLoadFlow.

New scripts:

- `python/own_models/scripts/31_prepare_python_ac_opf_debugging_artifacts.py`
- `python/own_models/scripts/32_run_python_ac_opf_deviation_diagnostics.py`
- `python/own_models/scripts/33_run_python_ac_opf_solver_start_and_pm_like_validation.py`
- `python/own_models/scripts/37_run_python_pm_like_equality_only_deviation.py`
- `python/own_models/scripts/38_run_python_opf_endpoint_pypowsybl_loadflow_validation.py`

New Python debugging tables:

- `results/own_vs_powermodels/python_ac_opf_debug_target_inventory.csv`
- `results/own_vs_powermodels/python_ac_opf_debug_target_summary.csv`
- `results/own_vs_powermodels/python_ac_opf_debug_recommended_run_queue.csv`
- `results/own_vs_powermodels/python_power_balance_bus_shunt_term_locations.csv`
- `results/own_vs_powermodels/python_deviation_minimization_diagnostics.csv`
- `results/own_vs_powermodels/python_deviation_variable_differences.csv`
- `results/own_vs_powermodels/python_deviation_inequality_levels.csv`
- `results/own_vs_powermodels/python_deviation_binding_summary.csv`
- `results/own_vs_powermodels/python_solver_start_diagnostics.csv`
- `results/own_vs_powermodels/python_start_validation_summary.csv`
- `results/own_vs_powermodels/python_pm_like_meaningful_validation_summary.csv`
- `results/own_vs_powermodels/python_pm_like/equality_only_deviation_targets.csv`
- `results/own_vs_powermodels/python_pm_like/equality_only_deviation_minimization.csv`
- `results/own_vs_powermodels/python_pm_like/equality_only_deviation_variable_differences.csv`
- `results/own_vs_powermodels/python_pm_like/equality_only_deviation_inequality_levels.csv`
- `results/own_vs_powermodels/python_pm_like/equality_only_deviation_binding_summary.csv`
- `results/own_vs_powermodels/python_endpoint_pypowsybl_loadflow_validation.csv`
- `results/own_vs_powermodels/python_endpoint_pypowsybl_lf_variable_comparison.csv`
- `results/own_vs_powermodels/python_endpoint_pypowsybl_lf_inequality_levels.csv`
- `results/own_vs_powermodels/python_endpoint_pypowsybl_lf_binding_summary.csv`
- `results/own_vs_powermodels/python_endpoint_pypowsybl_lf_violations.csv`
- `results/own_vs_powermodels/python_endpoint_pypowsybl_lf_README.md`

Organized copies:

- `results/own_vs_powermodels/_organized_for_meeting/08_python_ac_opf_debugging`
- `docs/notes/python_ac_opf_solver_start_pm_like_validation.md`

Bus-shunt audit finding:

- Python current bus-injection includes bus shunts through the diagonal of
  `Ybus`.
- Python branch-flow and PM-like formulations include explicit bus shunt terms
  in the balance as `gs * vm^2` and `bs * vm^2`, matching the PowerModels-like
  convention.

Queue interpretation:

- The raw Python debug inventory has many rows, but most are not deviation
  candidates because PowerModels and own Python are both locally infeasible.
- The regenerated recommended queue separates those into
  `objective_status_inspection` instead of sending them to deviation
  minimization.
- Only rows with solved PowerModels references should be used for constrained
  or equality-only deviation minimization.

Deviation batch run:

- Scope: all compact-reference Python deviation candidates currently in
  `python_ac_opf_debug_recommended_run_queue.csv`:
  five current-model `1888_rte` rows and one PM-like `1888_rte_load_x0.8` row.
- Critical diagnostic fix: PowerModels voltage angles must be shifted so the
  Python slack/reference bus angle is zero before building the deviation target.
- Without this shift, the deviation objective is contaminated by a reference
  angle convention mismatch.
- After shifting the PM angle target by the PM slack-bus angle, constrained
  full deviation reaches the PM point closely across all six rows:
  maximum L1 deviation `1.04e-3`, maximum `Pg` deviation `1.79e-4` pu,
  maximum `Vm` deviation `4.37e-6` pu, and maximum relative cost deviation
  `1.26e-6`.
- Equality-only full deviation reaches the PM point to numerical precision
  across all six rows:
  maximum `Pg` deviation `1.57e-11` pu, maximum `Vm` deviation `1.60e-9` pu,
  and maximum relative cost deviation `1.54e-13`.
- Constrained deviation has `28` binding-status mismatches across `60`
  scenario/family rows but zero diagnostic violations; these mismatches are
  therefore tolerance/near-binding classification effects rather than material
  infeasibilities.
- Equality-only has zero binding-status mismatches; its small reported
  violations are the same tiny PowerModels-limit overshoots evaluated at the
  PM point, around `1e-7` to `1e-6`.

Full-sweep Python PM-like equality-only run:

- Scope selected from `results/own_vs_powermodels/python_pm_like/summary.csv`
  using material objective/variable/flow mismatch thresholds.
- Selected scenarios:
  - `1888_rte/load_x0.8`;
  - `1888_rte/load_x1.2_reactive_stress`.
- Both equality-only diagnostics solve with Ipopt status `optimal` in 4
  iterations.
- Both reach the PowerModels OPF point to numerical precision:
  max `Pg` deviation about `2.39e-13` pu, max `Vm` deviation about
  `1.25e-12` pu.
- Externally evaluated inequality levels show zero binding mismatches and zero
  violations beyond the `1e-5` tolerance window. Raw max thermal-limit
  overshoot is about `1.8e-6`, below the reporting tolerance and present at the
  PM-level comparison scale.

Python endpoint load-flow validation through PyPowSyBl/OpenLoadFlow:

- Script:
  `python/own_models/scripts/38_run_python_opf_endpoint_pypowsybl_loadflow_validation.py`.
- Scope:
  material solved objective/variable mismatch rows from the Python current and
  Python PM-like sweeps.
- Selected rows:
  `47` Python current scenarios and `2` Python PM-like scenarios.
- Endpoint convention:
  each selected row is run twice, once warm-started/configured from the Python
  OPF endpoint and once from the PowerModels OPF endpoint.
- Network construction:
  the script writes the already-mutated scenario `PowerCase` to a temporary
  MATPOWER `.mat` file and lets PyPowSyBl import that file. This avoids the old
  custom PyPowSyBl builder limitation that rejected non-zero phase-shift
  branches.
- Load-flow configuration:
  `PyPowSyBl/OpenLoadFlow`, `voltage_init_mode = PREVIOUS_VALUES`,
  `distributed_slack = false`, `read_slack_bus = true`,
  `write_slack_bus = true`, `use_reactive_limits = true`,
  controls off, `acSolverType = NEWTON_RAPHSON`,
  `maxNewtonRaphsonIterations = 800`.
- Result:
  all `98` endpoint LF runs converge:
  `94/94` current endpoints and `4/4` PM-like endpoints.
- Independence caveat:
  Pg rows are dispatch/slack-balance checks, and generator/PV/slack voltage
  magnitudes are setpoint-like. The more meaningful independent checks are
  PQ-bus voltage magnitudes, Qg, branch thermal levels, and angle levels.
- Independent post-LF findings:
  - Python current: `857` independent LF violations across the 94 endpoint
    rows, with max violations about `7.03e-5` for Qg, `3.94e-5` for thermal
    squared-flow margins, and `4.60e-6` for VM.
  - Python PM-like: `5` independent LF violations across the 4 endpoint rows,
    with max violations about `6.39e-5` for Qg and `1.15e-6` for VM; no
    independent thermal or angle violations.
- Interpretation:
  this PyPowSyBl diagnostic is now implemented and informative, but it should
  be read as an external OpenLoadFlow/MATPOWER-import consistency check, not as
  a proof that the Python OPF formulation and PowerModels solve exactly the
  same post-PF problem under every control convention.

Current Python interpretation:

- For the solved objective-mismatch scenarios tested so far, the PowerModels
  point is representable by the current and PM-like Python formulations after
  aligning voltage-angle reference conventions.
- The remaining mismatch should therefore be investigated as a local optimum /
  solver trajectory issue first, not as a missing bus shunt term.
- Normal generation-cost OPF with zero start confirms this:
  - current-zero solves and matches PowerModels on four of six compact-reference
    rows, but still misses `load_x1.2` reactive stress and `qg_limits_x0.8`;
  - PM-like zero solves and matches PowerModels on five of six rows, including
    the two current-zero misses;
  - PM-like zero misses `load_x0.8`, where current-zero matches PowerModels.
- PM-like zero has lower median iterations than current-zero on the six-row
  batch (`264` vs `490`), but it is not universally dominant.
- Next: use the full-sweep Python PM-like equality-only tables together with
  the full-sweep agreement audit to decide whether any remaining Python
  discrepancies need a constrained-deviation or shunt-toggle follow-up.

## 5. Export Results Tables

Status: wait.

Do not upload yet. When ready, export/share:

- full Python comparison table;
- own-vs-PowerModels Python summary;
- per-scenario `case1888_rte` generator, branch, bus, and binding tables for
  the discrepancy cases;
- relevant case118/case300/case1888_rte N-1/security tables.

## 6. Self-Inspection Discipline

Status: ongoing.

For the meeting, every claim about `case1888_rte` should cite a raw table row or
an element-level inspection result. Avoid saying "different optimum" unless the
generator, branch, binding, and feasibility checks support it.
