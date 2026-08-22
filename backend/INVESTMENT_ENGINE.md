# Investment Engine

## What this is, and what it isn't

EAFC.Live has always had one engine answering one question: **"is there
money in this specific listing, right now?"** — the Trade/Opportunity
Engine (`GET /api/v2/opportunities`, `/api/v2/trade-finder`,
`app/services/futgg_recommendation_store.py`). It looks at one card's
current BIN against recent completed sales and flags a single mispriced
listing worth flipping in minutes.

The **Investment Engine** (this document) answers a different question:
**"is this market segment likely to appreciate over the next 1-3 days,
with enough supply to build a real position?"** It looks at a *cohort* of
cards (e.g. "premium/meta golds") going through a market cycle — launch
hype, a crash, a bottom, a rebound — and only once that cohort's own
state supports it does it recommend individual cards, sized for a
multi-day hold rather than a single flip.

**These are never merged.** A card can have a Trade Opportunity with no
Investment signal (one attractively mispriced BIN in an otherwise flat
market), or an Investment signal with no Trade Opportunity (nothing is
mispriced right now, but the cohort is bottoming and about to rebound).
Separate tables, separate API router, separate frontend page/nav item.

## Why this exists: FC27 launch, informed by FC26 research

The FC26 launch-market research (Phases 8/8B/8C/8D, `research/
fc26_launch_market/`) found that a naive "it's fallen a lot, buy it" rule
performs badly during price discovery — premium/meta cards fell from
Early-Access highs through a genuine crash (median buy-early loss ~-24%
to -29%, sub-20% win rate) before any real bottom. The one trailing
signal that reliably preceded a good entry was **deep drawdown +
decline deceleration** (40–55% drawdown + ≥2 days of decelerating
decline), with a short (~2 day) useful entry window and holds beyond
5-7 days deteriorating badly. A *different* cohort — desirable
starter/mid-price cards — had a different edge entirely: buying early
pre-Full-Release and holding 3-7 days, with no deep V-crash at all.

The Investment Engine encodes both findings as **priors**, not hardcoded
truths: the FC26 drawdown reference (40–56.5%) is one input blended into
a cycle-relative percentile, never a standalone gate; the starter
strategy never touches the crash trigger at all. See "FC26 prior vs
FC27 reality" below for how the two evidence sources are weighted over
time.

## Architecture: cohort state first, then individual cards

```
cards ──classify──▶ cohort (percentile within position group)
                          │
                          ▼
              cohort market-cycle state machine
     NORMAL → OVERHEATED → CRASHING → WATCH → BOTTOMING
        → INVESTMENT_WINDOW → REBOUND → EXIT → CLOSED
                          │
              (only INVESTMENT_WINDOW / REBOUND)
                          ▼
                 individual card ranking
                          │
                          ▼
                  investment_signals (the Ledger)
```

A card is **never** independently ranked as investable while its cohort
is still `CRASHING` — the regime gate in `state_machine.py` is a hard
precondition, enforced in `runner.py` before any per-card ranking runs.

### Package layout (`app/services/investment_engine/`)

Every module except `runner.py`, `ledger.py`, and `backtest.py`'s DB
entry point is a **pure function** — no I/O, no wall-clock access beyond
values passed in. This is deliberate: the exact same `cohorts.py` /
`drawdown.py` / `momentum.py` / `state_machine.py` / `ranking.py` code
runs inside the live FC27 runner *and* the FC26 backtest harness, so
there is exactly one implementation of "is this a crash-and-deceleration
setup" to validate, not two that can drift apart.

| Module | Responsibility |
|---|---|
| `config.py` | `InvestmentEngineConfig` frozen dataclass + env-var overrides. Single source of truth for every threshold/flag. |
| `cohorts.py` | Within-position-group launch-price percentile classification (premium_meta / mid_tier_meta / desirable_starter / ordinary / likely_fodder). |
| `drawdown.py` | Current drawdown from running peak, cycle-relative percentile, FC26 reference percentile, blended severity score/class. |
| `momentum.py` | Return-history momentum and decline-deceleration detection. |
| `state_machine.py` | The 9-state regime transition table (`next_state`). |
| `ranking.py` | Card-level ranking within an eligible cohort — liquidity, sales velocity, price freshness, tradeability. |
| `live_confirmation.py` | FC27-only live BIN/sales confirmation signal (see below). |
| `strategies/` | Strategy plugin registry — see below. |
| `ledger.py` | Opens/grades/closes `investment_signals` rows; tax/ROI math via `app.services.trading_math`. |
| `runner.py` | The only module (besides `ledger.py`) that talks to Postgres. Orchestrates one recompute pass per strategy. |
| `backtest.py` | Runs the same pure state-machine logic over FC26's historical daily prices, with a strict no-lookahead walk. |

