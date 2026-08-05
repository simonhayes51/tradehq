# Adaptive Priority Engine: Production Rollout (Milestones 1 & 1.5)

Covers rule-based shadow classification (Milestone 1) and conservative
cost-control demotion (Milestone 1.5) - see
`/root/.claude/plans/binary-meandering-raccoon.md` (or your copy of the
same plan doc) for full design context. This document is the exact,
step-by-step Coolify procedure: shadow-mode deploy, conservative-demotion
enablement, and rollback. Do not "monitor closely" without a specific
thing to look at - every step below names the exact metric, endpoint, or
SQL query to check.

**There is no separate Coolify resource for this feature.** Unlike
`auto_sync`'s four workers (see `auto_sync/COOLIFY.md`), the priority
engine runs **inside the existing `backend` FastAPI process** - migrations
044/045/046 apply automatically via `run_on_boot()` on backend startup
(`main.py`), and the classification/demotion loop
(`priority_recompute.refresher_loop`) starts automatically as an
`asyncio.create_task` in the same `main.py` lifespan, polling every
`PRIORITY_RECOMPUTE_INTERVAL_SECONDS` (default 900s/15min). **Deploying
this feature is deploying `backend` normally - there is no additional
resource to create, no separate cron to configure.**

---

## Part 1: Shadow-mode deploy (Milestone 1 - classification only, no writes)

Shadow mode is simply the default state: `PRIORITY_ENGINE_ALLOW_CONSERVATIVE_DEMOTION`
defaults to `false`, so deploying `backend` with no new env vars set at
all already gives you Milestone 1 in shadow mode - classification runs
and is fully observable, `next_sales_due_at` is never touched.

### 1. Migrations

Nothing to run by hand. `run_on_boot()` (`scripts/run_migrations.py`)
applies `044_card_priority_v2.sql`, `045_sbc_requirement_filters_v2.sql`,
and `046_priority_trigger_confirmation.sql` automatically against
`DATABASE_URL` (the `core` target) the moment the new backend image
starts, before it starts serving traffic. If you want to apply and verify
them by hand first (e.g. against a copy of the database, or just to see
the exact DDL before a live deploy):

```bash
psql "$DATABASE_URL" -f backend/migrations/044_card_priority_v2.sql
psql "$DATABASE_URL" -f backend/migrations/045_sbc_requirement_filters_v2.sql
psql "$DATABASE_URL" -f backend/migrations/046_priority_trigger_confirmation.sql
```

044 requires `futgg_players` to already exist (`requires-table:
futgg_players` in its header) - true on any live deployment, since
PriceSync/PlayerSync have already created it. 045 has a cross-target
dependency on 018's `sbc_challenges` table (created under the `player`
target in a multi-DB setup, but the same physical table in this repo's
current single-DB deployment) - this is safe via the same
requires-table skip-and-retry mechanism migration 038 already
established; if `sbc_challenges` isn't there yet, 045 cleanly no-ops and
retries on the next boot rather than failing it.

### 2. Deploy `backend` - no new env vars required

Deploy normally (whatever your existing Coolify `backend` resource
process is). No env var changes are required for shadow mode - every
Milestone 1/1.5 env var has a production-safe default (`PRIORITY_ENGINE_ALLOW_CONSERVATIVE_DEMOTION=false`).
Optional tuning (all have sane defaults, only set these if you have a
specific reason to):

| Env var | Default | Purpose |
|---|---|---|
| `PRIORITY_RECOMPUTE_INTERVAL_SECONDS` | `900` (15min) | Classification pass cadence |
| `PRIORITY_TRIGGER_CONFIRMATION_GRACE_HOURS` | `72` | How long an SBC trigger survives without re-confirmation before it grace-expires (migration 046) |
| `PRIORITY_EVENT_MATCH_FLOOR_TIER` | `HIGH` | Tier floor an active SBC match forces |
| `PRIORITY_WATCHLIST_WINDOW_DAYS` | `90` | Recent-window for watchlist popularity |
| `PRIORITY_RULE_*` | see `priority_config.py` | Rule-table thresholds for baseline_tier |

### 3. Confirm it's running

