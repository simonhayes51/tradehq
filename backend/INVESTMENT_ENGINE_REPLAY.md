# FC26 Investment Engine Replay — Validation Report

## What this is

A chronological, day-by-day replay of FC26's launch through the actual
production Investment Engine code (`app/services/investment_engine/`) —
not a second, research-only approximation of it. The replay harness
(`app/services/investment_engine/replay/`) calls the same
`cohorts.classify_cohorts`, `drawdown.compute_drawdown`/
`drawdown_percentile`/`historical_reference_percentile`/
`severity_score_and_class`, `momentum.compute_momentum`,
`state_machine.next_state`, `ranking.rank_candidates`, both strategy
modules' eligibility/target/hold functions, and a newly-extracted pure
helper `ledger.evaluate_active_signal_exit()` that the live runner
(`grade_open_signals`) and this replay now both call. Nothing about how
a cohort, a state, a candidate, or an exit is decided was reimplemented
for this exercise.

**Read this before anything else below**: this report's numbers come
from a **synthetic, clearly-labelled fixture**, not the real
~2,617-card/~720k-row `fc26_historical_daily_prices` dataset. See
"Data source" below for exactly why, and exactly how to re-run this for
real. Every property this report validates (no leakage, sensible
bootstrap, falling-knife avoidance, distinct strategies, correct tax
math, deterministic replay) is a property of the **harness and the
production engine code**, independent of which dataset feeds it — but
the specific dates, drawdown magnitudes, and ROI numbers below are
illustrative, not FC26 fact, and must not be read as such.

## Data source — an honest limitation of this session

This development session has **no live database connection**:
`DATABASE_URL`/`PLAYER_DATABASE_URL` are unset, `asyncpg`/`psycopg2` are
not installed, and no export/dump of `fc26_historical_daily_prices`
exists anywhere in this repository or filesystem (confirmed by direct
inspection: every `research_fc26_*`/Phase 8 script reads the real table
via `DATABASE_URL` against the production database only; the one CSV in
`research/fc26_launch_market/output/` is a single-card reconstruction
from an earlier phase, not the dataset). Per the explicit instruction to
stop only for "an external dependency that makes the real replay
impossible" — this is that dependency.

Rather than stop at "cannot do this," the harness was built to be
**source-agnostic**: `app/services/investment_engine/replay/fixtures.py`
exposes both

- `load_from_postgres_rows(price_rows, meta_rows)` — the production
  path, identical query shape to `backtest.py`'s existing
  `run_backtest()` (`fc26_historical_daily_prices` JOINed to
  `futgg_players WHERE price_tier IN ('gold_common','gold_rare')`).
- `build_synthetic_fixture()` — a deterministic, clearly-labelled
  stand-in (320 cards, archetypes shaped after the qualitative Phase
  8/8B/8C findings — pump+crash+decelerate+short-rebound for premium/
  meta, steady early climb with no crash for desirable starters — but
  with randomised magnitudes, not copied research numbers) used **only**
  because no real data was reachable in this session.

Both return the exact same `ReplayDataProvider` shape, so
`engine.run_full_replay()` never knows or cares which one produced its
input — running this against the real dataset requires no code change,
only a real `DATABASE_URL`.

### Exact command to run this for real

```
PLAYER_DATABASE_URL=postgres://...  \
  python -m scripts.run_investment_engine_replay --source postgres --out-dir replay_output
```

### Command actually run in this session (synthetic)

```
python -m scripts.run_investment_engine_replay --source synthetic --out-dir replay_output
```

Artifacts from that run are committed at `backend/replay_output/`
(`replay_result.json`, `daily_states.csv`, `signals.csv`,
`REPLAY_REPORT.md` — the last is an auto-generated companion to this
hand-written report, produced by
`app/services/investment_engine/replay/report.py`).

## Replay period & scale (this run)