## Cohort model

`cohorts.py` ranks cards by launch reference price **within their own
position group** (`POSITION_GROUPS` — never compares a striker to a
goalkeeper) and assigns a percentile-based cohort:

| Cohort | Percentile band (default) |
|---|---|
| `premium_meta` | ≥ 90th |
| `mid_tier_meta` | ≥ 70th |
| `desirable_starter` | ≥ 45th |
| `ordinary` | ≥ 20th |
| `likely_fodder` | below 20th |

Bands are env-overridable (`INVESTMENT_PREMIUM_META_PERCENTILE`, etc.),
data-driven (not fixed coin thresholds), and persisted per card in
`investment_cohort_memberships` with a `classifier_version` — a card's
cohort assignment, once written, never silently changes under an open
signal; a future reclassification runs under a new version instead.

## Dynamic drawdown & severity (no hardcoded 40%/50%)

`drawdown.py` computes, per cohort pass:

- `drawdown_pct` — from the cohort's own running price-index peak.
- `drawdown_percentile_current_cycle` — where today's drawdown ranks
  against this cycle's *own* history so far.
- `historical_reference_percentile` — where it sits against the FC26
  reference band (40–56.5%, `INVESTMENT_FC26_REF_DRAWDOWN_LOW/HIGH`).
- `severity_score` / `severity_class` (`normal|elevated|severe|extreme`)
  — a blend of the two percentiles, weighted by how much of the
  cycle's own history exists yet (`min_cycle_observations_for_percentile`).
  With little cycle history, the FC26 reference carries more weight;
  once enough of FC27's own cycle has been observed, the cycle-relative
  percentile dominates (`cycle_weight` up to 0.9 — the FC26 number is
  a light tiebreaker, not a ceiling a severe FC27 crash could get stuck
  under).

No absolute percentage is a hard gate anywhere in this pipeline —
verified by `test_investment_drawdown.py::test_no_absolute_fc26_
number_is_a_hard_gate`.

## Deceleration

`momentum.py` tracks a rolling short window of period-over-period
returns and counts a *strictly improving* (less negative) trailing
chain. `deceleration_required_periods` (default 2) sets how many
consecutive improving periods are required before `is_decelerating`
flips true — configurable, not hardcoded, per the FC26 finding that 1-2
periods were the validated zero-false-trigger plateau.

## FC27 live confirmation

`live_confirmation.py` is queried only once a cohort's severity reaches
`severe`/`extreme` (cheap short-circuit — it needs per-card queries).
It reads a bounded sample (50 cards) of `futgg_bin_history` and
`futgg_sales_history` and computes only what those tables can actually
support:

- **Implemented**: lowest-BIN trend/movement, recent sales velocity,
  recent sales count, median recent sale price, a `confirmed` boolean
  gated on `min_sales_count_recent`.
- **Deliberately `None` (not fabricated)**: `listings_near_floor` and
  `supply_depth_shrinking` — no listing-depth/order-book table exists
  in the current schema. These fields exist on the return object so a
  future schema addition can populate them without an API shape change,
  but they are never invented from data that isn't there.

In `state_machine.py`, `live_confirmation_ok = None` (unavailable, e.g.
early FC27 or FC26 backtest) is treated as **not blocking** — live
confirmation is additive evidence on top of the historical
drawdown+deceleration trigger, never a hard requirement that trigger
alone can't satisfy.

## Regime state machine

```
NORMAL → OVERHEATED → CRASHING → WATCH → BOTTOMING
   → INVESTMENT_WINDOW → REBOUND → EXIT → CLOSED
```

Transitions (`state_machine.next_state`) — full detail in the module,
key safety properties:

- `CRASHING` can only reach `WATCH` via `severity_class == "extreme"` —
  "it's fallen a lot" alone never reaches `WATCH` early, matching the
  FC26 finding that drawdown magnitude alone did not predict a good
  entry (tested: `TestPremiumBlockedDuringHypeAndEarlyCrash`).
- `WATCH` can only reach `BOTTOMING` via genuine deceleration.
- `BOTTOMING` reaches `INVESTMENT_WINDOW` once deceleration holds and
  live confirmation isn't actively negative.
- Any regime open longer than `regime_max_age_hours` (strategy-specific,
  e.g. 48h for premium) is force-exited regardless of other signals —
  prevents "still looks good" from turning into an indefinitely long
  hold (FC26: fresh entries were already poor ~2 days past the real
  bottom).

`INVESTABLE_STATES = (INVESTMENT_WINDOW, REBOUND)` — the only states in
which `runner.py` generates individual card candidates at all.

Only `PREMIUM_POST_CRASH_REBOUND` uses this state machine
(`USES_CRASH_TRIGGER = True`). `LAUNCH_STARTER_ACCUMULATION`
(`USES_CRASH_TRIGGER = False`) never imports `state_machine` — it uses
a separate, simpler elapsed-time eligibility check (see below) and is
orchestrated by `runner.run_launch_window_pass`, not
`runner.run_cohort_pass`.

## Strategies (plugin registry, `strategies/__init__.py`)

Each strategy module defines: `KEY`, `COHORT_KEY`, `USES_CRASH_TRIGGER`,
`DESCRIPTION`, `target_roi_pct(config)`, `max_hold_hours(config)`, plus
either `is_state_eligible_for_signals(state)` + `regime_max_age_hours
(config)` (crash-trigger strategies) or `is_launch_window_eligible
(hours_since_launch, config)` (elapsed-time strategies). Two are
implemented now:

### `PREMIUM_POST_CRASH_REBOUND`
Cohort: `premium_meta`. Uses the drawdown+deceleration crash trigger;
only signals while cohort state is `INVESTMENT_WINDOW`/`REBOUND`.
Target ROI 6% (`INVESTMENT_PREMIUM_TARGET_ROI_PCT`), max hold 72h
(`INVESTMENT_PREMIUM_MAX_HOLD_HOURS`), regime forced closed after 48h
(`INVESTMENT_PREMIUM_REGIME_MAX_AGE_HOURS`) — short, per FC26's finding
that 5-7+ day holds deteriorated substantially.

### `LAUNCH_STARTER_ACCUMULATION`
Cohort: `desirable_starter`. **Never** touches the crash trigger or
cohort regime state — eligible purely on elapsed time since launch
(`is_launch_window_eligible`, default 9 days,
`INVESTMENT_STARTER_LAUNCH_WINDOW_DAYS` — a translated, round default,
*not* FC26's literal 351-550 coin band, since that band doesn't
generalize across FC27's different price scale; the cohort assignment
itself is what does the translating). Target ROI 8%
(`INVESTMENT_STARTER_TARGET_ROI_PCT`), max hold 168h/7 days
(`INVESTMENT_STARTER_MAX_HOLD_HOURS`).

### Adding a future strategy
Not built now, but the shape is intentionally ready for: pre-promo
sell-off rebound, fodder accumulation, SBC rating investments, TOTW
requirement investments, promo panic sell-offs, reward-cycle
accumulation, Evo-driven demand. Each needs only: a new module in
`strategies/` implementing the interface above, registered in
`strategies/__init__.py`'s `STRATEGIES` dict. No change to `runner.py`,
`ledger.py`, or the API/frontend is required for a same-shape strategy;
a genuinely different eligibility concept (neither crash-trigger nor
elapsed-time) would need a third orchestration branch in
`runner.run_all_strategies`, following the same pattern as the
`USES_CRASH_TRIGGER` branch already there.

## FC26 prior vs FC27 reality

`runner._fc26_prior_weight(hours_since_launch, config)` implements the
required "prior fades as live evidence accumulates" behaviour as an
elapsed-time decay (mirrors `priority_config.py`'s
`demand_decay_bands_hours` pattern):

| Elapsed since launch | FC26 prior weight |
|---|---|
| ≤ 48h | 1.0 (FC27 has almost no history of its own) |
| ≤ 168h (1 week) | 0.6 (blended) |
| ≤ 336h (2 weeks) | 0.25 (FC27 evidence increasingly dominant) |
| beyond | 0.05 (`fc26_prior_minimal_weight` — established market) |

