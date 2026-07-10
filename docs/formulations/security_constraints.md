# Security Constraints

Working notes for security and contingency constraints.

## Slack-modelling policy

This section states, explicitly, the slack-bus policy already implemented by
`own_models/src/analysis/{post_contingency,balancing_policy,security_checks}.py`,
so that OPF, load-flow, and N-1 contingency analysis are documented as
consistent rather than left as implicit code behavior.

### Policy: single slack, static proportional-reserve redispatch on generator loss

- **Base case (OPF and power flow):** single, fixed slack bus (`PowerCase.slack_bus_id`).
  The reference angle is pinned at this bus in DC-OPF, AC-OPF, and Newton AC
  power flow alike (`opf/dc/constraints.py`, `opf/ac/constraints.py`,
  `pf/ac_power_flow.py`). There is no distributed-slack (participation-factor)
  mechanism anywhere in the codebase.

- **Branch outage:** the slack bus and base-case dispatch are unchanged. Any
  generation/loss mismatch introduced by the outage is absorbed entirely by the
  slack bus's generator, computed post-solve by `compute_slack_pg`
  (`security_checks.py`) as the residual needed to satisfy the slack bus's own
  power balance. This is standard single-slack PF behavior — no redispatch is
  attempted for branch contingencies.

- **Generator outage:** a two-step static policy, applied *before* the
  post-contingency power flow is solved (`balancing_policy.py`):
  1. **Slack reassignment** (`_update_slack_if_needed`): if the outaged
     generator was not the slack generator, the slack bus is unchanged. If it
     *was* the slack generator, the online generator with the largest active
     headroom (`pmax - base_pg`) is promoted to slack, and its bus is
     relabelled `SLACK` (other generator buses stay `PV`, load-only buses
     `PQ`). This is a new *single* fixed slack for that contingency, not
     simultaneous multi-bus slack.
  2. **Proportional-reserve redispatch** (`apply_balancing_policy`, policy
     name `"proportional_reserve"` — the only policy implemented): the lost
     generator's base-case `Pg` is redistributed across all other *online*
     generators in proportion to each one's headroom (`pmax - base_pg`).
     Generators with zero headroom don't participate. If total headroom is
     insufficient to cover the loss, the contingency is flagged
     (`has_generation_imbalance=True`) and no power flow is attempted rather
     than forcing an infeasible redispatch. After redispatch, the (possibly
     new) slack generator still absorbs whatever small residual/rounding
     mismatch remains once PF is solved, via the same `compute_slack_pg` path
     used for branch outages.

### Why this is the right starting policy

Single-slack is easier to interpret and debug than distributed slack, and it
keeps the load-flow slack treatment consistent with the OPF formulations
(which are themselves single-slack). Proportional-reserve redispatch for
generator outages is a minimal, defensible stand-in for AGC/economic
redispatch — it avoids the unrealistic extreme of dumping 100% of a lost
unit's output onto a single remaining generator, without requiring a full
redispatch-OPF re-solve per contingency (which would be a different, heavier
policy: see below).

### Consistency check: OPF vs. PF vs. contingency

All three use the *same* `PowerCase.slack_bus_id` field and the same
single-slack convention — there is no mismatch between how the OPF
formulations treat the reference bus and how the contingency pipeline treats
it. The one behavioral difference is that generator-outage contingencies can
*change* `slack_bus_id` (via reassignment) mid-analysis, whereas OPF and
branch-outage PF never do.

### Sensitivity / future work (not yet implemented)

- No distributed-slack option exists to compare against. If added, it would
  need a participation-factor mechanism in the PF solver itself (splitting
  slack across multiple buses during the Newton solve), not a pre-processing
  redispatch step like `proportional_reserve`.
- No "re-solve as OPF" redispatch policy exists (i.e., re-optimizing dispatch
  post-contingency subject to cost) — `proportional_reserve` is a fixed rule,
  not an economic redispatch.
- Only N-1 (single-element) contingencies are supported;
  `contingencies.generate_contingencies` raises `NotImplementedError` for
  `k != 1`.
