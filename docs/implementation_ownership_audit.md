# Implementation Ownership Audit

Date: 2026-06-17

Scope: repeated after OneDrive synchronization for `C:\Users\efthy\OneDrive - University College Dublin\power-systems-tools-learning`. This audit inspected the synchronized Julia own-model code, Python own-model code, PowerModels reference/export utilities, PyPowSyBl comparison/security utilities, validation scripts, and documentation. No modelling code was changed.

## Updated Repository

Repository audited: `C:\Users\efthy\OneDrive - University College Dublin\power-systems-tools-learning`.

This section reflects the synchronized repository state after OneDrive completed hydration of the Python own-model files. It supersedes the earlier inspection that could only see part of the codebase.

## Executive Position

The repository now shows two own-model implementation layers:

- `julia/own_models/src`: the earlier Julia implementation of DC PF, AC PF, DC OPF, and AC OPF, using JuMP/HiGHS for DC OPF and JuMP/Ipopt for AC OPF.
- `python/own_models/src`: the current Python implementation used for validation and N-1 studies. It includes data structures, PowerModels JSON ingestion, PyPowSyBl conversion/comparison, DC PF, AC PF, DC OPF, AC OPF, branch N-1 screening, generator N-1 screening, topology/islanding diagnostics, generator-outage balancing, and output tables.

The previous audit conclusion that Python own-model files were unreadable is no longer true. The synchronized repository contains concrete Python ownership for topology checks and generator N-1 reserve balancing.

High-confidence ownership claims:

- Data model ownership: `python/own_models/src/data/case.py:6-97` and `julia/own_models/src/data/case_structs.jl:1-75`.
- PowerModels JSON to own case conversion: `python/own_models/src/adapters/powermodels_csv_adapter.py:39-161`.
- PyPowSyBl conversion and comparison tooling: `python/own_models/src/adapters/pypowsybl_adapter.py`, `python/own_models/src/analysis/compare_with_pypowsybl.py:123-138`, `python/own_models/src/analysis/pypowsybl_security_validation.py:897-1016`.
- DC PF ownership: `python/own_models/src/pf/dc_power_flow.py:25-53`; Julia equivalent in `julia/own_models/src/pf/dc_power_flow.jl:11-44`.
- AC PF ownership: `python/own_models/src/pf/ac_power_flow.py:126-184`; Julia equivalents in `julia/own_models/src/pf/ac_power_flow_newton.jl` and `julia/own_models/src/pf/ac_power_flow_nlp.jl`.
- DC OPF ownership: `python/own_models/src/opf/dc_opf.py:234-292`; Julia equivalent in `julia/own_models/src/opf/dc_opf.jl`.
- AC OPF ownership: `python/own_models/src/opf/ac_opf_scipy.py:47-114`; Julia equivalent in `julia/own_models/src/opf/ac_opf.jl`.
- Branch N-1 ownership: `python/own_models/src/analysis/contingencies.py:29-62`, `python/own_models/src/analysis/post_contingency.py:54-144`.
- Generator N-1 ownership: `python/own_models/src/analysis/contingencies.py:65-81`, `python/own_models/src/analysis/balancing_policy.py:69-149`, `python/own_models/src/analysis/post_contingency.py:147-258`.
- Own topology/islanding ownership: `python/own_models/src/analysis/topology.py:37-105`. The Julia topology file remains a placeholder, but Python now supplies the current own-model security topology layer.

Important limits:

- Python AC OPF uses SciPy SLSQP, not Ipopt: `python/own_models/src/opf/ac_opf.py:7-12`, `python/own_models/src/opf/ac_opf_scipy.py:47-114`.
- Julia AC OPF uses JuMP/Ipopt and is nonconvex/local: `julia/own_models/src/opf/ac_opf.jl`.
- AC PF does not enforce generator Q limits or PV-PQ switching in the Python Newton path: `python/own_models/src/pf/ac_power_flow.py:126-184`.
- AC PF reconstructs generator reactive output at bus level; Python security code allocates required Q for checks, but the PF solve itself does not make individual generator Q a decision variable.
- Python `data/loaders.py` and `opf/constraints.py` remain placeholders; the actual Python paths use the adapter, PF/OPF modules, and constraint evaluators listed above.

