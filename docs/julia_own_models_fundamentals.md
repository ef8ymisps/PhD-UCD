# Julia Own Models Fundamentals

Date: 2026-06-17

Purpose: explain the fundamentals behind the Julia own-model implementations in `julia/own_models`, so they can be defended in a technical meeting. This is not a feature inventory. It explains what the models mean, what equations they solve, what assumptions they make, and where that behaviour appears in the code.

## Big Picture

The Julia own models represent a power network as buses connected by branches, with generators, loads, and shunts attached to buses.

The same network can be studied in four different ways:

| Model | Basic question | Type of problem |

| DC Load Flow | Given fixed active injections, what are the voltage angles and active branch flows? | Linear equation solve |
| AC Load Flow | Given fixed generation/load and voltage controls, what are bus voltages and branch P/Q flows? | Nonlinear equation solve |
| DC OPF | What active generation dispatch minimizes cost while satisfying a DC network model? | Optimization |
| AC OPF | What Pg, Qg, voltage magnitudes, and angles minimize cost while satisfying AC physics and limits? | Nonlinear optimization |

The key distinction is:

- Load flow solves the network equations for a fixed operating point.
- OPF chooses an operating point by optimizing generator dispatch subject to equations and limits.

## The Data Model

The repository first converts a PowerModels/PGLib case into its own `PowerCase`.

Evidence:

- Data structs: `julia/own_models/src/data/case_structs.jl:1-75`.
- Loader: `julia/own_models/src/data/case_loader.jl:6-8`.
- Adapter: `julia/own_models/src/adapters/powermodels_adapter.jl:38-171`.

The important objects are:

- `Bus`: where voltage magnitude `vm0`, angle `va0`, voltage limits, and bus type live.
- `Branch`: a line or transformer connecting two buses, with `r`, `x`, charging, tap, phase shift, rating, angle limits, and status.
- `Generator`: connected to a bus, with initial Pg/Qg, P/Q limits, status, and cost coefficients.
- `Load`: fixed active/reactive demand.
- `Shunt`: fixed conductance/susceptance at a bus.
- `PowerCase`: the whole network plus `baseMVA`, `slack_bus_id`, and bus indexing.

The adapter copies values parsed by PowerModels. It does not do a broad conversion from MW/kV/ohms itself. The internal convention is the convention of the parsed PowerModels data, usually per-unit on `baseMVA` for powers and network quantities.

## Per-Unit Basics

Power-system equations are usually easier in per-unit:

```text
P_pu = P_MW / baseMVA
Q_pu = Q_Mvar / baseMVA
```

In the Julia code, the general helper is minimal:

- `to_per_unit(value, base) = value / base`.
- `from_per_unit(value_pu, base) = value_pu * base`.
- Evidence: `julia/own_models/src/data/per_unit.jl:1-7`.

For the own Julia models, the more important point is that `PowerModels.parse_file` has already produced the normalized data structure, and `powermodels_to_powercase` copies that data into `PowerCase`.

## Buses: PQ, PV, And Slack

AC power flow depends on bus type.

The Julia code classifies buses from the input `bus_type` field:

- `bus_type == 1`: PQ bus.
- `bus_type == 2`: PV bus.
- `bus_type == 3`: slack/reference bus.

Evidence: `julia/own_models/src/pf/ac_power_flow_nlp.jl:47-67`.

The code requires exactly one slack bus in AC PF:

```text
if length(slack) != 1
    error("Expected exactly one slack bus.")
end
```

Evidence: `ac_power_flow_nlp.jl:62-63`.

### PQ Bus

At a PQ bus:

- Specified: active load/generation balance P and reactive load/generation balance Q.
- Unknown: voltage magnitude and voltage angle.
- Equations used: both P mismatch and Q mismatch.

### PV Bus

At a PV bus:

- Specified: active power P and voltage magnitude V.
- Unknown: voltage angle and reactive power Q needed to hold the voltage.
- Equations used: P mismatch only.
- Q is reconstructed after solving; it is not bounded inside AC PF.

### Slack Bus

At the slack bus:

- Specified: voltage angle reference and voltage magnitude.
- Unknown: active and reactive injection needed to balance the system.
- Equations used: no P/Q mismatch equation in the PF state solve.

The slack bus has two separate roles:

- It provides the reference angle.
- In power flow, it absorbs the remaining active/reactive imbalance.

In OPF, this changes: all online generators can be decision variables, so the reference bus does not have a special economic role.

## DC Load Flow Fundamentals

File: `julia/own_models/src/pf/dc_power_flow.jl`.

Supporting network model: `julia/own_models/src/network/dc_matrices.jl`.

### What DC Load Flow Assumes

DC load flow is a simplified approximation of AC power flow. It keeps the active-power/angle relationship and ignores most reactive and voltage-magnitude effects.

In this implementation:

- Voltage magnitudes are not variables.
- Reactive power is ignored.
- Active power depends on angle differences.
- Branch resistance is included in the coefficient through `x / (r^2 + x^2)`, not the simpler textbook `1/x`.
- Losses are not explicitly modelled as nonlinear losses.

Evidence:

- Coefficient: `dc_branch_flow_coefficient(branch)` in `dc_matrices.jl:5-12`.
- B matrix construction: `dc_matrices.jl:15-40`.

### Inputs

Fixed inputs are:

- Generator active powers `gen.pg0`.
- Loads `load.pd`.
- Shunts `shunt.gs`.
- Active branches.
- Slack bus id.

Evidence: `build_net_active_injection` in `dc_matrices.jl:42-74`.

Sign convention:

- Generation adds active injection.
- Load subtracts active injection.
- Shunt conductance subtracts active injection.

### Unknowns

The unknowns are voltage angles `theta` at all non-slack buses.

The slack angle is fixed to zero:

- Evidence: `dc_power_flow.jl:22-24`.

### Equation

The model solves:

```text
B_reduced * theta_non_slack = P_non_slack
```

Evidence:

- `Bred = B[non_slack, non_slack]`: `dc_power_flow.jl:19`.
- `Pred = P[non_slack]`: `dc_power_flow.jl:20`.
- `theta[non_slack] = Bred \ Pred`: `dc_power_flow.jl:23`.

### Outputs

The result contains:

- Bus angles `theta`.
- Active branch flows.
- Slack injection.

Evidence: `DCPowerFlowResult` in `dc_power_flow.jl:5-9`.

Branch flow convention:

```text
pf = b * (theta_from - theta_to)
```

Evidence: `dc_power_flow.jl:33-38`.

### What DC Load Flow Does Not Do

DC load flow does not:

- Optimize generator dispatch.
- Enforce generator limits.
- Enforce branch limits.
- Enforce voltage limits.
- Model reactive power.

It only answers: with the fixed injections, what angles and active flows satisfy the linear DC equations?

## AC Load Flow Fundamentals

Files:

- Public dispatcher: `julia/own_models/src/pf/ac_power_flow.jl:8-16`.
- Newton method: `julia/own_models/src/pf/ac_power_flow_newton.jl:96-165`.
- NLP method: `julia/own_models/src/pf/ac_power_flow_nlp.jl:174-278`.
- Ybus: `julia/own_models/src/network/admittance_matrix.jl:3-54`.

### What AC Load Flow Solves

AC load flow solves the nonlinear AC network equations:

```text
S = V * conj(Ybus * V)
```

where:

- `S = P + jQ`.
- `V` is complex bus voltage.
- `Ybus` is the network admittance matrix.

Evidence:

- `S = V .* conj.(Ybus * V)`: `admittance_matrix.jl:46-54`.

### Ybus Construction

The AC network model includes:

- Series impedance `r + jx`.
- Series admittance `1 / (r + jx)`.
- Branch-side shunts `g_fr + jb_fr`, `g_to + jb_to`.
- Transformer tap magnitude.
- Transformer phase shift.
- Bus shunts.

Evidence: `admittance_matrix.jl:3-44`.

Transformer convention:

```text
tap = tap_mag * exp(im * branch.shift)
```

Evidence: `admittance_matrix.jl:25-31`.

This means phase shift is assumed to be in radians in the internal data.

### Specified Power

The code builds specified power by:

- Adding online generator Pg/Qg.
- Subtracting online load Pd/Qd.

Evidence: `build_specified_power` in `ac_power_flow_nlp.jl:18-45`.

### Newton State Vector

The Newton state is:

```text
[angles of PV and PQ buses;
 voltage magnitudes of PQ buses]
```

Evidence:

- `angle_buses = sort(vcat(pv_buses, pq_buses))`: `ac_power_flow_newton.jl:102`.
- `va[angle_buses]` and `vm[pq_buses]`: `pack_state` in `ac_power_flow_nlp.jl:69-70`.

The slack angle is not in the state vector.
PV voltage magnitudes are not in the state vector.

### Mismatch Equations

Newton solves:

```text
dP = Pspec - Pcalc     for PV and PQ buses
dQ = Qspec - Qcalc     for PQ buses only
```

Evidence:

- `dP = Pspec[angle_buses] - Pcalc[angle_buses]`: `ac_power_flow_newton.jl:115`.
- `dQ = Qspec[pq_buses] - Qcalc[pq_buses]`: `ac_power_flow_newton.jl:116`.

### Jacobian

The Newton method builds the four standard Jacobian blocks:

```text
J11 = dP/dtheta
J12 = dP/dV
J21 = dQ/dtheta
J22 = dQ/dV
```

Evidence: `_newton_jacobian` in `ac_power_flow_newton.jl:29-94`.

The Newton update is:

```text
dx = J \ mismatch
va[angle_buses] += dx_angles
vm[pq_buses] += dx_voltages
```

Evidence: `ac_power_flow_newton.jl:126-131`.

### Convergence

The Newton method checks the infinity norm of the mismatch:

```text
mismatch_norm <= tol
```

Default tolerance is `1e-8`; default maximum iterations is `20`.

Evidence: `solve_ac_power_flow_newton(case; tol=1e-8, max_iter=20)` in `ac_power_flow_newton.jl:96`, and convergence check in `ac_power_flow_newton.jl:118-123`.

There is no damping or line search.

### Reactive Power In AC PF

Reactive power is represented in the AC equations, but generator Q is not optimized.

After solving, the code estimates bus-level generator Q as:

```text
qg_by_bus[bus_id] = Qcalc[i] + q_load
```

Evidence: `estimate_qg_by_bus` in `ac_power_flow_nlp.jl:155-172`.

Important limitation:

- Q is reconstructed by bus.
- Individual generator Q is not allocated.
- Qmin/Qmax are not enforced in AC PF.
- PV-PQ switching is not implemented.

So yes: AC PF can converge even if a PV bus would require reactive power outside generator limits.

## DC OPF Fundamentals

Files:

- Main solve: `julia/own_models/src/opf/dc_opf.jl:15-73`.
- Constraints: `julia/own_models/src/opf/constraints.jl:3-69`.
- Objective: `julia/own_models/src/opf/objectives.jl:3-15`.

### What DC OPF Does

DC OPF asks:

```text
What active generator dispatch minimizes generation cost while satisfying a DC network approximation and limits?
```

This is optimization, unlike DC load flow.

### Decision Variables

The variables are:

- `theta[bus_ids]`: bus voltage angles.
- `pg[gen_ids]`: generator active power outputs.

Evidence: `dc_opf.jl:26-30`.

### Objective

The objective is:

```text
min sum(c2 * pg^2 + c1 * pg + c0)
```

over online generators.

Evidence: `objectives.jl:3-15`.

### Equality Constraints

The core equality is active-power balance at each bus:

```text
generation - load - shunt = net branch flow leaving the bus
```

Evidence: `add_dc_power_balance_constraints!` in `constraints.jl:18-50`.

The slack angle is fixed:

```text
theta[slack_bus] == 0
```

Evidence: `constraints.jl:3-5`.

### Inequality Constraints

Generator limits:

```text
pmin <= pg <= pmax
```

for online generators; offline generators are fixed to zero.

Evidence: `constraints.jl:8-16`.

Branch angle limits:

```text
angmin <= theta_from - theta_to <= angmax
```

Evidence: `constraints.jl:59-62`.

Branch active-flow limits:

```text
-rate_a <= b * (theta_from - theta_to) <= rate_a
```

if `rate_a > 0`.

Evidence: `constraints.jl:64-67`.

### Solver

The model is built in JuMP and solved with HiGHS by default.

Evidence: `solve_dc_opf_own(case; optimizer=HiGHS.Optimizer)` in `dc_opf.jl:15-18`.

Depending on generator cost coefficients, the mathematical problem is:

- LP if all quadratic coefficients are zero.
- Convex QP if quadratic coefficients are nonnegative.

