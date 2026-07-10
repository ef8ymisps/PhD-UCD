# Julia Own Models Implementation Audit

Date: 2026-06-17

Scope: `julia/own_models` in `C:\Users\efthy\OneDrive - University College Dublin\power-systems-tools-learning`. This audit covers the Julia own-model data structures, PowerModels adapter, network matrices, DC PF, AC PF, DC OPF, AC OPF, validation/comparison utilities, scripts, and known placeholders. No modelling code was changed.

## Executive Position

The Julia own-model layer is a concrete implementation of:

- DC power flow.
- AC power flow by classical Newton-Raphson.
- AC power flow as a zero-objective JuMP/Ipopt NLP.
- DC OPF as a JuMP/HiGHS optimization model.
- AC OPF as a JuMP/Ipopt nonlinear optimization model.
- PowerModels comparison, validation-case generation, OPF inspection, DC-to-AC feasibility checks, and benchmarking.

It is not the current complete implementation for N-1 security screening. Julia has no implemented own topology extraction, no branch/generator N-1 screening orchestration, and no generator-outage balancing policy under `julia/own_models`. Those security features are currently in the Python own-model layer.

## Module And Entry Points

The main module is `julia/own_models/src/OwnPowerModels.jl`.

It includes:

- Data: `case_structs.jl`, `case_loader.jl`.
- Adapter: `adapters/powermodels_adapter.jl`.
- Network: `network/dc_matrices.jl`, `network/admittance_matrix.jl`.
- PF: `pf/dc_power_flow.jl`, `pf/ac_power_flow.jl`.
- OPF: `opf/objectives.jl`, `opf/constraints.jl`, `opf/dc_opf.jl`, `opf/ac_opf.jl`.
- Analysis: comparison, feasibility, benchmarking, validation cases, validation runner, OPF inspection, AC OPF validation.

Public exports include `PowerCase`, `load_powercase_from_powermodels`, `solve_dc_power_flow`, `solve_ac_power_flow_own`, `solve_ac_power_flow_nlp`, `solve_ac_power_flow_newton`, `solve_dc_opf_own`, and `solve_ac_opf_own`: `julia/own_models/src/OwnPowerModels.jl:27-82`.

Representative scripts:

```powershell
julia julia\own_models\scripts\01_test_neutral_loader.jl
julia julia\own_models\scripts\02_run_own_dc_pf.jl
julia julia\own_models\scripts\03_run_own_ac_pf.jl
julia julia\own_models\scripts\04_run_own_dc_opf.jl
julia julia\own_models\scripts\05_compare_with_powermodels.jl
```

Note: `scripts/02_run_own_dc_pf.jl` contains absolute include paths under `C:/Users/Efthymis/...`, unlike the other scripts that use relative module includes.

## Data Model

Core structs are in `julia/own_models/src/data/case_structs.jl:1-75`:

- `Bus`: id, index, bus type, initial voltage magnitude/angle, voltage bounds.
- `Branch`: id, endpoints, resistance/reactance, charging/shunts, rating, tap, phase shift, angle limits, status.
- `Generator`: id, bus, initial Pg/Qg, P/Q bounds, status, quadratic/linear/constant cost coefficients.
- `Load`: id, bus, Pd/Qd, status.
- `Shunt`: id, bus, Gs/Bs, status.
- `PowerCase`: name, baseMVA, bus/branch/generator/load/shunt vectors, slack bus id, bus-id-to-index map.

The per-unit helper file is minimal:

- `to_per_unit(value, base) = value / base`.
- `from_per_unit(value_pu, base) = value_pu * base`.
- Evidence: `julia/own_models/src/data/per_unit.jl:1-7`.

## PowerModels Adapter

Case loading is `load_powercase_from_powermodels(case_path)`, which calls `PowerModels.parse_file(case_path)` and then `powermodels_to_powercase`: `julia/own_models/src/data/case_loader.jl:6-8`.

The adapter maps PowerModels data into `PowerCase`:

- Sorts string dictionary keys numerically: `powermodels_adapter.jl:3-4`.
- Finds the first bus with `bus_type == 3` as slack/reference: `powermodels_adapter.jl:7-15`.
- Extracts generator cost coefficients with fallbacks for missing or short cost arrays: `powermodels_adapter.jl:18-35`.
- Copies base MVA: `powermodels_adapter.jl:39-40`.
- Builds bus vector and bus-id-to-index map: `powermodels_adapter.jl:42-64`.
- Builds branch vector, including `br_r`, `br_x`, `b_fr`, `b_to`, `g_fr`, `g_to`, `rate_a`, tap defaulting, shift, angle limits, and status: `powermodels_adapter.jl:66-100`.
- Builds generator vector with Pg/Qg, P/Q bounds, status, and cost: `powermodels_adapter.jl:102-123`.
- Builds load vector and optional shunt vector: `powermodels_adapter.jl:125-156`.
- Returns `PowerCase`: `powermodels_adapter.jl:158-171`.

The adapter copies PowerModels-parsed values. It does not perform a broad physical-unit conversion layer beyond the minimal helper functions.

## Network Models

DC matrix construction is in `julia/own_models/src/network/dc_matrices.jl`.

- Branch coefficient is `x / (r^2 + x^2)`: `dc_matrices.jl:5-12`.
- Near-zero impedance and near-zero reactance are rejected: `dc_matrices.jl:8-25`.
- Bbus is assembled with positive diagonal and negative off-diagonal entries for active branches: `dc_matrices.jl:15-40`.
- Net active injection adds active generator `pg0`, subtracts active load `pd`, and subtracts active shunt `gs`: `dc_matrices.jl:42-74`.
- Slack index is `case.bus_id_to_index[case.slack_bus_id]`: `dc_matrices.jl:76-78`.

AC admittance is in `julia/own_models/src/network/admittance_matrix.jl`.

- Ybus uses `1/(r+jx)`, branch-side admittances, tap magnitude, phase shift, and shunts: `admittance_matrix.jl:3-44`.
- Bus injections use `S = V .* conj.(Ybus * V)`: `admittance_matrix.jl:46-54`.
- Near-zero branch impedance is rejected: `admittance_matrix.jl:12-13`.

Topology is not implemented in Julia:

- `network_topology(case::NeutralCase)` immediately errors: `julia/own_models/src/network/topology.jl:1-3`.

## DC Power Flow

Implementation: `julia/own_models/src/pf/dc_power_flow.jl:5-45`.

Result fields:

- `theta::Vector{Float64}`.
- `branch_flows::Dict{String, Float64}`.
- `slack_injection::Float64`.

Solve flow:

- Build Bbus and fixed net active injection.
- Remove the slack row/column.
- Solve `Bred \ Pred`.
- Set slack angle to zero.
- Compute branch flows as `dc_branch_flow_coefficient(branch) * (theta[i] - theta[j])`.
- Compute slack injection as `B[slack_idx, :] ⋅ theta`.

DC PF does not enforce generator, branch, or voltage limits. It solves the linear fixed-dispatch equations and returns the implied slack injection.

## AC Power Flow

Public dispatcher: `julia/own_models/src/pf/ac_power_flow.jl:8-16`.

- `method=:newton` calls `solve_ac_power_flow_newton`.
- `method=:nlp` calls `solve_ac_power_flow_nlp`.
- Unknown methods error.

Shared support comes from `julia/own_models/src/pf/ac_power_flow_nlp.jl`:

- `ACPowerFlowResult`: convergence flag, iterations, Vm/Va, calculated P/Q, `qg_by_bus`, branch flows, mismatch norm: `ac_power_flow_nlp.jl:6-16`.
- Specified P/Q sums active generators and subtracts loads: `ac_power_flow_nlp.jl:18-45`.
- Bus type classification requires exactly one slack bus: `ac_power_flow_nlp.jl:47-67`.
- State order is voltage angles for PV/PQ buses and voltage magnitudes for PQ buses: `ac_power_flow_nlp.jl:69-93`.
- AC branch flows include taps and branch shunts and return `pf/qf/pt/qt`: `ac_power_flow_nlp.jl:113-153`.
- Reactive generator estimate is bus-level `Qcalc + q_load`: `ac_power_flow_nlp.jl:155-172`.

