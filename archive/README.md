# Archive

Superseded/closed analyses moved out of the active codebase on 2026-07-25,
per an explicit decision that they have no further or future application.
Kept for reference, not deleted -- none of this was previously committed to
git, so moving it here (rather than deleting) preserves it without cluttering
the active `julia/`/`python/` script trees or the current documentation.

- `julia_scripts/`, `python_scripts/` -- the scripts themselves.
- `docs/` -- documentation specific to an archived analysis.
- `results/` -- primary/canonical result files for each archived analysis.
  Note: `results/` at the repo root is entirely gitignored and has
  accumulated many redundant nested copies of these same files (inside
  `_organized_for_meeting/`, `_source_top_level_archive/`, and the
  `presentation_upload_bundle_*/` folders) -- only the primary/canonical
  copies were moved here; the redundant nested copies were left alone since
  chasing every duplicate had no value (they're gitignored either way).

## What's here and why

- **Bus-shunt toggle experiment** (`julia_scripts/33_run_bus_shunt_toggle_experiment.jl`,
  `results/bus_shunt_toggle/`): tested zeroing bus shunts across three model
  families; all three went infeasible, so the intended sensitivity table was
  never produced. Dead-end as its own investigation. Superseded further by
  the bus-injection formulation's deletion (one of the three families this
  compared no longer exists).

- **Pre-PM-like formulation comparison** (`julia_scripts/28_run_ac_opf_powermodels_like_experiment.jl`,
  `29_summarize_ac_opf_powermodels_like_experiment.jl`,
  `results/pre_pm_like_formulation_comparison/`): the experiment that led to
  promoting the arc-based/PowerModels-like formulation to the sole AC-OPF
  model (2026-07-24). Fully superseded -- both formulations being compared
  here (the old bus-injection model and the then-experimental PM-like
  variant) have since been consolidated into the current sole model.

- **Own Newton-Raphson AC power-flow validation**
  (`docs/pv_pq_switching.md`, `docs/ac_pf_q_limit_cross_language_validation.md`):
  cross-language PV->PQ switching validation for the own Newton-Raphson AC
  power-flow solver. Per a 2026-07-25 decision, this solver is no longer
  used for any validation or analysis -- PowerModels' own `compute_ac_pf`
  and PyPowSyBl/OpenLoadFlow are the load-flow engines used going forward.
  The solver implementation itself (`ac_power_flow_newton.jl`/
  `ac_power_flow.py`) was NOT deleted, only its validation docs archived.

- **Deviation-minimization diagnostics, all generations**
  (`julia_scripts/34_run_pm_like_constrained_deviation_minimization.jl`,
  `36_run_equality_only_deviation_minimization.jl`,
  `69_verify_q10_deviation_reconstruction.jl`,
  `python_scripts/22_run_ac_opf_deviation_minimization_experiment.py`,
  `32_run_python_ac_opf_deviation_diagnostics.py`,
  `37_run_python_pm_like_equality_only_deviation.py`,
  `39_run_python_pm_like_constrained_deviation_minimization.py`,
  `docs/superseded_status_doc_sections.md` (C2 write-up),
  `results/deviation_minimization/`): the whole
  `x = target + Delta^+ - Delta^-` / minimize-total-deviation technique,
  across every generation this project used it in (original bus-injection
  C2 test, PM-like-era constrained/equality-only variants, and the final
  Q1-Q10 ladder's linking-LP deviation reconstruction). Confirmed
  formulation equivalence every time it was run; no further/future
  application identified. The technique's *recipe* remains documented in
  `docs/formulations/ac_opf.md`'s Diagnostic Ladder (step 9) if a future
  objective mismatch ever needs re-deriving this kind of proof -- re-implement
  fresh from that description rather than reviving these scripts.

- **AC-OPF optimality-gap / OBBT bound-tightening study**
  (`docs/superseded_status_doc_sections.md`, item 12): SOC-relaxation and
  PowerModels' `solve_obbt_opf!`-based lower bounds on AC-OPF's true global
  optimum. Its own script was already gone from disk before this archiving
  pass; the closed, citable finding (gap-by-case table, the `5_pjm`/
  `30_ieee` results) is preserved in the same doc.

## What's explicitly NOT archived (deliberately kept live)

- The Python-specific gen-114 warm-start debugging investigation was
  **deleted outright**, not archived -- see the git commit history around
  2026-07-25 if it's ever needed again. The one durable outcome it produced,
  changing Python's compact-validation warm-start default from `case` to
  `flat` (`python/own_models/src/analysis/ac_opf_sweeps.py`), was kept.
- PowerModels' `compute_ac_pf` and PyPowSyBl/OpenLoadFlow load-flow usage
  (topics 5b/5c) -- these remain the active load-flow engines.
- The N-1 severity-metric chain, the PowerModels iteration-gap
  investigation, DC-OPF/DC-SCOPF, the negative-branch-impedance data-quality
  scan, and the PyPowSyBl convergence-recovery study were all judged still
  relevant and left in place.
