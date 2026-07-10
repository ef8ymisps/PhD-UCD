# PowerModels LF Replacement-Note Sweep

## Purpose

This sweep evaluates how selected PGLib replacement-note variants affect PowerModels load-flow behavior. The primary result is AC load-flow convergence with `PowerModels.compute_ac_pf`; the DC sweep is a secondary active-power reference.

## Methods

AC LF uses exactly:

```julia
PowerModels.compute_ac_pf(data_variant; method=:newton, ftol=1e-8, xtol=1e-8, iterations=100)
```

DC LF uses exactly:

```julia
PowerModels.compute_dc_pf(data_variant)
```

Bus-voltage replacement notes are applied only to `vm_start` and `va_start`. They are initialization values, not final enforced voltage constraints. Generator PG/QG notes modify `gen[...]["pg"]` and `gen[...]["qg"]` after MW/Mvar to per-unit conversion. Generator VG notes modify `gen[...]["vg"]`. Load notes, where present, modify `load[...]["pd"]` and `load[...]["qd"]` after MW/Mvar to per-unit conversion.

## Case Set

Cases swept: 7. The large/problematic subset is `case300_ieee`, `case1354_pegase`, `case1888_rte`, `case6470_rte`, `case9241_pegase`, `case13659_pegase`, `case78484_epigrids`. `case300_ieee` is included as a problematic medium-large benchmark; its generator-to-bus ratio in this sweep is 0.23. This is a diagnostic hypothesis only, not a causal claim.

## Mapping Audit

Mapping audit rows: 264538; unmatched rows: 0.

## Large/Problematic AC Convergence Summary

The headline convergence rates are computed only over the 7 large/problematic cases. Small systems are retained in the row-level CSV, but they are excluded from this summary because flat-start convergence is already expected there.

The highest large/problematic AC convergence count in this run is `G_PGQG_from` with 4 converged runs out of 7.

```text
5×4 DataFrame
 Row │ variant_name               n_runs  n_converged  convergence_rate
     │ String                     Int64   Int64        Float64
─────┼──────────────────────────────────────────────────────────────────
   1 │ original                        7            2              0.29
   2 │ BV_from_warmstart               7            3              0.43
   3 │ G_PGQG_from                     7            4              0.57
   4 │ G_PGQG_from_plus_BV_from        7            4              0.57
   5 │ G_PGQG_from_plus_GVG_from       7            4              0.57
```

The effect is not monotonic; the CSVs should be used for case-by-case interpretation.

## DC Sweep Summary

```text
4×6 DataFrame
 Row │ variant_name              n_runs  n_converged  n_available  mean_solve_time_sec  max_active_power_residual
     │ String                    Int64   Int64        Int64        Float64?             Missing
─────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────
   1 │ original                       7            7            7                 0.37                    missing
   2 │ G_PG_from                      7            7            7                 0.42                    missing
   3 │ load_from                      7            0            0           missing                       missing
   4 │ G_PG_from_plus_load_from       7            0            0           missing                       missing
```

DC variants exclude bus-voltage, reactive-power, and generator-voltage notes because those quantities do not enter the DC formulation.

## Outputs

- AC rows: `results\presentable\powermodels_lf_replacement_sweep\base_large\powermodels_ac_pf_sweep_rows.csv`
- DC rows: `results\presentable\powermodels_lf_replacement_sweep\base_large\powermodels_dc_pf_sweep_rows.csv`
- Figures: `results\presentable\powermodels_lf_replacement_sweep\base_large\figures`

## Cautions

- This does not reproduce full PGLib replacement-note semantics; only the parsed load, bus-voltage, generator PG/QG, and generator VG note families are used.
- Iteration counts are not available from `compute_ac_pf` in this workflow and are recorded as unavailable.
- No PyPowSyBl/OpenLoadFlow comparison is made here. That is the next separate step.
