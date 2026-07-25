# PV-PQ Switching for AC Power Flow

This note documents the Phase 1/2 implementation of generator reactive-power
limit enforcement in the own AC power-flow solvers.

## Scope

Implemented:

- fixed-type Newton-Raphson AC PF core;
- optional one-way PV-PQ switching controller;
- aggregate bus reactive-limit checks;
- deterministic individual generator Q allocation;
- slack-bus Q-limit reporting.

Not implemented in this phase:

- reversible PV-PQ switching;
- cycle-detection logic for reversible switching;
- AC OPF changes.

AC OPF is intentionally unchanged because it already models `Qg` as a bounded
decision variable and `Vm` as an optimization variable.

## Formulation

For fixed bus types, the Newton state remains:

- voltage angles for PV and PQ buses;
- voltage magnitudes for PQ buses.

PV bus voltage magnitudes and the slack angle remain fixed.

The fixed-type core accepts working PV/PQ sets, optional reactive-power
specification overrides, and optional voltage warm starts. The mismatch and
Jacobian equations are otherwise unchanged.

## One-Way Switching

After each converged fixed-type Newton solve:

1. Reconstruct aggregate reactive generation at each online generator bus:
   `Qg_bus = Qcalc_bus + Qd_bus`.
2. For every non-slack PV bus, compare against aggregate limits:
   `Qmin_bus = sum(Qmin_g)` and `Qmax_bus = sum(Qmax_g)`.
3. If `Qg_bus > Qmax_bus + tolerance`, switch PV to PQ and fix
   `Qg_bus = Qmax_bus`.
4. If `Qg_bus < Qmin_bus - tolerance`, switch PV to PQ and fix
   `Qg_bus = Qmin_bus`.
5. Resolve the network using the previous `Vm/Va` as a warm start.

For a switched bus:

```text
Qspec_bus = Qg_fixed - Qd_bus
```

Bus shunts and branch charging are not subtracted from this specification
because they are already represented in `Ybus` and therefore in `Qcalc`.

## Generator Q Allocation

AC PF determines only aggregate reactive generation at a bus. Individual
generator Q values are assigned by a deterministic reporting convention:

1. Start every generator at `Qmin`.
2. Allocate the remaining bus Q requirement in proportion to
   `Qmax_g - Qmin_g`.
3. Respect individual generator limits.
4. Report aggregate infeasibility if the requested bus total lies outside
   aggregate capability.

This allocation is not uniquely determined by the PF equations.

## Slack Bus

The slack/reference bus is never converted to PQ in this phase.

After convergence, the slack-bus reactive requirement is reconstructed and
checked against aggregate and individual limits. A slack Q violation is reported
separately using:

```text
power_flow_converged = true
q_limit_converged = false
slack_q_violation = true
termination_reason = "slack_q_limit_violated"
```

## Public APIs

Python:

```python
solve_ac_power_flow(case)                       # unchanged fixed-type PF
solve_ac_power_flow(case, enforce_q_limits=True)
solve_ac_power_flow_newton_fixed_types(...)
solve_ac_power_flow_with_q_limits(...)
allocate_qg_to_generators(...)
```

Julia:

```julia
solve_ac_power_flow_own(case; method=:newton)          # unchanged fixed-type PF
solve_ac_power_flow_own(case; method=:newton_q_limits)
solve_ac_power_flow_newton_fixed_types(...)
solve_ac_power_flow_with_q_limits(...)
allocate_qg_to_generators(...)
```

## Result Fields

The AC PF result now reports:

- `final_bus_types`;
- `switched_buses`;
- `switching_events`;
- `outer_iterations`;
- `qg_by_bus`;
- `qg_by_generator`;
- `slack_q_violation`;
- `q_limit_converged`;
- `termination_reason`;
- `power_flow_converged`.

Existing fixed-type callers continue to receive the original fields.

## Reversible Switching

Reversible switching is intentionally deferred. The preferred next design is
trial restoration:

1. Temporarily restore a switched bus to PV.
2. Restore the original voltage setpoint.
3. Remove the fixed-Q override.
4. Resolve PF.
5. Accept restoration only if required Q lies safely inside limits.

This avoids incorrectly using the current PQ solution, where Q is fixed by
construction, as evidence that PV restoration is feasible.
