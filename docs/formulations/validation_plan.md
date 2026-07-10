# Validation Plan for Custom PF/OPF Implementations

This document defines how the custom PF/OPF implementations will be validated against reference tools and controlled perturbation tests.

Matching PowerModels on one case is not sufficient. The implementation must be tested under relaxed, congested, generator-limited, and infeasible conditions.

## Validation Metrics

### Objective Comparison

For OPF problems, compare the absolute objective difference:

```math
|\text{obj}^{own} - \text{obj}^{ref}|
```

and the relative objective difference:

```math
\frac{|\text{obj}^{own} - \text{obj}^{ref}|}{1 + |\text{obj}^{ref}|}
```

### Variable Comparison

Use maximum absolute deviation:

```math
\max_i |x_i^{own} - x_i^{ref}|
```

Compare:

- Generator active power `Pg`
- Generator reactive power `Qg`, for AC OPF
- Voltage magnitudes `|V|`
- Voltage angles `theta`
- Branch active flows `Pf`, `Pt`
- Branch reactive flows `Qf`, `Qt`, for AC models

### Constraint Checks

Check:

- Maximum active-power balance violation
- Maximum reactive-power balance violation
- Maximum branch-limit violation
- Maximum voltage-limit violation
- Maximum generator-limit violation

The validation should answer:

1. Are the equations satisfied?
2. Are the operational limits respected?
3. Is the objective comparable to the reference?
4. Are the same constraints active or binding?

## Test A: Relaxed System

### Modification

Use a small case, such as 5-bus or 14-bus, and relax:

- Branch thermal limits
- Generator upper limits
- Voltage bounds

### Expected Result

- Feasible solution
- No binding branch constraints
- Objective close to reference solver
- Voltage and angle values within numerical tolerance

### Purpose

Check that the base equations and objective are correct when the system is easy.

## Test B: Congested System

### Modification

Tighten one or more branch limits.

### Expected Result

- At least one branch limit becomes binding
- Dispatch changes compared to the relaxed case
- Objective increases compared to the relaxed case

### Purpose

Check whether network constraints are correctly implemented and affect the dispatch.

## Test C: Generator-Limited System

### Modification

Tighten generator active-power capacities.

### Expected Result

- Some generators hit upper or lower bounds
- More expensive or slack generation compensates
- Objective changes
- Problem may become infeasible if limits are too tight

### Purpose

Check whether generator constraints are correctly implemented.

## Test D: Artificial Infeasible Case

### Modification

Force an obviously impossible condition, such as:

- Total load greater than total generation capacity
- Voltage bounds too narrow
- Branch limits unrealistically low

### Expected Result

- Solver reports infeasibility
- The code identifies infeasibility cleanly
- No misleading solution is accepted

### Purpose

Check whether failure is detected correctly.
