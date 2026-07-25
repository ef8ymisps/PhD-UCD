# AC OPF Large-Case Lower-Bound Policy

Date: 2026-07-20

This note closes the lower-bound policy question for the AC-OPF optimality-gap
validation layer.

## Existing Validation Layers

Two lower-bound scripts exist:

- `julia/own_models/scripts/15_run_ac_opf_optimality_gap_experiment.jl`
  computes a one-shot SOC relaxation lower bound with `SOCWRPowerModel`.
- `julia/own_models/scripts/19_run_ac_opf_optimality_gap_obbt.jl` computes a
  tighter PowerModels OBBT/QC-based lower bound where practical.

These lower bounds answer a different question from the own-vs-PowerModels
dispatch comparison. A cheaper own-model local OPF point can still be valid if
it remains above the relaxation lower bound. But an AC objective below the
relaxation lower bound is a hard validation failure unless explained by a
numerical or modelling issue in the bound calculation itself.

## Runtime Evidence

The saved OBBT result table currently contains completed rows only through
`300_ieee`:

| Case | Buses | OBBT iterations | Runtime |
| --- | ---: | ---: | ---: |
| `5_pjm` | 5 | 6 | 4.94 s |
| `14_ieee` | 14 | 5 | 9.86 s |
| `30_ieee` | 30 | 5 | 38.08 s |
| `118_ieee` | 118 | 6 | 1063.87 s |
| `300_ieee` | 300 | 3 | 4346.79 s |

For `300_ieee`, this is about 24 minutes per OBBT iteration in the recorded
run. The OBBT script header documents the same scaling concern using the
earlier calibration of roughly 28 minutes per iteration at 300 buses.

The script plan included a possible one-iteration `1354_pegase` row, but the
saved CSV does not contain that row. Therefore the current empirical OBBT
evidence stops at `300_ieee`.

## Policy Decision

Routine validation uses this concrete rule:

```text
If n_buses > 300, use the one-shot SOC relaxation as the permanent routine
lower bound. Do not run OBBT as part of the normal validation workflow unless a
separate long-running compute job is explicitly scheduled and documented.
```

This means the following existing large cases are SOC-only for routine
optimality-gap reporting:

- `1354_pegase`;
- `1888_rte`;
- `6470_rte`;
- the stressed `1888_rte` validation scenarios.

Cases with `n_buses <= 300` may use OBBT when needed:

- small cases can run OBBT to convergence or near-convergence;
- `300_ieee` can use a small bounded OBBT iteration count because the runtime is
  already around an hour for three iterations.

## Why Not Add One OBBT Iteration For The Large Cases?

A single OBBT iteration for `1354_pegase`, `1888_rte`, or `6470_rte` would be
expensive and would still not give a converged OBBT bound. Since `300_ieee`
already costs around 24-28 minutes per iteration, the larger cases are expected
to require many hours per iteration or worse.

For routine validation, the value of a one-shot large-case OBBT row is not high
enough to justify the cost. It would also create a mixed interpretation problem:
one incomplete OBBT iteration is tighter than SOC, but not comparable to the
converged/small-case OBBT rows.

Therefore the large-case lower-bound layer is intentionally SOC-only unless a
future experiment is explicitly scoped as a long OBBT compute run.

## Hard Invariant

Implementation history: the already-saved CSVs were first updated by the
refresh utility:

```text
julia/own_models/scripts/39_refresh_optimality_gap_quality_flags.jl
```

That refresh was a one-off correction to existing outputs, not evidence that
fresh runs of scripts `15` and `19` would automatically write the new columns.
The scripts have now been wired to include the same shared helper module and
annotate their `DataFrame`s before `CSV.write`, so future fresh runs produce the
invariant columns directly.

Both optimality-gap output scripts now include a hard invariant flag:

```text
bound_violated = true
```

means the AC-OPF objective is below the reported lower bound by more than the
relative tolerance:

```text
objective < lower_bound - 1e-6 * (1 + abs(lower_bound))
```

This should never occur for a correct AC solution and a valid relaxation lower
bound. If it appears in a new case or scenario, it should not be explained away
as the known `1888_rte` different-local-optimum issue. The correct next step is
to point the existing validation toolkit at the AC-vs-bound comparison itself:

- deviation minimization;
- shunt-toggle checks;
- equality-only deviation;
- solver diagnostics;
- data-quality flags, including negative branch impedance.

## Negative Branch Impedance Flag

The case-library scan

```text
julia/own_models/scripts/38_scan_negative_branch_impedance.jl
```

uses `PowerModels.parse_file` and writes:

```text
results/own_julia/data_quality/negative_branch_impedance_scan.csv
results/own_julia/data_quality/negative_branch_impedance_summary.csv
```

The optimality-gap result tables include:

- `has_negative_branch_r`;
- `has_negative_branch_x`;
- `negative_branch_count`.

These fields are precautionary. Negative resistance or reactance values can
appear in real-grid-derived cases, for example from Ward-equivalent network
reduction. We are not currently using a DC lower bound for this validation
layer, but these flags make unusual branch data visible before interpreting
any unexpected bound relationship as an OPF formulation bug.

The current scan of `data/raw/pglib/matpower_m/base_case/*.m` found:

| Case | Negative r count | Negative x count | Negative branch count |
| --- | ---: | ---: | ---: |
| `13659_pegase` | 78 | 16 | 94 |
| `1888_rte` | 0 | 77 | 77 |
| `300_ieee` | 0 | 1 | 1 |
| `6470_rte` | 0 | 80 | 80 |
| `9241_pegase` | 75 | 16 | 91 |

The default AC-OPF optimality-gap set therefore has negative-reactance flags on
`300_ieee`, `1888_rte`, and `6470_rte`. No current SOC or OBBT optimality-gap
row violates the lower-bound invariant.
