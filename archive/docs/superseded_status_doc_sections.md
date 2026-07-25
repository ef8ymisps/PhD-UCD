# Archived Sections From `docs/model_development_and_validation_status.md`

Archived 2026-07-25 per an explicit decision to archive the deviation-
minimization diagnostic (all generations -- no further/future application)
and the AC-OPF optimality-gap/OBBT bound-tightening study (its own script
was already gone from disk; this preserves the closed finding). Verbatim
text of the two removed numbered items, kept for reference -- these are
closed, historical findings, not live/ongoing work.

## C2 (deviation-minimization formulation-equivalence test)

10. **C2 closed (2026-07-12)**: deviation-minimization formulation-
    equivalence test
    (`julia/own_models/scripts/13_run_deviation_minimization_experiment.jl`).
    Method: solve PowerModels' AC-OPF for a case, then solve own model's
    AC-OPF with the cost objective replaced by a signed slack-variable
    deviation objective (`x = target + dx_plus - dx_minus`, minimize
    `sum(dx_plus + dx_minus)`, all of own model's real constraints still
    apply) -- if own model's feasible region can be driven to ~0 deviation
    from PowerModels' point, that's direct evidence the two formulations
    describe the same feasible region at that point, not just that their
    optimal *objectives* happen to coincide. New objective:
    `add_ac_target_deviation_objective!` in
    `julia/own_models/src/opf/ac/objective.jl`.

    Four variants per case (all keep Pg/Va pinned; vary whether Qg/Vm are
    also pinned or left free): `full`, `no_qg`, `no_vm`, `no_qg_no_vm`. Run
    on all 8 default cases (base, well-conditioned) plus the two scenarios
    with the only genuinely large own-vs-PM `max_pg_diff` in the whole
    607-row `compact_opf_validation_summary.csv` (4.33 and 1.80 pu -- six
    orders of magnitude above every other row, which sit at ~1e-6 noise):
    `1888_rte_load_x0.8` (load_scaling, both P&Q x0.8) and
    `1888_rte_load_x1.2` (reactive_power_stress, Q-only x1.2).

    **`full` variant: ~0 deviation (<=1e-8 pu) on every single case and
    scenario, no exceptions** -- including both stressed scenarios. Direct,
    strong confirmation of formulation equivalence, holding even under the
    worst documented reactive stress. `6470_rte`'s *base* case is the one
    mild outlier even in `full`: max Qg/Vm deviation ~0.006/0.001 pu
    (small vs. the stressed scenarios' ~1-2 pu, but non-zero where every
    other base case is ~1e-8-1e-9) -- consistent with `6470_rte`'s
    outlier status throughout this session (warm-start sensitivity, etc.).

    **Freeing Qg (`no_qg`/`no_qg_no_vm`) behaves completely differently on
    the two stressed scenarios**: on `1888_rte_load_x0.8`, freeing Qg lets
    Ipopt land on a genuinely different local optimum -- not just Qg
    drifting (0.13-2.02 pu) but Pg/Va drifting too (0.76-1.50 pu Pg) --
    expected for a nonconvex problem, freeing one variable group changes
    the whole solution landscape. On `1888_rte_load_x1.2`, deviation stays
    ~0 even with everything free. Since `load_x1.2` is the exact scenario
    that showed a 1.8 pu Pg gap under the *regular* cost objective in the
    original compact validation sweep, this is evidence that specific
    divergence is a **starting-point/local-optimum artifact of the cost
    objective**, not a sign the feasible regions differ -- while
    `load_x0.8` shows real, additional non-uniqueness beyond that. The two
    "known non-unique" scenarios are not uniformly non-unique in the same
    way; worth keeping in mind before generalizing from either one alone.

    (This whole diagnostic technique -- across all its later generations,
    including the PM-like constrained/equality-only variants and the final
    Q1-Q10 ladder's linking-LP deviation reconstruction -- was archived
    2026-07-25; see `archive/julia_scripts/` and `archive/python_scripts/`.
    The final ladder's conclusions remain live in
    `docs/formulations/ac_opf.md`/`comments.md`.)

## Item 12 (AC-OPF optimality gap / OBBT bound-tightening)

