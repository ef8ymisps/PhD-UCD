## Starting Point And Solver Configuration Actually Used For The Sweep

The own-vs-PowerModels validation sweep (the three-way
generation-cost/active-losses tables in
`results/own_vs_powermodels/submission_tables/`) called both languages with
the same concrete settings on every case/scenario:

- `warm_start = :zero` / `"zero"` -- fully flat start: `Pg = Qg = 0`,
  `V_i = 1.0`, `theta_i = 0`, every arc flow `p_a = q_a = 0`, clipped into
  each variable's own bounds. Matches PowerModels.jl's own default start
  exactly, so neither side gets a head start (an earlier `:case` start was
  more failure-prone -- see `docs/notes/solver_tolerances.md`).
- `voltage_bounds = nothing` / `None` -- each case file's own native
  `Vmin`/`Vmax`, no override.


## Constraint Names (Implementation Reference)

Every constraint above is named/indexed in both languages, reachable by
name, not just container position. Variable bounds
(`Pmin/Pmax`, `Qmin/Qmax`, `Vmin/Vmax`) are native variable bounds in both
languages, not separate named constraints, so they have no entry below.

| Formulation constraint | Julia (JuMP, `model.jl`) | Python (Pyomo, `model.py`) |
| --- | --- | --- |
| Reference angle (`theta_ref = 0`) | `reference_angle` (`constraints.jl`) | `slack_angle` (`constraints.py`) |
| P balance, per bus | `power_balance_p[bus_id]` | `p_balance` (indexed over `model.BUS`) |
| Q balance, per bus | `power_balance_q[bus_id]` | `q_balance` (indexed over `model.BUS`) |
| Ohm's law, `p_(l,f,t)` | `ohms_from_p[branch_id]` | `ohms_from_p` (indexed over `model.BRANCH`) |
| Ohm's law, `q_(l,f,t)` | `ohms_from_q[branch_id]` | `ohms_from_q` (indexed over `model.BRANCH`) |
| Ohm's law, `p_(l,t,f)` | `ohms_to_p[branch_id]` | `ohms_to_p` (indexed over `model.BRANCH`) |
| Ohm's law, `q_(l,t,f)` | `ohms_to_q[branch_id]` | `ohms_to_q` (indexed over `model.BRANCH`) |
| Angle-difference bound | `branch_angle_diff[branch_id]` | `angle_limits` (indexed over `model.ANGLE_BRANCH`) |
| Thermal limit, from-end | `branch_thermal_from[branch_id]` | `from_thermal_limits` (indexed over `model.LIMITED_BRANCH`) |
| Thermal limit, to-end | `branch_thermal_to[branch_id]` | `to_thermal_limits` (indexed over `model.LIMITED_BRANCH`) |

Decision-variable container names:

| Math | Julia | Python |
| --- | --- | --- |
| `V_i` | `vm[bus_id]` | `model.vm[bus_id]` |
| `theta_i` | `va[bus_id]` | `model.va[bus_id]` |
| `Pg_g` | `pg[gen_id]` | `model.pg[gen_id]` |
| `Qg_g` | `qg[gen_id]` | `model.qg[gen_id]` |
| `p_a`, `q_a` | `p[arc]`, `q[arc]` | `model.p[arc]`, `model.q[arc]` |

An arc is the tuple `(branch_id, from_bus, to_bus)` in both languages.

### Diagnostics For An Objective Value Mismatch

Own-vs-PowerModels agreement is the norm across the full validation grid
(~1e-6 relative or tighter). Where it doesn't hold, it has consistently
turned out to be `case1888_rte` landing on a different-but-equally-valid
operating point in the same feasible region, not a missing constraint or a
bug. Below is the checklist that actually distinguishes those two
possibilities -- run all of it before concluding either way.
 `p`/`e` the PowerModels and own-model solutions, `g`/`h` the equality/inequality
constraints, `h-bar` the published PGLib/PowerModels limits:

1. Is `p^{p*} = p^{e*}`? Is `u^{p*} = u^{e*}`? -- print and inspect both
   solutions directly, generator-by-generator and bus-by-bus.
2. Evaluate the own model's equality constraints at PowerModels' point: is
   `g^e(x^{p*}) = 0`?
3. Evaluate the own model's inequality constraints at PowerModels' point:
   do they match PowerModels' own reported values?
4. Are the own model's inequality constraints at its own solution close to
   the same constraints evaluated at PowerModels' point (`h^e(x^{e*})` vs
   `h^e(x^{p*})`)?
5. Is PowerModels' point, evaluated through the own model's inequality
   formulas, feasible against the published limits (`h^e(x^{p*}) <= h-bar`)?
6. Does a Newton-Raphson power flow, using PowerModels' point as the fixed
   `Pg`/`Vm` operating point, recover a feasible state within limits?