Newton method:

- Implementation: `julia/own_models/src/pf/ac_power_flow_newton.jl:96-165`.
- Starts from case `vm0` and `va0`: `ac_power_flow_newton.jl:105-106`.
- Solves active mismatch for PV/PQ buses and reactive mismatch for PQ buses: `ac_power_flow_newton.jl:115-117`.
- Builds an analytic Jacobian: `ac_power_flow_newton.jl:29-94`.
- Uses direct linear solve `J \ mismatch`: `ac_power_flow_newton.jl:126-131`.
- No damping, line search, PV-PQ switching, or generator Q-limit enforcement is implemented.

NLP method:

- Implementation: `julia/own_models/src/pf/ac_power_flow_nlp.jl:174-278`.
- Uses JuMP/Ipopt with zero objective.
- Sets Ipopt `tol` and `max_iter`: `ac_power_flow_nlp.jl:186-189`.
- Voltage variables are bounded by bus voltage limits: `ac_power_flow_nlp.jl:191-199`.
- Slack angle and slack voltage are fixed to input values: `ac_power_flow_nlp.jl:202-203`.
- PV bus voltage magnitudes are fixed to input values: `ac_power_flow_nlp.jl:205-206`.
- Active equations are enforced for PV/PQ buses; reactive equations for PQ buses: `ac_power_flow_nlp.jl:233-238`.

AC PF limitations:

- No generator Q-limit enforcement.
- No PV-PQ switching.
- Reactive generator output is bus-level, not individual-generator dispatch.
- NLP PF has voltage variable bounds, but PV and slack voltage magnitudes are still fixed to case inputs.

## DC OPF

Implementation: `julia/own_models/src/opf/dc_opf.jl:7-73`, with constraints in `constraints.jl` and objective in `objectives.jl`.

Solver:

- JuMP model with default `HiGHS.Optimizer`: `dc_opf.jl:15-23`.

Variables:

- `theta[bus_ids]`.
- `pg[gen_ids]`.
- Evidence: `dc_opf.jl:26-30`.

Constraints:

- Slack angle fixed to zero: `constraints.jl:3-5`.
- Active generator limits for online generators, inactive generators fixed to zero: `constraints.jl:8-16`.
- DC nodal balance: sum online generator Pg minus load Pd minus shunt Gs equals branch-flow sum: `constraints.jl:18-50`.
- Branch angle-difference limits: `constraints.jl:53-62`.
- Branch active-flow limits when `rate_a > 0`: `constraints.jl:64-67`.

Objective:

- `min sum(cost_c2 * pg^2 + cost_c1 * pg + cost_c0)` over online generators: `objectives.jl:3-15`.

Result:

- Objective value.
- JuMP termination status.
- Bus angle dictionary.
- Generator Pg dictionary.
- Active branch-flow dictionary.
- Evidence: `dc_opf.jl:41-72`.

## AC OPF

Implementation: `julia/own_models/src/opf/ac_opf.jl:8-319`.

Solver:

- JuMP model with default `Ipopt.Optimizer`: `ac_opf.jl:18-26`.
- The formulation is nonlinear and nonconvex; results are local unless otherwise proven.

Variables:

- `vm[bus_ids]`.
- `va[bus_ids]`.
- `pg[gen_ids]`.
- `qg[gen_ids]`.
- Evidence: `ac_opf.jl:36-39`.

Bounds and starts:

- Bus voltage magnitudes are bounded by `vmin/vmax`.
- Vm/Va starts come from `bus.vm0/bus.va0`.
- Online generator Pg/Qg are bounded by `pmin/pmax/qmin/qmax`.
- Pg/Qg starts come from `gen.pg0/gen.qg0`.
- Offline generators are fixed to zero.
- Slack angle is fixed to zero.
- Evidence: `ac_opf.jl:45-70`.