The code delegates the optimization to HiGHS.

### Outputs

The result contains:

- Objective.
- Termination status.
- Bus angles.
- Generator Pg.
- Branch active flows.

Evidence: `DCOPFResult` in `dc_opf.jl:7-13`.

## AC OPF Fundamentals

File: `julia/own_models/src/opf/ac_opf.jl:18-319`.

### What AC OPF Does

AC OPF asks:

```text
What Pg, Qg, voltage magnitudes, and voltage angles minimize generation cost while satisfying full AC power balance and operating limits?
```

This is the most physically complete formulation among the four core models.

### Decision Variables

The variables are:

- `vm[bus_ids]`: voltage magnitude.
- `va[bus_ids]`: voltage angle.
- `pg[gen_ids]`: generator active power.
- `qg[gen_ids]`: generator reactive power.

Evidence: `ac_opf.jl:36-39`.

### Bounds And Initial Values

For each bus:

- `vmin <= vm <= vmax`.
- Start from `bus.vm0`.
- Angle starts from `bus.va0`.

Evidence: `ac_opf.jl:45-50`.

For each online generator:

- `pmin <= pg <= pmax`.
- `qmin <= qg <= qmax`.
- Starts from `gen.pg0`, `gen.qg0`.

Evidence: `ac_opf.jl:53-62`.

Offline generators:

- `pg = 0`.
- `qg = 0`.

Evidence: `ac_opf.jl:63-66`.

Slack angle:

- `va[case.slack_bus_id] = 0`.

Evidence: `ac_opf.jl:69-70`.

### AC Power Balance

The code defines nonlinear active and reactive injection expressions:

```text
P_i = sum_j Vm_i Vm_j (G_ij cos(theta_i - theta_j) + B_ij sin(theta_i - theta_j))

Q_i = sum_j Vm_i Vm_j (G_ij sin(theta_i - theta_j) - B_ij cos(theta_i - theta_j))
```

Evidence:

- P expression: `ac_opf.jl:82-96`.
- Q expression: `ac_opf.jl:100-114`.

At every bus, the model enforces:

```text
sum Pg at bus - Pd = Pcalc
sum Qg at bus - Qd = Qcalc
```

Evidence: `ac_opf.jl:128-159`.

### Branch Limits

For active branches, AC OPF enforces:

```text
angmin <= va_from - va_to <= angmax
```

Evidence: `ac_opf.jl:166-175`.

If `rate_a > 0`, it also enforces apparent-power limits at both ends:

```text
pf^2 + qf^2 <= rate_a^2
pt^2 + qt^2 <= rate_a^2
```

Evidence: `ac_opf.jl:180-252`.

This is important: AC OPF branch limits use MVA-like apparent power, not just MW active power.

### Objective

Same cost form as DC OPF:

```text
min sum(c2 * pg^2 + c1 * pg + c0)
```

Evidence: `ac_opf.jl:259-269`.

### Solver And Optimality

AC OPF is solved with JuMP and Ipopt by default.

Evidence: `ac_opf.jl:18-26`.

AC OPF is nonlinear and nonconvex. Ipopt returns a local solution, not a guaranteed global optimum.

That is not a bug. It is a fundamental property of AC OPF.

## Initialization: IEEE 14 Example

For Julia AC OPF, initialization comes directly from the case data:

- Pg starts from `gen.pg0`.
- Qg starts from `gen.qg0`.
- Vm starts from `bus.vm0`.
- Va starts from `bus.va0`.

Evidence: `ac_opf.jl:45-62`.

For `results/reference/powermodels/ac_opf/pglib_opf_case14_ieee_base_case.json`, the start vector in the order:

```text
pg[1:5], qg[1:5], vm[1:14], va[1:14]
```

is:

```text
1.7,0.295,0,0,0,0.05,0,0.2,0.09,0.09,1,1,1,1,1,1,1,1,1,1,1,1,1,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0
```

This is a case-data start, not a flat-start rule hardcoded in the solver and not a DC warm start.

## Reactive Power: What To Defend

### In AC PF

Reactive power is part of the AC equations, but generator reactive output is not a decision variable.

Defensible statement:

```text
In AC PF, Q is reconstructed at bus level after the voltage solution is found.
```

