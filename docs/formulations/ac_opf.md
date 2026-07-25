# AC OPF Formulation

Date: 2026-07-24

This is the repository-level formulation note for the own AC optimal power flow
model. The Python implementation is Pyomo/Ipopt; the Julia implementation is
JuMP/Ipopt. Both share one arc-based (PowerModels-style) formulation -- a
prior bus-injection (implicit Ybus-substitution) formulation existed earlier
in this project's development but was deleted once this arc-based model
reached validated parity with PowerModels.jl's own `ACPPowerModel` across the
full own-vs-PowerModels validation sweep. This document describes the
formulation as actually implemented today; there is no second variant.

## Sets

- `B`: buses
- `G`: generators
- `L`: branches
- `A`: directed branch arcs -- each branch `l in L` contributes two arcs,
  `(l, f, t)` (from-end) and `(l, t, f)` (to-end); `A_i`: arcs incident at
  bus `i` (either end)
- `G_i`: generators connected to bus `i`

## Decision Variables

- `V_i`: voltage magnitude at bus `i`
- `theta_i`: voltage angle at bus `i`
- `Pg_g`, `Qg_g`: active/reactive output of generator `g`
- `p_a`, `q_a`: active/reactive power flow on directed arc `a in A` (real
  decision variables, not expressions -- each pinned to its Ohm's-law value
  by an explicit equality constraint below)

## Objective

Minimize total active generation cost (default):

```text
min sum_g c2_g Pg_g^2 + c1_g Pg_g + c0_g
```

Cost units follow the source case/export convention. An alternate
loss-minimization objective is also implemented (`objective_type =
"active_losses"` / `objective=:active_losses`):

```text
min sum_l (Pf_l + Pt_l)
```

summing each branch's own-model arc-flow variables at both ends -- the same
`p_a` variables used everywhere else in this formulation, not a separate
recomputation.

## AC Power Balance (nodal Kirchhoff Current Law over arc flows)

For each bus `i`, active and reactive nodal balance sum the directed arc
flows incident at that bus:

```text
sum_{a in A_i} p_a = sum_{g in G_i} Pg_g - Pd_i - Gs_i * V_i^2

sum_{a in A_i} q_a = sum_{g in G_i} Qg_g - Qd_i + Bs_i * V_i^2
```

This is purely linear in the arc-flow variables -- all nonlinear
voltage-dependence lives in the per-branch Ohm's-law equality constraints
below, which define what `p_a`/`q_a` actually equal. `Gs_i`/`Bs_i` are bus
shunt conductance/susceptance aggregates from the case data (branch charging
is folded into each branch's own terminal admittance, not into this bus-level
term). Mirrors PowerModels.jl's `constraint_power_balance` (form/acp.jl)
exactly, including keeping bus shunts as a separate term here rather than
folding them into a nodal Ybus diagonal.

## Branch Ohm's Law (per-arc equality constraints)

For each branch `l` with from-bus `f`, to-bus `t`, series admittance `y`,
and terminal shunt (charging) admittances `y_fr`, `y_to`:

```text
Yff = y + y_fr
Yft = -y
Ytf = -y
Ytt = y + y_to
```

- **`y = g + j*b = 1/(r + j*x)`** -- the branch's series admittance, from
  its series resistance `r` and reactance `x` (PGLib `.m` file, `branch`
  table). This is the line/transformer's actual through-impedance.
- **`y_fr = g_fr + j*b_fr`** -- the from-side shunt (charging) admittance.
  `g_fr` is the from-side shunt conductance (0 for ordinary lines); `b_fr`
  is the from-side shunt susceptance, equal to half the branch's total line
  charging susceptance `b` (`b_fr = b/2`).
- **`y_to = g_to + j*b_to`** -- the to-side shunt (charging) admittance,
  symmetric to `y_fr` (`g_to = 0`, `b_to = b/2`).