## Repository Map

| Area | Primary files | Status |
| --- | --- | --- |
| Data model | `python/own_models/src/data/case.py:6-97` | Implemented dataclasses for buses, branches, generators, loads, shunts, and `PowerCase` |
| Unit conversion | `python/own_models/src/data/per_unit.py:4-33` | Per-unit conversions for power, impedance, and admittance |
| PowerModels adapter | `python/own_models/src/adapters/powermodels_csv_adapter.py:39-161` | Reads exported/reference PowerModels JSON into `PowerCase` |
| PyPowSyBl adapter | `python/own_models/src/adapters/pypowsybl_adapter.py` | Builds own `PowerCase` from PyPowSyBl network tables using physical-unit conversions |
| DC matrices | `python/own_models/src/network/dc_matrices.py:8-52` | Builds Bbus and active injection vectors |
| AC admittance | `python/own_models/src/network/admittance.py:10-81` | Builds Ybus, bus injections, branch flows |
| DC PF | `python/own_models/src/pf/dc_power_flow.py:25-53` | Linear solve with slack bus removed |
| AC PF | `python/own_models/src/pf/ac_power_flow.py:126-184` | Newton-Raphson in polar variables |
| DC OPF | `python/own_models/src/opf/dc_opf.py:234-292` | SciPy linprog for linear costs or SLSQP for quadratic costs |
| AC OPF | `python/own_models/src/opf/ac_opf_scipy.py:47-114` | SciPy SLSQP with AC equality and inequality constraints |
| AC OPF variables | `python/own_models/src/opf/ac_variables.py:11-125` | Variable layout, initial point, bounds |
| AC OPF constraints | `python/own_models/src/opf/ac_constraints.py:35-119` | P/Q balance, slack angle, branch MVA, angle, violation evaluation |
| Contingencies | `python/own_models/src/analysis/contingencies.py:8-101` | Branch and generator N-1 generation/application |
| Topology | `python/own_models/src/analysis/topology.py:37-105` | Connected components and island diagnostics |
| Generator balancing | `python/own_models/src/analysis/balancing_policy.py:69-149` | Reserve-proportional balancing after generator outage |
| Security checks | `python/own_models/src/analysis/security_checks.py:150-321` | AC/DC post-contingency limit checks |
| Security labels | `python/own_models/src/analysis/security_labels.py:93-457` | Stable classification labels and output rows |
| N-1 orchestration | `python/own_models/src/analysis/post_contingency.py:54-258` | Branch and generator contingency screening |
| Output CSVs | `python/own_models/src/analysis/contingency_outputs.py:21-82` | Detail and summary dataframes |

## Data Ownership And Unit Handling

Python `PowerCase` stores normalized model data: base MVA, buses, branches, generators, loads, shunts, slack bus id, and a bus-id-to-index map. Bus constants are `PQ=1`, `PV=2`, `SLACK=3` in `python/own_models/src/data/case.py:6-8`.

PowerModels JSON ingestion copies already-exported PowerModels values into `PowerCase`:

- `base_mva = data["baseMVA"]`: `powermodels_csv_adapter.py:40`.
- Bus `index` is 1-based while `bus_id_to_index` is 0-based: `powermodels_csv_adapter.py:58`.
- Branch data copies resistance, reactance, charging, tap, shift, status, rate, and angle bounds: `powermodels_csv_adapter.py:61-88`.
- Generator data copies P/Q bounds, dispatch, voltage setpoint, status, and cost coefficients: `powermodels_csv_adapter.py:91-112`.
- Loads and shunts are copied into explicit tables: `powermodels_csv_adapter.py:114-140`.

