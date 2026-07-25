# Python AC OPF Solver-Start and PM-Like Validation

Date: 2026-07-17

This note records the Python AC OPF validation step after the deviation
diagnostics.

## Purpose

The deviation-minimization diagnostics showed that the PowerModels solution is
reachable by the Python formulations once the PowerModels voltage-angle target
is shifted so the Python reference bus angle is zero.

The next question was therefore:

- does the normal generation-cost OPF reach the PowerModels solution if started
  like PowerModels?
- does the Python PM-like formulation improve agreement, variable differences,
  binding structure, solve time, and iteration count?

## Scripts

- `python/own_models/scripts/31_prepare_python_ac_opf_debugging_artifacts.py`
- `python/own_models/scripts/32_run_python_ac_opf_deviation_diagnostics.py`
- `python/own_models/scripts/33_run_python_ac_opf_solver_start_and_pm_like_validation.py`
- `python/own_models/scripts/37_run_python_pm_like_equality_only_deviation.py`

## Output Tables

Main source:

- `results/own_vs_powermodels/_organized_for_meeting/08_python_ac_opf_debugging`

Tables:

- `python_ac_opf_debug_recommended_run_queue.csv`
- `python_solver_start_diagnostics.csv`
- `python_start_validation_summary.csv`
- `python_pm_like_meaningful_validation_summary.csv`
- `python_pm_like_equality_only_deviation_targets.csv`
- `python_pm_like_equality_only_deviation_minimization.csv`
- `python_pm_like_equality_only_deviation_variable_differences.csv`
- `python_pm_like_equality_only_deviation_inequality_levels.csv`
- `python_pm_like_equality_only_deviation_binding_summary.csv`

## Batch Scope

The run used the six compact-reference rows where PowerModels has solved target
vectors and the row is meaningful for deviation/start validation:

- `1888_rte_angle_20.0deg`
- `1888_rte_generator_114_outage`
- `1888_rte_load_x1.2` reactive power stress
- `1888_rte_qg_limits_x0.6`
- `1888_rte_qg_limits_x0.8`
- `1888_rte_load_x0.8`

For each scenario we ran:

- current Python model with zero start;
- Python PM-like model with zero start.

## Findings

All 12 runs solved with Ipopt status `optimal`.

Current model with zero start:

- matches PowerModels on `angle_20deg`, `generator_114_outage`,
  `qg_limits_x0.6`, and `load_x0.8`;
- still lands in a different local solution on:
  - `load_x1.2` reactive stress, objective relative difference `0.00242`;
  - `qg_limits_x0.8`, objective relative difference `0.04877`.

Python PM-like with zero start:

- matches PowerModels on five of the six rows, including the two rows where
  current-zero misses (`load_x1.2` reactive stress and `qg_limits_x0.8`);
- is usually faster/fewer iterations than current-zero on the difficult rows;
- misses on `load_x0.8`, where objective relative difference is `0.09428`,
  max `Pg` difference is about `10.5` pu, and max `Pf` difference is about
  `13.49` pu.

Full-sweep Python PM-like equality-only deviation:

- Material mismatch selection from the full `python_pm_like` sweep selected
  `1888_rte/load_x0.8` and `1888_rte/load_x1.2_reactive_stress`.
- The equality-only PM-like diagnostic solved both with Ipopt status `optimal`
  in 4 iterations.
- It reached the PowerModels point to numerical precision:
  max `Pg` deviation `2.39e-13` pu and max `Vm` deviation `1.25e-12` pu.
- External inequality-level evaluation shows zero binding mismatches and zero
  violations beyond the `1e-5` tolerance window.
- Therefore the full-sweep PM-like objective/variable mismatch rows are not
  explained by missing equality physics or by failing to represent the
  PowerModels inequality levels.

Iteration comparison:

- current-zero median iterations over these six rows: `490`;
- PM-like zero median iterations over these six rows: `264`;
- PowerModels iteration counts vary by scenario, but PM-like generally tracks
  the PowerModels-style trajectory better on the stressed local-basin cases.

Solver diagnostics:

- final Ipopt primal infeasibility / constraint violation values are small for
  all rows;
- the rows with large objective/variable differences still terminate cleanly;
- therefore these discrepancies are local optimum / solver trajectory effects,
  not obvious numerical nonconvergence.

## Interpretation

Python's remaining AC OPF discrepancies are not explained by missing bus shunts
or basic equality/inequality infeasibility.

The stronger interpretation is:

- start matters;
- formulation matters;
- PM-like Python is useful and often closer to PowerModels;
- PM-like is not universally dominant, because it can also land in a different
  local basin (`1888_rte_load_x0.8`).

For presentation, show:

1. `python_ac_opf_debug_recommended_run_queue.csv`
2. `python_deviation_minimization_diagnostics.csv`
3. `python_start_validation_summary.csv`
4. `python_pm_like_meaningful_validation_summary.csv`
5. `python_solver_start_diagnostics.csv`
6. `python_pm_like_equality_only_deviation_binding_summary.csv`
