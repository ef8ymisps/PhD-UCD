# Julia AC OPF Validation Conclusion For Meeting

Date: 2026-07-17

Use this note as the compact meeting narrative for the Julia AC OPF validation
work. The presentation source folder is:

```text
results/own_vs_powermodels/_organized_for_meeting
```

The original top-level result CSVs remain in place for scripts. The organized
folder contains copied, categorized presentation tables.

## Main Conclusion

The remaining Julia objective and binding-constraint discrepancies are not
explained by missing bus shunts, missing AC equality physics, obvious inequality
limit violations, or solver nonconvergence.

The strongest current interpretation is:

```text
The discrepancies are caused by nonconvex local optimum / solver trajectory
behaviour under the normal generation-cost objective.
```

## Evidence Summary

### 1. Bus shunts are included and necessary

In the current bus-injection formulation, bus shunts enter through the diagonal
of `Ybus`. Therefore they appear inside the bus-injection balance through the
`Gii` and `Bii` terms.

In the PM-like branch-flow formulation, bus shunts are written explicitly in the
nodal balance:

```text
active balance:   ... = Pg - Pd - gs * Vm^2
reactive balance: ... = Qg - Qd + bs * Vm^2
```

The shunt-toggle experiment confirms that these terms are not cosmetic. For the
tested `1888_rte` objective-mismatch scenarios, switching explicit bus shunts
off makes PowerModels, the current Julia model, and the PM-like Julia model all
return `LOCALLY_INFEASIBLE`.

The current model also keeps parallel lines correctly. Parallel branch
admittances are summed inside `Ybus` for nodal balance, but branch-flow,
thermal, and angle constraints are still created per physical branch row. For
`1888_rte` base, the audit finds 2,531 physical branch rows, 197 parallel
bus-pair groups, and 2,531 thermal constraint instances.

Tables:

```text
05_shunt_and_formulation/power_balance_bus_shunt_term_locations.csv
05_shunt_and_formulation/julia_parallel_branch_ybus_audit.csv
05_shunt_and_formulation/shunt_toggle_solution_summary_julia.csv
05_shunt_and_formulation/shunt_toggle_binding_summary_julia.csv
```

### 2. Solver diagnostics are clean

For the objective-mismatch cases, Ipopt reports successful convergence. The
diagnostic table records the Ipopt output quantities with their correct
meaning:

- `inf_pr`: original unscaled primal constraint violation;
- `inf_du`: internal scaled dual infeasibility;
- `mu`: barrier parameter, not exact complementarity.

The suspicious rows solve to Ipopt's desired tolerances according to the visible
callback diagnostics. This reduces the likelihood that the mismatch is simply a
failed solve.

Tables:

```text
01_objective_and_solver/solver_diagnostics_objective_mismatch_julia.csv
01_objective_and_solver/solver_diagnostics_ipopt_notes_julia.csv
```

### 3. Constrained deviation minimization succeeds

The all-constraints deviation-minimization problem was run for both:

- current Julia model vs PowerModels;
- PM-like Julia model vs PowerModels.

This problem keeps all normal OPF constraints active and replaces the cost
objective with an L1 deviation objective to the PowerModels OPF point.

For the `full` variant, where `Pg`, `Qg`, `Vm`, and `Va` are all targeted:

- the deviation to the PowerModels point is numerical zero;
- the evaluated cost is essentially the PowerModels cost;
- binding mismatches against PowerModels are zero.

Therefore the PowerModels OPF point is feasible in both own Julia formulations
when all their normal constraints are active.

Tables:

```text
07_deviation_minimization/current_constrained_deviation_minimization_julia.csv
07_deviation_minimization/current_constrained_deviation_binding_summary_julia.csv
07_deviation_minimization/pm_like_constrained_deviation_minimization_julia.csv
07_deviation_minimization/pm_like_constrained_deviation_binding_summary_julia.csv
```