Per-unit conversion helpers exist in `python/own_models/src/data/per_unit.py:4-33`. They define base impedance as `nominal_v_kv**2 / base_mva`, convert impedance/admittance, and convert MW/Mvar/MVA to and from per-unit.

PyPowSyBl comparison code converts own results back to physical units for comparison:

- Own bus voltage magnitude becomes kV via `vm * nominal_v_kv`: `python/own_models/src/analysis/compare_with_pypowsybl.py:17-45`.
- Own branch P/Q flows become MW/Mvar via `case.base_mva`: `compare_with_pypowsybl.py:63-100`.

## DC PF Formulation

Primary Python implementation: `python/own_models/src/pf/dc_power_flow.py:25-53`.

Formulation:

- Active branches form Bbus with coefficient `x / (r^2 + x^2)`, not the simplified `1/x`: `python/own_models/src/network/dc_matrices.py:8-30`.
- Net active injection equals active generator `pg0` minus load `pd` minus shunt conductance `gs`: `dc_matrices.py:33-48`.
- The slack row and column are removed and the reduced linear system is solved: `dc_power_flow.py:39-44`.
- Branch active flow is `b * (theta_from - theta_to)`: `dc_power_flow.py:46-49`.
- Slack injection is reconstructed from `bbus @ theta`: `dc_power_flow.py:50-52`.

Limits are not enforced in PF. DC PF reports convergence based on non-slack active mismatch tolerance, not branch, generator, or voltage feasibility.

## AC PF Formulation

Primary Python implementation: `python/own_models/src/pf/ac_power_flow.py:126-184`.

Formulation:

- Ybus is built from series admittance `1/(r+jx)`, branch charging, complex tap ratio, phase shift, and shunts: `python/own_models/src/network/admittance.py:10-39`.
- Bus injection is `S = V * conj(YV)`: `admittance.py:42-45`.
- Branch flows are computed at both ends using the same tap convention: `admittance.py:48-81`.
- Slack bus comes from `case.slack_bus_id`; PV buses are input `bus_type == PV`; PQ buses are input `bus_type == PQ`: `ac_power_flow.py:28-32`.
- The Newton state contains angles for PV/PQ buses and voltage magnitudes for PQ buses: `ac_power_flow.py:126-184`.
- Mismatch is active-power mismatch for PV/PQ buses plus reactive-power mismatch for PQ buses: `ac_power_flow.py:148-156`.
- No damping, line search, PV-PQ switching, or generator Q-limit enforcement is implemented in this Newton path.

Reactive generation reporting:

- `_estimate_qg_by_bus` estimates required bus-level Q as `Qcalc + q_load` for buses with generators: `ac_power_flow.py:112-123`.
- This is a bus-level reconstruction, not an individual generator reactive dispatch.

## DC OPF Formulation

Primary Python implementation: `python/own_models/src/opf/dc_opf.py:234-292`.

Variables:

- Active generator outputs for active generators.
- Voltage angles for all buses.

Constraints:

- Nodal active balance for each bus: generator injection minus demand minus branch-flow terms equals zero in matrix form: `dc_opf.py:54-72`, `89-122`.
- Slack angle fixed to zero: `dc_opf.py:124-127`.
- Branch angle-difference limits enforced where provided: `dc_opf.py:129-141`.
- Branch active-flow limits enforced where `rate_a > 0`: `dc_opf.py:143-150`.
- Generator active-power bounds are in variable bounds: `dc_opf.py:166-186`.

Objective:

- Generator cost is `c2 * pg^2 + c1 * pg + c0`: `python/own_models/src/opf/objectives.py:6-7`.
- Linear-cost cases use `scipy.optimize.linprog(method="highs")`: `dc_opf.py:189-231`.
- Quadratic-cost cases use SciPy SLSQP with the same linear constraints and bounds: `dc_opf.py:234-292`.

