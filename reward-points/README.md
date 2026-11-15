# reward-points — statechart board for the credit-card tools (spike)

A white-canvas board of the state machines inside `finance-tools/rewards.py` and the
card-related parts of `debt_plan.py`, drawn in Harel & Politi (STATEMATE) notation.

```bash
task board            # opens board/index.html in the default browser
```

Or open `board/index.html` directly. No build, no server, no dependencies.

## What is on the board

Two charts, switchable with the tabs:

- **CARD_STATES** — one instance per card. An and-state with eight orthogonal
  components, each a status the code computes: statement-credit period
  (`FUTURE → OPEN → USED | PARTIAL | MISSED`), enrollment, earn-rule cap, sign-up bonus,
  fee anniversary, issuer balance check, promo APR (`debts.yaml › promos`), and the
  paydown role (`debt_plan.py`: paid in full vs carried).
- **REWARDS_CONTROL** — one run of `task rewards`: offline/online fork, snapshot, rule
  loading, per-card analysis, render, with the three `SystemExit` paths.

Board controls: drag a state (children follow), drag the background to pan, wheel to
zoom, `f` to fit, `reactions` to hide the static-reaction text, `reset layout` to
forget your drags (positions are kept in the browser's localStorage), `SVG` / `PNG` to
export, `legend` for the notation.

## Files

| Path | What |
|---|---|
| `board/index.html`, `board/board.js` | the renderer: SVG, pan/zoom/drag, export |
| `board/model.js` | the two charts with layout coordinates |
| `model/rewards.yaml` | the same model in the triview schema (dictionary, activities, statecharts, modules) |
| `model/CARD_STATES.statemate.md` | the four artifacts, decisions with book citations, the two *Blocked* lists, the self-check |

`board/model.js` and `model/rewards.yaml` are hand-synced; the board is the picture,
the YAML is the model. The next step, if the spike is kept, is to generate the board from
the YAML (`triview-architecture.md` §4.3) and to add `.puml`.

## What the spike learned

- The card tool's real state machines are the per-credit / per-rule / per-card
  enumerations, and they are *generic*: one `PERIOD` per credit × period. Drawing them
  once and recording the multiplicity (ch. 14) is the book's answer, not a cheat.
- Every component's initial state is decided by YAML, so all seven default entrances go
  through a `C` connector. The triview schema cannot carry `C`, `T`, general static
  reactions, or `tr()` / `ch()` triggers — see *Blocked on schema*.
- One racing finding (day tick vs the day's transactions) that the batch code sidesteps
  by fixing `today`; recorded in the stage 9 walk.
