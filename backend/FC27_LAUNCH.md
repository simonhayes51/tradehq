# FC27 Launch Runbook

Operational guide for taking EAFC.Live through the FC27 launch. Companion
to `INVESTMENT_ENGINE.md` (architecture) and `INVESTMENT_ENGINE_REPLAY.md`
(FC26 validation evidence).

---

## 1. The two products — keep them distinct

| | **Trades** (Opportunities / Trade Finder) | **Investments** |
|---|---|---|
| Question | Can I make coins from this card *right now*? | Is this a good time to buy this market and *hold*? |
| Caused by | Mispriced listings, snipes, BIN discrepancies | Accumulation, sell-offs, repricing, post-crash rebounds |
| Horizon | Minutes to hours | 1–7 days |
| Unit | One listing | A cohort / market segment |
| FC27 launch status | **Live and public** | **Shadow mode — no public card-level calls** |

They are separate tables, separate engines, separate API routers, and
separate frontend pages. Investment signals are never merged into
Opportunities.

---

## 2. Game cycle — the single source of truth

`app/services/game_cycle.py` decides which game year is active. Before
it, `FUTGG_GAME_YEAR` (default `26`) and `INVESTMENT_ENGINE_GAME_YEAR`
(default `27`) disagreed, which would have had ingestion writing FC26
rows while the Investment Engine queried FC27 cohorts — producing no
error at all, just an engine that silently found nothing.

**Set `EAFC_GAME_YEAR=27` and leave the legacy per-service variables
unset.** If either legacy variable is set and disagrees, the mismatch is
logged as an ERROR at startup and reported by `/api/ops/launch-health`.

| Variable | Purpose | Launch value |
|---|---|---|
| `EAFC_GAME_YEAR` | The active game year. Everything derives from this. | `27` |
| `EAFC_WEB_APP_LAUNCH_DATE` | `YYYY-MM-DD`. Enables phase labelling + FC26-prior decay. | set when known |
| `EAFC_EARLY_ACCESS_DATE` | `YYYY-MM-DD` | set when known |
| `EAFC_FULL_RELEASE_DATE` | `YYYY-MM-DD` | set when known |
| `FUTGG_GAME_YEAR` | **Legacy** ingestion override. | leave unset |
| `INVESTMENT_ENGINE_GAME_YEAR` | **Legacy** engine override. | leave unset |

Launch phases (`PRE_LAUNCH → WEB_APP → EARLY_ACCESS → FULL_RELEASE →
ESTABLISHED`) are **descriptive labels only**. No investment decision is
ever gated on a phase — eligibility comes from live market evidence, never
from "the calendar says day 9". This is pinned by a test
(`test_phase_is_never_used_to_gate_an_investment_decision`).

An unset launch date yields phase `UNKNOWN` and `hours_since_launch =
None` — never a guessed `0`.

---

## 3. FC27 bootstrap — how the new cycle starts safely

Everything Investment-Engine-related is keyed by `game_year`:

- `investment_cohort_memberships` — `UNIQUE(game_year, source_card_id, classifier_version)`
- `investment_market_states` — indexed `(game_year, strategy_key, cohort_key, computed_at DESC)`
- `investment_signals` — `game_year` column
- `investment_engine_runs`, `investment_decision_log` — `game_year` column

So switching `EAFC_GAME_YEAR` to `27` starts genuinely fresh: no FC27
cohort memberships exist, no FC27 market states exist, so running peaks,
drawdown history, momentum history and regime state all begin empty.
**FC26 rows are never read by an FC27 pass** — they remain queryable only
as historical research/reference.

### Insufficient data is a valid state

`app/services/investment_engine/readiness.py` runs *before* any market
judgement and returns a **named** suppression reason:

| Reason | Meaning |
|---|---|
| `INSUFFICIENT_CARD_COVERAGE` | Too few cards discovered/priced in this cohort |
| `INSUFFICIENT_PRICE_HISTORY` | Not enough of this cohort's own observations to have a drawdown that means anything |
| `INSUFFICIENT_SALES_DATA` | Sales evidence required by this strategy is absent |
| `STALE_MARKET_DATA` | Too small a fraction of prices are fresh |
| `COHORT_UNSTABLE` | Cohort still settling |
| `LIVE_CONFIRMATION_UNAVAILABLE` | Waiting on live BIN/sales confirmation |
| `COHORT_STILL_CRASHING` | Prices still falling — deliberately not buying |
| `REGIME_NOT_INVESTABLE` | No window open |
| `LAUNCH_WINDOW_CLOSED` | Early accumulation window has passed |
| `PUBLICATION_DISABLED` | Calculated, not published |

