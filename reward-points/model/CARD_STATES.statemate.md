# CARD_STATES and REWARDS_CONTROL — statecharts for the credit-card tools

Spike, 2026-11-15. Built with the `statemate-modeling` recipe
(`~/projects/statemate/study/07-method-statechart.md`) against the code in
`finance-tools/rewards.py`, `rewards.yaml`, `debts.yaml` (`promos:`) and `debt_plan.py`.
Every decision cites Harel & Politi so it can be checked. The picture is
`../board/index.html`; the triview model is `rewards.yaml` beside this file.

Not claimed: completeness or validation. Stage 9 was walked for three scenarios only
(listed at the end); stage 11 (EWS comparison) was done by analogy, not line by line.

---

## Stage 0 — control activities and siblings (§7.2.1, §7.3.1, §13.3.2)

| Chart | Parent activity | Siblings it may start / stop / sense | Inbound flows | Outbound |
|---|---|---|---|---|
| `REWARDS_CONTROL` | `REWARDS` (one `task rewards` run) | `FETCH_MONARCH`, `READ_SNAPSHOT`, `WRITE_SNAPSHOT`, `LOAD_RULES`, `TRACK_CARD`, `RENDER_REPORT` | `OFFLINE` from `OPERATOR`; `sp()` of each sibling; `MONARCH_OK`, `SNAPSHOT_EXISTS`, `CARDS_RESOLVED` | `EXIT_CODE` |
| `CARD_STATES` | `TRACK_CARD` (generic: one instance per card, ch. 14) | `FILTER_TXNS`, `ACCUMULATE_EARN`, `TRACK_CREDITS`, `TRACK_BONUS`, `CHECK_BALANCE` | the transaction events, the operator's YAML edits as events, `TODAY` | the statuses the report prints |

**Was a chart needed?** For `REWARDS_CONTROL`: barely. It is a straight pipeline with two
failure branches; the book would accept hardware-activation style (§7.2.3) plus a
mini-spec. It is drawn because the `--offline` fork and the three `SystemExit` paths are
the only control decisions in the tool and the operator asked for them on the board.
For `CARD_STATES`: yes. Every component is a status enumeration the code literally
computes (`used | partial | missed | open`, `met | expired | in_progress`, promo
active/standard, carried/paid-in-full). Those are modes, not reactions.

**Honesty note.** `CARD_STATES` starts and stops almost nothing; the siblings run
throughout `ACTIVE` (hardware-activation style, §7.2.3). Its value is the modes and the
reactions, which §7.2.1 (p. 104) allows: "a single state with static reactions" is a
legal chart, and this one has eight of them side by side.

---

## Stage 1 — dictionary

See `rewards.yaml › dictionary` for the full table. The decisions that shaped it:

| Element | Kind | Why this kind (citation) |
|---|---|---|
| `PURCHASE`, `REFUND`, `CREDIT_POSTED`, `QUALIFYING_SPEND`, `FEE_POSTED` | event | a settled transaction line is instantaneous (§3.2). The code sorts them oldest first and folds them one by one; one event per line reproduces that. |
| `BALANCE_TYPED`, `ENROLL`, `ACTIVATE`, `CLOSE_CARD` | event | operator edits to YAML, modelled as commands (§3.2). The batch program sees only their result; the chart sees the moment. |
| `OFFLINE`, `ENROLLED_FLAG`, `SUB_DEFINED`, `ANNIV_KNOWN`, `PROMO_DEFINED`, `ACTIVATED`, `AUTOPAY_FULL`, `CARRIED_FLAG` | condition | states of the configuration, true or false until changed (§3.3). Used as `[C]` guards on the default-entrance connectors. |
| `TODAY` | data-item | `--as-of` or `date.today()`. The daily tick is `ch(TODAY)`, not an invented `DAY` event (recipe stage 1 table: "when X changes → `ch(X)`"). |
| `tr(TODAY > P_END)` etc. | derived event | the moment a date passes, `tr(C)` with `C` a comparison of data-items (expression ref §2, §3). Preferred over `tm(E,N)` because the code compares dates, it does not count ticks. |
| `PERIOD_ROLLOVER` | event | a cap period ended. Could be `tr(TODAY > CAP_P_END)`; named because the rule's period and the credit's period are different calendars. |
| `$U` | context variable | `min(SPEND, room)` is used three times in one action; `$U` is current within the step (§5.2.2, expression ref §4). |

