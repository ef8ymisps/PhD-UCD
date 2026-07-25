# Julia Parallel Branch And Equality-Level Audit

Date: 2026-07-17

This note answers two follow-up validation questions:

1. Does the current Ybus formulation lose parallel-line information?
2. Did the equality-only deviation-minimization diagnostic actually evaluate
   inequality levels after the solve?

## Parallel Branch Handling

The current Julia bus-injection model uses Ybus for nodal power balance. In
Ybus, parallel lines between the same two buses are summed into the same
off-diagonal admittance entry. That is mathematically correct for the bus
injection equations.

This does not mean that the physical parallel branches disappear from the OPF.
Thermal limits, angle-difference limits, and branch-flow rows are still built
per physical `branch_id` in the branch-constraint block.

The audit table is:

```text
results/own_vs_powermodels/_organized_for_meeting/05_shunt_and_formulation/julia_parallel_branch_ybus_audit.csv
```

Important `1888_rte` base-case counts:

- physical branch rows: 2,531;
- unique undirected bus pairs: 2,308;
- parallel bus-pair groups: 197;
- branches inside parallel groups: 420;
- maximum parallel count on one bus pair: 5;
- thermal constraint instances: 2,531;
- thermal constraints match physical branch rows: true.

Interpretation:

```text
Ybus aggregates parallel admittances for nodal balance, but branch limits remain
per physical branch. Therefore parallel lines are not ignored by the model.
```

## Equality-Only Inequality Levels

The equality-only deviation-minimization diagnostic does evaluate the removed
inequality constraints after the solve.

Full inequality-level table:

```text
results/own_vs_powermodels/_organized_for_meeting/07_deviation_minimization/equality_only_deviation_inequality_levels_julia.csv
```

Compact presentation tables:

```text
results/own_vs_powermodels/_organized_for_meeting/07_deviation_minimization/equality_only_deviation_inequality_level_by_family_julia.csv
results/own_vs_powermodels/_organized_for_meeting/07_deviation_minimization/equality_only_deviation_inequality_level_top_rows_julia.csv
```

The compact family-level table records, by case/scenario/variant/constraint
type:

- number of evaluated constraints;
- number of binding mismatches;
- number of equality-only limit violations;
- maximum violation;
- maximum absolute value difference against PowerModels;
- maximum absolute margin difference against PowerModels.

For the tested `1888_rte` objective-mismatch scenarios, the family-level tables
show zero binding mismatches and zero material violations after the equality-only
projection. The largest value and margin differences are at numerical tolerance
level.

Interpretation:

```text
The equality-only projection reaches the PowerModels point, and the externally
evaluated generator, voltage, thermal, and angle inequalities agree with
PowerModels to numerical tolerance.
```
