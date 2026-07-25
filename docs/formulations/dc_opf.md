# DC OPF Formulation

Date: 2026-07-15

This is the repository-level formulation note for the own DC optimal power flow
models. The current Python DC OPF implementation is Pyomo/HiGHS; the Julia DC
OPF implementation is JuMP/HiGHS.

## Sets

- `B`: buses
- `G`: generators
- `L`: active branches
- `G_i`: generators connected to bus `i`

## Decision Variables

- `theta_i`: voltage angle at bus `i`
- `Pg_g`: active output of generator `g`

## Objective

Minimize active generation cost:

```text
min sum_g c2_g Pg_g^2 + c1_g Pg_g + c0_g
```

Linear-cost cases are solved as linear programs. Quadratic costs require a
solver path that supports the quadratic objective.

## DC Power Balance

For each bus `i`:

```text
sum_{g in G_i} Pg_g - Pd_i - Gs_i = sum_{l incident to i} Pf_l
```

The `Gs_i` term is the active bus shunt conductance/load contribution. It is
included in the own DC network injection construction and should be included
when presenting the formulation.

## Branch Flow

For branch `l = (i, j)`, the DC active-power flow is:

```text
Pf_l = b_l (theta_i - theta_j - shift_l)
```

where `b_l` is the branch susceptance term used by the own DC network model.

## Bounds And Limits

Generator bounds:

```text
Pmin_g <= Pg_g <= Pmax_g
```

Slack/reference angle:

```text
theta_ref = 0
```

Branch flow and angle limits:

```text
-rate_l <= Pf_l <= rate_l
angmin_l <= theta_i - theta_j <= angmax_l
```

## Validation Implication

DC OPF is convex under the linear/quadratic cost assumptions used here, so
large dispatch differences are less likely to be local-optimum effects than in
AC OPF. Remaining differences should usually be traced to units, inactive/active
status, cost interpretation, bounds, shunts, branch orientation, or solver
tolerances.