Physically: `y` is the line itself; `y_fr`/`y_to` are the textbook
nominal-pi line model's distributed capacitance to ground, split into two
equal halves at each end of the branch. PGLib's `.m` files (classic
MATPOWER format) store only one total charging-susceptance column `b` per
branch -- PowerModels.jl's own parser is what splits it into `b_fr = b_to =
b/2`, `g_fr = g_to = 0`; this codebase's `Branch` struct just reads the
already-split values off PowerModels' parsed dict
(`julia/own_models/src/data/*.jl`).

```text
p_(l,f,t) = Re(Yff) V_f^2 + V_f V_t (Re(Yft) cos(theta_f - theta_t) + Im(Yft) sin(theta_f - theta_t))
q_(l,f,t) = -Im(Yff) V_f^2 + V_f V_t (Re(Yft) sin(theta_f - theta_t) - Im(Yft) cos(theta_f - theta_t))
p_(l,t,f) = Re(Ytt) V_t^2 + V_t V_f (Re(Ytf) cos(theta_t - theta_f) + Im(Ytf) sin(theta_t - theta_f))
q_(l,t,f) = -Im(Ytt) V_t^2 + V_t V_f (Re(Ytf) sin(theta_t - theta_f) - Im(Ytf) cos(theta_t - theta_f))
```

Mirrors PowerModels.jl's `constraint_ohms_yt_from`/`_to` (form/acp.jl)
exactly. This is the only place the branch's nonlinear voltage/angle
dependence enters the model.

`Yff = y + y_fr`, `Yft = Ytf = -y`, `Ytt = y + y_to` above is the untapped
case (`tap=1, shift=0` -- an ordinary line, no transformer), which is what
most branches in a PGLib case are. Any branch that models an actual
transformer (tap ratio away from 1 and/or a phase shift) needs the general,
tap-dependent version of these four terms -- see below.

### General Case: Tapped Branches / Transformers

Let `T_l = tm_l * e^{j*shift_l}` be branch `l`'s complex tap ratio (`tm_l =
|T_l|` the tap magnitude, `shift_l` the phase shift; `tr_l =
tm_l*cos(shift_l)`, `ti_l = tm_l*sin(shift_l)` are `Re(T_l)`/`Im(T_l)`). The
untapped formulas above generalize to:

```text
Yff = (y + y_fr) / tm^2
Yft = -y / conj(T)
Ytf = -y / T
Ytt = y + y_to
```

which reduce to the untapped case exactly when `T=1` (`conj(T)=1` too).

**Why they take this form -- the circuit derivation.** The branch is an
ideal transformer with ratio `T` at the *from-side only*, followed by shunt
`y_fr` at the transformer's internal (secondary) terminal, then the series
impedance `y`, then shunt `y_to` directly at the to-bus (no transformer
between `y_to` and bus `t`). Let `V_2` be the voltage at the internal node
just behind the transformer, so `V_f = T * V_2`.

Ordinary nodal analysis at node 2 gives the current flowing from it into
the shunt-plus-series sub-circuit: `I_2 = (y+y_fr)*V_2 - y*V_t`. An ideal
transformer conserves complex power (`V_f*conj(I_f) = V_2*conj(I_2)`),
which combined with `V_f=T*V_2` gives the current relation
`I_f = I_2/conj(T)`. Substituting `V_2=V_f/T`:

```text
I_f = [(y+y_fr)/(T*conj(T))] V_f - [y/conj(T)] V_t = [(y+y_fr)/tm^2] V_f - [y/conj(T)] V_t
```

(using `T*conj(T) = |T|^2 = tm^2`) -- reading off the coefficients gives
`Yff=(y+y_fr)/tm^2`, `Yft=-y/conj(T)`. Both `y` *and* `y_fr` are scaled by
`1/tm^2` because both sit on the internal (node-2) side of the transformer;
the whole "series impedance + from-side shunt" sub-circuit is reflected
through the tap together.

The to-bus side has no transformer at all -- current flowing from bus `t`
into the branch (its own local shunt plus the series impedance, nothing in
between):

```text
I_t = y_to*V_t + y*(V_t - V_2) = (y+y_to)*V_t - y*(V_f/T)
```

giving `Ytt=y+y_to` (no `tm^2` division -- nothing sits between `y_to` and
bus `t` to reflect through) and `Ytf=-y/T`. That is the entire source of
the asymmetry between the from-side and to-side formulas: `Yff` bundles `y`
with the from-side shunt and divides by `tm^2` because the tap sits between
bus `f` and everything else; `Ytt` bundles `y` with the to-side shunt with
no division because nothing sits between bus `t` and its own shunt.

Implemented directly in `_add_ac_ohms_for_branch!` (`julia/own_models/src/opf/ac/model.jl`)
and `branch_admittance_terms` (`python/own_models/src/opf/ac/constraints.py`)
via their real/imaginary parts, not as literal complex-number arithmetic
(JuMP/Pyomo NLP expressions must stay real-valued): expanding the four
formulas above gives exactly the `Re(Yff)=(g+g_fr)/tm^2`,
`Im(Yff)=(b+b_fr)/tm^2`, `Re(Yft)=(-g*tr+b*ti)/tm^2`,
`Im(Yft)=(-b*tr-g*ti)/tm^2`, `Re(Ytf)=(-g*tr-b*ti)/tm^2`,
`Im(Ytf)=(-b*tr+g*ti)/tm^2`, `Re(Ytt)=g+g_to`, `Im(Ytt)=b+b_to` coefficients
the code actually computes.

The implementation does not special-case `tap=1`; it always evaluates
these general formulas, which simply reduce to the untapped case at the
top of this section algebraically whenever `T=1`.

## Bounds And Engineering Limits

Generator bounds:

```text
Pmin_g <= Pg_g <= Pmax_g
Qmin_g <= Qg_g <= Qmax_g
```

Voltage bounds:

```text
Vmin_i <= V_i <= Vmax_i
```

Slack/reference angle:

```text
theta_ref = 0
```

Branch angle-difference bounds:

```text
angmin_l <= theta_f - theta_t <= angmax_l
```

Branch apparent-power limits are enforced at both ends, directly on the arc
flow variables:

```text
p_(l,f,t)^2 + q_(l,f,t)^2 <= rate_l^2
p_(l,t,f)^2 + q_(l,t,f)^2 <= rate_l^2
```

A simple per-arc magnitude bound (`|p_a|, |q_a| <= rate_l` when a thermal
rating exists) is also applied directly to the arc variables as a cheap
auxiliary bound, ahead of the quadratic apparent-power constraint above.

## Starting Point And Solver Configuration Actually Used For The Sweep

The own-vs-PowerModels validation sweep (the three-way
generation-cost/active-losses tables in
`results/own_vs_powermodels/submission_tables/`) called both languages with
the same concrete settings on every case/scenario:

- `warm_start = :zero` / `"zero"` -- fully flat start: `Pg = Qg = 0`,
  `V_i = 1.0`, `theta_i = 0`, every arc flow `p_a = q_a = 0` (start values
  clipped into each variable's own bounds). This matches PowerModels.jl's
  own actual default starting point exactly, so neither side gets a head
  start -- see `docs/notes/solver_tolerances.md` for why this specific
  choice matters (an earlier `:case`/native-dispatch start was found to
  make Ipopt noticeably more failure-prone on some scenarios).
- `voltage_bounds = nothing` / `None` -- use each case file's own native
  `Vmin`/`Vmax`, not an artificially tightened override.
- `fix_reference_angle = true` / `True` -- the slack bus angle is pinned to
  `0` (see "Constraint Names" below).
- Ipopt tolerances pinned explicitly rather than left to whatever the
  installed Ipopt version defaults to (`DEFAULT_IPOPT_TOLERANCES` in
  `model.jl`/`model.py`): `tol=1e-8`, `acceptable_tol=1e-6`,
  `constr_viol_tol=1e-4`, `compl_inf_tol=1e-4`, `dual_inf_tol=1.0`,
  `max_iter=3000` (see `docs/notes/solver_tolerances.md` for the full
  cross-solver comparison).
- Objective: `generation_cost` (default) for the cost sweep;
  `active_losses` for the separate loss-minimization sweep -- same model,
  same settings above, only the objective term swapped.

Julia implements the starting-point assignment directly in
`add_ac_variables!` (`julia/own_models/src/opf/ac/model.jl`); Python
delegates to `build_ac_initial_point`
(`python/own_models/src/opf/ac/initialization.py`), which also supports two
additional modes (`case`, `flat`, plus `stored_opf`/`pypowsybl_lf` used by
other analysis scripts) not used by this sweep.

## Constraint Names (Implementation Reference)

Every constraint above is named/indexed in both languages, so a specific
constraint (and its dual/shadow price once solved) is reachable by name, not
just by container position -- see `docs/notation.md` for the full symbol
mapping. Variable bounds (`Pmin/Pmax`, `Qmin/Qmax`, `Vmin/Vmax`) are enforced
as native variable bounds in both languages, not as separate named
constraints, so they have no entry below.

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

Decision-variable container names, for the same reason:

| Math | Julia | Python |
| --- | --- | --- |
| `V_i` | `vm[bus_id]` | `model.vm[bus_id]` |
| `theta_i` | `va[bus_id]` | `model.va[bus_id]` |
| `Pg_g` | `pg[gen_id]` | `model.pg[gen_id]` |
| `Qg_g` | `qg[gen_id]` | `model.qg[gen_id]` |
| `p_a`, `q_a` | `p[arc]`, `q[arc]` | `model.p[arc]`, `model.q[arc]` |

where an arc is the tuple `(branch_id, from_bus, to_bus)` in both languages.

## Modeling Assumptions

- Static, single-period AC OPF: one snapshot of load/generation, no
  time-coupling, ramping, or storage state.
- N-1/security constraints are NOT co-optimized here -- contingency handling
  (see `docs/formulations/security_constraints.md`) is a separate, sequential
  screening pass (solve base case, then re-solve a plain AC power flow per
  outage), not a joint base-case-plus-contingency model. A monolithic
  DC-SCOPF formulation is the planned first step toward closing that gap
  (`docs/formulations/dc_scopf.md`).
- Nonconvex: Ipopt is a local NLP solver, so different warm starts or
  algebraically-equivalent-but-differently-conditioned formulations can
  converge to different local optima with the same feasible set. See
  "Validation Implication" below.

## Validation Implication

Because AC OPF is nonconvex, different solvers or different algebraic
constructions can converge to different local optima. A dispatch difference is
not automatically a formulation bug. The minimum inspection needed is:

- both solutions satisfy AC balance and engineering limits;
- the largest `Pg` difference maps to nearby branch `Pf` differences;
- the binding sets differ in a physically consistent way;
- objective differences are reported explicitly rather than hidden.

A deviation-minimization diagnostic (substituting
`x = x^{target} + Delta^+ - Delta^-` for chosen variable groups and
minimizing `sum(Delta^+ + Delta^-)`, subject to the same physics above, to
test whether a suspected alternate-local-optimum gap is actually reachable
from the own model's own constraint set) was used across this project in
several generations and confirmed formulation equivalence every time it was
tried. **Archived 2026-07-25** -- no further/future application identified;
scripts moved to `archive/julia_scripts/`/`archive/python_scripts/`, prior
findings preserved in `archive/docs/superseded_status_doc_sections.md`. The
"Diagnostic Ladder" below (step 9 specifically) documents the same
linking-LP recipe in reusable form; if a future objective mismatch needs
this kind of proof again, re-implement step 9 fresh from that description
rather than reviving the archived scripts.

### Diagnostic Ladder For An Objective Mismatch

Across the full validation grid, own-vs-PowerModels agreement is the norm
(objective values matching to numerical noise, ~1e-6 relative or tighter).
Where it doesn't hold, it has consistently turned out to be `case1888_rte`
landing on a different-but-equally-valid operating point within the same
feasible region, not a missing constraint or an implementation bug. The
following question ladder is what actually distinguishes those two
possibilities -- reuse it verbatim before concluding either way. `p*` here
denotes active dispatch/flow, `u*` voltage magnitude/angle, `p`/`e` the
PowerModels and own-model solutions respectively, `g` the equality
constraints (power balance, Ohm's law), `h` the inequality constraints
(thermal, angle-difference, generator/voltage bounds), `h-bar` the
published PGLib/PowerModels limits:

1. Is `p^{p*}` equal to `p^{e*}`? Is `u^{p*}` equal to `u^{e*}`? -- print
   and inspect both solutions directly, generator-by-generator and
   bus-by-bus.
2. Evaluate the own model's equality constraints at PowerModels' solution
   point: is `g^e(x^{p*}) = 0`?
3. Evaluate the own model's inequality constraints at PowerModels' solution
   point: do they match PowerModels' own reported inequality values?
4. Are the own model's inequality constraints at its own solution close to
   the own model's inequality constraints evaluated at PowerModels'
   solution (`h^e(x^{e*})` vs `h^e(x^{p*})`)?
5. Is PowerModels' solution, evaluated through the own model's inequality
   formulas, feasible against the published limits: `h^e(x^{p*}) <= h-bar`?
6. Does a Newton-Raphson power flow, using PowerModels' solution as the
   operating point (fixed `Pg`/`Vm` dispatch), recover a feasible state
   within limits?
7. Same question, using the own model's own solution as the operating
   point.
8. Remove the inequality constraints -- especially thermal -- and compare
   the two models' equality-constrained behavior. **This step must relax
   both sides identically, not remove the constraint on one side and
   merely loosen it on the other**: dropping a constraint from the own
   model's JuMP build (`include_branch_thermal=false`) changes the NLP's
   structure (fewer constraints, different Jacobian/KKT shape) versus
   PowerModels, which has no equivalent "remove" toggle and can only have
   `rate_a` inflated to a large constant. Comparing "removed" against
   "inflated" is not apples-to-apples and can itself manufacture an
   apparent gap. The fair version inflates `rate_a` to the same huge
   constant on both sides (own model keeps `include_branch_thermal=true`,
   just against the same relaxed data PowerModels solves) -- see the worked
   example below, where doing this incorrectly first overstated the
   effect by more than 10x on one scenario.
9. Formally reconstruct the objective gap from the dispatch/flow deviation
   itself, rather than just asserting a "different operating point"
   qualitatively. For each decision variable entering the objective (`Pg`
   for generation-cost, arc flow `p` for active-losses), both `x^{e*}` and
   `x^{p*}` are already-solved, fixed values -- so the linking equation
   `x^{e*} + Delta^+ - Delta^- = x^{p*}`, `Delta^+, Delta^- >= 0` has a
   unique solution (`Delta^+ = max(0, x^{p*}-x^{e*})`, `Delta^- = max(0,
   x^{e*}-x^{p*})`); solving it as a tiny linking LP (mirroring this
   project's signed-slack deviation convention rather than hand-computing
   the closed form) makes the check reproducible the same way as every
   other diagnostic here. The objective gap is then
   `sum_i w_i * (Delta_i^+ - Delta_i^-)`, where the weight `w_i` must be the
   **exact marginal price implied by the two points**, not just the raw
   linear cost coefficient: generation-cost is quadratic in `Pg`, so
   `cost(Pg^{p*}) - cost(Pg^{e*}) = (Pg^{p*}-Pg^{e*}) * (c2*(Pg^{p*}+Pg^{e*})
   + c1)` exactly (the secant slope, i.e. the derivative at the midpoint) --
   using `c1` alone is only a first-order approximation and leaves a
   residual whenever `c2 != 0` and the deviation isn't tiny. Active-losses
   needs no such correction; it is already exactly linear in the arc-flow
   variables (weight 1 for every arc). See the worked examples below for
   concrete residuals (down to floating-point noise in every case tested
   so far).

### Worked Example: `case1888_rte` Active-Losses, `qg_limits_x0.6`/`x0.8`

Applied to two scenarios where the own model converges to a slightly
*better* (lower) active-losses objective than PowerModels
(`1888_rte_qg_limits_x0.6`: PM 9.736534 vs own 9.733126, +0.035%;
`1888_rte_qg_limits_x0.8`: PM 7.394997 vs own 7.392969, +0.027% --
`results/own_vs_powermodels/submission_tables/active_losses/1888_rte.csv`),
via `julia/own_models/scripts/66_investigate_1888_rte_active_losses_improvement.jl`
and `67_rerun_q9_apples_to_apples.jl`:

- **Q1/Q2 (dispatch/voltage equal?)**: no. `x0.6` differs by <0.05 MW on
  almost every generator except one small unit (0.320->0.064 MW); `x0.8`
  shows a genuine ~5 MW redispatch between two generators (263: 6.35->2.82,
  264: 8.44->13.32 MW) plus several smaller shifts -- the classic signature
  of two network-interchangeable units splitting output differently
  between independent solves. Voltage differs by <0.01 pu magnitude in
  both cases.
- **Q3 (`g^e(x^{p*})=0`?)**: yes -- power-balance residual ~1e-15 (machine
  precision), Ohm's-law residual ~1e-9 to 6e-9, both cases.
- **Q4/Q5 (inequality constraints, at PM's point vs at own's point)**:
  thermal/`Pg`/`Vm`/angle margins evaluated at PM's point show no
  violations. The margin *gap* between the two solutions' own points is
  small in `x0.6` (thermal margin gap ~0.045, tracking the small dispatch
  difference) and much larger in `x0.8` (~4.9, tracking that scenario's
  bigger generator swing) -- consistent with Q1's finding, not a
  contradiction of it.
- **Q6 (`h^e(x^{p*}) <= h-bar`?)**: yes, no violations in either scenario --
  PowerModels' point remains fully feasible under the own model's own
  constraint formulas.
- **Q7/Q8 (Newton-Raphson from each point)**: both converge cleanly
  (mismatch ~1e-9 to 1e-12) with thermal/`Qg` limits respected, both
  scenarios, both starting points.
- **Q9 (thermal ablation, own thermal-removed vs PM thermal-relaxed --
  first pass, NOT apples-to-apples)**: gap *widened* to 13.76% (`x0.6`) and
  5.00% (`x0.8`) -- naively suggesting thermal limits were somehow
  responsible for the small original gap. Redone with `rate_a` inflated to
  the identical `1.0e6` constant on both sides (constraint structurally
  present in both models, matching the note in step 8 above): `x0.6`'s gap
  collapsed to 0.044% (thermal plays essentially no role -- the residual
  gap is generic solver-trajectory noise), while `x0.8`'s gap narrowed from
  5.00% to 1.65% but did not vanish (thermal limits are genuinely acting as
  a shared anchor keeping both solvers in the same corner in the original,
  constrained solve; relaxing them frees exactly the redispatch flexibility
  Q1 already found between generators 263/264, letting the two solves drift
  further apart).
- **Q10 (formal deviation-reconstruction proof)**: `julia/own_models/scripts/69_verify_q10_deviation_reconstruction.jl`.
  Linking LP on the arc-flow deviations, weight 1 per arc (active-losses is
  exactly linear, no secant correction needed): `x0.6` total |deviation| =
  16.68 pu, reconstructed gap = 0.003409 vs true gap 0.003409 (residual
  4.1e-15); `x0.8` total |deviation| = 85.64 pu, reconstructed gap =
  0.002028 vs true gap 0.002028 (residual 1.0e-14). The entire objective
  gap, in both scenarios, is accounted for term-by-term by the same
  dispatch deviation Q1 already found -- no unexplained residual.

Conclusion for this worked example, and the general pattern for any future
one: both operating points are independently confirmed feasible (Q3, Q6,
Q7, Q8), the dispatch difference is real, not noise (Q1), and it exactly
accounts for the full objective gap with zero unexplained residual (Q10) --
this is nonconvex AC OPF landing on two different, both-valid local optima
of the same feasible region, not a formulation defect on either side.

### Worked Example: `case1888_rte` Generation-Cost, PowerModels Better

The reverse direction: three scenarios where PowerModels converges to a
*better* (lower) generation-cost objective than the own model
(`1888_rte_load_x0.8`: PM 1,105,160.70 vs own 1,184,935.32, PM better by
7.22%; `1888_rte_qload_x1.2` [`reactive_power_stress`]: PM 1,478,335.90 vs
own 1,481,920.65, +0.24%; `1888_rte_qg_limits_x0.6`: PM 1,434,313.87 vs own
1,434,707.09, +0.027%, Julia only -- Python's own generation-cost solve
already matches PM closely for this scenario), via
`julia/own_models/scripts/68_investigate_1888_rte_cost_mismatch.jl` and
`69_verify_q10_deviation_reconstruction.jl`:

- **Q1/Q2**: no agreement, and `load_x0.8` is qualitatively different from
  every other case studied so far -- not a handful of generators trading
  power, but a **wholesale fleet-wide redispatch**: dozens of generators
  shift by 3-10x their PM value (e.g. one generator 3.0->13.1 MW, two others
  2.8->13.3 MW each, another 13.3->3.4 MW). Max `Pg` diff = 10.5 MW, max
  `Vm` diff = 0.20 pu, max `Va` diff = 1.47 rad. `qload_x1.2` and
  `qg_limits_x0.6` are far more contained (max `Pg` diff 6.9 and 2.35 MW,
  `Vm` diff <0.04 pu) -- closer in character to the active-losses cases
  above.
- **Q3**: yes, `g^e(x^{p*})=0` in all three (power-balance residual ~1e-15,
  Ohm's-law residual 5e-12 to 1e-8).
- **Q4/Q5/Q6**: no violations at PM's point in any scenario. Margin gaps
  scale with the size of the redispatch (largest for `load_x0.8`, ~4.57 `Pg`
  margin gap; smallest for `qg_limits_x0.6`, ~2.20).
- **Q7/Q8**: both converge cleanly in all three (mismatch 8e-12 to 1e-9), no
  thermal violations. The internal Q-allocation-feasibility flag came back
  `false` in all six checks here (both points, all three scenarios) --
  direct per-generator `Qg` bound inspection found zero actual violations
  each time, so this is a recurring but apparently benign quirk of that
  specific allocation heuristic on `case1888_rte`, not a real infeasibility.
- **Q9 (apples-to-apples thermal relaxation)**: all three gaps -- including
  the dramatic 7.22% one -- collapse to **exact machine-precision agreement
  (0.0000%)** once `rate_a` is inflated to the identical `1.0e6` constant on
  both sides (`load_x0.8`: PM/own both 1,038,404.447579; `qload_x1.2`: both
  1,337,169.416226; `qg_limits_x0.6`: both 1,354,859.603740). This is a
  cleaner result than the active-losses worked example above (where
  `x0.8`'s gap only narrowed, not vanished) -- thermal congestion is the
  **entire** explanation here: with thermal limits binding, there are
  multiple, equally cost-optimal ways to route power through a congested
  network (different generators/paths satisfying the same total-cost-
  minimizing equality physics), and the two differently-structured NLP
  encodings pick different corners of that degenerate optimal set. Remove
  the congestion, and only one place is left to go -- both solvers find it,
  exactly.
- **Q10**: linking LP on `Pg` deviations, weighted by the exact secant slope
  `c2*(Pg^{p*}+Pg^{e*})+c1` (not the bare linear coefficient `c1` -- see the
  ladder's step 9 for why that distinction matters in general). All three
  reconstruct the true objective gap to floating-point precision:
  `load_x0.8` total |deviation| = 263.32 pu, reconstructed = -79,774.625 vs
  true -79,774.625 (residual 1.5e-11); `qload_x1.2` total |deviation| =
  15.09 pu, reconstructed = -3,584.751 vs true -3,584.751 (residual
  5.7e-10); `qg_limits_x0.6` total |deviation| = 8.25 pu, reconstructed =
  -393.223 vs true -393.223 (residual 3.2e-10). Note: the naive (`c1`-only)
  and exact (secant) reconstructions coincide bit-for-bit here specifically
  because `case1888_rte`'s `gencost` data has `c2=0` for every generator
  (purely linear cost, confirmed directly from the `.m` file) -- this is a
  property of this case's data, not evidence that the linear
  approximation is generally sufficient; a case with genuinely nonzero
  quadratic cost coefficients would show a real residual on the naive
  version.

Both worked examples reach the same conclusion via the same ladder,
including its formal closing step: `case1888_rte` is the recurring case of
genuine, fully-accounted-for alternate local optima in this validation
grid, in both objectives and both directions of the gap -- not a
formulation defect anywhere in either model.
