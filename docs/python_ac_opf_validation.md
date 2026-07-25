# Python AC OPF Validation

Date: 2026-07-15

This note records the current Python AC OPF validation status. It supersedes
older notes that described the Python AC OPF as a SciPy/SLSQP solve.

## Current Implementation

The current Python AC OPF implementation builds a Pyomo nonlinear model and
solves it with Ipopt.

- Model builder: `python/own_models/src/opf/ac/model.py`
- Variables: `python/own_models/src/opf/ac/variables.py`
- Constraints and post-solve violation checks:
  `python/own_models/src/opf/ac/constraints.py`
- Objective: `python/own_models/src/opf/ac/objective.py`
- Solver wrapper: `python/own_models/src/opf/ac/solve.py`

The optimized variables are:

- bus voltage magnitude `Vm`
- bus voltage angle `Va`
- generator active power `Pg`
- generator reactive power `Qg`

The enforced limits include generator `P/Q` bounds, bus voltage bounds, slack
angle, AC active/reactive nodal balance, branch angle-difference limits, and
two-ended branch apparent-power limits.

AC OPF is nonconvex, so Ipopt returns a local nonlinear solution. Matching a
recognized reference implementation is therefore evidence of formulation
consistency on the tested cases, not proof of global optimality.

## Reference Comparison

The Python validation compares the own Pyomo/Ipopt results against
PowerModels.jl AC OPF reference results exported from Julia.

Primary current table:

```text
results/own_python/ac_opf_validation/compact_ac_opf_full_comparison.csv
```

The comparison checks:

- solve/outcome class
- objective value
- generator `Pg` and `Qg`
- bus `Vm` and `Va`
- branch `Pf`, `Qf`, `Pt`, and `Qt`
- constraint violations and binding-limit classifications
- solver iteration counts and wall time where available

## Current Interpretation

Most rows either solve and match near numerical tolerance or fail consistently
under intentionally difficult stress. The current evidence supports the AC OPF
formulation, including bus shunts and branch charging through the network
admittance model.

The remaining issues are concentrated in large/stressed `case1888_rte`
scenarios. These should be treated as solver robustness and local-solution
selection issues until disproved by element-level inspection.

From the full comparison table:

- `215` scenarios are `optimal` vs `LOCALLY_SOLVED`.
- `92` scenarios are infeasible on both sides.
- The clearest outcome mismatch is one Python compact-pipeline row:
  `1888_rte_generator_114_outage`.

This means the current debugging question is narrow: why does the compact
MAT-based Python validation fail on that large generator outage when the
JSON-based own-vs-PowerModels run for the same generator-outage concept matches
PowerModels cleanly?

## Open Debugging Targets

The strongest current Python AC OPF debugging target is:

```text
1888_rte_generator_114_outage
```

In the full-comparison table this is the clearest outcome mismatch: the own
model fails the post-solve feasibility/outcome test while PowerModels reports a
local solution. This should be inspected before claiming the Python AC OPF is
fully validated on stressed large cases.

Observed evidence:

- Full comparison:
  `own_status = infeasible`, `pm_status = LOCALLY_SOLVED`,
  `objective_rel_diff = 0.07545`, own `771` iterations vs PM `210`.
- Targeted diagnostic:
  Ipopt converges to a locally infeasible point; the remaining material
  residual is reactive balance, about `0.019` p.u. maximum. Active balance,
  voltage, thermal, and angle checks are zero in that diagnostic table.
- Older compact sweep:
  the same row hit `maxIterations`; treat that output as stale relative to the
  later diagnostic/full-comparison artifacts.
- JSON own-vs-PowerModels spotlight:
  `1888_rte_gen_114_out...` solves and matches PM to numerical precision.

Working hypothesis:

```text
compact MAT pipeline / initialization / Ipopt robustness issue
before formulation bug
```

Targeted rerun result:

```text
results/own_python/ac_opf_validation/debug_gen114_outage_warm_starts.csv
```

The compact MAT case reproduces the PowerModels objective when the own model is
started from `flat` or `zero`:

- compact MAT + `case`: locally infeasible, `1061` iterations, max Q-balance
  residual `1.896e-2` p.u.
- compact MAT + `flat`: optimal, `192` iterations, objective
  `1500311.129639`, residuals near numerical tolerance.
- compact MAT + `zero`: optimal, `742` iterations, objective
  `1500311.129639`, residuals near numerical tolerance.
- JSON + `case`: optimal, `575` iterations, objective `1500311.129639`.

Conclusion: this row is a warm-start-sensitive Ipopt/local-basin issue in the
compact Python validation path. It is not evidence for an AC OPF formulation
bug. The practical validation fix is to rerun this scenario, and possibly the
remaining abnormal large outage rows, with `warm_start="flat"`.

Implementation update: the compact AC validation sweep default is now
`warm_start="flat"` in `python/own_models/src/analysis/ac_opf_sweeps.py`.
The lower-level `solve_ac_opf_pyomo` function still accepts explicit
warm-start modes, so experiments can continue to compare `case`, `flat`, and
`zero`.

Across the existing base-case warm-start ablation, `flat` is mostly neutral on
small and mid-size systems, improves `6470_rte` substantially, and fixes this
`1888_rte` gen-114 compact outage. It is not universally fastest: `case` remains
better for the `1888_rte` reactive-stress `Qd x1.2` scenario.

Formulation note: Python also implements the branch-flow formulation, but it is
not the default. The all-base-case Python matrix in
`results/own_python/ac_opf_formulation_warm_start_matrix/formulation_warm_start_matrix.csv`
shows why: with `warm_start="flat"`, branch-flow is neutral/slightly worse on
most systems, much worse on `1888_rte` and `6470_rte`, and reaches a higher
local optimum on `1888_rte`. Therefore Python keeps
`formulation="bus_injection"` while Julia can default to branch-flow.

Other large/stressed `1888_rte` rows show objective and dispatch gaps consistent
with different local optima or different active binding patterns, especially
reactive-stress and tightened-Q-limit scenarios.

## Different-Optimum Evidence Needed

To be confident that a large `Pg`/`Pf` discrepancy is a different optimum rather
than a formulation bug, inspect the detailed result tables for the same
scenario:

1. Sort `generators.csv` by `abs_pg_diff`.
2. Sort `branches.csv` by `abs_pf_diff`.
3. Map the top generator bus to the top discrepant branch endpoints.
4. Check whether the generator is at `pmin` in one model and `pmax` in the
   other, or whether nearby constraints are binding differently.
5. Check `constraints_binding.csv` for voltage, reactive-power, thermal, and
   angle-limit binding mismatches around the same buses/branches.
6. Confirm both solutions satisfy AC balance and engineering limits to Ipopt
   tolerance.

If the largest branch-flow discrepancy is on a branch adjacent to the largest
active-dispatch discrepancy, and both solutions are feasible but have different
binding sets, the branch-flow gap is likely a consequence of different dispatch
rather than an independent branch-flow formula error.

## Known Documentation Caveat

Some older audit/fundamentals notes were written when the Python AC OPF path was
SciPy/SLSQP. Those statements are historical and should not be used for the
current validation narrative unless explicitly marked as legacy.
