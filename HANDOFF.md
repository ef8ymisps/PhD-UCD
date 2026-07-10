# Handoff — continuing this project on another machine

Written to resume work (specifically: reworking the OPF validation plots) on
a different computer. For full technical detail on everything built and
found so far, see **`docs/implementation_status.md`** — this file is the
quick-start/orientation layer on top of that.

## What this repo is

Python (Pyomo) and Julia (JuMP) "own model" implementations of DC/AC OPF,
validated against PowerModels.jl, plus an N-1 security-analysis layer
(pypowsybl/OpenLoadFlow for Python, PowerModels.jl's `compute_ac_pf` for
Julia). See `docs/formulations/` for the modelling docs and
`docs/implementation_status.md` for a full status record (what's built, what
was validated, every bug found and fixed, all with file:line references).

## Environment setup on the new machine

**Python**: `conda env create -f python/environment.yml` (env name
`power-systems-learning`). This file was just corrected this session — it
now declares `ipopt` explicitly as a conda-forge dependency. On the original
machine, `ipopt` was actually installed in the **base** conda env and only
worked here by accident (base's `Library/bin` was on PATH); that fragility is
now fixed in the file, but if you're setting up somewhere ipopt still isn't
found, `conda install -n power-systems-learning -c conda-forge ipopt` is the
manual fix. Verify with:
```
conda run -n power-systems-learning python -c "import pyomo.environ as pyo; print(pyo.SolverFactory('ipopt').available())"
```
Should print `True`. Python 3.12.13 on the original machine.

**Julia**: `julia --project=julia/own_models -e "import Pkg; Pkg.instantiate()"`.
Julia 1.12.6 on the original machine. Verify with:
```
julia --project=julia/own_models -e "include(\"julia/own_models/src/OwnPowerModels.jl\"); println(\"loaded OK\")"
```

**Data**: PGLib case files already live under `data/raw/pglib/` (matpower
`.m` for Julia, `.mat` for Python's `matpower_adapter`) — no download needed.

**Git**: `.git/` exists but is empty (no commits, ever) — there is no version
history to pull from or push to. Treat the working tree as the only copy;
sync the folder itself (OneDrive, in the original setup) rather than relying
on git.

## What's done (see `docs/implementation_status.md` for full detail)

- AC/DC OPF implemented and validated in both languages, solver-tolerance
  agreement with PowerModels across all 8 PGLib test cases (5 to 6470 buses),
  full stress-scenario sweep (607 rows,
  `results/own_julia/opf_validation_sweeps/compact_opf_validation_summary.csv`).
- Binding-constraint agreement: 99.94%. The residual mismatches are
  characterized, not bugs (AC-OPF non-uniqueness under reactive stress).
- Slack-modelling policy documented (`docs/formulations/security_constraints.md`).
- N-1 security screening built for both languages and run on case118 (pypowsybl
  + OpenLoadFlow for Python, PowerModels.jl `compute_ac_pf` for Julia) — two
  independent tools corroborate: case118 is genuinely N-1 fragile in stock
  PGLib form.
- Ipopt `max_iter` raised 500→3000 (was causing real convergence failures on
  the largest case).

## What's next: reworking the plots

This is why you're picking this up on another machine. The current plotting
setup:

- **`python/own_models/scripts/18_plot_opf_variable_diffs.py`** — the
  per-variable-diff / binding-set / severity plots built this session. Reads
  Python's own driver (solves case5/14/30 × 5 scenarios fresh against
  pre-exported PowerModels JSON references) plus Julia's
  `compact_opf_validation_summary.csv` and both languages'
  `detail_rows/*.csv` (long-format per-element data). Writes everything to
  `results/presentable/opf_validation_findings/`.
- **`python/own_models/scripts/17_plot_opf_validation_findings.py`** — older,
  untouched this session, objective/solve-time/outcome-only plots, same
  output directory.

Current output inventory (as of last run) — see the conversation history or
just re-list `results/presentable/opf_validation_findings/` — includes, per
language × model (Python/Julia × AC/DC): element-diff distribution box plots,
per-scenario mean-diff box plots, solver-iteration comparison charts, plus
summary/binding/mismatch/convention-check CSVs.

No specific plot-change requirements were captured before switching machines
— start the new session by describing what needs to change (chart types,
layout, styling, new metrics to visualize, etc.) and pointing at
`18_plot_opf_variable_diffs.py` as the file that owns this.

## Housekeeping notes

- No background jobs are currently running.
- The retired-but-not-deleted own-model Python Newton-Raphson N-1 path
  (`post_contingency.py`, scripts `08`/`09`) still exists for case5/14/30 —
  don't confuse its stale output
  (`results/own_python/security/n1_{branch,generator}_case118_*.csv`) with
  the current authoritative results
  (`n1_pypowsybl_case118_contingencies.csv`).
- `matplotlib` was added to `python/requirements.txt` this session (wasn't
  there before, needed for both plotting scripts).
