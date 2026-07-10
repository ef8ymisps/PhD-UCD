# Power Flow vs Optimal Power Flow Taxonomy

This document clarifies the conceptual distinction between power-flow problems and optimal power-flow problems in this repository.

The main correction is that power flow is not, by itself, an optimization problem. Power flow solves a set of physical network equations for a fixed operating condition. Optimal power flow solves an optimization problem where the power-flow equations appear as constraints.

## Summary Table

| Problem | Is it optimization? | Main unknowns | Solver type | Main purpose |
|---|---:|---|---|---|
| DC Power Flow | No | Voltage angles, slack injection | Linear system | Compute active-power flows under DC assumptions |
| AC Power Flow | No | Voltage angles, PQ-bus voltage magnitudes, reactive outputs, slack injection | Nonlinear equation solver, usually Newton-Raphson | Compute the AC operating state for fixed injections and bus types |
| DC Optimal Power Flow | Yes | Generator active powers, voltage angles | LP/QP solver | Find least-cost dispatch under DC network constraints |
| AC Optimal Power Flow | Yes | Generator active/reactive powers, voltage magnitudes, voltage angles | Nonlinear optimization solver | Find least-cost AC-feasible operating point |

## DC Power Flow

### Inputs

- Bus loads
- Generator active-power injections, usually fixed except the slack generator
- Branch reactances
- Reference/slack bus angle

### Unknowns

- Bus voltage angles
- Slack active-power injection

### Equations

The DC power-flow model solves a linear active-power balance system:

```math
B\theta = P
```

where `B` is the network susceptance matrix, `theta` is the vector of bus voltage angles, and `P` is the vector of net active-power injections.

There is no objective function.

## AC Power Flow

### Inputs

- Bus types: slack, PV, PQ
- Specified active power at PV and PQ buses
- Specified reactive power at PQ buses
- Specified voltage magnitude at slack and PV buses
- Specified slack voltage angle

### Unknowns

- Voltage angles at PV and PQ buses
- Voltage magnitudes at PQ buses
- Reactive power injections at PV and slack buses
- Active power injection at the slack bus

### Equations

The AC power-flow model solves the nonlinear active and reactive power-balance equations:

```math
P_i = |V_i| \sum_j |V_j|
\left(
G_{ij}\cos(\theta_i-\theta_j)
+
B_{ij}\sin(\theta_i-\theta_j)
\right)
```

```math
Q_i = |V_i| \sum_j |V_j|
\left(
G_{ij}\sin(\theta_i-\theta_j)
-
B_{ij}\cos(\theta_i-\theta_j)
\right)
```

There is no objective function.

A Newton-Raphson method is the classical default solver for AC power flow. An Ipopt/JuMP formulation can be used as a validation or prototyping method, but it should not be presented as the default conceptual definition of load flow.

## DC Optimal Power Flow

### Decision Variables

- Generator active powers `Pg`
- Bus voltage angles `theta`
- Optionally branch active flows `Pij`

### Objective

Usually minimize generation cost:

```math
\min \sum_g c_{2,g}P_g^2 + c_{1,g}P_g + c_{0,g}
```

### Constraints

- DC active-power balance
- Generator active-power limits
- Branch active-flow limits
- Reference angle constraint

## AC Optimal Power Flow

### Decision Variables

- Generator active powers `Pg`
- Generator reactive powers `Qg`
- Bus voltage magnitudes `|V_i|`
- Bus voltage angles `theta_i`

### Objective

Usually minimize generation cost:

```math
\min \sum_g c_{2,g}P_g^2 + c_{1,g}P_g + c_{0,g}
```

### Constraints

- AC active-power balance
- AC reactive-power balance
- Generator active/reactive power limits
- Voltage magnitude limits
- Branch apparent-power limits
- Reference angle constraint

## Repository Convention

In this repository:

- `dc_power_flow.jl` should implement a non-optimization DC PF solve.
- `ac_power_flow.jl` should eventually implement a classical nonlinear equation solver, preferably Newton-Raphson.
- The current JuMP/Ipopt AC PF implementation should be treated as a validation/prototyping solver.
- `dc_opf.jl` and `ac_opf.jl` are optimization formulations and correctly use JuMP with HiGHS or Ipopt.

This document fixes the conceptual issue directly.