7. Same, using the own model's own point.
8. Remove the inequality constraints and compare
   equality-constrained behavior.
9. Formally reconstruct the objective gap from the deviation itself, rather
   than just asserting "different operating point." Both `x^{e*}` and
   `x^{p*}` are already-solved, fixed values, so the linking equation
   `x^{e*} + Delta^+ - Delta^- = x^{p*}` (`Delta^+, Delta^- >= 0`) has a
   unique solution. The gap is then
   `sum_i w_i * (Delta_i^+ - Delta_i^-)`.

### Worked Examples: `case1888_rte`, Both Objectives, Both Directions

Applied to five scenarios spanning both objectives and both directions of
the gap:

| Scenario | Objective | PM | Own | Gap |
| --- | --- | --- | --- | --- |
| `qg_limits_x0.6` | active-losses | 9.736534 | 9.733126 | own better, 0.035% |
| `qg_limits_x0.8` | active-losses | 7.394997 | 7.392969 | own better, 0.027% |
| `load_x0.8` | generation-cost | 1,105,160.70 | 1,184,935.32 | PM better, 7.22% |
| `qload_x1.2` (`reactive_power_stress`) | generation-cost | 1,478,335.90 | 1,481,920.65 | PM better, 0.24% |
| `qg_limits_x0.6` | generation-cost | 1,434,313.87 | 1,434,707.09 | PM better, 0.027% (Julia only -- Python matches PM here) |

**Q1/Q2 (dispatch/voltage)**: never equal, but the size of the redispatch
varies a lot. 

**Q3-Q8**: identical outcome in all five scenarios. `g^e(x^{p*})=0` to
numerical precision (power-balance residual ~1e-15, Ohm's-law 5e-12 to 6e-9).
No inequality violations anywhere at PM's point; the margin *gap* between
the two solutions' own points simply scales with how big the redispatch
was (largest for `load_x0.8`, smallest for the two `qg_limits_x0.6` cases).
Newton-Raphson power flow from both points converges cleanly in all five
(mismatch 8e-12 to 1e-9), no thermal/`Qg` violations -- the internal
Q-allocation-feasibility flag reads `false` in most of these checks despite
zero actual per-generator bound violations, an apparently benign quirk of
that allocation heuristic on `case1888_rte`, not real infeasibility.

**Q9 (thermal relaxation, `rate_a=1e6` on both sides)**: 
Removing thermal entirely from
the own model while only relaxing it in PowerModels left the own model
noticeably *more expensive* than PowerModels on both active-losses and generation cost problems, wrongly suggesting
thermal limits were somehow responsible for the original gap.
Re-running with `rate_a` inflated identically on both sides instead
resolves that:

| Scenario (objective) | Relaxed |
| --- | --- |
| `qg_limits_x0.6` (losses) | 0.044% |
| `qg_limits_x0.8` (losses) | 1.65% |
| `load_x0.8` (cost) | 0.0000% (exact, 1,038,404.447579 both sides) |
| `qload_x1.2` (cost) | 0.0000% (exact, 1,337,169.416226 both sides) |
| `qg_limits_x0.6` (cost) | 0.0000% (exact, 1,354,859.603740 both sides) |

**Q10 (deviation reconstruction, linking LP + weighted sum)**:

| Scenario (objective) | Total \|deviation\| | Reconstructed gap | True gap | Residual |
| --- | --- | --- | --- | --- |
| `qg_limits_x0.6` (losses) | 16.68 pu | 0.003409 | 0.003409 | 4.1e-15 |
| `qg_limits_x0.8` (losses) | 85.64 pu | 0.002028 | 0.002028 | 1.0e-14 |
| `load_x0.8` (cost) | 263.32 pu | -79,774.625 | -79,774.625 | 1.5e-11 |
| `qload_x1.2` (cost) | 15.09 pu | -3,584.751 | -3,584.751 | 5.7e-10 |
| `qg_limits_x0.6` (cost) | 8.25 pu | -393.223 | -393.223 | 3.2e-10 |

Cost reconstructions use the exact secant weight per ladder step 9. The
naive (`c1`-only) and exact (secant) versions happen to coincide bit-for-bit
here because `case1888_rte`'s `gencost` has `c2=0` for every generator
(confirmed from the `.m` file) -- a property of this case's data, not
license to skip the secant correction on a case with real quadratic costs.

**Conclusion**: in all five scenarios, both operating points are
independently confirmed feasible (Q3-Q8), the dispatch difference is real
(Q1), and it exactly accounts for the full objective gap with zero
unexplained residual (Q10) -- nonconvex AC OPF landing on two different,
both-valid local optima of the same feasible region, in both objectives and
both directions of the gap, not a formulation defect in either model.