Julia equivalent:

- Julia DC OPF uses JuMP and HiGHS with analogous variables, active balance, generator limits, branch angle/flow limits, and cost under `julia/own_models/src/opf`.

## AC OPF Formulation

Primary Python implementation: `python/own_models/src/opf/ac_opf_scipy.py:47-114`.

Variables and ordering:

- `pg` for active generators.
- `qg` for active generators.
- `vm` for all buses.
- `va` for all buses.
- The layout is defined in `python/own_models/src/opf/ac_variables.py:11-53`.

Constraints:

- AC P/Q balance residuals: `python/own_models/src/opf/ac_constraints.py:35-51`.
- Slack angle residual: `ac_constraints.py:54-56`.
- Optional branch apparent-power margins at both ends: `ac_constraints.py:59-71`.
- Optional branch angle-difference margins: `ac_constraints.py:74-84`.
- Pg, Qg, and Vm bounds: `python/own_models/src/opf/ac_variables.py:115-125`.

Objective:

- Same generator cost function as DC OPF, evaluated on active-generator Pg values.

Solver:

- Python AC OPF uses SciPy SLSQP: `python/own_models/src/opf/ac_opf_scipy.py:47-114`.
- `solve_ac_opf(..., solver="ipopt")` is explicitly not implemented in Python: `python/own_models/src/opf/ac_opf.py:7-12`.
- Reported success is based on post-solve feasibility checks against `feasibility_tol`, not simply raw `result.success`: `ac_opf_scipy.py:92-100`.

Julia equivalent:

- Julia AC OPF uses JuMP/Ipopt and enforces AC P/Q balances, voltage limits, generator P/Q bounds, branch angle limits, and two-ended branch MVA limits in `julia/own_models/src/opf/ac_opf.jl`.

## AC OPF Initialization

Python initialization is in `python/own_models/src/opf/ac_variables.py:79-112`:

- Build a variable layout for active generators and buses.
- Attempt an AC Newton PF with `pf_max_iter=30`.
- If PF converges, use PF voltage magnitudes/angles and bus-level Q estimates.
- If PF does not converge, use case input `pg0`, `qg0`, `vm0`, and `va0`.
- Clip Pg, Qg, and Vm to variable bounds.
- Force slack angle to zero.

Exact synchronized extraction for `results/reference/powermodels/ac_opf/pglib_opf_case14_ieee_base_case.json`:

- Active generator ids: `1,2,3,4,5`.
- Bus ids: `1,2,3,4,5,6,7,8,9,10,11,12,13,14`.
- Variable order: `pg[5], qg[5], vm[14], va[14]`.
- Vector length: `38`.
- Initial vector:

```text
1.7,0.295,0,0,0,0,0.3,0.4,0.0828824152258,0.0568094150356,1,1,1,0.968773898545,0.967206646015,1,0.989993021541,1,0.984861958941,0.979557981371,0.985927237897,0.984080058605,0.978900702587,0.962897278369,0,-0.109004039219,-0.264823798802,-0.208023306306,-0.177277323394,-0.284810667138,-0.267742770917,-0.267742770917,-0.299327324663,-0.302489372772,-0.296274767279,-0.301941524112,-0.303571005973,-0.321312255744
```

## PF Versus OPF

| Feature | DC PF | AC PF | DC OPF | AC OPF |
| --- | --- | --- | --- | --- |
| Mathematical task | Solve fixed linearized active-power equations | Solve fixed nonlinear AC PF equations | Minimize cost under DC network constraints | Minimize cost under nonlinear AC constraints |
| Dispatch variables | None | None | Active generator Pg | Active generator Pg and Qg |
| Voltage variables | Angles except slack | Angles for PV/PQ and Vm for PQ | Angles | Vm and Va |
| Reactive power | Ignored | Solved through AC equations, bus-level Q estimate | Ignored | Decision variable with Q bounds |
| Branch limits | Not enforced | Not enforced by solve | Active-flow and angle limits | Apparent-power and angle limits |
| Generator limits | Not enforced | Not enforced by solve | Pg bounds | Pg/Qg bounds |
| Optimization solver | None | Newton linear solves | SciPy linprog/SLSQP or Julia HiGHS | Python SciPy SLSQP or Julia Ipopt |

