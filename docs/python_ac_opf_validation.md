# Python AC OPF Validation

## Purpose

This note freezes the current Python AC OPF validation milestone.

The Python AC OPF implementation is validated against PowerModels/PGLib for the
tested small benchmark cases and controlled scenarios. PowerModels/PGLib is the
OPF reference because it provides the benchmark network data and real generator
cost data. PyPowSyBl remains the PF/security/ML-data reference, not the OPF
objective reference.

## Tested Scope

Cases:

- `pglib_opf_case5_pjm.m`
- `pglib_opf_case14_ieee.m`
- `pglib_opf_case30_ieee.m`

Scenarios:

- `base`
- `relaxed`
- `branch_congested`
- `generator_limited`
- `load_scaled`

Reference solver:

- PowerModels AC OPF
- Ipopt

Python solver:

- Own AC OPF formulation
- SciPy SLSQP backend

## Compared Quantities

For solved feasible cases, the validation compares:

- objective value
- generator `Pg` and `Qg`
- bus `Vm` and `Va`
- branch `Pf`, `Qf`, `Pt`, and `Qt`
- active and reactive power-balance residuals
- generator limit violations
- voltage limit violations
- branch apparent-power violations
- branch angle-difference violations

For infeasible or stressed cases, validation compares practical outcome class
rather than exact raw solver status strings.

Outcome classes:

- `SOLVED_FEASIBLE`
- `FAILED_OR_INFEASIBLE`
- `NUMERICAL_FAILURE`

## Current Result

The latest AC OPF validation run produced:

```text
outcome_match = true
```

for all tested case/scenario combinations.

Solved feasible cases show small objective, dispatch, voltage, and branch-flow
differences consistent with nonlinear solver tolerances. Constraint residuals
are small for solved cases, and stressed/infeasible cases are classified
consistently with PowerModels.

## Relaxed-Case Reactive Dispatch Note

The largest remaining difference is in relaxed-case reactive dispatch, especially:

```text
pglib_opf_case5_pjm / relaxed
max_qg_diff ~= 0.304 pu
```

This is not currently treated as a formulation failure because the diagnostic
checks show:

- `Pg` values match closely.
- `Vm` and `Va` match closely.
- active and reactive power-balance residuals are small.
- generator reactive limits are relaxed and non-binding.
- voltage limits are relaxed and mostly non-binding except intentionally loose
  upper-voltage operation.
- branch thermal and angle-difference violations are zero.

Since the objective depends on active generation cost, not reactive generation
cost, relaxed reactive limits can create non-unique or weakly determined `Qg`
allocations. In that situation, two solvers can select different feasible
reactive dispatches while still solving the same AC OPF problem.

The supporting diagnostic table is stored at:

```text
results/own_python/ac_opf/ac_opf_relaxed_qg_diagnostic.csv
```

## Conclusion

Within the tested scope, the Python AC OPF model is validated against
PowerModels/PGLib. The remaining relaxed-case `Qg` differences are documented as
likely non-unique reactive allocation under relaxed constraints, not as evidence
of an AC OPF formulation mismatch.

The next research-relevant layer is:

```text
base OPF solution
-> N-1 contingency analysis
-> critical contingency labels
-> ML-ready features and labels
```
