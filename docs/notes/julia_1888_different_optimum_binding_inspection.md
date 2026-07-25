# Julia 1888 RTE Different-Optimum / Binding Inspection

Date: 2026-07-16

Purpose: document the self-inspection evidence for the two `1888_rte`
scenarios where the current Julia AC-OPF model shows material variable
differences from PowerModels. The question is whether these rows look like a
formulation error, numerical binding-classification noise, or a different
local optimum / active-set choice.

## Files

Focused outputs:

- `results/own_julia/deviation_minimization/1888_significant_scenario_summary.csv`
- `results/own_julia/deviation_minimization/1888_top_variable_difference_elements.csv`
- `results/own_julia/deviation_minimization/1888_binding_mismatch_elements.csv`
- `results/own_julia/deviation_minimization/deviation_minimization_results.csv`

Raw per-scenario tables:

- `results/own_vs_powermodels/julia/1888_rte/load_x0.8/`
- `results/own_vs_powermodels/julia/1888_rte/load_x1.2_reactive_stress/`

## Significant Rows

Only two current-model rows have material variable differences under the
thresholds used here (`Pg/Pf > 0.1`, `Qg/Qf > 0.05`, `Vm > 0.005`,
`Va > 0.05`):

| Scenario | max Pg | max Pf | max Qg | max Qf | max Vm | max Va | objective rel diff |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `1888_rte_load_x0.8` | 4.3253 | 4.3253 | 0.2951 | 0.3893 | 0.00554 | 0.1729 | 1.13e-5 |
| `1888_rte_load_x1.2_reactive_stress` | 1.8000 | 1.8000 | 0.2472 | 0.2670 | 0.01576 | 0.1809 | 5.00e-4 |

Both rows are solved in both models, so the issue is not a simple status
mismatch.

## Deviation-Minimization Evidence

The deviation-minimization experiment solves PowerModels first, then replaces
the own-model cost objective with an L1 objective that tries to reproduce the
PowerModels point in the own model.

For both significant scenarios, the `full` variant pins `Pg`, `Qg`, `Vm`, and
`Va` to the PowerModels target and reaches near-zero deviation. This is the
strongest evidence that the PowerModels solution is feasible in the own Julia
formulation.

Important details from `deviation_minimization_results.csv`:

- `1888_rte_load_x0.8`: `full` has max deviations around numerical tolerance,
  but relaxed variants drift:
  - `no_qg`: max `Pg` 0.7584, max `Qg` 0.1324, max `Vm` 0.0140, max `Va` 0.2181.
  - `no_v`: max `Pg` 0.4121, max `Qg` 0.0262, max `Vm` 0.0121, max `Va` 0.2078.
  - `no_qg_no_vm`: max `Pg` 1.5022, max `Qg` 2.0183, max `Vm` 0.1148, max `Va` 0.1604.
- `1888_rte_load_x1.2_reactive_stress`: all variants stay close once `Pg` is
  pinned, with max deviations near numerical tolerance.

Interpretation: the formulation appears capable of representing the
PowerModels point. The observed original AC-OPF differences are better
explained by local optimum / active-set behavior than by missing constraints.

## Element-Level Evidence

For `1888_rte_load_x0.8`, the largest active-power dispatch and active-flow
differences coincide structurally:

- generator `167` at bus `1713`: max `Pg` difference `4.3253`;
- branch `2369`, `577 -> 1713`: max `Pf` difference `4.3253`.

This is physically consistent with the radial/pendant-bus explanation: the
dispatch difference at the generator bus leaves through the only incident
branch, so `Pg` and `Pf` have the same magnitude.

For `1888_rte_load_x1.2_reactive_stress`, the largest stored generator
difference is:

- generator `79` at bus `1649`: `Pg` difference `6.7000`;
- branch `2281`, `185 -> 1649`: `Pf` difference `6.7000`.

Again, the top generator discrepancy is mirrored by the adjacent branch-flow
discrepancy.

## Binding Mismatch Evidence

Binding mismatches from `1888_binding_mismatch_elements.csv`:

| Scenario | Binding mismatch count | Families |
| --- | ---: | --- |
| `1888_rte_load_x0.8` | 10 | `pg_min`, `pg_max`, `qg_min`, `vm_min` |
| `1888_rte_load_x1.2_reactive_stress` | 39 | `pg_min`, `pg_max`, `qg_min`, `qg_max`, `vm_min`, `vm_max` |

The mismatches include material active-power bound flips, not only small
voltage-bound tolerance effects. Examples:

- `load_x0.8`: generator `215` is at `pg_max` in own and at `pg_min` in
  PowerModels; generator `290` shows the same max/min flip.
- `load_x1.2_reactive_stress`: generator `79` is at `pg_max` in own and at
  `pg_min` in PowerModels; several small generators flip between active
  lower/upper bounds.

This is consistent with different active sets. It is not just a binding
classifier threshold artifact.

## Meeting Conclusion

The current Julia model has two material `1888_rte` variable-discrepancy rows.
The deviation-minimization test shows the PowerModels point is feasible in the
own formulation, while the raw solution tables show coherent generator-bound,
branch-flow, and voltage/reactive active-set differences. Therefore the best
current explanation is:

> The rows are different local optima / active-set selections in a stressed
> nonconvex AC-OPF landscape, not evidence of a missing AC balance or branch
> flow constraint.

Keep the caveat that this does not prove global optimality. It says the
available evidence points away from a formulation bug and toward
non-uniqueness/local optimum behavior.