PF answers whether a fixed dispatch can satisfy equations. OPF chooses a dispatch to optimize cost subject to network and equipment constraints. PF convergence does not prove security because limits are checked outside the PF solve.

## Bus Types, Slack, And Multiple Generators

Slack handling:

- Python PowerModels adapter picks the first bus with `bus_type == SLACK`: `powermodels_csv_adapter.py:32-36`.
- Python AC PF uses `case.slack_bus_id` directly: `ac_power_flow.py:28-32`.
- Python AC OPF fixes the slack angle through an equality residual: `ac_constraints.py:54-56`.
- Python AC PF does not explicitly validate that there is exactly one slack bus.

PV/PQ handling:

- PV buses participate in active-power mismatch equations and keep voltage magnitude fixed.
- PQ buses participate in active and reactive mismatch equations.
- No PV-PQ switching occurs when generator Q limits are exceeded in AC PF.

Multiple generators:

- PF aggregates generator active/reactive injections by bus when forming specified power: `ac_power_flow.py:35-51`.
- AC PF Q reporting is bus-level.
- OPF keeps individual active generators as variables and bounds.
- AC OPF P/Q balance sums generator variables at each bus through vector aggregation in `ac_constraints.py:25-51`.

## Branch, Transformer, And Limit Treatment

AC network model:

- Branch series admittance uses `r` and `x`.
- Line charging is split through `b/2`.
- Tap magnitude defaults to 1.0 if missing or zero.
- Phase shift is included in the complex tap.
- Shunts are added directly to the bus diagonal.

Limit treatment:

- PF does not enforce voltage, branch MVA, generator P, generator Q, or angle limits during the solve.
- DC OPF enforces Pg, branch active-flow, and branch angle limits.
- AC OPF enforces Pg, Qg, voltage, branch apparent-power, and branch angle limits.
- Post-contingency security checks evaluate active generation, reactive generation, voltage, thermal, and angle violations: `python/own_models/src/analysis/security_checks.py:150-321`.

## N-1 Branch Screening

Branch contingency generation:

- `generate_branch_contingencies` produces active branch N-1 outages only; higher-order `k` is not implemented: `python/own_models/src/analysis/contingencies.py:29-45`.
- `apply_contingency` sets the selected branch status to zero: `contingencies.py:84-101`.

Screening flow:

- Build a base dispatch case from AC OPF results: `python/own_models/src/analysis/post_contingency.py:30-47`.
- Apply a branch outage.
- Run island diagnostics before PF: `post_contingency.py:62-75`.
- Run DC security checks for comparison: `post_contingency.py:77`.
- Run post-contingency AC PF: `post_contingency.py:79-99`.
- If PF converges, evaluate security checks and labels: `post_contingency.py:101-114`.
- Batch screening is `run_contingency_screening`: `post_contingency.py:117-144`.

Outputs:

- `python/own_models/scripts/08_run_n1_branch_screening.py` solves the base AC OPF, runs branch screening, and writes detail/summary CSVs.
- `python/own_models/scripts/10_compare_n1_branch_with_pypowsybl.py` runs PyPowSyBl validation against own branch N-1 results.

## N-1 Generator Screening And Balancing

Generator contingency generation:

- `generate_generator_contingencies` produces active generator N-1 outages only: `python/own_models/src/analysis/contingencies.py:65-81`.
- `apply_contingency` sets generator status to zero and sets `pg0/qg0` to zero: `contingencies.py:84-101`.