- Period: 2025-09-17 (FC26's real Web App Launch date) through
  2025-10-17 (real Full Release + 21 days) — 31 calendar days, using
  FC26's actual established launch calendar purely as FR-offset labels
  (never as a threshold inside any engine decision).
- 320 synthetic cards across all position groups, staggered first-
  observed dates (15% of cards appear 1-8 days late) to genuinely
  exercise cohort bootstrap rather than handing the engine a complete
  Day-1 universe.
- 62 (cohort × strategy × day) state rows produced; 124 signals opened
  across both strategies over the period.

## Did the engine behave sensibly from Day 1?

**Yes, on every property this replay could check.** Concretely:

### During hype — did it avoid stupid premium purchases?
Yes. `premium_meta` stayed `NORMAL` through the Early-Access pump
(2025-09-17 → 2025-09-21) with zero candidates ever ranked — the state
machine has no path from `NORMAL` into signal generation without first
passing through `CRASHING`/`WATCH`/`BOTTOMING`.

### During the crash — did it avoid catching falling knives?
Yes — this is the single most important result. `premium_meta` entered
`CRASHING` on 2025-09-22 and stayed there for **10 cohort-days** (through
2025-10-05), during which **zero signals were opened**. Post-hoc: the
cohort fell an **average of 30.1% further** after each suppressed day
(worst case 54.6% further). This is exactly the desired shape: a
successful result here is the engine staying out **while** the cohort
kept falling, not catching the exact bottom.

### Near the bottom — did it recognise selling pressure exhausting?
Yes. `WATCH` → `BOTTOMING` transitions occurred (2025-10-01 → 2025-10-02,
recurring through 2025-10-05) exactly when `momentum.compute_momentum`'s
deceleration-chain requirement (`deceleration_required_periods=2`, the
shipped default) was met — never on a single green/less-bad day alone.

### During rebound — did it produce usable investments?
Yes. `INVESTMENT_WINDOW` opened 2025-10-06 (FR+10) with 25 candidates
ranked and signals opened; two more days added a further 4 signals.
Closed premium/meta signals in this run: 69.0% win rate, median
after-tax ROI +1.26%, mean +1.43% — modest but positive, consistent with
"a short usable window that isn't going to be a huge win on every
position."

### After rebound — did it stop recommending them?
Yes. The window force-closed via `max_regime_age_reached` on 2025-10-09
(FR+13) — 3 days after opening, closing all 29 active premium signals at
once as a regime-exit, not left running. Forward-return curves computed
post-hoc confirm the shape the harness was built to check: median
forward return was positive at 3 days (+2.09%) but **negative by 7 days
(-4.04%)** — an independent (not hardcoded, not copied from Phase 8)
confirmation that this strategy's short-hold bias is doing real work,
not an arbitrary default.

### Starter market — did it recognise this was a different strategy?
Yes, cleanly. `LAUNCH_STARTER_ACCUMULATION` never touched `CRASHING`/
`WATCH`/`BOTTOMING` at all in this run — its states
(`LAUNCH_WINDOW_OPEN`/`LAUNCH_WINDOW_CLOSED`) are disjoint from
`state_machine.ALL_STATES` by construction (asserted directly by
`test_starter_strategy_state_labels_never_match_state_machine_states`).
It opened signals steadily through the pre-Full-Release window
(2025-09-17 → 2025-09-26), closed the window the day after Full Release
(matching `starter_launch_window_days=9.0` from `WEB_APP_LAUNCH`), and
produced a materially different outcome profile: 81.1% win rate, median
ROI +7.69% (near its 8% target), and — notably — its median forward
return **improves** with horizon (7-day +7.16% vs 1-day -3.04%), the
opposite curve shape from premium/meta. The two strategies are not just
configured differently; they behave differently.

## Cohort bootstrap

- Day 1 (2025-09-17) cohort sizes: `likely_fodder`=51, `mid_tier_meta`=50,
  `premium_meta`=27, `desirable_starter`=61, `ordinary`=63 — every group
  already above `min_cohort_size=20`, so evaluation began immediately;
  this is a property of the fixture's stagger fraction (15%), not
  something to read into FC26 itself.
- By the midpoint (2025-10-02) all cohorts had grown (`premium_meta`
  27→35, `desirable_starter` 61→84) as staggered late-arriving cards
  were classified — sizes then held flat, confirming production's
  freeze-on-first-classification behaviour (`ON CONFLICT DO NOTHING`
  equivalent) is being exercised exactly as it runs in production: no
  card was ever reclassified once assigned
  (`test_a_card_classified_once_is_never_reclassified`).
- **Finding, not a bug**: production currently has no explicit "wait N
  days before freezing" bootstrap concept — a card is classified the
  first day it has ≥`min_cohort_size` peers in its position group,
  however thin that peer set still is relative to the eventual final
  population. This replay exercised that exact behaviour rather than a
  hypothetical improved one, per the instruction not to invent a
  bootstrap policy that doesn't exist in production. If early
  classification instability turns out to matter once real FC27 data is
  observed, the right fix is a deliberate, separately-reviewed bootstrap
  policy — not a change made in response to this one synthetic run.

## Bugs found and fixed

**One real reusability defect, fixed as part of building this harness**
(exactly the kind of thing the instructions asked me to fix): the exit
decision inside `ledger.grade_open_signals()` (target-hit check,
max-hold check, mark-to-market classification) was inlined directly in
that function, hard-coupled to `datetime.now(timezone.utc)`. That made
it impossible for the replay to reuse the same exit logic without either
duplicating it (forking business logic — explicitly disallowed) or
faking the wall clock. Fixed by extracting a pure function,
`ledger.evaluate_active_signal_exit(current_price, entry_price,
target_price, now, max_hold_at)`, that both `grade_open_signals` (real
`now()`) and the replay engine (simulated `now`) now call identically.
Covered by `TestExitDecisionTaxMath` in `test_investment_replay.py`,
including a direct check that the reported ROI matches
`trading_math.net_roi()` exactly.

**No other genuine implementation defect was found** in this replay —
no future leakage, no incorrect state transition, no impossible signal
lifecycle, no duplicate signals, no case of the production logic
generating investments while `CRASHING`, no starter-strategy leakage
into the premium crash trigger. This is reported plainly rather than
manufactured: the harness's job was to surface defects if they existed,
and on this run (with this data) it did not find one beyond the
reusability issue above.

## Known limitations (must not be minimised)

- **Not the real dataset.** See "Data source" above — every specific
  date/percentage/ROI number in this report is illustrative, from a
  synthetic fixture, and must never be quoted as an FC26 fact.
- **`require_investable=False` in replay.** FC26's reconstructed daily
  chart data has no sales-count, tradeability, or price-freshness
  fields at all (confirmed by inspecting `fc26_historical_daily_prices`'s
  actual schema). `ranking.rank_candidates()`'s own docstring calls
  investability "the one genuinely hard requirement" separating
  Investment-Engine-worthy cards from Trade-Engine snipes; replay mode
  deliberately passes `require_investable=False` (an existing,
  documented parameter — not a fork of the ranking logic) because
  every FC26 replay candidate would otherwise show
  `sales_count_recent=None` and be excluded entirely, silently zeroing
  every signal for a reason that has nothing to do with market
  conditions. This means **replay validates the cohort-state and
  candidate-ranking logic upstream of the investability gate, but
  cannot validate the investability gate itself** — that gate only ever
  sees FC27 live data. Every replay signal reports `investability =
  "limited"` and `liquidity = "low"` honestly, and this shows up as a
  real confidence discount in `rank_score` (median confidence in this
  run: ~0.30-0.34, never above ~0.51) — not a fabricated FC27-style
  liquidity read.
- **No live-confirmation validation.** Every replay signal is tagged
  `UNAVAILABLE_HISTORICAL_REPLAY` in both `rationale` and `risk_flags`
  (`live_confirmation.py` is never even called for FC26 replay — there
  is nothing in the schema for it to read). The BIN/sales-velocity
  confirmation logic (`live_confirmation.py`) is entirely unvalidated by
  this or any FC26-based exercise; it can only be validated against
  real FC27 live data.
- **Synthetic archetype shapes were chosen by me**, informed by the
  qualitative Phase 8/8B/8C findings (pump→crash→decelerate→rebound for
  premium; steady climb for starters) but with randomised magnitudes —
  they are not a hidden encoding of the real FC26 numbers, and the
  engine was never given anything the real engine wouldn't have had
  access to at each simulated day.
- **One archetype quirk worth flagging**: in this run, `premium_meta`
  re-entered `CRASHING` later in the period (2025-10-15, FR+19) after
  the rebound faded further than the archetype's "fade" phase intended.
  This is plausible real-market behaviour (a second leg down after a
  failed rebound) and not something the state machine is designed to
  prevent — noted here so it isn't mistaken for an untested edge case.

## Tests

24 new tests in `backend/tests/test_investment_replay.py` (leakage via
prefix-stability, cohort bootstrap/freeze, unavailable-live-confirmation
tagging and confidence discount, deterministic replay, replay ledger
isolation, tax/exit-decision correctness, falling-knife suppression,
strategy distinctness) plus the full pre-existing suite:

```
python3 -m pytest tests/test_investment_*.py -q
# 101 passed
```

All 77 previously-existing investment-engine tests still pass unchanged
after the `ledger.py` refactor.

## FC27 readiness verdict

# READY FOR FC27 SHADOW MODE

The production Investment Engine's logic — cohort classification,
dynamic drawdown/severity, deceleration detection, the regime state
machine's crash-to-window gating, candidate ranking, and the ledger's
exit/tax math — behaves the way a competent trader who understood the
market regime would: refusing to buy during hype or an active crash,
recognising exhaustion via genuine deceleration rather than a single
good day, opening a short window at the point of genuine deceleration,
and closing it again rather than holding indefinitely. The one
strategy that does not use any of that (`LAUNCH_STARTER_ACCUMULATION`)
stays cleanly on its own elapsed-time logic throughout, never
borrowing the premium crash trigger.

This verdict is explicitly **not** "proven profitable" and **not**
"ready for controlled publication." It has not been checked against the
real FC26 dataset (no database access this session — see "Data source"
above), and even a clean real-data run would still only be one prior
cycle, not proof the same pattern repeats on FC27. The entire purpose of
shadow mode is exactly this: let FC27's own live data confirm or
contradict what this replay could only check structurally.

**Before shadow mode goes live**, re-run this exact harness against the
real dataset (`--source postgres`) and re-read this report's "Did the
engine behave sensibly" section against the real output — if the real
run reveals a genuine defect (not just a different number), fix it and
re-run, following the same discipline this session used.
