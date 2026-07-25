# Python AC OPF Debugging Start Point

Date: 2026-07-17

This note starts the Python AC OPF debugging pass using the same discipline as the Julia validation.

## What Is Already Clear

- The current Python bus-injection formulation includes explicit bus shunts through the Ybus diagonal.
- The Python branch-flow and PM-like formulations include bus shunts explicitly in the nodal balance as `gs * vm^2` and `bs * vm^2` terms.
- The first Python debugging scope should use scenarios already flagged by objective, variable, residual, status, or binding differences.

## Generated Tables

- `results/own_vs_powermodels/python_ac_opf_debug_target_inventory.csv`
- `results/own_vs_powermodels/python_ac_opf_debug_target_summary.csv`
- `results/own_vs_powermodels/python_ac_opf_debug_recommended_run_queue.csv`
- `results/own_vs_powermodels/python_power_balance_bus_shunt_term_locations.csv`
- `results/own_vs_powermodels/python_deviation_minimization_diagnostics.csv`
- `results/own_vs_powermodels/python_deviation_variable_differences.csv`
- `results/own_vs_powermodels/python_deviation_inequality_levels.csv`
- `results/own_vs_powermodels/python_deviation_binding_summary.csv`
- organized copies under `results/own_vs_powermodels/_organized_for_meeting/08_python_ac_opf_debugging`

## Immediate Run Scope

- high-priority target rows selected: 280
- de-duplicated recommended run-queue rows: 366
- high-priority recommended run-queue rows: 270
- source/case summary rows: 280
- shunt-formulation audit rows: 8

## Implemented Runner

- `python/own_models/scripts/32_run_python_ac_opf_deviation_diagnostics.py` runs constrained and equality-only deviation minimization.
- It supports `current` and `pm_like_zero` variants.
- It aligns PowerModels voltage-angle targets by subtracting the PM slack-bus angle before solving, because Python fixes the reference angle to zero.
- It writes summary, variable-difference, inequality-level, and binding-summary tables.

Pilot result already run:

- `1888_rte_load_x1p2__reactive_power_stress__none__none__q_load_multiplier__1p2`, current Python model.
- Constrained/full after angle alignment: max Pg deviation about `4.89e-5` pu and max Vm deviation about `1.27e-7`.
- Equality-only/full after angle alignment: max Pg deviation about `1.26e-13` pu and max Vm deviation about `1.01e-12`.

Next coding step:

1. Run the deviation diagnostic on the remaining solved-reference rows in the recommended queue.
2. Add Python solver diagnostics for objective/status-inspection rows.
3. Run PM-like Python deviation diagnostics on its solved-reference row.
4. Add Python shunt-toggle only if a Python-specific inconsistency remains after those checks.