Balancing policy:

- Implemented policy is `proportional_reserve` for exactly one generator outage: `python/own_models/src/analysis/balancing_policy.py:69-80`.
- If the outaged generator was at the slack bus and no active slack-bus generator remains, a new slack generator is selected by largest reserve headroom, then generator index/id: `balancing_policy.py:32-66`.
- Lost generation comes from the base OPF dispatch, not the raw case value: `balancing_policy.py:84`.
- Eligible generators receive replacement output in proportion to available headroom `pmax - base_pg`: `balancing_policy.py:92-119`.
- If reserve is insufficient, the case is labelled as generation imbalance before AC PF: `balancing_policy.py:101-116`.

Screening flow:

- Apply generator outage.
- Run island diagnostics.
- Apply reserve-proportional balancing.
- If balancing fails, produce a generation-imbalance result.
- Otherwise run DC checks, AC PF, and AC security checks.
- Batch screening is `run_generator_contingency_screening`: `python/own_models/src/analysis/post_contingency.py:235-258`.

This means the synchronized repository does implement generator N-1 screening with a deterministic static reserve policy. It is not a dynamic AGC or time-domain reserve model.

## Islanding And Labels

Own topology/islanding logic exists in Python:

- Connected components are built over active branches: `python/own_models/src/analysis/topology.py:37-61`.
- `island_diagnostics` identifies energized components, slack component, islanded load/generation outside the slack component, load-only and generation-only islands, and balanced-island counts: `topology.py:64-105`.

Label priority is deterministic in `python/own_models/src/analysis/security_labels.py:93-105`:

1. `ISLANDING`
2. `GENERATION_IMBALANCE`
3. `PF_DID_NOT_CONVERGE`
4. `ACTIVE_GENERATION_LIMIT_VIOLATION`
5. `VOLTAGE_VIOLATION`
6. `REACTIVE_LIMIT_VIOLATION`
7. `BRANCH_THERMAL_VIOLATION`
8. `ANGLE_LIMIT_VIOLATION`
9. `SECURE`

The Julia topology file is still not the active topology implementation for these security studies; the Python analysis module is.

## Validation Evidence

PowerModels reference exports:

- DC OPF references are exported by `julia/tools/powermodels/scripts/06_export_dc_opf_reference.jl`.
- AC OPF references are exported by `julia/tools/powermodels/scripts/07_export_ac_opf_reference.jl`.
- Scenario definitions are in `config/validation/dc_opf_scenarios.json`.

Python validation:

- DC OPF validation script: `python/own_models/scripts/06_validate_dc_opf_against_powermodels.py`.
- AC OPF validation script: `python/own_models/scripts/07_validate_ac_opf_against_powermodels.py`.
- AC OPF validation documentation: `docs/python_ac_opf_validation.md`.

Documented AC OPF validation coverage includes PGLib case5, case14, and case30 across base, relaxed, branch-congested, generator-limited, and load-scaled scenarios. The documentation states outcome matches for the tested combinations and notes a relaxed-case Qg difference risk.

PyPowSyBl validation:

- Own-vs-PyPowSyBl PF comparison utilities are in `python/own_models/src/analysis/compare_with_pypowsybl.py`.
- Branch N-1 PyPowSyBl validation is in `python/own_models/src/analysis/pypowsybl_security_validation.py:897-1016`.
- The comparison explicitly harmonizes outputs and notes that own angle-limit checks have no direct PyPowSyBl equivalent.

## Runnable Entry Points

Representative Python commands from the repository root:

```powershell
python python\own_models\scripts\02_run_own_dc_pf.py
python python\own_models\scripts\03_run_own_ac_pf.py
python python\own_models\scripts\04_run_own_dc_opf.py
python python\own_models\scripts\06_validate_dc_opf_against_powermodels.py
python python\own_models\scripts\07_validate_ac_opf_against_powermodels.py
python python\own_models\scripts\08_run_n1_branch_screening.py
python python\own_models\scripts\09_run_n1_generator_screening.py
python python\own_models\scripts\10_compare_n1_branch_with_pypowsybl.py
```