AC equations:

- Builds Ybus and separates G/B: `ac_opf.jl:76-78`.
- Defines nonlinear P and Q injection expressions: `ac_opf.jl:82-114`.
- For every bus, enforces `sum(pg) - pd == Pcalc` and `sum(qg) - qd == Qcalc`: `ac_opf.jl:128-159`.

Branch limits:

- Enforces angle-difference bounds for active branches: `ac_opf.jl:166-175`.
- If `rate_a > 0`, expands branch-flow equations into real-valued JuMP nonlinear expressions: `ac_opf.jl:180-249`.
- Enforces apparent-power limits at both from and to ends: `ac_opf.jl:251-252`.

Objective:

- Same quadratic/linear/constant online-generator cost: `ac_opf.jl:259-269`.

Result handling:

- If the model has no values, returns NaNs and empty branch-flow dictionary: `ac_opf.jl:281-297`.
- Otherwise returns objective, status, Vm/Va/Pg/Qg dictionaries, and post-calculated branch flows: `ac_opf.jl:300-318`.

## AC OPF Initial State

Julia AC OPF starts directly from the input case, not from an AC PF warm start.

Exact extraction for `results/reference/powermodels/ac_opf/pglib_opf_case14_ieee_base_case.json`, matching Julia AC OPF variable order:

- Generator ids: `1,2,3,4,5`.
- Bus ids: `1,2,3,4,5,6,7,8,9,10,11,12,13,14`.
- Variable order: `pg[5], qg[5], vm[14], va[14]`.
- Vector length: `38`.
- Initial vector:

```text
1.7,0.295,0,0,0,0.05,0,0.2,0.09,0.09,1,1,1,1,1,1,1,1,1,1,1,1,1,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0
```

This differs from the Python AC OPF initial vector because the Python implementation first attempts a Newton PF warm start.

## Validation And Analysis Utilities

Comparison with PowerModels:

- `compare_dc_pf_with_powermodels`: `julia/own_models/src/analysis/compare_with_powermodels.jl:34-118`.
- `compare_dc_opf_with_powermodels`: `compare_with_powermodels.jl:119-251`.
- `compare_ac_pf_with_powermodels`: `compare_with_powermodels.jl:252-355`.
- `compare_ac_opf_with_powermodels`: `compare_with_powermodels.jl:356+`.

DC-to-AC feasibility:

- Applies a DC OPF Pg dispatch into a case: `julia/own_models/src/analysis/dc_to_ac_feasibility.jl:42-76`.
- Checks voltage, bus-level reactive, and branch thermal violations after AC PF: `dc_to_ac_feasibility.jl:78-205`.

Validation cases:

- Relaxed limits: `julia/own_models/src/analysis/validation_cases.jl:116-156`.
- Congested branch: `validation_cases.jl:157-179`.
- Generator-limited case: `validation_cases.jl:181-208`.
- Infeasible/load-stress case: `validation_cases.jl:210-231`.
- Default suite construction: `validation_cases.jl:253-291`.

Validation runner:

- Runs DC and/or AC OPF, captures status, objective, binding counts, and error messages: `julia/own_models/src/analysis/validation_runner.jl:153-242`.
- Batch runner: `validation_runner.jl:268+`.

AC OPF validation against PowerModels:

- Tolerance/result structs: `julia/own_models/src/analysis/ac_opf_validation.jl:10-28`.
- Own AC OPF and PowerModels AC OPF solve path: `ac_opf_validation.jl:200-229`.
- Scenario data construction: `ac_opf_validation.jl:339-372`.
- Suite runner: `ac_opf_validation.jl:372+`.

Benchmarking:

- Benchmarks own DC/AC OPF against PowerModels solve paths using repeated calls: `julia/own_models/src/analysis/benchmarking.jl:88-248`.

OPF solution inspection:

- Generator, bus, branch, summary, and angle-limit inspection helpers exist in `julia/own_models/src/analysis/opf_solution_inspection.jl`.

