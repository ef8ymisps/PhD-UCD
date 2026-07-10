# Python Own Models Fundamentals

Date: 2026-06-17

Purpose: explain the fundamentals behind the Python own-model implementations in `python/own_models`, so the load-flow, OPF, validation, and security-screening logic can be defended in a technical meeting. This is an explanation first, with code evidence attached to the important claims.

## Big Picture

The Python own models use a shared `PowerCase` data model and then solve four core problems:

| Model | Basic question | Type of problem | Main solver |
| --- | --- | --- | --- |
| DC Load Flow | Given fixed active injections, what are the voltage angles and active branch flows? | Linear equations | NumPy linear solve |
| AC Load Flow | Given fixed P/Q injections and voltage controls, what are bus voltages and branch P/Q flows? | Nonlinear equations | Newton-Raphson |
| DC OPF | What active generator dispatch minimizes cost under DC network constraints? | LP/QP-like constrained optimization | SciPy `linprog` or SLSQP |
| AC OPF | What Pg, Qg, Vm, and Va minimize cost under AC equations and limits? | Nonlinear constrained optimization | SciPy SLSQP |

The mental model is:

```text
PowerCase
-> build network equations
-> solve fixed equations for PF
or
-> optimize dispatch and voltages for OPF
-> validate against PowerModels/PGLib and PyPowSyBl/OpenLoadFlow
```

## Data Model

The Python data model is in `python/own_models/src/data/case.py`.

Important constants:

- `PQ = 1`
- `PV = 2`
- `SLACK = 3`

Evidence: `case.py:6-8`.

Important dataclasses:

- `Bus`: id, index, bus type, initial Vm/Va, voltage bounds, nominal voltage.
- `Branch`: endpoints, r/x, charging/shunts, rating, tap, shift, angle limits, status, branch type.
- `Generator`: bus, Pg/Qg, P/Q limits, voltage setpoint flag, status, cost coefficients.
- `Load`: bus, Pd/Qd, status.
- `Shunt`: bus, Gs/Bs, status.
- `PowerCase`: base MVA, buses, branches, generators, loads, shunts, slack bus id, bus-id-to-index map.

Evidence: `case.py:11-97`.

The main PowerModels JSON adapter is `python/own_models/src/adapters/powermodels_csv_adapter.py`.

It:

- Reads `baseMVA`.
- Copies bus, branch, generator, load, and shunt data.
- Converts sorted PowerModels dictionary keys into ordered Python objects.
- Chooses the slack bus from the first bus with `bus_type == SLACK`.

Evidence: `powermodels_csv_adapter.py:32-161`.

## Units And Per-Unit Basics

The helper functions are more complete than the Julia helper file:

- `mw_to_pu(value_mw, base_mva)`.
- `mvar_to_pu(value_mvar, base_mva)`.
- `mva_to_pu(value_mva, base_mva)`.
- `impedance_ohm_to_pu`.
- `admittance_siemens_to_pu`.

Evidence: `python/own_models/src/data/per_unit.py:4-33`.

The basic convention is:

```text
P_pu = P_MW / base_mva
Q_pu = Q_Mvar / base_mva
S_pu = S_MVA / base_mva
```

PowerModels JSON data is already in the normalized PowerModels convention and is copied into `PowerCase`. PyPowSyBl comparison/building code performs explicit physical-unit conversions where needed.

Evidence:

- PowerModels copy path: `powermodels_csv_adapter.py:39-161`.
- Own-to-PyPowSyBl bus voltage comparison uses `vm * nominal_v_kv`: `python/own_models/src/analysis/compare_with_pypowsybl.py:17-45`.
- Own branch P/Q comparison uses `case.base_mva`: `compare_with_pypowsybl.py:63-100`.

## Buses: PQ, PV, And Slack

Python AC PF classifies buses in `_bus_type_indices`:

- Slack index is `case.bus_id_to_index[case.slack_bus_id]`.
- PV buses are `bus.bus_type == PV`.
- PQ buses are `bus.bus_type == PQ`.

Evidence: `python/own_models/src/pf/ac_power_flow.py:28-32`.

Important difference from Julia:

- Python AC PF does not explicitly check that exactly one slack bus exists.
- It trusts `case.slack_bus_id`.

### PQ Bus

At a PQ bus:

- Specified: net active power P and reactive power Q.
- Unknown: voltage angle and voltage magnitude.
- Equations: P mismatch and Q mismatch.

### PV Bus