Watch the backend's own boot log for:
```
✅ Adaptive priority engine started (poll every 900s)
```
Then, ~30s after boot (the loop's own startup delay) plus one full poll
interval, confirm a real pass completed:

```sql
SELECT * FROM pipeline_heartbeats WHERE worker = 'priority_recompute';
```
`ok` should be `true`, `last_run_at` recent, and `detail` (JSON) should
show `cards_failed: 0` (or a small number - see acceptance thresholds
below) and a populated `classification.baseline_tier_counts`.

Also check the backend logs for the one-line-per-run structured summary
(Step 4/observability, not one log line per card):
```
priority_recompute run summary: {"engine_version": "priority-2.0", "duration_seconds": 4.2, "conservative_demotion_enabled": false, "classification": {...}, "conservative_demotion": {...}, "cards_processed": 9432, "cards_failed": 0}
```

### 4. Run the validation report

Read-only, no writes - safe to run anytime, including against production,
including while the app is live:

```bash
cd backend
DATABASE_URL=postgresql://... python scripts/priority_validation_report.py
# or, for scripting/alerting:
DATABASE_URL=postgresql://... python scripts/priority_validation_report.py --json
```

**Example (abbreviated) output:**
```
=== Tier distribution ===
Total active cards: 9,432   Classified: 9,432 (100.0%)   Missing: 0
  CRITICAL   baseline=  41 (0.4%)  effective=  58 (0.6%)
  HIGH       baseline= 512 (5.4%)  effective= 561 (5.9%)
  NORMAL     baseline=1,890 (20.0%)  effective=1,890 (20.0%)
  LOW        baseline=2,340 (24.8%)  effective=2,323 (24.6%)
  DORMANT    baseline=4,649 (49.3%)  effective=4,600 (48.8%)
baseline != effective: 49 cards   cards with an active trigger: 49

=== Trigger health ===
active by type: {'sbc_match': 49}
expired-still-present: 0   missing-expiry-non-manual: 0   orphaned: 0
effective-tier-understates-forced-tier mismatches: 0
...

=== Conservative-demotion projection (as if enabled) ===
eligible_count: 3,812 (of 4,600 DORMANT-effective cards)
current_avg_interval_minutes (estimate): 2,880.0
proposed_interval_minutes: 4,320
requests/day before (estimate): 1,905.0
requests/day after (estimate): 1,270.7
projected reduction: 634.3 requests/day (33.3%)
excluded_by_reason: {'active_trigger': 49, 'premium_card_type': 210, 'recent_meaningful_sales': 340, 'user_interest_above_threshold': 88, 'newly_released': 101}

=== Scheduling health ===
overdue by tier: {'CRITICAL': 0, 'HIGH': 2, 'NORMAL': 11, 'LOW': 40, 'DORMANT': 190}
oldest successful scrape by tier: {'CRITICAL': '2026-08-05T11:40:00Z', ...}
beyond discovery ceiling: 0   negative due gap: 0   no due timestamp: 12

=== Audit health ===
1h: promotions=2 demotions=1 schedule_writes=0 total=6
24h: promotions=18 demotions=9 schedule_writes=0 total=61
7d: promotions=110 demotions=54 schedule_writes=0 total=340
events with invalid/missing tier: 0   missing reason codes: 0   missing engine version: 0
```

### 5. What to watch for the first 24-48 hours of shadow mode

Check these specific things - not "monitor closely":

- **`pipeline_heartbeats.ok = true` on every check.** If `false`, read
  `detail->cards_failed` and the backend logs for `compute_priority
  failed for card_id=...` warnings.
- **Tier distribution looks like a real trading platform's card
  population**: DORMANT should be the largest bucket (bulk of the
  catalogue is genuinely low-liquidity tail cards), CRITICAL/HIGH should
  be a small minority (liquid/premium/event cards only). A healthy
  starting distribution is roughly: CRITICAL <2%, HIGH 3-10%, NORMAL
  15-30%, LOW 20-30%, DORMANT 35-55% - exact numbers depend on your
  catalogue's real liquidity shape, but DORMANT should never be near-zero
  (that would mean almost everything is scoring as active, which is
  itself a red flag worth investigating) and CRITICAL should never be
  more than a handful of percent (that tier is meant to be rare).
- **`baseline_tier != effective_tier` count stays proportional to
  currently-active SBCs/events**, not a large fraction of the catalogue -
  a spike here with no corresponding real SBC event live is a sign the
  matching logic is over-triggering.
- **Trigger health's `orphaned`/`expired-still-present`/
  `effective-tier-understates-forced-tier` counts are 0** on every run -
  any of these is a real bug signal, not noise.
- **Classification integrity's `DORMANT-effective-despite-trigger`,
  `never-scraped-ever-delayed`, and `new-cards-actually-demoted` are all
  0** - these are should-always-be-zero invariant checks; nonzero is a
  stop-and-investigate signal, not something to average out.
- **`check_classification_invariance`'s `as_of_dependent_mismatches` is
  0** - confirms the no-freshness-input invariant empirically on live
  data, not just in code review.

### 6. Useful SQL for inspecting specific cards/reason codes

```sql
-- A specific card's current classification and why:
SELECT card_id, baseline_tier, effective_tier, reason_codes, computed_at
FROM card_priority WHERE card_id = 84131117;

-- Full history of tier changes for one card:
SELECT occurred_at, previous_baseline_tier, new_baseline_tier,
       previous_effective_tier, new_effective_tier, reason, reason_codes
FROM priority_engine_events
WHERE card_id = 84131117
ORDER BY occurred_at DESC;

-- Which SBC/trigger is elevating a card's effective_tier:
SELECT t.*, e.title FROM priority_triggers t
LEFT JOIN market_events e ON e.id = t.source_event_id
WHERE t.card_id = 84131117 AND (t.expires_at IS NULL OR t.expires_at > now());

-- Distribution snapshot right now:
SELECT baseline_tier, effective_tier, count(*)
FROM card_priority GROUP BY 1, 2 ORDER BY 1, 2;
```

---

## Part 2: Enabling conservative demotion (Milestone 1.5)

Only do this after shadow mode has run cleanly (all Part 1 checks green)
for at least the 24-48h window above, and the validation report's
"Conservative-demotion projection" section shows a sane, expected
eligible population.

### 1. The exact flag

```
PRIORITY_ENGINE_ALLOW_CONSERVATIVE_DEMOTION=true
```
Set on the `backend` Coolify resource's environment. **Requires a
restart** of the `backend` process to take effect - `PriorityEngineConfig`
is a frozen dataclass constructed once at import time
(`PRIORITY_ENGINE_CONFIG = PriorityEngineConfig()` in
`priority_config.py`), it does not re-read the environment on each pass.

### 2. Recommended initial interval

Leave `PRIORITY_CONSERVATIVE_DEMOTION_MAX_INTERVAL_MIN` at its default
(`4320` = 72h) for the first enablement - see the config's own comment
for why this specific value was chosen (a deliberately modest step past
`auto_sync`'s existing `FUTGG_SALES_UNDER_75_INTERVAL_MIN` floor of
2880min/48h, not the full DORMANT interval). Do not raise it on day one;
revisit only after confirming the mechanism behaves as expected at this
conservative setting.

Other tunables, also fine to leave at default initially:
`PRIORITY_CONSERVATIVE_DEMOTION_MAX_CARD_TYPE_SCORE` (`0.3`),
`PRIORITY_CONSERVATIVE_DEMOTION_WATCHER_MAX` (`1`),
`PRIORITY_CONSERVATIVE_DEMOTION_MIN_CARD_AGE_DAYS` (`14`).

### 3. Confirm writes are happening

```sql
SELECT count(*) FROM priority_engine_events
WHERE reason = 'conservative_demotion' AND occurred_at > now() - interval '1 hour';
```
Should be nonzero within the first couple of poll cycles after restart
(assuming the validation report's projection showed a nonzero eligible
count). Also check the heartbeat detail:
```sql
SELECT detail::json -> 'conservative_demotion' FROM pipeline_heartbeats WHERE worker = 'priority_recompute';
```
Look for `"enabled": true` and a nonzero `"delayed"` count.

### 4. Measure the actual request-volume reduction

This is the metric the milestone exists to produce. Two ways to see it:

- **Directly on SalesSync** (`auto_sync/futgg_sales_sync.py`'s own
  request-volume/429-rate logs) - watch the trend over several days after
  enabling. This is the real, ground-truth signal.
- **Via the priority-metrics endpoint or validation report**, comparing
  the "projected" estimate (computed identically whether the flag is on
  or off) against the actual `delayed`/`eligible_count` numbers now being
  written for real - `GET /api/v2/market/priority-metrics` (admin-only)
  returns `conservative_demotion_eligible_count`,
  `projected_requests_per_day_before_estimate`,
  `projected_requests_per_day_after_estimate`, and
  `projected_request_reduction_pct_estimate`.

### 5. Verify never-scraped protection held under real production data

```sql
-- Should always return 0 rows - a never-scraped card must never be
-- pushed later by this mechanism (is_eligible_for_conservative_demotion's
-- own hard gate on next_sales_due_at/last_sales_status_at IS NOT NULL).
SELECT p.source_card_id FROM futgg_players p
JOIN priority_engine_events e ON e.card_id = p.source_card_id
WHERE e.reason = 'conservative_demotion'
  AND (p.next_sales_due_at IS NULL OR p.last_sales_status_at IS NULL);
```

### 6. Confirm trigger pull-forward (exclusion) behavior

A card that gains an active trigger (e.g. a new SBC match) after being
demoted should stop appearing as a demotion candidate on the next pass -
it does **not** get its `next_sales_due_at` pulled back in (that's a
later, not-yet-built milestone), but it does stop being pushed further:
```sql
-- Cards currently DORMANT-effective with a large next_sales_due_at AND
-- an active trigger - these must be absent from any NEW conservative_
-- demotion event after the trigger activated.
SELECT p.source_card_id, p.next_sales_due_at, t.trigger_type, t.activated_at
FROM futgg_players p
JOIN card_priority cp ON cp.card_id = p.source_card_id
JOIN priority_triggers t ON t.card_id = p.source_card_id
    AND (t.expires_at IS NULL OR t.expires_at > now())
WHERE cp.effective_tier = 'DORMANT' AND p.next_sales_due_at > now() + interval '24 hours';
```
Then confirm no new `conservative_demotion` event appears for that
`card_id` in `priority_engine_events` after `t.activated_at`.

---

## Part 3: Rollback

### Disabling conservative demotion

```
PRIORITY_ENGINE_ALLOW_CONSERVATIVE_DEMOTION=false
```
Restart `backend`. No further pushes happen from that point on.

### Do delayed timestamps need repair?

**No.** Already-pushed `next_sales_due_at` values need no repair - they
naturally resolve as those cards come due again on their own now-longer
(but still finite, capped) interval; SalesSync's own `select_due()` will
simply pick them up once `next_sales_due_at` passes, same as any other
card. This was a deliberate design property of the mechanism (see the
plan doc's Milestone 1.5 rollback note) - disabling is a pure flag flip,
not a data-repair operation.

If you want to proactively pull previously-demoted cards back to a
normal interval sooner rather than waiting out the full 72h ceiling (not
required, but available if you want faster observable rollback):
```sql
-- Preview first:
SELECT p.source_card_id, p.next_sales_due_at
FROM futgg_players p
JOIN priority_engine_events e ON e.card_id = p.source_card_id
WHERE e.reason = 'conservative_demotion' AND e.occurred_at > now() - interval '7 days';

-- Then, if you want to pull them back to "due now" so SalesSync picks
-- them up on its next normal cycle:
UPDATE futgg_players SET next_sales_due_at = now()
WHERE source_card_id IN (
    SELECT DISTINCT card_id FROM priority_engine_events
    WHERE reason = 'conservative_demotion' AND occurred_at > now() - interval '7 days'
);
```

### Confirm disabling actually prevents further demotion

```sql
SELECT count(*) FROM priority_engine_events
WHERE reason = 'conservative_demotion' AND occurred_at > now() - interval '15 minutes';
```
Should be 0 on every check after the restart with the flag off - the
disabled-flag path in `run_conservative_demotion_pass()` returns
immediately (`{"enabled": false, ...}`) before any query or write runs.

### Audit history is always preserved

Nothing in the rollback path deletes or modifies `priority_engine_events`
- disabling the flag only stops new writes; the full audit trail of every
demotion that ever happened stays queryable indefinitely (same table,
same rows, no TTL/cleanup job touches it).

### Rolling back classification itself (Milestone 1)

There is no flag for this - Milestone 1 is unconditional shadow
classification with no writes to `futgg_players` at all, so "rollback"
here just means stopping the backend's own refresher task (redeploy an
older backend image, or comment out the `asyncio.create_task(...)` call
in `main.py` if you need classification to stop running but keep
everything else). `card_priority`/`priority_triggers`/
`priority_engine_events` are read-only from the rest of the application's
perspective (nothing else in the codebase reads them to make scheduling
decisions yet - that's a later milestone) so leaving stale rows in place
after stopping the loop is harmless.

---

## Acceptance and rollback thresholds

Refined from the review's suggested defaults using this repo's actual
`futgg_sales_sync.py` tiering (`FUTGG_SALES_UNDER_75_INTERVAL_MIN=2880`,
i.e. the existing 48h floor for the lowest-rated band) as the real-world
reference point.

### Shadow-mode acceptance (before enabling conservative demotion)

- `pipeline_heartbeats.ok = true` for the full 24-48h window, with no
  more than a handful of isolated `cards_failed` per run (a single-digit
  `cards_failed` count on an otherwise-large catalogue pass is tolerable
  transient noise - e.g. one malformed snapshot row; a `cards_failed`
  count that's a meaningful fraction of `cards_processed`, or that
  persists across multiple consecutive runs, is not).
- Validation report's should-always-be-zero checks (trigger
  orphaned/expired-still-present/mismatch counts, classification
  DORMANT-despite-trigger/never-scraped-delayed/new-card-demoted counts,
  `as_of_dependent_mismatches`, audit invalid-tier/missing-reason-codes/
  missing-engine-version counts) are **actually 0**, every run, not just
  "close to 0."
- Tier distribution roughly matches the healthy-shape ranges in Part 1
  Step 5 (DORMANT the largest bucket, CRITICAL a small minority) and is
  stable pass-to-pass (no wild oscillation of a large fraction of the
  catalogue between tiers between consecutive 15-minute passes, which
  would indicate a scoring instability rather than genuine market
  movement).
- `baseline_tier != effective_tier` count stays proportional to actually-
  active SBCs/events in the catalogue over the same window (cross-check
  against `market_events` directly).

### Conservative-demotion rollback triggers (after enabling)

Roll back immediately (flip the flag off, see Part 3) if **any** of:

- The never-scraped-protection query in Part 2 Step 5 ever returns a
  nonzero count - this is the one invariant that must never break, full
  stop.
- `pipeline_heartbeats.ok` goes `false` for `priority_recompute`, or
  `cards_failed` spikes to a meaningful fraction of `cards_processed`,
  correlated with enabling the flag (vs. pre-existing shadow-mode noise).
- SalesSync's own error/429 rate **increases** after enabling (the
  mechanism should only ever reduce SalesSync's request volume - any
  correlated increase means something is behaving opposite to design and
  needs investigation before continuing).
- A card that should clearly still be actively traded (a real, currently-
  live SBC-relevant card, or a card with real recent sales) gets demoted
  - i.e. the eligibility predicate is admitting cards it shouldn't. Cross-
  check any surprising `conservative_demotion` event's card_id against
  `futgg_market_snapshot.sales_count`/`priority_triggers` by hand.
- Discovery-ceiling violations (Part 1's validation report /
  priority-metrics `discovery_ceiling_violations`) becomes nonzero - a
  card's `next_sales_due_at` has drifted further than the configured
  ceiling permits, meaning the anchor-to-`last_sales_status_at` logic is
  not behaving as designed.

### Success signal (not a rollback trigger - the thing you're waiting to see)

SalesSync's measured request volume (and/or 429 rate) trending
**downward** over the days following enablement, roughly tracking the
validation report's own projected-reduction estimate for the population
that's actually eligible in your real catalogue. This is genuinely a
multi-day signal, not something to judge from the first hour.
