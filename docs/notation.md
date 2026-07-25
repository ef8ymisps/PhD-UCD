# Notation Reference: Math Symbols → Code Identifiers

Maps the mathematical notation used across the model-development/validation
docs to where each symbol actually lives in code, in **all four**
implementations (Python AC, Python DC, Julia AC, Julia DC). Written as part
of "A5" (see `docs/model_development_and_validation_status.md` §7): Julia's
constraints were anonymous until this pass (see "Constraints" below) — this
doc is also the map you need to pull duals/shadow prices out of either
language's model.

Verified directly against the source at the time of writing (2026-07-11),
not assumed — if a variable/constraint gets renamed, this doc goes stale;
grep to confirm before trusting it on an unfamiliar case.

## Decision variables

| Symbol | Meaning | Units | Python AC | Python DC | Julia AC | Julia DC |
| --- | --- | --- | --- | --- | --- | --- |
| $P_g$ | Generator active power | p.u. | `model.pg[gen_id]` | `model.Pg[gen_id]` **(capitalized — inconsistent, see below)** | `pg[gen.id]` | `pg[gen.id]` |
| $Q_g$ | Generator reactive power | p.u. | `model.qg[gen_id]` | n/a (DC has no reactive power) | `qg[gen.id]` | n/a |
| $V_m$ | Bus voltage magnitude | p.u. | `model.vm[bus_id]` | n/a (DC assumes flat voltage) | `vm[bus.id]` | n/a |
| $V_a$ / $\theta$ | Bus voltage angle | rad | `model.va[bus_id]` | `model.theta[bus_id]` | `va[bus.id]` | `theta[bus.id]` |

**Naming inconsistency worth knowing**: Python's DC model uses `Pg`
(capitalized) while every other combination (Python AC, Julia AC, Julia DC)
uses lowercase `pg`. Not a bug — just a real inconsistency in this codebase;
don't assume `Pg`/`pg` are interchangeable across files without checking
which one you're in.

## Case/data parameters (not decision variables)

| Symbol | Meaning | Python identifier | Julia identifier |
| --- | --- | --- | --- |
| $P_d$, $Q_d$ | Fixed bus demand | `data.load_by_id[id].pd`/`.qd` (AC), `pd_by_bus[bus_id]` (DC) | `data.pd_by_bus[bus_id]`/`data.qd_by_bus[bus_id]` |
| $Y_{bus}$ (G, B terms) | Nodal admittance matrix, real/imag parts | `data.ybus_terms_by_bus[bus_id]` (AC) | `data.ybus_row_terms[bus_id]` (AC) |
| $B_{bus}$ | DC nodal susceptance matrix | `bbus` (matrix arg to `add_power_balance_constraints`) | `data.Bbus` |
| $B_f$ | DC branch-flow susceptance matrix (`Pf = Bf * theta`) | `bf` (matrix arg to `add_branch_flow_expressions`) | `data.Bf` |
| $Y_{ff}, Y_{ft}, Y_{tf}, Y_{tt}$ | AC branch terminal admittances | `branch_admittance_terms(branch)` → dict `yff`/`yft`/`ytf`/`ytt` | `ac_branch_admittance_terms(branch)` → NamedTuple `Yff`/`Yft`/`Ytf`/`Ytt` |
| $c_2, c_1, c_0$ | Generator cost coefficients | `gen.cost_c2`/`cost_c1`/`cost_c0` (via case `Generator`) | `gen.cost_c2`/`cost_c1`/`cost_c0` |
| $P_{g0}, Q_{g0}, V_{m0}, V_{a0}$ | Case-file baseline dispatch (warm-start source) | n/a (Python threads this through `initialization.py`'s `ACInitialPoint`, not stored on the case object directly) | `gen.pg0`/`qg0`, `bus.vm0`/`va0` — see `docs/notes/solver_tolerances.md` for why this is *not* the same as PowerModels' flat start |

## Constraints

Python's Pyomo models were already named/indexed by construction (every
`pyo.Constraint(...)` call assigns to a `model.xxx` attribute, indexed over
a Pyomo `Set`). **Julia's `@constraint`/`@NLconstraint` calls were anonymous
until this pass** — added inside bare `for` loops with no name and no way
to look up a specific constraint's dual afterward. A5 fixed this: every
constraint-adding function in `julia/own_models/src/opf/{ac,dc}/constraints.jl`
now returns named, id-indexed `Dict`s (or NamedTuples of them) of
`ConstraintRef`s.