At a PV bus:

- Specified: active power and voltage magnitude.
- Unknown: voltage angle and reactive power needed to hold voltage.
- Equations: P mismatch only.

### Slack Bus

At the slack bus:

- Specified: voltage angle reference and voltage magnitude.
- Unknown: active/reactive injection needed to balance PF.
- In OPF, the slack bus only fixes the reference angle; dispatch is optimized across generators.

## DC Load Flow Fundamentals

Files:

- `python/own_models/src/network/dc_matrices.py`.
- `python/own_models/src/pf/dc_power_flow.py`.

### What DC Load Flow Assumes

DC load flow simplifies the network to active-power/angle relationships:

- Voltage magnitudes are not solved.
- Reactive power is ignored.
- Active branch flow depends on angle difference.
- The coefficient is `x / (r^2 + x^2)`, not plain `1/x`.

Evidence: `dc_branch_flow_coefficient` in `dc_matrices.py:8-12`.

### Inputs

Fixed inputs are:

- Online generator `pg0`.
- Online load `pd`.
- Online shunt `gs`.
- Active branches.
- Slack bus id.

Evidence: `build_net_active_injection` in `dc_matrices.py:33-48`.

Sign convention:

- Generation adds active injection.
- Load subtracts active injection.
- Shunt conductance subtracts active injection.

### Unknowns

The unknowns are voltage angles at all non-slack buses.

Evidence:

- Slack index: `dc_matrices.py:51-52`.
- Non-slack index set: `dc_power_flow.py:28-29`.

### Equation

The code solves:

```text
B_reduced * theta_non_slack = P_non_slack
```

Evidence:

- `reduced_b = bbus[np.ix_(keep, keep)]`: `dc_power_flow.py:30`.
- `theta[keep] = np.linalg.solve(reduced_b, p[keep])`: `dc_power_flow.py:38-39`.

### Branch Flow

Branch flow convention:

```text
pf = b * (theta_from - theta_to)
```

Evidence: `dc_power_flow.py:46-49`.

### Convergence

Python DC PF computes the mismatch after solving and sets:

```text
converged = norm(non_slack_mismatch, inf) <= 1e-8
```

Evidence: `dc_power_flow.py:45-52`.

### What DC Load Flow Does Not Do

DC PF does not:

- Optimize dispatch.
- Enforce generator limits.
- Enforce branch limits.
- Model voltage magnitudes.
- Model reactive power.

## AC Load Flow Fundamentals

Files:

- `python/own_models/src/network/admittance.py`.
- `python/own_models/src/pf/ac_power_flow.py`.

### AC Network Equation

The AC model uses:

```text
S = V * conj(Ybus * V)
```

Evidence: `calc_bus_power_injections` in `admittance.py:42-45`.

### Ybus Construction

Ybus includes:

- Series impedance `r + jx`.
- Series admittance `1 / (r + jx)`.
- Branch-side shunts.
- Tap magnitude.
- Phase shift.
- Bus shunts.

Evidence: `build_admittance_matrix` in `admittance.py:10-39`.

Transformer convention:

```text
tap = tap_mag * exp(j * shift)
```

Evidence: `admittance.py:24-33`.

### Specified Power

The specified bus power is built by:

- Adding online generator Pg/Qg.
- Subtracting online load Pd/Qd.

Evidence: `_specified_power` in `ac_power_flow.py:35-51`.

### State Vector

The Newton state is:

```text
[angles of PV and PQ buses;
 voltage magnitudes of PQ buses]
```

Evidence:

- `angle_buses = sorted(pv_buses + pq_buses)`: `ac_power_flow.py:138`.
- Updates to `va[angle_buses]` and `vm[pq_buses]`: `ac_power_flow.py:163-164`.

### Mismatch

The mismatch is:

```text
Pspec - Pcalc     for PV and PQ buses
Qspec - Qcalc     for PQ buses
```

Evidence: `ac_power_flow.py:151-156`.

### Jacobian And Update

The Jacobian is built by `_jacobian`, with the standard AC PF block structure:

```text
dP/dtheta, dP/dV
dQ/dtheta, dQ/dV
```

Evidence: `ac_power_flow.py:54-109`.

The update is direct Newton:

```text
dx = np.linalg.solve(jac, mismatch)
va[angle_buses] += dx[:n_angle]
vm[pq_buses] += dx[n_angle:]
```

Evidence: `ac_power_flow.py:158-164`.

There is no damping, line search, or PV-PQ switching.