The resulting weight is stored per `investment_market_states` row
(`fc26_prior_weight`) and threaded into a signal's `risk_flags` (a
signal opened while the prior is still heavily weighted carries an
explicit `fc26_prior_still_heavily_weighted` flag) — never silently
absorbed into a score with no trace of how much history backed it.
`INVESTMENT_ENGINE_FC26_PRIOR_ENABLED=false` disables the prior
entirely (weight always 0.0), for testing FC27 evidence in isolation.

## Database (migrations 058–061)

- **`investment_cohort_memberships`** (058) — versioned, auditable
  cohort assignment. `UNIQUE(game_year, source_card_id,
  classifier_version)`.
- **`investment_market_states`** (059) — the engine's own bounded
  regime time series (drawdown/severity/momentum/deceleration/breadth/
  live confirmation/FC26 prior weight per pass), read back with a
  `LIMIT N` instead of re-deriving cohort history from raw price data
  every pass. Indexed on `(game_year, strategy_key, cohort_key,
  computed_at DESC)`.
- **`investment_signals`** (060) — the **Investment Ledger**. One
  lifecycle row per signal (not split snapshot/outcome tables):
  strategy, cohort, entry/max-buy/target, expected ROI/hold, confidence,
  liquidity, investability, rationale, risk flags, `shadow` flag,
  status, and (once closed) close price, outcome, after-tax profit/ROI,
  exit reason. `CREATE UNIQUE INDEX investment_signals_active_uniq ON
  investment_signals (source_card_id, strategy_key) WHERE status =
  'active'` makes every recompute idempotent — a card already carrying
  an open signal for a strategy is never double-opened.
- **`investment_engine_runs`** (061) — one row per recompute pass:
  strategy, cohort, previous/new state, eligible/candidate counts,
  signals created/closed/suppressed (+ reasons), duration, error.