12. **New, closed (2026-07-12)**: AC-OPF optimality gap, plus a widened
    Julia warm-start ablation
    (`julia/own_models/scripts/15_run_ac_opf_optimality_gap_experiment.jl`).
    AC-OPF is nonconvex; Ipopt is a *local* solver, so a `LOCALLY_SOLVED`
    status alone says nothing about distance to the true global optimum.
    Scoped to AC only, per explicit direction -- DC-OPF is linear/convex, so
    its own optimum is already global by construction and needs no such
    check.

    Method: solve PowerModels.jl's SOC (second-order-cone) relaxation
    (`SOCWRPowerModel`) for each case -- convex, smooth, and solved directly
    by Ipopt (zero new solver dependency; no SDP/conic solver like
    SCS/COSMO/Clarabel/Mosek is installed in this project, and none was
    needed). Its optimal objective is a certified *lower bound* on AC-OPF's
    true global optimum, so `gap_pct = 100 * (f - lower_bound) /
    lower_bound` is a real, bounded optimality gap for any converged AC-OPF
    solution `f`. One SOC relaxation solve per case, shared across that
    case's four upper-bound rows (own model's `case`/`flat`/`zero`
    warm-start modes + native PowerModels). Run on all 8 default PGLib
    cases plus both `1888_rte` stressed scenarios from C2
    (`load_x0.8`/`load_x1.2`) -- folding in the plan's outstanding "extend
    warm-start ablation across multiple validation scenarios" item.

    **Gap tracks network topology/loading, not case size.** Range: 0.11%
    (`14_ieee`) to 23.2% (`30_ieee`). The two largest gaps are on two of the
    *smallest* cases -- `5_pjm` (17.0%) and `30_ieee` (23.2%) -- both far
    above every case from `118_ieee` up to `6470_rte` (all <3%). Not a
    sign those two AC solutions are worse: all four solvers agree on the AC
    objective to 5+ significant figures for both cases, strong independent
    evidence the AC solution already is the global optimum -- the SOC
    relaxation is just documented in the literature as loose for these two
    specific PGLib cases' R/X ratios and loading, independent of the
    own-model implementation being evaluated.

    **Warm start changes the gap only when it changes which local optimum
    is found.** Every case/scenario except `1888_rte` (Qd x1.2) reports the
    same gap to 3 decimal places across all four solvers, since the SOC
    lower bound is a per-case constant. The one exception is the known
    non-uniqueness case: `case`/`flat` starts land on a 7.316% gap, but
    `zero`-start and PowerModels' native solve land on the other two,
    slightly worse local optima already known from the warm-start ablation
    (7.542%/7.595%) -- turning the earlier "four distinct local optima, all
    within ~0.5%" finding into a quantified bound: the worst of the four
    converged answers is provably at most ~0.3 percentage points further
    from global optimality than the best.

    **Reactive stress measurably loosens the gap on its own, independent of
    the local-optimum effect above.** Both `1888_rte` stressed scenarios
    (6.5% at Qd x0.8, 7.3-7.6% at Qd x1.2) sit well above `1888_rte`'s own
    base case (2.1%) and above every other base case except
    `5_pjm`/`30_ieee`. Report:
    `results/presentable/ac_opf_warm_start_experiment/warm_start_ablation_report.html`.

    **Correction, 2026-07-13: the one-shot SOC bound above was real but
    substantially looser than PowerModels.jl's actual built-in mechanism
    for this -- most of the "large gap on small cases" framing was an
    artifact of that.** `solve_obbt_opf!` (Optimality-Based Bound
    Tightening: QC relaxation, iteratively tightened per-variable
    voltage-magnitude and angle-difference bounds) is PowerModels.jl's
    genuine, purpose-built tool for this -- never used in the original pass.
    Confirmed empirically on `case14_ieee`: one-shot SOC gap 0.109% vs
    OBBT's 0.0029%, ~38x tighter. New script:
    `julia/own_models/scripts/19_run_ac_opf_optimality_gap_obbt.jl`.
    Results: `results/own_julia/ac_opf_optimality_gap/optimality_gap_obbt_results.csv`.

    Run to full convergence on the 5 smallest/medium cases:

    | Case | One-shot SOC | OBBT (corrected) |
    |---|---|---|
    | `5_pjm` | 17.01% | **10.18%** |
    | `14_ieee` | 0.109% | **0.0029%** |
    | `30_ieee` | 23.21% | **0.074%** |
    | `118_ieee` | 0.911% | **0.251%** |
    | `300_ieee` | 2.69% | **0.194%** |

    **`30_ieee`'s huge original gap was almost entirely relaxation
    looseness** -- 23.2% -> 0.07%, confirming the AC solution there was
    already very close to global-optimal; `14_ieee`/`118_ieee`/`300_ieee`
    tighten similarly dramatically. **`5_pjm` is the one genuine exception**:
    OBBT converged on its own after 6 iterations (not iteration-capped) and
    the gap only closed to 10.2% -- a real, substantial gap survives the
    best available bound, consistent with `5_pjm`'s known small/lossy/
    R/X-heavy structure genuinely admitting a materially non-global local
    optimum, not just a loose relaxation.

    **`1354_pegase`/`1888_rte`/`6470_rte` (and the two `1888_rte` stressed
    scenarios) were NOT re-run -- OBBT is computationally impractical
    there, confirmed empirically, not assumed.** Cost scales far worse
    than linearly with case size (~2s/iteration at 14 buses, ~28min/
    iteration at 300 buses -- confirmed directly, not extrapolated only).
    Extrapolating that trend, a single OBBT iteration on `1354_pegase` was
    estimated at 9+ hours and not attempted; `6470_rte` would be on the
    order of days. Also confirmed PowerModels.jl's `time_limit` kwarg does
    **not** protect against this -- the check only happens *between*
    complete iterations (`obbt.jl:348`), so a single iteration exceeding
    `time_limit` still runs to completion regardless. `max_iter` is the
    only genuinely safe bound (enforced the same way, but exactly -- 1
    iteration always means exactly 1). These cases keep the original,
    looser one-shot SOC gap (1.6-2.1% base, 6.5-7.6% stressed); given the
    pattern on the 5 corrected cases, these are plausibly overestimates
    too, but this is explicitly flagged as unconfirmed, not silently
    assumed -- open item for a future pass if ever worth the cost.

    Artifact updated in place (not a new file) with a Method column
    distinguishing OBBT from one-shot-SOC rows:
    `results/presentable/ac_opf_warm_start_experiment/warm_start_ablation_report.html`.
