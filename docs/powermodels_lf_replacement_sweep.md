# PowerModels LF Replacement-Note Sweep

Consolidated from 5 separate near-duplicate run write-ups (`base` [this
file's original content], `api`, `base_large`, `mixed_full`, `sad`) into one
document with a per-run comparison table, since all 5 shared the same
purpose/methods/cautions text and differed only in case-set scope and
result numbers.

## Purpose

This sweep evaluates how selected PGLib replacement-note variants affect
PowerModels load-flow behavior. The primary result is AC load-flow
convergence with `PowerModels.compute_ac_pf`; the DC sweep is a secondary
active-power reference.

## Methods

AC LF uses exactly:

```julia
PowerModels.compute_ac_pf(data_variant; method=:newton, ftol=1e-8, xtol=1e-8, iterations=100)
```

DC LF uses exactly:

```julia
PowerModels.compute_dc_pf(data_variant)
```

Bus-voltage replacement notes are applied only to `vm_start` and `va_start`.
They are initialization values, not final enforced voltage constraints.
Generator PG/QG notes modify `gen[...]["pg"]` and `gen[...]["qg"]` after
MW/Mvar to per-unit conversion. Generator VG notes modify `gen[...]["vg"]`.
Load notes, where present, modify `load[...]["pd"]` and `load[...]["qd"]`
after MW/Mvar to per-unit conversion.

The large/problematic case subset referenced throughout is `case300_ieee`,
`case1354_pegase`, `case1888_rte`, `case6470_rte`, `case9241_pegase`,
`case13659_pegase`, `case78484_epigrids`. `case300_ieee` is included as a
problematic medium-large benchmark; its generator-to-bus ratio in this sweep
is 0.23. This is a diagnostic hypothesis only, not a causal claim.

## Runs

Five separate sweep runs exist, differing in case-set scope (and, for
`mixed_full`/`sad`, run-to-run timing noise only — see note below the
table). Each run's outputs live under
`results/presentable/powermodels_lf_replacement_sweep/<run>/`.

| Run | Cases swept | Mapping audit rows | Large-case AC convergence best variant | Best variant rate | DC: variants converging 12/12 |
| --- | --- | --- | --- | --- | --- |
| `base` (root output dir, no subfolder) | 12 | 265,229 | `G_PGQG_from` (4/7) | 57% | `original`, `G_PG_from` |
| `api` | 12 | 214,917 | `G_PGQG_from`/`+BV`/`+GVG` (3/7 each) | 43% | none — `load_from` variants failed entirely (0/12) |
| `base_large` | 7 | 264,538 | `G_PGQG_from`/`+BV`/`+GVG` (4/7 each) | 57% | `original`, `G_PG_from` |
| `mixed_full` | 11 | 468 | `original` only (1/6) | 17% | `original`, `G_PG_from`; `load_from` variants converged only 1/12 |
| `sad` | 11 | 468 | `original` only (1/6) | 17% | `original`, `G_PG_from`; `load_from` variants converged only 1/12 |

**`mixed_full` vs `sad`**: these two runs used the same case/variant scope
and produced identical convergence counts and mapping-audit row counts —
only DC mean-solve-time differs, by run-to-run timing noise (0.17-0.18s vs
0.19-0.21s). This is a reproducibility check that passed, not a duplicate
run left in by mistake — both are kept in the table as independent
confirmations.

**Why the runs disagree on which variant "wins"**: the large/problematic
convergence rate is computed only over whichever large cases that
particular run's case set happened to include — `base_large` limits to
exactly the 7 large/problematic cases, while `base`/`api` sweep the full
12-case set (small cases included in the row-level CSV but excluded from
the headline rate), and `mixed_full`/`sad` use an 11-case set that only
overlaps 6 of the 7 large/problematic cases. Different denominators, not
different underlying convergence behavior — **use the row-level CSVs for
case-by-case interpretation, not the headline rate alone**, per the
original per-run caution.

DC variants exclude bus-voltage, reactive-power, and generator-voltage
notes because those quantities do not enter the DC formulation.

## Outputs

Per run, replace `<run>` with `base` (root, no subfolder — i.e. drop the
`<run>/` segment), `api`, `base_large`, `mixed_full`, or `sad`:

- AC rows: `results/presentable/powermodels_lf_replacement_sweep/<run>/powermodels_ac_pf_sweep_rows.csv`
- DC rows: `results/presentable/powermodels_lf_replacement_sweep/<run>/powermodels_dc_pf_sweep_rows.csv`
- Figures: `results/presentable/powermodels_lf_replacement_sweep/<run>/figures/`

## Cautions

- This does not reproduce full PGLib replacement-note semantics; only the
  parsed load, bus-voltage, generator PG/QG, and generator VG note families
  are used.
- Iteration counts are not available from `compute_ac_pf` in this workflow
  and are recorded as unavailable.
- No PyPowSyBl/OpenLoadFlow comparison is made here — see
  `docs/model_development_and_validation_status.md` §4.3 for that
  comparison, which was done as a separate, later step.
- The effect of replacement-note variants on convergence is **not
  monotonic** across cases in any run; the row-level CSVs should be used for
  case-by-case interpretation rather than only the headline aggregate rate.