Reasons are persisted on `investment_engine_runs`, surfaced by
`/api/ops/launch-health` and `/api/v2/investments/shadow-report`, and each
has plain-language copy in `REASON_COPY` (no percentile/z-score jargon —
pinned by test).

Crucially, **missing is not zero**: `cards_with_recent_sales=None` ("the
sales pipeline hasn't reported") is kept distinct from `0` ("measured,
genuinely none"), so a pipeline gap never reads as absence of demand.

| Readiness tunable | Default |
|---|---|
| `INVESTMENT_MIN_COHORT_SIZE` | `20` |
| `INVESTMENT_MIN_FRESH_PRICE_FRACTION` | `0.60` |
| `INVESTMENT_MIN_MARKET_STATE_OBS` | `6` |
| `INVESTMENT_MIN_CARDS_WITH_SALES` | `10` |

---

## 4. Shadow mode & the publication gate

Three independent states:

```
calculate   INVESTMENT_ENGINE_ENABLED=true
shadow      enabled + INVESTMENT_ENGINE_PUBLISH_SIGNALS unset/false   <-- FC27 LAUNCH
publish     enabled + INVESTMENT_ENGINE_PUBLISH_SIGNALS=true
```

- Both flags **default off** — an unconfigured deploy is inert.
- Publication requires an explicit affirmative (`1/true/yes/on`). Any
  typo, blank, or ambiguous value fails **closed** to shadow (pinned by
  a parametrised test).
- Every signal is written with `shadow = not publish_signals`; no call
  site may hardcode `shadow=False` (pinned structurally by test).
- `GET /investments/signals`, `/ledger` and `/budget-plan` filter
  `shadow = FALSE` unless the caller passes `include_shadow=true` **and**
  clears `require_admin`.

In shadow mode the engine still: classifies cohorts, tracks market
states, generates internal candidate signals, records them, records
suppressed decisions, and grades outcomes later. Users see **zero**
card-level investment calls.

**Market-state intelligence is public by design** —
`GET /api/v2/investments/market-state` returns a regime label per cohort
and never a card list (pinned by test). That is what powers the public
"Premium Gold Market: WATCHING" indicator from day one.

---

## 5. Opportunity Engine — early-market safety

**Already present before this work** (documented, not changed):

- `trend_states_blocking_buy = ("falling_knife", "downtrend")` — a buy
  signal is **blocked outright** in either state, regardless of how large
  the apparent discount is. This is the primary falling-knife guard.
- `trend_states_capping_signal = ("stabilising", "insufficient_trend_data")`
  — caps at `buy`, never `strong_buy`.
- `min_sales_for_signal = 5`, per-tier price-staleness limits,
  dispersion and interdecile gates.

**The genuine hole found and fixed:** a *cap* is not a *block*. The trend
layer needs 6 observations over 90 minutes, so in a new cycle's opening
hours **every** card is `insufficient_trend_data`. A card repricing
downward fast then shows a large gap against its backward-looking sales
median, with no trend evidence available to contradict it — and would be
published as a `buy`.

`futgg_intelligence.py` now declines to guess in exactly that case
(reason code `unverifiable_discount`, signal becomes `watch`):

> no trend reading **AND** an implausibly large discount **AND** no
> well-established price level to be discounted *from*.

The third clause is what keeps the guard from eating the core snipe
product: a card with 30 tightly-clustered recent sales *has* an
established level, so a BIN far below it remains a genuine mispricing and
is still a buy. Only a thin/scattered sales picture — the normal
new-cycle condition — makes a large discount unverifiable.

Deliberately **general**: no dates, no game year, no launch-phase check,
no threshold borrowed from FC26 (pinned by test). It is a statement about
evidence, true in any market at any time; it simply binds hardest during
a launch.

| Variable | Default | Purpose |
|---|---|---|
| `FUTGG_UNVERIFIABLE_DISCOUNT_GUARD` | `true` | Kill switch |
| `FUTGG_UNVERIFIABLE_DISCOUNT_RATIO` | `0.25` | Discount size that needs confirming |
| `FUTGG_UNVERIFIABLE_MIN_SALES_FOR_LEVEL` | `20` | Sales needed to call a level established |
| `FUTGG_UNVERIFIABLE_MAX_DISPERSION_FOR_LEVEL` | `0.15` | Max dispersion for an established level |

---

## 5b. Live confirmation — what is actually measured

`live_confirmation.py` reports one of four **named** states, never a bare
bool. A tri-state boolean cannot distinguish "the sales data says selling
pressure has not eased" from "we do not have enough sales data to know",
and treating the second as the first would penalise a card's confidence
for a gap in *our* pipeline rather than for anything the market did.

| State | Meaning |
|---|---|
| `confirmed` | Live data positively supports the historical signal |
| `conflicting` | Live data actively contradicts it (a real, measured no) |
| `insufficient` | Some data present, not enough to judge |
| `unavailable` | The input does not exist at all |

**Computed today** (from `futgg_bin_history` / `futgg_sales_history`):
lowest BIN, whether lowest BIN is still making new lows, recent sales
count, median recent sale price, whether sales are clearing higher.

**Not computed** (no source data exists — never fabricated):
listing count/depth, listings near floor, listing consumption, spread.
These return `None` and would only change if a scraper starts capturing
genuine listing depth.

In the state machine, `unavailable`/`insufficient` are **neutral, not
blocking** — live confirmation is additive evidence on top of the
historical regime signal, never a hard requirement that signal alone
cannot satisfy. Only a measured `conflicting` holds the regime at
`BOTTOMING`. Both behaviours are pinned by tests.

---

## 6. FC27 learning dataset

`investment_decision_log` (migration 062) records **every decision, both
recommended and suppressed**, with outcomes graded on later.

Captured at decision time: cohort, card drawdown/momentum/relative
strength, liquidity, investability, confidence, market state, severity,
market drawdown, cohort price index, reason, shadow flag, engine version.

Graded later (`decision_log.grade_pending_outcomes`): price at +6h, +12h,
+24h, +48h, +72h; after-tax ROI at 24h/72h via `trading_math`; max
adverse/favourable excursion. A NULL outcome means **not yet known**,
never "no change".

**Why suppressed decisions matter most:** a card the engine refused
during `CRASHING` that then fell another 22% is evidence the engine
worked — and nothing in the schema before this table could reconstruct
it.

**Write-volume discipline:** decisions are fingerprinted (decision +
reason + market state + a log-spaced 5% price band). An unchanged
fingerprint is a no-op via a partial unique index, and a genuine change
supersedes the previous row. A card whose situation is unchanged for six
hours produces **one** row, not seventy. Suppressed logging is capped per
pass at the same limit a recommending pass uses.

---

## 7. Launch health & shadow diagnostics

**`GET /api/ops/launch-health`** (admin) — the one-screen launch-morning
answer: game cycle + consistency check, cards discovered/priced/fresh,
price/sales/BIN pipeline status, Opportunity Engine status + active
count, Investment Engine mode (`DISABLED`/`SHADOW`/`PUBLISHING`), per-cohort
market states, shadow vs published signal counts, suppression histogram.

Checks that cannot be performed return the literal string `unknown` —
never a fabricated green tick. Each check is individually fault-tolerant:
a missing table degrades that check only.

**`GET /api/v2/investments/shadow-report`** (admin) — how shadow decisions
are actually performing: median/avg after-tax ROI at 24h and 72h, grouped
by strategy, cohort, decision, reason, and market state at entry, plus
signal-level outcome aggregates.

`GET /api/ops/freshness` (unchanged) remains the uptime-monitor surface.

---

## 8. Environment variables — recommended FC27 launch values

```bash
# ---- Game cycle (single source of truth) ----
EAFC_GAME_YEAR=27
EAFC_WEB_APP_LAUNCH_DATE=          # YYYY-MM-DD once announced
EAFC_EARLY_ACCESS_DATE=
EAFC_FULL_RELEASE_DATE=
# Leave FUTGG_GAME_YEAR and INVESTMENT_ENGINE_GAME_YEAR UNSET.

# ---- Investment Engine: SHADOW MODE ----
INVESTMENT_ENGINE_ENABLED=true
INVESTMENT_ENGINE_PUBLISH_SIGNALS=false     # <-- the launch-critical line
INVESTMENT_ENGINE_FC26_PRIOR_ENABLED=true
INVESTMENT_ENGINE_INTERVAL_SECONDS=300

# ---- Opportunity Engine launch safety (defaults are correct) ----
FUTGG_UNVERIFIABLE_DISCOUNT_GUARD=true
```

### Kill switches

| To disable | Set |
|---|---|
| Investment calculations entirely | `INVESTMENT_ENGINE_ENABLED=false` |
| Investment publication only | `INVESTMENT_ENGINE_PUBLISH_SIGNALS=false` |
| Unverifiable-discount guard | `FUTGG_UNVERIFIABLE_DISCOUNT_GUARD=false` |
| Discord notifications | `DISCORD_RECONCILE_INTERVAL_SECONDS=0` (see discord_integration.py) |

---

## 9. Deployment steps

1. Apply migrations: `python -m scripts.run_migrations` (adds 062).
2. Set the environment variables in section 8.
3. Deploy backend. Confirm in logs:
   - `✅ Investment Engine started (… phase=… mode=SHADOW)`
   - **no** `⚠️ GAME YEAR MISCONFIGURATION` lines
   - **no** `🔔 Investment Engine is PUBLISHING` line
4. Deploy frontend.
5. Hit `GET /api/ops/launch-health` as an admin and confirm
   `investment_engine.mode == "SHADOW"` and
   `game_cycle.consistency.consistent == true`.

### Manual checks after deploy

- There is no longer an Investments page to check — it was removed from
  the product surface precisely because shadow mode means it can never
  show a customer a signal (see INVESTMENT_ENGINE.md). `/investments`
  redirects to `/opportunities`.
- `/opportunities` still lists trades normally.
- A signed-out visitor cannot see any card-level investment call.
- A non-admin hitting `/api/v2/investments/signals?include_shadow=true`
  gets 401/403, not data.
- Discord role sync still reconciles.
- Mobile: Opportunities, Player detail.

---

## 10. Launch plan

**Before the Web App opens**
- Migrations applied; env vars set; shadow mode confirmed via launch-health.
- Set the three launch dates once EA announces them.
- Confirm `EAFC_GAME_YEAR=27` and legacy overrides unset.

**First hour**
- Watch `/api/ops/launch-health`: cards discovered climbing, price
  pipeline `HEALTHY`.
- Expect Investment cohorts to report readiness suppression
  (`INSUFFICIENT_CARD_COVERAGE` / `INSUFFICIENT_PRICE_HISTORY`). **This is
  correct**, not a fault.
- Watch Opportunities for `unverifiable_discount` watches on
  fast-repricing cards — the guard doing its job.

**First 24 hours**
- Cohorts should classify as coverage passes `min_cohort_size`.
- Market states begin populating; expect `MARKET_FORMING`/`NORMAL`, then
  possibly `OVERHEATED`.
- Sanity-check a few Opportunities by hand against fut.gg.

**Early Access**
- Compare FC27's shape against the FC26 prior — *without* assuming it
  repeats.
- Review `/api/v2/investments/shadow-report`: are suppressions being
  vindicated (suppressed cards falling further)?

**Full Release / crash watch**
- Expect `OVERHEATED → CRASHING → WATCH → BOTTOMING`.
- **Do not publish at `BOTTOMING`.** The FC26 replay showed the first
  apparent bottom was followed by another decline: deceleration means the
  crash may be slowing, not that sellers are finished.

**When to consider enabling public Investments**
Only when *all* of:
- shadow signals have accumulated a meaningful sample across at least one
  full crash/rebound cycle;
- shadow outcomes at 24h/72h are genuinely positive, not marginal;
- suppression decisions look vindicated in the shadow report;
- live confirmation (BIN/sales) is measurably contributing, not
  `unavailable`;
- you flip `INVESTMENT_ENGINE_PUBLISH_SIGNALS=true` **manually and
  deliberately**. Nothing enables it automatically, and nothing should
  enable it on elapsed time alone.

---

## 11. Known limitations

- **Investment strategies are unvalidated on FC27.** The FC26 replay
  validated architecture and safety, not profitability — and its own
  outcome numbers were mixed-to-negative. Shadow mode exists precisely
  because this is unresolved.
- **Live confirmation is partial.** `listings_near_floor` and
  `supply_depth_shrinking` are always `None` — no order-book/listing-depth
  table exists. Lowest-BIN movement, sales count/velocity and sale-price
  trend are computed; depth is not.
- **No DB-level integration tests.** All 692 passing tests are
  pure/unit/router-level. Nothing exercises the engine against a real
  Postgres in CI (no CI pipeline exists in this repo).
- **Cohort membership is frozen per `classifier_version`.** Over a long
  season, cohort composition drifts as new cards join. Fine for a launch
  window; a full-season analysis should account for it.
- **`max_adverse_pct`/`max_favourable_pct` columns exist but are not yet
  populated** — they need an intra-window price scan the current grader
  does not perform.
- The FC26 replay's two open questions — the premium "fakeout bottom" and
  the starter strategy's weak ranking signal — are **carried forward
  unresolved by design**, to be answered with FC27 data rather than by
  tuning against FC26.
