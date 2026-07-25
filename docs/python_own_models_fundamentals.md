# Python Own Models Fundamentals

Date: 2026-07-15

This note summarizes the current Python own-model implementation in
`python/own_models`. It replaces older fundamentals notes that described legacy
SciPy-based OPF paths.

## Big Picture

The Python layer owns the current end-to-end workflow for data ingestion,
power-flow studies, OPF validation, and N-1 screening.

| Model | Basic question | Type of problem | Current solver |
| --- | --- | --- | --- |
| DC PF | Given fixed active injections, what are bus angles and active branch flows? | Linear equations | NumPy linear solve |
| AC PF | Given fixed P/Q injections and voltage controls, what are bus voltages and branch P/Q flows? | Nonlinear equations | Newton-Raphson |
| DC OPF | What active dispatch minimizes cost under DC network constraints? | Optimization | Pyomo + HiGHS |
| AC OPF | What Pg, Qg, Vm, and Va minimize cost under AC equations and limits? | Nonconvex NLP | Pyomo + Ipopt |

The common workflow is:

```text
PowerModels/PGLib or PyPowSyBl data
-> own PowerCase
-> PF/OPF model
-> validation tables and security-screening outputs
```

## Data Model

The shared Python data model is `python/own_models/src/data/case.py`.

It stores:

- buses
- branches
- generators
- loads
- shunts
- base MVA
- slack bus id
- bus-id to index mapping

PowerModels JSON ingestion is handled by
`python/own_models/src/adapters/powermodels_csv_adapter.py`. It copies the
PowerModels/PGLib case data into the own `PowerCase` convention, including bus
shunts.

## Network Physics

DC PF and DC OPF use the active-power network approximation. Active bus shunt
conductance contributes to net active injection:

```text
net active injection = generation - load - Gs
```

AC PF and AC OPF use the AC admittance model in
`python/own_models/src/network/admittance.py`, including branch series
admittance, line charging, taps, phase shifts, and bus shunts.

For AC nodal balance, the bus shunt terms must be included:

```text
sum Pg - Pd - Gs = active network injection
sum Qg - Qd + Bs = reactive network injection
```

This is correct in the code path and should also appear in formulation slides.

## DC OPF

The current Python DC OPF implementation is under
`python/own_models/src/opf/dc/`.

It optimizes:

- generator active power `Pg`
- bus voltage angle `theta`

It enforces:

- active power balance
- generator active-power bounds
- slack/reference angle
- branch active-flow limits
- branch angle-difference limits where available

The current solver path is Pyomo + HiGHS.

## AC OPF

The current Python AC OPF implementation is under
`python/own_models/src/opf/ac/`.

Important files:

- `model.py`: builds the Pyomo model
- `variables.py`: defines `Vm`, `Va`, `Pg`, and `Qg`
- `constraints.py`: adds/evaluates AC balance and engineering limits
- `objective.py`: adds generation cost
- `solve.py`: calls Ipopt and extracts results

It optimizes:

- generator active power `Pg`
- generator reactive power `Qg`
- bus voltage magnitude `Vm`
- bus voltage angle `Va`

It enforces:

- AC active/reactive balance at every bus
- generator `P/Q` bounds
- voltage magnitude bounds
- slack/reference angle
- branch angle-difference limits
- apparent-power limits at both ends of each branch

AC OPF is nonlinear and nonconvex. Ipopt gives a local solution, not a
guaranteed global optimum.

## Validation Role

PowerModels.jl is the primary OPF reference. PyPowSyBl/OpenLoadFlow is mainly
used for PF/security comparison rather than OPF objective validation.

The most important current Python AC OPF validation table is:

```text
results/own_python/ac_opf_validation/compact_ac_opf_full_comparison.csv
```

The more inspection-oriented own-vs-PowerModels tables are under:

```text
results/own_vs_powermodels/python/
```

For large/stressed AC OPF cases, local optimum selection matters. A dispatch
difference should be investigated through feasibility, binding sets, and
element-level generator/branch tables before calling it a formulation bug.

## Security Screening

The Python layer also owns the N-1 screening workflow:

- branch contingencies
- generator contingencies
- islanding/topology diagnostics
- deterministic reserve-proportional generator redispatch
- AC PF/DC checks after contingencies
- CSV outputs for labels and summaries

This is static security screening, not dynamic simulation or AGC modelling.

## Claims To Make Carefully

Safe claims:

- Python owns the current PF/OPF plus N-1 screening workflow.
- Python AC OPF is Pyomo/Ipopt.
- Julia AC OPF is JuMP/Ipopt.
- AC OPF enforces generator Q limits through `Qg` bounds.
- AC PF does not enforce generator Q limits or PV-PQ switching.
- AC OPF validation supports formulation consistency on tested cases.

Claims to avoid:

- AC OPF is globally optimal.
- AC PF enforces generator Q limits.
- AC PF switches PV buses to PQ.
- N-1 screening is dynamic simulation or AGC.
- PowerModels/PyPowSyBl replace the own implementation.