Evidence: `estimate_qg_by_bus`, `ac_power_flow_nlp.jl:155-172`.

Limit:

```text
AC PF does not enforce Qmin/Qmax and does not do PV-PQ switching.
```

### In AC OPF

Reactive generator output is a decision variable:

```text
qg[gen_ids]
```

Evidence: `ac_opf.jl:36-39`.

Bounds are enforced inside the solver:

```text
qmin <= qg <= qmax
```

Evidence: `ac_opf.jl:58-59`.

Defensible statement:

```text
AC OPF enforces individual generator reactive limits; AC PF does not.
```

## PV-PQ Switching

PV-PQ switching is not implemented in the Julia own models.

What that means:

- A PV bus keeps its voltage magnitude fixed.
- The solver computes whatever bus reactive injection is needed.
- If that implied Q exceeds generator limits, the PF solve does not reclassify the bus as PQ.
- The model does not resolve with Q fixed at the violated limit.

This limitation applies to both Newton AC PF and NLP AC PF.

AC OPF is different because Qg is directly optimized with Q limits.

## Slack Treatment

### Reference Angle

The reference angle is a numerical anchor. Without fixing one angle, the equations are rotationally symmetric.

In DC PF:

- Slack angle is set to zero after solving reduced equations.
- Evidence: `dc_power_flow.jl:22-24`.

In DC OPF:

- Slack angle constraint is `theta[slack_bus] == 0`.
- Evidence: `constraints.jl:3-5`.

In AC OPF:

- Slack voltage angle is fixed: `fix(va[case.slack_bus_id], 0.0; force=true)`.
- Evidence: `ac_opf.jl:69-70`.

### Active-Power Balancing

Do not confuse reference angle with active-power balancing.

In PF:

- Generator dispatch is fixed.
- Slack injection absorbs the remaining active mismatch.

In OPF:

- Generator Pg is optimized.
- Multiple online generators can move if economically useful and feasible.
- The reference bus does not have a special cost role.

## PF Versus OPF

| Question | PF | OPF |
| --- | --- | --- |
| Is there an objective function? | No | Yes |
| Is generator dispatch fixed? | Yes | No, generator variables are optimized |
| Does it enforce limits? | Mostly no; AC NLP PF has voltage bounds | Yes, according to formulation |
| What does convergence mean? | Equations were solved numerically | Solver found a numerical solution/status for the optimization model |
| Does convergence prove physical security? | No | Only for limits included in the formulation |

Why PF is not optimization:

- It does not minimize cost.
- It does not choose dispatch.
- It solves equations for a given operating point.

Why PF convergence does not prove all limits:

- AC PF may converge with generator Q outside limits.
- Branch ratings are not enforced in PF.
- Generator P limits are not enforced in PF.

Why OPF convergence does not prove the formulation is correct:

- A solver can solve the wrong equations.
- Validation is needed against known formulations and reference tools.

## Validation Fundamentals

Validation answers different questions depending on the tool.

### PowerModels/PGLib

PowerModels validation checks whether the own Julia PF/OPF formulations behave similarly to a recognized modelling framework on the same PGLib-style data.

Relevant files:

- `julia/own_models/src/analysis/compare_with_powermodels.jl`.
- `julia/own_models/src/analysis/ac_opf_validation.jl`.
- `julia/own_models/src/analysis/validation_runner.jl`.

What it can support:

- Numerical similarity.
- Formulation consistency for tested cases.
- Detection of obvious modelling differences.

What it cannot prove:

- Global correctness for all systems.
- Correctness of untested transformer/shunt/control cases.
- Global optimality of AC OPF.

### PyPowSyBl/OpenLoadFlow

The Julia own-model tree does not contain the PyPowSyBl validation layer. PyPowSyBl validation is implemented in Python.

So for Julia specifically, do not claim:

```text
The Julia own models directly validate against PyPowSyBl.
```

The correct claim is:

```text
The repository has PyPowSyBl/OpenLoadFlow validation tooling in the Python layer, while the Julia own-model layer primarily validates against PowerModels/PGLib.
```

## Five-Minute Verbal Explanation

Start with the data model:

```text
We parse a PowerModels/PGLib case into our own PowerCase. That gives us buses, branches, generators, loads, shunts, base MVA, and a slack bus. From that common data model, we build either a DC approximation or the full AC admittance matrix.
```