## Placeholders And Gaps

Implemented:

- Data model.
- PowerModels case loading.
- DC matrix construction.
- AC Ybus construction.
- DC PF.
- AC PF Newton.
- AC PF NLP.
- DC OPF.
- AC OPF.
- PowerModels comparisons.
- OPF validation and inspection utilities.

Not implemented or incomplete in Julia:

- `julia/own_models/src/network/topology.jl`: topology extraction immediately errors.
- `julia/own_models/src/analysis/metrics.jl`: `solution_metrics` immediately errors.
- `julia/own_models/src/analysis/violations.jl`: `check_violations` immediately errors.
- No Julia own branch N-1 screening orchestration equivalent to the Python security flow.
- No Julia own generator N-1 balancing policy.
- No Julia own PyPowSyBl adapter/comparison layer under `julia/own_models`.

## Risk Register

| Risk | Area | Evidence | Practical effect | Mitigation |
| --- | --- | --- | --- | --- |
| AC PF does not enforce generator Q limits | AC PF | Newton/NLP PF use fixed bus equations and bus-level `qg_by_bus` | PF can converge while generator Q is infeasible | Use AC OPF for Q-bound feasibility; report PF Q checks separately |
| No PV-PQ switching | AC PF | No switching logic in Newton or NLP PF | Limit-hit PV buses remain PV | State limitation explicitly |
| AC OPF is local nonlinear optimization | AC OPF | JuMP/Ipopt nonconvex model | Cannot claim global optimum | Compare with PowerModels and report status/tolerances |
| Julia topology missing | Security/topology | `network/topology.jl:1-3` | Julia cannot classify islands itself | Use Python topology/security layer or implement Julia topology |
| Julia N-1 security missing | Security | No screening/balancing modules under Julia own models | Cannot claim Julia owns N-1 workflow | Keep Julia claims to PF/OPF/validation |
| Some analysis helpers are placeholders | Metrics/violations | `metrics.jl`, `violations.jl` | Generic metrics/violation API not available | Use implemented feasibility/inspection functions |
| Absolute path in one script | Scripts | `scripts/02_run_own_dc_pf.jl:1-2` | May fail on other usernames | Prefer module-based scripts or update include path |

## Defensible Statements

- The Julia own models implement DC PF, AC PF, DC OPF, and AC OPF.
- Julia DC OPF is a JuMP/HiGHS model with Pg and theta variables, active balance, Pg bounds, branch angle limits, optional branch active-flow limits, and generator cost.
- Julia AC OPF is a JuMP/Ipopt model with Vm, Va, Pg, Qg variables, voltage bounds, generator P/Q bounds, full AC P/Q balances, branch angle limits, and two-ended branch MVA limits.
- Julia AC PF can be run either as a Newton solve or as a zero-objective Ipopt NLP.
- Julia AC PF estimates reactive generation at bus level and does not enforce individual generator Q limits.
- Julia has validation and comparison utilities against PowerModels.
- Julia does not currently own topology/islanding or N-1 security workflows.

## Statements To Avoid

- "Julia owns the complete N-1 branch/generator security workflow." It does not in the inspected `julia/own_models` tree.
- "Julia AC PF enforces Q limits." It does not.
- "Julia AC OPF is globally optimal." It is a local nonlinear Ipopt solve.
- "Julia topology is implemented." The Julia topology file is a placeholder.
- "All Julia analysis helpers are complete." Some are placeholders.

## Short Meeting Summary

The Julia own-model code is strong for PF and OPF formulation ownership. It defines its own `PowerCase`, ingests PowerModels cases, builds DC and AC network models, solves DC PF, solves AC PF by Newton or Ipopt NLP, solves DC OPF with JuMP/HiGHS, and solves AC OPF with JuMP/Ipopt. It also includes useful PowerModels comparison, validation, feasibility, inspection, and benchmarking tools. The main limits are clear: Julia AC PF does not enforce generator Q limits or PV-PQ switching, AC OPF is local, and the Julia tree does not implement topology/islanding or N-1 security screening.