### Convergence

Default tolerance is `1e-8`; default maximum iterations is `20`.

Evidence: `solve_ac_power_flow_newton(case, tol=1e-8, max_iter=20)` in `ac_power_flow.py:126-130`.

Convergence is based on the infinity norm of the mismatch:

Evidence: `ac_power_flow.py:151-162`, `169-181`.

### Reactive Power In AC PF

Reactive generation is reconstructed after solving:

```text
qg_by_bus[bus_id] = qcalc[idx] + qd_at_bus
```

Evidence: `_estimate_qg_by_bus` in `ac_power_flow.py:112-123`.

This is bus-level Q, not individual generator Q.

Important limitation:

- Qmin/Qmax are not enforced inside AC PF.
- PV-PQ switching is not implemented.
- AC PF can converge with reactive limit violations.

## DC OPF Fundamentals

File: `python/own_models/src/opf/dc_opf.py`.

### What DC OPF Does

DC OPF asks:

```text
What active generator dispatch minimizes cost while satisfying DC active-power balance and network limits?
```

This is optimization, unlike DC PF.

### Variables

The variable vector is:

```text
[pg for active generators;
 theta for all buses]
```

Evidence:

- `_unpack`: `dc_opf.py:41-48`.
- Constraint matrix variable ordering: `dc_opf.py:89-162`.

### Objective

The objective is generator cost:

```text
c2 * pg^2 + c1 * pg + c0
```

Evidence:

- `python/own_models/src/opf/objectives.py:6-7`.
- DC objective wrapper: `dc_opf.py:248-250`.

### Constraints

Nodal balance:

```text
generation - demand - branch flow terms = 0
```

Evidence:

- `_power_balance_vector`: `dc_opf.py:54-72`.
- Linear equality matrix: `dc_opf.py:101-122`.

Slack angle:

```text
theta_slack = 0
```

Evidence: `dc_opf.py:124-127`.

Branch angle limits:

```text
angmin <= theta_from - theta_to <= angmax
```

Evidence: `dc_opf.py:129-141`.

Branch active-flow limits:

```text
-rate_a <= b * (theta_from - theta_to) <= rate_a
```

if `rate_a > 0`.

Evidence: `dc_opf.py:143-150`.

Generator Pg bounds are built from online generator `pmin/pmax`.

Evidence: `_variable_bounds` in `dc_opf.py:175-183`.

### Solver

If all generator costs are linear, Python uses:

```text
scipy.optimize.linprog(method="highs")
```

Evidence: `_solve_linear_dc_opf` in `dc_opf.py:189-231`.

Otherwise it uses:

```text
scipy.optimize.minimize(..., method="SLSQP")
```

Evidence: `dc_opf.py:262-271`.

### Outputs

The result includes:

- `success`.
- `status`.
- `message`.
- `objective`.
- `pg`.
- `theta`.
- `branch_flows`.
- `max_balance_violation`.

Evidence: `DCOPFResult` in `dc_opf.py:13-23`.

## AC OPF Fundamentals

Files:

- Wrapper: `python/own_models/src/opf/ac_opf.py`.
- Solver: `python/own_models/src/opf/ac_opf_scipy.py`.
- Variables: `python/own_models/src/opf/ac_variables.py`.
- Constraints: `python/own_models/src/opf/ac_constraints.py`.

### What AC OPF Does

AC OPF asks:

```text
What Pg, Qg, voltage magnitudes, and voltage angles minimize cost while satisfying AC power balance and operating limits?
```

### Solver

Python AC OPF uses SciPy SLSQP.

Evidence:

- `solve_ac_opf(..., solver="scipy")`: `ac_opf.py:7-12`.
- `minimize(..., method="SLSQP")`: `ac_opf_scipy.py:80-89`.

`solver="ipopt"` is not implemented in Python.

Evidence: `ac_opf.py:10-12`.

### Variable Ordering

The variable vector is:

```text
[pg for active generators;
 qg for active generators;
 vm for all buses;
 va for all buses]
```

Evidence:

- `pack_ac_opf_variables`: `ac_variables.py:31-35`.
- `unpack_ac_opf_variables`: `ac_variables.py:38-53`.

### Initialization

Python AC OPF tries to warm-start from AC PF:

1. Run Newton AC PF with `pf_max_iter=30`.
2. If it converges, use PF Vm/Va and bus-level Q estimate.
3. If it does not converge, use case data.
4. Clip Pg, Qg, and Vm to bounds.
5. Force slack angle to zero.

