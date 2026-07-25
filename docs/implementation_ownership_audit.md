# Implementation Ownership Audit

Date: 2026-07-15

This audit summarizes which parts of the repository are own implementations and
which parts are reference/comparison tooling. It replaces the older June audit
that referred to legacy SciPy OPF paths.

## Executive Position

The repository contains two own-model implementation layers:

- `python/own_models/src`: current Python workflow for PF, OPF validation,
  PowerModels/PyPowSyBl conversion, N-1 screening, topology diagnostics,
  balancing policy, security labels, and output tables.
- `julia/own_models/src`: Julia own-model PF/OPF implementation and
  PowerModels-facing validation/benchmarking utilities.

PowerModels.jl and PyPowSyBl/OpenLoadFlow are reference/comparison tools. They
do not replace the own Python or Julia implementations.

## Current Solver Stack

| Layer | Model | Current implementation | Solver |
| --- | --- | --- | --- |
| Python | DC PF | `python/own_models/src/pf/dc_power_flow.py` | NumPy linear solve |
| Python | AC PF | `python/own_models/src/pf/ac_power_flow.py` | Newton-Raphson |
| Python | DC OPF | `python/own_models/src/opf/dc/` | Pyomo + HiGHS |
| Python | AC OPF | `python/own_models/src/opf/ac/` | Pyomo + Ipopt |
| Julia | DC OPF | `julia/own_models/src/opf/dc/` | JuMP + HiGHS |
| Julia | AC OPF | `julia/own_models/src/opf/ac/` | JuMP + Ipopt |

AC OPF is nonconvex in both Python and Julia. Ipopt returns local nonlinear
solutions, not guaranteed global optima.

## Python Ownership Map

| Area | Primary path | Ownership status |
| --- | --- | --- |
| Data model | `python/own_models/src/data/case.py` | Own `PowerCase`, buses, branches, generators, loads, shunts |
| PowerModels adapter | `python/own_models/src/adapters/powermodels_csv_adapter.py` | Own ingestion of exported/reference PowerModels JSON |
| PyPowSyBl adapter | `python/own_models/src/adapters/pypowsybl_adapter.py` | Own conversion from PyPowSyBl network tables |
| Network admittance | `python/own_models/src/network/admittance.py` | Own Ybus/injection/branch-flow construction |
| DC PF | `python/own_models/src/pf/dc_power_flow.py` | Own linear DC load-flow solve |
| AC PF | `python/own_models/src/pf/ac_power_flow.py` | Own Newton AC load-flow solve |
| DC OPF | `python/own_models/src/opf/dc/` | Own Pyomo DC OPF |
| AC OPF | `python/own_models/src/opf/ac/` | Own Pyomo AC OPF |
| Contingencies | `python/own_models/src/analysis/contingencies.py` | Own branch/generator outage generation |
| Topology | `python/own_models/src/analysis/topology.py` | Own islanding/component diagnostics |
| Balancing policy | `python/own_models/src/analysis/balancing_policy.py` | Own static reserve-proportional redispatch |
| Security checks | `python/own_models/src/analysis/security_checks.py` | Own post-contingency limit checks |
| Security labels | `python/own_models/src/analysis/security_labels.py` | Own label construction/output rows |

## OPF Formulation Ownership

Python AC OPF is an own Pyomo model (arc-based/PowerModels-style formulation --
directed branch arcs carry real p/q flow variables, each pinned to its
Ohm's-law formula via an equality constraint; see
`docs/formulations/ac_opf.md`). The main components are:

- `python/own_models/src/opf/ac/model.py` (variable/constraint assembly AND
  solver execution -- also builds/solves the model)
- `python/own_models/src/opf/ac/variables.py`
- `python/own_models/src/opf/ac/constraints.py`
- `python/own_models/src/opf/ac/objective.py`

It enforces AC active/reactive nodal balance (arc-flow KCL), generator `P/Q`
bounds, voltage bounds, slack/reference angle, branch angle-difference
limits, and two-ended apparent-power limits. Bus shunts are part of the
network/admittance model and must be included in formulation descriptions.

Python DC OPF is an own Pyomo model under `python/own_models/src/opf/dc/`. It
enforces active balance, generator active-power bounds, slack/reference angle,
branch flow limits, and branch angle-difference limits where available.

## Validation Ownership

PowerModels reference exports and comparisons support OPF validation. Current
important outputs include:

- `results/own_python/ac_opf_validation/compact_ac_opf_full_comparison.csv`
- `results/own_vs_powermodels/python_pm_like/summary.csv`
- `results/own_vs_powermodels/python_pm_like/<case>/<scenario>/`

(The non-`_pm_like` `results/own_vs_powermodels/python/` tree is a frozen
pre-2026-07-24 bus-injection snapshot -- that formulation was deleted and can
no longer be regenerated; kept only because a couple of diagnostic scripts
still read it by path for historical comparison.)

PyPowSyBl/OpenLoadFlow comparison is used mainly for load-flow and security
screening behaviour, not as the OPF objective reference.

## Limits To State Clearly

- AC OPF is local/nonconvex; do not claim global optimality.
- AC PF does not enforce generator Q limits or PV-PQ switching.
- AC PF reconstructs reactive output after solving rather than optimizing
  individual generator `Qg`.
- Generator N-1 balancing is deterministic static reserve-proportional
  redispatch, not AGC or dynamic simulation.
- N-1 screening covers single outages, not arbitrary N-k contingencies.
- PowerModels and PyPowSyBl are references for validation/comparison, not the
  implementation being claimed as own work.

## Current Open Issues

The remaining OPF investigation items are concentrated in large/stressed AC OPF
cases, especially `case1888_rte`. The main open questions are:

- whether the largest `Pg`/`Pf` discrepancies are different local optima with
  different binding sets;
- why the Python AC OPF has a robustness/outcome mismatch on
  `1888_rte_generator_114_outage`;
- why own Julia/Python models are slower or require more Ipopt iterations than
  PowerModels.jl on large cases.