Then DC load flow:

```text
DC load flow is the simplest model. It assumes active power is mainly driven by voltage angle differences. The unknowns are non-slack bus angles. The code builds a B matrix from active branches using x/(r^2+x^2), forms net active injections from generation minus load minus shunts, removes the slack row and column, solves a linear system, and computes active branch flows. It does not optimize and it does not enforce limits.
```

Then AC load flow:

```text
AC load flow solves the nonlinear AC equations using Ybus. The code supports Newton-Raphson and an Ipopt NLP version with zero objective. PQ buses solve for angle and voltage magnitude; PV buses solve for angle while voltage is fixed; the slack bus fixes angle and voltage. The mismatch is P for PV/PQ buses and Q for PQ buses. Reactive generation is reconstructed at bus level afterward. Q limits and PV-PQ switching are not implemented.
```

Then DC OPF:

```text
DC OPF turns the DC approximation into an optimization problem. The variables are generator active powers and bus angles. The objective is quadratic or linear generation cost. The constraints enforce active balance, generator P limits, branch angle limits, and branch active-flow limits. It is built in JuMP and solved with HiGHS.
```

Then AC OPF:

```text
AC OPF is the full nonlinear optimization model. The variables are voltage magnitudes, voltage angles, generator Pg, and generator Qg. It enforces AC P and Q balance at every bus, voltage bounds, generator P and Q bounds, branch angle limits, and apparent-power limits at both ends of each branch. It is solved with Ipopt, so it is a local nonlinear solution, not a guaranteed global optimum.
```

End with validation:

```text
For validation, the Julia models are compared mainly against PowerModels/PGLib. That gives evidence that the implemented equations and outputs are numerically consistent on tested cases. PyPowSyBl/OpenLoadFlow validation exists elsewhere in the repository in the Python layer, mainly for AC load-flow and security comparison. I would not claim validation proves universal correctness; it supports the tested formulations and highlights gaps like Q-limit handling, PV-PQ switching, and missing Julia topology/N-1 screening.
```

## Questions You Should Be Ready For

1. What is the difference between load flow and OPF?
2. Why is DC load flow linear?
3. Why does your DC coefficient use `x/(r^2+x^2)` instead of `1/x`?
4. What does the slack bus do?
5. Is the slack bus the same as the cheapest generator?
6. Which buses have voltage magnitude fixed in AC PF?
7. What happens if a PV bus hits Q limits?
8. Does AC PF enforce branch MVA limits?
9. Does AC OPF enforce Q limits?
10. Are AC OPF results globally optimal?
11. What variables are optimized in DC OPF?
12. What variables are optimized in AC OPF?
13. How are transformer taps and phase shifts represented?
14. Are branch limits MW or MVA?
15. How are multiple generators at one bus handled?
16. How is reactive power allocated in AC PF?
17. What exactly is validated against PowerModels?
18. What exactly is validated against PyPowSyBl?
19. What are the biggest limitations?
20. What would you improve first?

## Things You Can Defend Confidently

- The Julia code has its own `PowerCase` data model.
- DC PF solves a reduced linear angle system.
- AC PF solves AC equations using Ybus, either by Newton or zero-objective Ipopt NLP.
- DC OPF is a JuMP/HiGHS model with Pg and theta variables.
- AC OPF is a JuMP/Ipopt model with Vm, Va, Pg, and Qg variables.
- AC OPF enforces generator Q limits; AC PF does not.
- Branch apparent-power limits are enforced in AC OPF at both branch ends.
- The Julia topology/N-1 security workflow is not implemented.

## Known Limitations To State Openly

- No PV-PQ switching.
- No AC PF generator Q-limit enforcement.
- AC PF Q is reconstructed at bus level.
- AC OPF is local/nonconvex.
- Julia topology extraction is a placeholder.
- Julia does not own the repository's current N-1 screening workflow.
- Some generic metrics/violation functions are placeholders.
- One Julia script uses absolute include paths.

## Claims Not To Make Yet

- Do not claim Julia AC PF enforces Q limits.
- Do not claim Julia AC OPF is globally optimal.
- Do not claim Julia implements complete N-1 security screening.
- Do not claim Julia directly validates against PyPowSyBl.
- Do not claim all branch/control modelling variants have been exhaustively validated.