Names: all ≤ 31 chars, letters/digits/underscore, none reserved (App. A §A.1).
`DAYS_LEFT`, `ROOM`, `DRIFT` are nouns; states are nouns/adjectives (§2.2.1).

---

## Stage 2–3 — modes and hierarchy (§4.1, §4.3)

`CARD_STATES`: `ACTIVE` | `INACTIVE`, exclusive. Default → `ACTIVE` (the registry
default is an open card). `CLOSE_CARD` leaves `ACTIVE` by its boundary, so every
component is exited at once (§4.6.1).

`PROMO_ACTIVE` is an or-state with `ADDING_SPEND` → `STOP_ADDING` inside it, so that
`tr(TODAY >= UNTIL)` can be drawn once from the superstate (§4.3, Fig. 4.9: a transition
from a superstate applies to all its substates). Priority: a transition from the parent
wins over one inside it only when both are enabled; here `STOP_DATE < UNTIL` always, so
they never race (§6.3.1).

`REWARDS_CONTROL`: flat. Hierarchy would add nothing to seven states in a line.

---

## Stage 4 — orthogonality (§4.4, Fig. 4.14)

`ACTIVE` is an and-state with eight components. Test per component (recipe stage 4.1):
can it be in any combination with the others, and would the product explode?

| Component | Independent of the others because | Instances |
|---|---|---|
| `PERIOD` | a credit's period status depends only on that credit's spend and dates | one per credit × period (generic) |
| `ENROLLMENT` | `enrolled:` is a flag the operator sets; the period status is computed regardless (`credit_usage` does not read it; `analyze_card` filters totals by it) | one per credit |
| `EARN_CAP` | cap bookkeeping is per rule per calendar period | one per rule |
| `SIGNUP_BONUS` | window `[OPENED, DEADLINE]`, nothing else | one |
| `FEE_CYCLE` | anniversary arithmetic only | one |
| `BALANCE_CHECK` | typed-in readings only | one |
| `PROMO_RATE` | `debts.yaml › promos`, a date timeline | one |
| `PAYDOWN_ROLE` | `debt_plan.py` decides it from `autopay_type`, `carried`, and the promo | one |

Communication between components uses `en(S)` only: `PAYDOWN_ROLE` listens for
`en(PROMO_ACTIVE)` and `en(STANDARD_RATE)` (§4.4.2, Fig. 4.15). No hand-set flags.

**Decision recorded for triview:** yes, a real model reached for orthogonal regions, and
immediately for *generic* ones (one `PERIOD` per credit × period). That is the
multiplicity problem (recipe stage 4.4, ch. 14), see *Blocked on behavior*.

---

## Stage 5 — labels (expression reference)

Spellings follow the chapters: `>=`, `<=`, `#` would be inequality (unused). Time is by
comparison, `tr(TODAY > P_END)`, never `tm()`. Static reactions sit under a divider in
the state box (§5.4.2). Entry reactions are written `ns / …` (§5.4.1).

The ordering trap (Fig. 6.4) was hit twice and resolved the same way: an assignment made
in a step is not seen by another action in the same step.

- `OPEN`: `QUALIFYING_SPEND / SPEND := SPEND + AMT` and, one step later,
  `ch(SPEND) or ch(POSTED) / USED_AMT := min(AMOUNT, max(SPEND, POSTED))`. Writing
  `USED_AMT` in the same reaction would read the old `SPEND`.
- `ONE_READING → TWO_READINGS`: `DRIFT := (PTS - REPORTED) - EARNED_SINCE; REPORTED := PTS`
  is correct *because* `REPORTED` on the right is the old value: the drift is measured
  against the previous reading.

---

## Stage 6 — connectors (§4.5, §6.3.1)

Seven `C` connectors, all on default entrances, because the initial state of each
component is decided by the YAML (`enrolled`, `signup_bonus`, `anniversary`, `promos`,
`autopay_type`). Each guard set is exhaustive and pairwise exclusive:

| Connector | Guards |
|---|---|
| `PERIOD` | `[P_END < BENEFIT_START]` / `[P_END >= BENEFIT_START]` |
| `ENROLLMENT` | `[ENROLLED_FLAG]` / `[not ENROLLED_FLAG]` |
| `SIGNUP_BONUS` | `[SUB_DEFINED]` / `[not SUB_DEFINED]` |
| `FEE_CYCLE` | `[ANNIV_KNOWN]` / `[not ANNIV_KNOWN]` |
| `PROMO_RATE` | `[not PROMO_DEFINED]` / `[PROMO_DEFINED and not ACTIVATED]` / `[PROMO_DEFINED and ACTIVATED]` |
| `PAYDOWN_ROLE` | `[AUTOPAY_FULL and not CARRIED_FLAG]` / `[not AUTOPAY_FULL or CARRIED_FLAG]` |
| `REWARDS_CONTROL` entry | `[OFFLINE]` / `[not OFFLINE]` |

`OPEN → C` on `tr(TODAY > P_END)[USED_AMT < AMOUNT]`, then `[USED_AMT > 0]` → `PARTIAL`,
`[USED_AMT = 0]` → `MISSED`. The guard on the segment into the connector keeps it
exclusive of `OPEN → USED` on `tr(USED_AMT >= AMOUNT)`. Nothing is assigned on the way
into a connector (the Fig. 6.4 trap in the skill's trap list).

`T` connector in `REWARDS_CONTROL`: the run stops itself after `RENDERING` or `FAILED`
(§7.2.2, self-termination).

---

## Stage 7–8 — activities and mini-specs (§7.3, §7.4)

`REWARDS_CONTROL` uses `throughout` in every state (§7.3.3): the sibling runs for exactly
as long as the state is active and its `sp()` is the exit trigger. This is Fig. 7.7's
replacement of `A_DONE` by `sp(A)`, applied six times.

`TRACK_CARD` is started once per card. `st!(TRACK_CARD)` on an already-active instance is
a no-op (§7.3.1), so N cards need N generic instances: *Blocked on behavior*.

Mini-specs that belong in the Data Dictionary, not in a chart (§7.4):

- `FILTER_TXNS`: the `exclude:` block (`card_transactions`, `is_excluded`), plus
  "a posted statement credit never earns points" (`is_any_posted_credit`).
- `ACCUMULATE_EARN`: first-match-wins over the ordered `earn:` list, last rule is base.
- `TRACK_CREDITS`: `periods_in_year`, calendar-based (Jan–Mar is Q1).

---

## Stage 9 — three step-semantics walks (ch. 6)

1. **A Lululemon charge on the last day of Q3.** Step 1: `QUALIFYING_SPEND` → `SPEND`
   updated. Step 2: `ch(SPEND)` → `USED_AMT := 75`; in the same step
   `tr(USED_AMT >= AMOUNT)` is *not yet* true (old value). Step 3: `tr(...)` fires,
   `OPEN → USED`. Meanwhile `ch(TODAY)` to Oct 1 may land in any of these steps; if it
   lands in step 2, `tr(TODAY > P_END)[USED_AMT < AMOUNT]` sees `USED_AMT = 0` (old) and
   fires → `MISSED`. **Racing finding (§6.3.2):** the code avoids it by evaluating with
   `today` fixed; the chart needs the day tick to be ordered after the day's
   transactions. Recorded, not fixed.
2. **Two balance readings on the same day.** Two `BALANCE_TYPED` events cannot occur in
   one step unless the operator types two rows with one `as_of`; the code sorts by
   `as_of` and takes the last. The chart would take them in two steps, `DRIFT` from the
   second being `PTS2 - PTS1 - 0`. Same answer.
3. **Promo ends.** `tr(TODAY >= UNTIL)` in `PROMO_ACTIVE` → `STANDARD_RATE`; `en(STANDARD_RATE)`
   is an event in the *next* step (Fig. 6.1), so `PAYDOWN_ROLE` moves one step later.
   Fine: nothing reads `PAYDOWN_ROLE` in between.

Lost-event check (Fig. 6.1): `ENROLL` while in `ENROLLED` is lost; harmless. `ACTIVATE`
while in `NO_PROMO` (after `ACTIVATE_BY` passed) is lost; the code would also ignore it.

---

## Stage 11 — against the EWS answer key (App. B)

The shape matches Fig. B.4's `ON` and-state: components that are "what the operator is
doing" and "what the world is doing", communicating by `in()`/`en()`. The default-entrance
`C` connectors have no EWS analogue; the closest is Fig. 4.19's conditional entrance.
The EWS has one timeout (`tm(en(COMPARING), …)`); this model has none, by design.

---

## Decisions

| # | Decision | Citation |
|---|---|---|
| 1 | Two charts, one per control activity; `CARD_STATES` generic | §7.2.1, §12.2, ch. 14 |
| 2 | Status enumerations become or-states; the daily tick is `ch(TODAY)` | §4.1, recipe stage 1 |
| 3 | Eight orthogonal components under `ACTIVE`, generics drawn once | §4.4, Fig. 4.14, ch. 14 |
| 4 | Cross-component coupling only through `en(S)` | §4.4.2, Fig. 4.15 |
| 5 | Date passing is `tr(X > Y)`, not `tm()` | expression ref §2 |
| 6 | Two-step updates where a value is read after being written | Fig. 6.4, §6.1.1 |
| 7 | Default entrances through `C` with exhaustive, exclusive guards | §4.5, §6.3.1 |
| 8 | `throughout` + `sp(A)` for every pipeline state | §7.3.3, Fig. 7.7 |
| 9 | `T` connector ends the run; `FAILED` sets the exit code on entry | §7.2.2, §5.4.1 |
| 10 | `exclude:` rules are a mini-spec of `FILTER_TXNS`, not chart content | §7.4 |

## Blocked on behavior

- **Multiplicity.** One `PERIOD` per credit × period, one `ENROLLMENT` per credit, one
  `EARN_CAP` per rule, one `TRACK_CARD` per card. Drawn once each; the book's answer is
  generic charts with parameters (ch. 14, §14.3), which this spike did not instantiate.
- **Ordering of the day tick vs. the day's transactions** (walk 1). The notation has no
  way to say "all of today's events before `ch(TODAY)`" short of a queue (§8.4.3).
- **"Open" with a deadline that depends on the period kind** (`monthly` / `quarterly` /
  `semiannual` / `annual`). `P_START`/`P_END` are data-items the chart receives; the
  calendar arithmetic is outside it.
- **Posted-credit proof of use** (`used = max(spend, posted)`) is expressed, but
  "a Resy restaurant posting under its own name" is a merchant-matching problem the
  chart cannot see; it lives in `FILTER_TXNS`.

## Blocked on schema (triview §3.1)

- `C` connectors (seven default-entrance ones plus `OPEN → C`): no node type; encoded as
  guarded transitions from the same source, with the default branch lost.
- `T` connector: no node type; noted as a comment.
- General static reactions (`E / A` inside a state): only `entry`/`exit` exist; written
  into `desc:` strings, which is not checkable.
- `tr(C)` and `ch(X)` as `on:` values: the schema says `on:` "must exist in
  `dictionary.events`"; derived events are not declarable.
- `throughout` vs `within`: `starts:` cannot say the state's exit stops the activity.
- Generic instance multiplicity: no way to say "one per credit".
- Reaction order across steps (decision 6) is semantics the YAML cannot express or check.

## Self-check (recipe list)

- [x] every or-state has a default entrance (`PROMO_ACTIVE` → `ADDING_SPEND`; `CARD_STATES` → `ACTIVE`; each component through its `C`)
- [x] names ≤ 31 chars, not reserved (`STOP_ADDING`, `OVER_CAP` checked against §A.1.1)
- [x] activities in `sp()`/`throughout` are siblings of the control activity
- [x] `en()` targets are in the same chart (`PROMO_ACTIVE`, `STANDARD_RATE` are in `CARD_STATES`)
- [x] `REWARDS_CONTROL` self-terminates and has a `T`; `CARD_STATES` runs while `TRACK_CARD` runs and has none
- [x] every `C` guard set exhaustive and exclusive (table above)
- [ ] no transition and static reaction meant to fire together: `OPEN` violates this in walk 1 (recorded)
- [x] no data-item written twice in one step (`REPORTED` is written once; the read is the old value by design)
- [ ] `>` marker for Data Dictionary content: not drawn on the board
- [x] no offpage connectors; no self-loop outside a state with `ns`/`xs` reactions
- [x] no action on a segment into a connector

Sections worth reading next for this chart: §7.3.3 (every pipeline state uses
`throughout`), §4.4.2 (the `en()` coupling), §6.3.2 (the racing finding), ch. 14 (the
multiplicity that blocks four components).