### 4. Equality-only deviation minimization also succeeds

The equality-only diagnostic removes generator/voltage/thermal/angle
inequality constraints from the diagnostic NLP, keeps only AC equality physics,
and minimizes L1 deviation to the PowerModels OPF point. After the solve, the
removed inequalities are evaluated externally.

For the tested current and PM-like objective-mismatch scenarios:

- the equality-only solve reaches the PowerModels point to numerical tolerance;
- externally evaluated inequality violations are zero;
- binding mismatches against PowerModels are zero.

Therefore the PowerModels point is reachable under own-model AC equality
physics and does not become suspicious when the removed inequalities are
evaluated afterward.

Tables:

```text
07_deviation_minimization/equality_only_deviation_minimization_julia.csv
07_deviation_minimization/equality_only_deviation_binding_summary_julia.csv
07_deviation_minimization/equality_only_deviation_inequality_levels_julia.csv
07_deviation_minimization/equality_only_deviation_inequality_level_by_family_julia.csv
```

## Professor Table Sequence

Show tables in this order.

1. Shunt toggle summary

```text
05_shunt_and_formulation/shunt_toggle_solution_summary_julia.csv
```

Purpose: show that bus shunts are included and necessary. Switching them off
makes the tested `1888_rte` scenarios locally infeasible across all three model
families.

2. Solver diagnostics

```text
01_objective_and_solver/solver_diagnostics_objective_mismatch_julia.csv
```

Purpose: show the suspicious objective-mismatch solves are not obvious failed
Ipopt solves.

3. Objective mismatch summary

```text
01_objective_and_solver/objective_mismatch_summary_julia_current.csv
01_objective_and_solver/objective_mismatch_summary_julia_pm_like.csv
```

Purpose: state which scenarios remain materially different under the normal
generation-cost objective.

4. Constrained deviation summary

```text
07_deviation_minimization/current_constrained_deviation_minimization_julia.csv
07_deviation_minimization/pm_like_constrained_deviation_minimization_julia.csv
07_deviation_minimization/current_constrained_deviation_binding_summary_julia.csv
07_deviation_minimization/pm_like_constrained_deviation_binding_summary_julia.csv
```

Purpose: show that both own formulations can reproduce the PowerModels point
with all normal OPF constraints active.

5. Equality-only deviation summary

```text
07_deviation_minimization/equality_only_deviation_minimization_julia.csv
07_deviation_minimization/equality_only_deviation_binding_summary_julia.csv
07_deviation_minimization/equality_only_deviation_inequality_level_by_family_julia.csv
```

Purpose: show that equality physics alone can reach the PowerModels point, and
that externally evaluated inequalities do not reveal violations.

6. Binding mismatch details only if asked

```text
02_binding_constraints/binding_constraint_mismatch_details_julia_current.csv
02_binding_constraints/binding_constraint_mismatch_details_julia_pm_like.csv
07_deviation_minimization/*_binding_differences_julia.csv
```

Purpose: inspect exact generator, bus, branch, or constraint instances only if
the professor asks for element-level evidence.

## What To Say Verbally

Use this short version:

```text
I checked the suspected missing bus-level terms. The bus shunts are present:
in the current model through the Ybus diagonal, and in the PM-like model
explicitly as -gs Vm^2 and +bs Vm^2. Switching them off makes the tested 1888
RTE mismatch scenarios locally infeasible in all models, including
PowerModels.

Then I checked whether the objective mismatch is due to solver failure or a
wrong feasible region. Ipopt diagnostics look clean. Constrained
deviation-minimization shows that both my current and PM-like Julia models can
reproduce the PowerModels OPF point with all normal constraints active.
Equality-only deviation-minimization also reaches the PowerModels point, and
the externally evaluated inequalities show no violations or binding mismatches.

So the evidence does not point to missing constraints. It points to nonconvex
local optimum or solver-trajectory behaviour under the normal generation-cost
objective.
```