All four follow the repo's existing migration conventions (`-- target:
core`, applied by `backend/scripts/run_migrations.py`, tracked in
`schema_migrations`).

## API (`app/routers/v2/investments.py`, mounted at `/api/v2`)

| Endpoint | Purpose |
|---|---|
| `GET /investments/strategies` | Static list of implemented strategies. |
| `GET /investments/market-state` | Latest per-(strategy, cohort) regime snapshot — powers the plain-language "CRASHING / BOTTOMING / INVESTMENT WINDOW OPEN" indicator. |
| `GET /investments/signals` | Active/closed published signals, filterable by strategy/cohort/status/min_confidence/budget. `include_shadow=true` requires admin (`require_admin`) — shadow-mode rows never leak to a normal caller. |
| `GET /investments/signals/{id}` | Signal detail. |
| `GET /investments/ledger` | Every **closed** signal, winners and losers alike — never filtered to favourable outcomes. |
| `GET /investments/summary` | Published track record per strategy (`ledger.track_record`), gated on a minimum sample size like the existing `/market/track-record`. |
| `GET /investments/budget-plan` | "I have N coins, where can I deploy it" — greedy allocation across active signals ordered by confidence, capped per card by a liquidity-tier multiple so one illiquid card never absorbs the whole budget. |

Ungated (no entitlement check), mirroring `futgg_market.py`'s
provider-neutral precedent — a future paid split can layer
`fair_value.py`'s `_teaser()` convention on top without changing this
router.

## Frontend

- **`frontend/src/v2/hooks/useInvestments.js`** — React Query hooks
  (`useInvestmentMarketState`, `useInvestmentSignals`,
  `useInvestmentLedger`, `useInvestmentSummary`,
  `useInvestmentBudgetPlan`), same defensive-fetch pattern as
  `useFutggMarket.js` (an unconfigured/shadow-only deployment renders a
  calm empty state, not an error).
- **`frontend/src/v2/pages/Investments/Investments.jsx`** +
  `investments.css` — new page: a market-cycle regime strip in plain
  language (no percentiles/severity scores shown), a budget input that
  filters/allocates active signals, and signal cards in the required
  format (player, "Buy ≤ X", "Target: Y", expected after-tax ROI,
  expected hold in days, Confidence High/Medium/Low, Supply
  High/Medium/Low, "Why now" bullets, "Risk" bullets).
- **Route**: `/v2/investments` in `App.v2.jsx`.
- **Nav**: new "Investments" item in `Sidebar.jsx`'s `ITEMS`, separate
  from "Opportunities"/"Trade Finder" so the distinction is visible in
  the UI, not just internal.
- The existing Opportunities page is untouched and unbroken — it
  remains the "Trades" surface; nothing here removes or reroutes it.

## Shadow mode / publish mode

Two independent flags, both default **off** — the engine is fully inert
until explicitly enabled:

- **`INVESTMENT_ENGINE_ENABLED`** (default `false`) — without this, the
  refresher loop is never registered in `main.py`'s `lifespan()` at all.
- **`INVESTMENT_ENGINE_PUBLISH_SIGNALS`** (default `false`) — with the
  engine enabled but this off, every opened signal is written with
  `shadow = true`. It is calculated, logged, and stored, but
  `GET /investments/signals` and `/ledger` (non-admin) never return it.
  This is the shadow-mode requirement: validate the engine's calls
  against FC27 reality before any user ever sees a recommendation.
  Admins can inspect shadow signals via `include_shadow=true`.

**Recommended path for FC27 launch**: set `INVESTMENT_ENGINE_ENABLED=true`
with `INVESTMENT_ENGINE_PUBLISH_SIGNALS` left `false` (shadow mode) for
at least the first several days of live data, then flip
`PUBLISH_SIGNALS` on once the shadow-mode signals have been checked
against what actually happened.

## Scheduling

Registered in `main.py`'s `lifespan()` exactly like the other refresher
loops (`fair_value`, `futgg_market_snapshot`, `priority_recompute`):
`asyncio.create_task(investment_engine_refresher_loop(pool, poll,
game_year=...))`, cancelled in the same `finally` cleanup block.

| Env var | Default | Purpose |
|---|---|---|
| `INVESTMENT_ENGINE_INTERVAL_SECONDS` | 300 | Recompute cadence — more responsive than FC26's daily backtest cadence since FC27 has intraday data, without polling every second. |
| `INVESTMENT_ENGINE_COHORT_REFRESH_INTERVAL_SECONDS` | 21600 | How often cohort membership is (re-)classified for uncovered cards. |
| `INVESTMENT_ENGINE_GAME_YEAR` | 27 | Which game year's cards this deployment's engine instance targets. |

## Tests (all pure, no DB — 77 passing)

```
backend/tests/test_investment_cohorts.py
backend/tests/test_investment_drawdown.py
backend/tests/test_investment_momentum.py
backend/tests/test_investment_state_machine.py
backend/tests/test_investment_ranking.py
backend/tests/test_investment_strategies.py
backend/tests/test_investment_no_leakage.py
backend/tests/test_investment_backtest.py
```

Coverage includes: cohort percentile classification (position-relative,
never cross-position), dynamic severity (no absolute-number hard gate),
deceleration detection, full state-machine transition table (including
"cannot skip crash→window" and "cannot stay open past max age"),
strategy eligibility for both strategies (including that the starter
strategy never references `state_machine`/`INVESTABLE_STATES`), and a
dedicated no-future-leakage test that mechanically proves appending
future days to a price series never changes an already-computed
historical state.

Run: `python3 -m pytest backend/tests/test_investment_*.py -q`

**Not run in this session**: the pre-existing full backend suite
(blocked on `fastapi`/`asyncpg` not being installed in this environment
— confirmed via `ModuleNotFoundError` on collection) and any DB-backed
integration test. `main.py` and `investments.py` were syntax-checked via
`ast.parse()` rather than imported/executed. This should be re-verified
in a real deployment environment before relying on it.

## Performance

- `runner.py` never scans `futgg_bin_history`/`futgg_sales_history`
  across the whole catalogue: cohort card-ID lists are bounded
  (`investment_cohort_memberships`, indexed), current prices come from
  the already-optimized `futgg_market_snapshot` materialized view
  scoped with `= ANY($1::bigint[])`, and live-confirmation queries are
  further bounded to a 50-card sample with `LIMIT 500` on top.
- Drawdown/momentum history is read back from this engine's *own*
  `investment_market_states` time series (`LIMIT 30`), not re-derived
  from raw price history every pass.
- `EXPLAIN` was not run against a live database in this session (no DB
  access available) — the query shapes follow the same
  scoped/indexed/bounded pattern as `futgg_market.py`'s existing
  queries, but should be verified with `EXPLAIN ANALYZE` once deployed
  against production-scale data.

## Deployment

1. Run migrations 058–061 via the existing
   `backend/scripts/run_migrations.py` (advisory-locked, idempotent).
2. Set `INVESTMENT_ENGINE_ENABLED=true` (leave `PUBLISH_SIGNALS` unset/
   `false` for shadow mode).
3. Set `INVESTMENT_ENGINE_GAME_YEAR=27` once FC27 data is flowing (the
   engine reads `futgg_players`/`futgg_market_snapshot`, which must
   already be populated for FC27 by the existing sync jobs).
4. Deploy backend — the refresher loop starts automatically 30s after
   boot, then every `INVESTMENT_ENGINE_INTERVAL_SECONDS`.
5. Deploy frontend — `/v2/investments` and the "Investments" nav item
   ship automatically; with the engine in shadow mode the page renders
   its calm "no investment-cycle signal available" empty state until
   signals are published.
6. Watch `investment_engine_runs` and application logs (format:
   `investment_engine strategy=... cohort=... STATE -> STATE (reason)
   drawdown=...% severity=... decel=... eligible=N candidates=N
   signals_created=N signals_closed=N duration_ms=N`) for the first
   several FC27 launch days before flipping `PUBLISH_SIGNALS=true`.

## Known limitations

- **Not validated against live FC27 data.** Every strategy encodes an
  FC26-derived prior; none of it has been checked against FC27 reality
  yet — that is exactly what shadow mode is for.
- **`listings_near_floor` / `supply_depth_shrinking` are always
  `None`.** No order-book/listing-depth table exists in the current
  schema, so these fields are honest placeholders, not estimates.
  Investability currently falls back to sales-count/liquidity-tier
  heuristics only.
- **No integration/DB-level test coverage.** All pure-function tests
  (101 as of the FC26 replay addition) exercise the engine's decision
  logic thoroughly, but nothing has exercised `runner.py`'s actual SQL,
  `ledger.py`'s DB writes, or `investments.py` against a real database
  or a running FastAPI process in this session. See
  `backend/INVESTMENT_ENGINE_REPLAY.md` for a chronological, production-
  logic replay of FC26's launch (currently run against a synthetic
  stand-in dataset, since no live database was reachable in that
  session either) — the closest validation this repo has to an
  end-to-end check today, but still not a DB-level integration test.
- **Only two strategies implemented.** The plugin registry is ready for
  more, but promo/SBC/TOTW/Evo-driven strategies are explicitly out of
  scope for this pass.
- **Budget planner is intentionally simple** — a greedy,
  confidence-ordered, liquidity-capped allocator, not a portfolio
  optimizer. Sufficient for "where can I deploy N coins" without
  overbuilding for a first pass, per product guidance.
- **Track record / summary requires a minimum sample size** before it
  reports anything meaningful — early in shadow mode, `/investments/
  summary` will legitimately have little or nothing to show.

## Recommendation

The Investment Engine is technically ready to run in **shadow mode**
for the FC27 launch: it is additive (no existing engine/table/endpoint
was modified in a way that changes its behaviour), gated behind two
independent flags both defaulting off, and its core logic is unit
tested for the specific safety properties the product spec called
out (no premature crash-to-window transitions, no accidental long
holds, no crash-trigger leakage into the starter strategy, no future
leakage in the historical walk) - see
`backend/INVESTMENT_ENGINE_REPLAY.md` for a full chronological replay
of FC26's launch through this exact code, which reached the same
"READY FOR FC27 SHADOW MODE" verdict independently.

**This is not a claim that the strategies are profitable on FC27.** The
FC26 evidence supports the shape of both strategies (deep-drawdown-plus-
deceleration for premium/meta; early-entry-and-hold for desirable
starters), but FC26 is one prior cycle, not proof the same pattern
repeats. The entire purpose of shadow mode is to let FC27's own live
data confirm or contradict that prior before any signal reaches a real
user — `PUBLISH_SIGNALS` should stay off until that comparison has
actually been made.