**Two-step fix, not one**: the first pass only *indexed* (stored refs in a
`Dict`) without actually *naming* the AC power-balance and thermal-limit
constraints, because those were built with the legacy `@NLconstraint`/
`@NLexpression` macros, which don't support `base_name` or `set_name` at
all (both error — verified directly). Fixed by switching those specific
constraints to JuMP's modern, unified `@constraint`/`@expression` macros
(JuMP 1.30.1, installed here, handles nonlinear expressions with these
natively — the legacy `@NL*` macros predate that unification and are no
longer necessary). Verified this swap doesn't change solve behavior: same
objective, same solution to full float precision on a nonlinear test
problem, and real-model objectives before/after agree to ~1e-11 relative
(floating-point-noise level from a different internal expression-graph
representation, not a real change — Ipopt's own convergence tolerance is
`1e-8`).

| Math role | Python AC | Python DC | Julia AC | Julia DC |
| --- | --- | --- | --- | --- |
| Reference angle fixed | `model.slack_angle` | `model.reference_angle` | `constraints.reference_angle` (scalar) | `constraints.reference_angle` (scalar) |
| Nodal active balance | `model.p_balance[bus_id]` | `model.power_balance[bus_id]` | `constraints.power_balance_p[bus_id]` | `constraints.power_balance[bus_id]` |
| Nodal reactive balance | `model.q_balance[bus_id]` | n/a | `constraints.power_balance_q[bus_id]` | n/a |
| Branch angle-difference limit | `model.angle_limits[branch_id]` | `model.angle_difference_limit[branch_id]` | `constraints.branch_angle_diff[branch_id]` | `constraints.branch_angle_limits[branch_id]` |
| Branch thermal/flow limit | `model.from_thermal_limits[branch_id]`, `model.to_thermal_limits[branch_id]` (two-ended, apparent power) | `model.branch_flow_limit[branch_id]` (one-sided, active power) | `constraints.branch_thermal_from[branch_id]`, `constraints.branch_thermal_to[branch_id]` | `constraints.branch_flow_limits[branch_id]` |

**Coverage caveat**: a constraint only appears in these containers if it was
actually added for that element — e.g. `branch_thermal_from`/`branch_flow_limit`
only include branches with `rate_a > 0`, and the angle/thermal containers
are empty entirely if `include_branch_angles`/`include_branch_thermal` was
`False`/`false`. Always `haskey(...)` (Julia) or check membership (Python)
before indexing, don't assume every branch/bus id is present.

## How to pull a dual / shadow price

**Python** (Pyomo needs an explicit `Suffix` requesting duals before solving):

```python
model.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
# ... solve ...
shadow_price = model.dual[model.p_balance[bus_id]]
```

**Julia** (works directly on any `ConstraintRef` once the model is solved;
`build_ac_opf_model`/`build_dc_opf_model` are exported specifically so a
caller can get at the constraint containers — `solve_ac_opf_own`/
`solve_dc_opf_own`'s result structs do *not* carry duals, by design, to keep
the common-path result small):

```julia
model, data, pg, qg, vm, va, branch_flows, constraints = build_ac_opf_model(case; optimizer=ac_optimizer)
optimize!(model)
shadow_price = dual(constraints.power_balance_p[bus_id])
```

Verified working end-to-end for both languages (AC and DC) on `case5_pjm`
as part of this pass — `dual(power_balance_p["1"])` and
`dual(power_balance[bus_id])` both return real, non-zero marginal prices on
a converged solve.

## Deviation-minimization objective (C2)

`julia/own_models/src/opf/ac/objective.jl`'s `add_ac_target_deviation_objective!`
(status doc §7 item 10) minimizes deviation from an arbitrary target point
(e.g. PowerModels' solution) using explicit signed slack variables, not a
squared penalty — for each pinned variable group `X` (Pg, Qg, Vm, or Va):

| Symbol | Julia identifier |
| --- | --- |
| $\Delta X^+_i$ | `constraints.deviation_slacks.<X>_plus[id]` |
| $\Delta X^-_i$ | `constraints.deviation_slacks.<X>_minus[id]` |
| Link constraint | $x_i = x^*_i + \Delta X^+_i - \Delta X^-_i$, named `<X>_deviation_link[id]` |

where `<X>` is `Pg`/`Qg`/`Vm`/`Va` and `id` is the generator/bus id. Only
present for variable groups actually passed a `target_*` argument — a
variable group left `nothing` (e.g. `target_qg=nothing` to leave Qg free)
has no slack pair and isn't in `constraints.deviation_slacks` at all.
`constraints.deviation_slacks` is `nothing` entirely unless
`objective=:target_deviation` was used.

## Related docs

- `docs/model_development_and_validation_status.md` — overall model status,
  §7 tracks this as item A5.
- `docs/notes/solver_tolerances.md` — solver tolerance values and the
  starting-point/warm-start investigation that motivated inspecting these
  constraints closely in the first place.
