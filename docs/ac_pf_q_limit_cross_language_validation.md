# AC PF Q-Limit Cross-Language Validation

This note records the first validation step after adding one-way PV-PQ switching
to the Python and Julia AC power-flow implementations.

## Scope

The comparison checks the Python and Julia implementations against each other
on the same scenario-modified PGLib data.

Tested cases:

- `pglib_opf_case14_ieee`
- `pglib_opf_case30_ieee`
- `pglib_opf_case118_ieee`

Tested scenarios:

- `base`: original PGLib reactive limits.
- `wide_q_limits`: all generator Q limits widened so no PV-PQ switching is
  expected.
- `single_pv_qmax`: one non-slack PV bus is forced just above its aggregate
  Qmax and should switch to PQ.
- `multi_pv_slack_qmax`: multiple non-slack PV buses are forced above Qmax,
  and the slack bus Q limit is also tightened. Non-slack PV buses switch to PQ;
  the slack remains slack and reports a slack Q violation.

## Method

The Julia exporter:

```text
julia/own_models/scripts/06_export_ac_pf_q_limit_results.jl
```

parses the PGLib case, applies the deterministic Q-limit scenario, exports the
modified case JSON, solves Julia one-way AC PF, and exports the Julia result.

The Python comparator:

```text
python/own_models/scripts/11_compare_ac_pf_q_limits_python_julia.py
```

loads the same exported modified case JSON, solves Python one-way AC PF, and
compares:

- switching sequence
- final bus types
- voltage magnitudes and angles
- bus-level Qg
- generator-level Qg
- branch P/Q flows
- final mismatch norm
- termination reason

## Outputs

Comparison CSVs are written under:

```text
results/comparisons/ac_pf_q_limits/
```

Main summary:

```text
results/comparisons/ac_pf_q_limits/ac_pf_q_limits_summary.csv
```

Detailed tables:

```text
ac_pf_q_limits_buses.csv
ac_pf_q_limits_generators.csv
ac_pf_q_limits_branches.csv
ac_pf_q_limits_events.csv
```

## Result

For all 12 tested case/scenario combinations:

- Python and Julia both converged.
- Switching sequences matched.
- Final bus types matched.
- Termination reasons matched.
- Switched bus sets matched.
- Voltage, angle, bus Qg, and generator Qg differences were at numerical
  roundoff level.

Largest observed differences in the summary table were approximately:

- voltage magnitude: `1.4e-14`
- voltage angle: `1.2e-14`
- bus-level Qg: `1.0e-13`
- generator-level Qg: `1.0e-13`

This validates that the Python and Julia one-way PV-PQ switching
implementations are behaviourally aligned on the selected PGLib systems.

## Next Step

The next validation layer is comparison against OpenLoadFlow/PyPowSyBl with
reactive-limit enforcement enabled. Reversible switching remains deferred
because it introduces restoration and cycling logic, rather than simply
enforcing one-way reactive limits.