Evidence: `build_ac_opf_initial_point` in `ac_variables.py:79-112`.

For `results/reference/powermodels/ac_opf/pglib_opf_case14_ieee_base_case.json`, the extracted Python AC OPF initial vector is:

```text
1.7,0.295,0,0,0,0,0.3,0.4,0.0828824152258,0.0568094150356,1,1,1,0.968773898545,0.967206646015,1,0.989993021541,1,0.984861958941,0.979557981371,0.985927237897,0.984080058605,0.978900702587,0.962897278369,0,-0.109004039219,-0.264823798802,-0.208023306306,-0.177277323394,-0.284810667138,-0.267742770917,-0.267742770917,-0.299327324663,-0.302489372772,-0.296274767279,-0.301941524112,-0.303571005973,-0.321312255744
```

Ordering:

```text
pg[5], qg[5], vm[14], va[14]
```

This differs from Julia because Python uses a PF warm-start attempt.

### AC Balance Constraints

The equality constraint is:

```text
[pgen - pd - pcalc,
 qgen - qd - qcalc,
 slack_angle]
```

Evidence:

- `ac_power_balance_residuals`: `ac_constraints.py:35-51`.
- `slack_angle_residual`: `ac_constraints.py:54-56`.
- Equality constraint assembly: `ac_opf_scipy.py:66-74`.

### Inequality Constraints

Branch thermal margins:

```text
rate^2 - (pf^2 + qf^2) >= 0
rate^2 - (pt^2 + qt^2) >= 0
```

Evidence: `branch_thermal_margins` in `ac_constraints.py:59-71`.

Branch angle margins:

```text
angle - angmin >= 0
angmax - angle >= 0
```

Evidence: `branch_angle_margins` in `ac_constraints.py:74-84`.

Generator Pg/Qg and voltage magnitude limits are SLSQP bounds.

Evidence: `build_ac_opf_bounds` in `ac_variables.py:115-125`.

### Objective

The objective is generation cost over active-generator Pg values.

Evidence:

- `generation_cost_from_vector`: `objectives.py:20-28`.
- AC objective call: `ac_opf_scipy.py:62-64`.

### Success And Feasibility

Python AC OPF does not simply trust raw SLSQP success. It evaluates constraint violations after solving.

Evidence:

- `evaluate_ac_opf_constraint_violations`: `ac_constraints.py:87-119`.
- Feasibility-based success: `ac_opf_scipy.py:92-100`.

This is a good defensive point: the code distinguishes solver return status from practical feasibility tolerance.

## Reactive Power: What To Defend

### AC PF

Reactive power appears in AC equations, but generator Q is reconstructed afterward at bus level.

Evidence: `ac_power_flow.py:112-123`.

Defensible statement:

```text
Python AC PF can report the bus reactive requirement, but it does not enforce individual generator Q limits.
```

### AC OPF

Reactive power is a decision variable:

```text
qg for active generators
```

Evidence: `ac_variables.py:31-53`.

Q bounds are part of the optimizer bounds:

Evidence: `ac_variables.py:115-125`.

Constraint-violation reporting includes Qg limit violation:

Evidence: `ac_constraints.py:102-115`.

Defensible statement:

```text
Python AC OPF enforces Q limits through optimizer bounds and checks final Q-limit violation afterward.
```

## PV-PQ Switching

PV-PQ switching is not implemented in Python AC PF.

What happens:

- PV bus voltage magnitude remains fixed.
- Reactive requirement is reconstructed after the solve.
- If Q is outside limits, AC PF itself does not reclassify the bus.
- It does not clip Q.
- It does not resolve as PQ.

AC OPF handles Q limits differently because Qg is a bounded decision variable.

## Slack Treatment

### Reference Angle

In DC PF:

- Slack bus is removed from the linear system.
- Slack angle is zero.

Evidence: `dc_power_flow.py:28-39`.

In DC OPF:

- Slack angle row fixes theta at the slack bus.

Evidence: `dc_opf.py:124-127`.

In AC OPF:

- `slack_angle_residual` constrains slack Va to zero.

Evidence: `ac_constraints.py:54-56`.

### Active-Power Balancing

In PF:

- Dispatch is fixed.
- Slack injection emerges from the equations.

In OPF:

- Generator Pg variables can adjust.
- The reference bus has no special economic role.

For post-contingency generator outages, Python adds a separate static balancing policy:

- It redistributes lost generator output according to available reserve headroom.
- It may select a new slack generator if needed.

Evidence: `python/own_models/src/analysis/balancing_policy.py:69-149`.

That policy is for security screening, not ordinary base AC OPF.

## PF Versus OPF

| Feature | DC PF | AC PF | DC OPF | AC OPF |
| --- | --- | --- | --- | --- |
| Solves equations? | Yes | Yes | Uses equations as constraints | Uses equations as constraints |
| Optimizes cost? | No | No | Yes | Yes |
| Pg fixed or variable? | Fixed | Fixed | Variable | Variable |
| Qg fixed or variable? | Ignored | Fixed/reconstructed | Ignored | Variable |
| Vm variable? | No | PQ buses only | No | All buses |
| Va variable? | Non-slack buses | PV/PQ buses | All buses with slack fixed | All buses with slack fixed |
| Branch limits enforced? | No | No | Active-flow/angle | Apparent-power/angle |
| Generator limits enforced? | No | No | Pg | Pg/Qg |
| Losses | Not explicit | AC losses represented | DC approximation | AC losses represented |

Key explanations:

- PF is not optimization because no objective is minimized and dispatch is fixed.
- PF convergence means equations were solved, not that all limits are secure.
- OPF convergence means a solver solved the written optimization problem, not that the formulation is universally correct.
- AC OPF is nonconvex, so SLSQP gives a local numerical solution.

## Validation Fundamentals

### PowerModels/PGLib

Python validates OPF formulations against exported PowerModels/PGLib reference results.

Relevant files:

- DC OPF validation script: `python/own_models/scripts/06_validate_dc_opf_against_powermodels.py`.
- AC OPF validation script: `python/own_models/scripts/07_validate_ac_opf_against_powermodels.py`.
- DC comparison functions: `python/own_models/src/analysis/compare_with_powermodels.py`.
- AC comparison functions: `python/own_models/src/analysis/compare_ac_with_powermodels.py`.
- Documentation: `docs/python_ac_opf_validation.md`.

What validation supports:

- Numerical similarity for tested cases.
- Outcome agreement for tested scenarios.
- Detection of formulation or data-mapping mismatches.

What it does not prove:

- Global optimality.
- Correctness for all possible PGLib cases.
- Correctness for every transformer/control variant.

### PyPowSyBl/OpenLoadFlow

Python also has PyPowSyBl/OpenLoadFlow comparison and security validation.

Relevant files:

- Base AC PF comparison: `python/own_models/src/analysis/compare_with_pypowsybl.py`.
- PyPowSyBl security validation: `python/own_models/src/analysis/pypowsybl_security_validation.py`.
- Branch N-1 comparison script: `python/own_models/scripts/10_compare_n1_branch_with_pypowsybl.py`.

Important details:

- Own bus voltages are compared to PyPowSyBl in kV and degrees.
- Own branch flows are converted using `base_mva`.
- PyPowSyBl security validation harmonizes secure/insecure labels and notes that own angle-limit checks do not have a direct v1 PyPowSyBl equivalent.

Evidence:

- Unit comparisons and conversions: `compare_with_pypowsybl.py:17-100`.
- Security run and output writing: `pypowsybl_security_validation.py:897-1016`.
- Angle-limit note: `pypowsybl_security_validation.py:845-861`.

## N-1 Security Basics

Although this document is mainly about PF/OPF fundamentals, Python also owns the current N-1 screening flow.

Branch N-1:

- Generate one active branch outage at a time.
- Apply outage by setting branch status to zero.
- Check islanding.
- Run post-contingency AC PF.
- Evaluate voltage, thermal, angle, active/reactive generator violations.

Evidence:

- Branch contingencies: `python/own_models/src/analysis/contingencies.py:29-45`.
- Apply contingency: `contingencies.py:84-101`.
- Branch screening flow: `python/own_models/src/analysis/post_contingency.py:54-144`.

Generator N-1:

- Generate one active generator outage at a time.
- Apply outage by setting generator status to zero and Pg/Qg to zero.
- Apply proportional reserve balancing.
- Possibly reassign slack.
- Run AC PF and security checks.

Evidence:

- Generator contingencies: `contingencies.py:65-81`.
- Balancing policy: `balancing_policy.py:69-149`.
- Generator screening flow: `post_contingency.py:147-258`.

This is static post-contingency screening, not dynamic simulation or AGC.

## Five-Minute Verbal Explanation

Start with the data:

```text
The Python own models parse PowerModels-style data into a PowerCase containing buses, branches, generators, loads, shunts, base MVA, and a slack bus. That common case object is then used to build DC or AC network equations.
```

DC load flow:

```text
DC load flow is a fixed-dispatch linear equation solve. The code forms net active injections from generator Pg minus load Pd minus shunt Gs, builds a B matrix using x/(r^2+x^2), removes the slack bus, solves for non-slack angles, and computes active branch flows as b times the angle difference. It does not enforce limits or optimize dispatch.
```

AC load flow:

```text
AC load flow builds Ybus with branch impedances, shunts, taps, and phase shifts, then solves nonlinear P and Q mismatch equations by Newton-Raphson. PV and PQ bus angles are unknown; PQ voltage magnitudes are unknown; PV and slack voltage magnitudes are fixed. Reactive generation is reconstructed by bus after solving. There is no Q-limit enforcement or PV-PQ switching in AC PF.
```

DC OPF:

```text
DC OPF optimizes active generator dispatch and bus angles. The objective is generator cost. The constraints are active power balance, generator Pg bounds, slack angle, branch angle limits, and branch active-flow limits. Linear-cost cases use SciPy HiGHS through linprog, while nonlinear cost cases use SLSQP.
```

AC OPF:

```text
AC OPF optimizes Pg, Qg, voltage magnitudes, and voltage angles. It enforces full AC P and Q balance, generator P and Q bounds, voltage bounds, slack angle, branch angle limits, and apparent-power limits at both ends of each branch. It uses SciPy SLSQP, so the result is local and should be judged with final feasibility checks.
```

Validation:

```text
PowerModels/PGLib validation checks OPF formulation consistency on tested benchmark cases. PyPowSyBl/OpenLoadFlow validation checks AC network and security behaviour, especially branch N-1 screening. These comparisons support numerical agreement for tested cases but do not prove universal correctness or global optimality.
```

## Questions You Should Be Ready For

1. Why is DC PF not an optimization problem?
2. What is the DC branch coefficient and why is it not simply `1/x`?
3. How is the slack angle fixed in DC PF, DC OPF, and AC OPF?
4. What does AC PF solve for at PQ buses?
5. What does AC PF solve for at PV buses?
6. Does Python AC PF enforce Q limits?
7. Does Python AC PF implement PV-PQ switching?
8. How is reactive power reconstructed after AC PF?
9. What variables are optimized in Python DC OPF?
10. What variables are optimized in Python AC OPF?
11. What constraints are equality constraints in AC OPF?
12. What constraints are inequality constraints in AC OPF?
13. Why is Python AC OPF local rather than global?
14. How does Python AC OPF initialize its variables?
15. How is the Python AC OPF initial state different from Julia?
16. How are multiple generators at a bus handled?
17. What does PowerModels validation demonstrate?
18. What does PyPowSyBl validation demonstrate?
19. What happens in generator N-1 balancing?
20. What are the biggest limitations?

## Things You Can Defend Confidently

- Python owns the current end-to-end PF/OPF plus N-1 screening workflow.
- DC PF is a fixed-dispatch linear equation solve.
- AC PF is Newton-Raphson on AC P/Q mismatch equations.
- DC OPF uses SciPy `linprog` for linear-cost cases and SLSQP otherwise.
- AC OPF uses SciPy SLSQP.
- Python AC OPF enforces generator Q limits through bounds.
- Python AC PF does not enforce Q limits.
- Python AC OPF uses a PF warm-start attempt.
- Python has PyPowSyBl/OpenLoadFlow security validation tooling.

## Known Limitations To State Openly

- No PV-PQ switching in AC PF.
- No AC PF generator Q-limit enforcement.
- AC PF Q is bus-level reconstruction.
- Python AC OPF is local/nonconvex.
- Python Ipopt AC OPF path is not implemented.
- Higher-order N-k contingencies are not implemented.
- Generator N-1 balancing is static reserve-proportional, not dynamic AGC.
- Own angle-limit checks do not map exactly to PyPowSyBl v1 security labels.

## Claims Not To Make Yet

- Do not claim Python AC OPF uses Ipopt.
- Do not claim Python AC PF enforces Q limits.
- Do not claim Python AC PF switches PV buses to PQ.
- Do not claim AC OPF is globally optimal.
- Do not claim N-1 screening is dynamic simulation.
- Do not claim PyPowSyBl and own labels are identical in every detail.