`python/own_models/scripts/05_compare_with_pypowsybl.py` is still a placeholder script even though comparison library code exists.

Representative Julia areas:

```powershell
julia julia\tools\powermodels\scripts\06_export_dc_opf_reference.jl
julia julia\tools\powermodels\scripts\07_export_ac_opf_reference.jl
```

## Risk Register

| Risk | Area | Evidence | Practical effect | Mitigation |
| --- | --- | --- | --- | --- |
| AC PF lacks Q-limit enforcement and PV-PQ switching | AC PF | `python/own_models/src/pf/ac_power_flow.py:126-184` | PF can converge with reactive limit violations | Report Q violations separately; do not claim PF enforces Q limits |
| AC PF individual Q allocation is not a solve variable | AC PF/security | `ac_power_flow.py:112-123` | Multiple-generator bus Q claims need care | State bus-level PF Q and security allocation separately |
| Python AC OPF is local SLSQP | AC OPF | `ac_opf_scipy.py:47-114` | Cannot claim global optimality | State local nonlinear solve and compare against reference tools |
| Python Ipopt AC OPF path is not implemented | AC OPF | `ac_opf.py:7-12` | Python and Julia solver stacks differ | Name solver stack precisely |
| Higher-order contingencies not implemented | Security | `contingencies.py:29-45`, `65-81` | Only N-1 can be claimed | Keep claims to single outages |
| Generator balancing is static reserve proportional | Security | `balancing_policy.py:69-149` | Not AGC, transient, or dynamic reserve | Describe as deterministic static post-contingency redispatch |
| PyPowSyBl has no direct own angle-limit equivalent comparison | Validation | `pypowsybl_security_validation.py:838-894` | Some label differences may be expected | Report angle checks as own-side criterion |

## Defensible Statements

- The repository owns complete DC PF, AC PF, DC OPF, and AC OPF formulations in code.
- Python is the current complete own-model path for N-1 branch and generator screening.
- Python branch N-1 screening includes own islanding diagnostics, AC PF, DC checks, security labels, and CSV outputs.
- Python generator N-1 screening includes outage application, reserve-proportional balancing, possible slack reassignment, AC PF, DC checks, security labels, and CSV outputs.
- AC PF does not enforce generator reactive limits or switch PV buses to PQ.
- AC OPF enforces Q limits through Qg decision-variable bounds.
- Python AC OPF is SciPy SLSQP; Julia AC OPF is JuMP/Ipopt.
- PowerModels and PyPowSyBl are reference/comparison tools, not replacements for the own Python security implementation.

## Statements To Avoid

- "The Python AC OPF uses Ipopt." It does not; only Julia AC OPF uses Ipopt.
- "AC PF enforces Q limits." It does not in the inspected Newton implementation.
- "The N-1 generator policy is dynamic reserve or AGC." It is static reserve-proportional balancing.
- "The repository implements N-k contingencies." It implements N-1 only.
- "AC OPF is globally optimal." The AC formulation is nonconvex and solved locally.
- "Topology/islanding is missing from the repository." It is missing from the Julia placeholder but implemented in Python.

## Short Meeting Summary

After OneDrive synchronization, the implementation picture is clearer: the Python own-model layer contains the current end-to-end workflow. It parses PowerModels/PyPowSyBl-style data into an own `PowerCase`, solves DC/AC PF, solves DC/AC OPF, validates against PowerModels, runs branch N-1 and generator N-1 screening, classifies islanding/nonconvergence/violations, and writes summary outputs. Julia remains a useful independent own-model implementation for PF/OPF, especially JuMP/Ipopt AC OPF, but the security and contingency ownership is now demonstrably in Python.
