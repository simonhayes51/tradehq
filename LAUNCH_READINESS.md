# Launch readiness, measurement and operational safety

Branch `claude/eafc-live-launch-readiness`, 7 commits on top of the Phase 2
tip (`3a238bb`). No Phase 1 or Phase 2 commit was modified, squashed or
reordered.

---

## 1. Entitlement leakage found

Auditing endpoints one at a time is how the `/companion` bypass survived a
whole phase, so this pass added a test that **walks the routers**: any
route that can return card analysis must take a viewer or be named in
`PUBLIC_BY_DESIGN` with a reason. It found four aliases nobody had looked
at, on top of the ones the objective named.

| endpoint | what a signed-out caller received |
|---|---|
| `/api/v2/players` (search) | The complete row. Took **no viewer at all**. |
| `/api/v2/players/{id}` | Full live price, fair value, trend, sales |
| `/api/v2/players/{id}/prices` | The whole price series |
| `/api/v2/players/{id}/sales` | Every completed sale |
| `/api/v2/companion/card/{id}` | Exact reference BIN, fair value, sales median, verdict |
| **`/api/players/{id}/market-metrics`** | Real sold price vs BIN, liquidity, volatility, snipe index, tax-aware margin |
| **`/api/players/batch/market-metrics`** | The same, **150 cards per request** |
| **`/api/players/{id}/lazy-buyer-score`** | Confidence score and percentile rank |
| **`/api/players/{id}/sales-candles`** | OHLC built from completed sales |
| `/api/players/{id}/price`, `/history`, `/bin-history`, `/sales-history` | Prices and completed sales |

The last five are legacy FUTBIN-backed aliases. Those tables are likely
empty in a FUT.GG-only deployment — but "probably empty in production" is
not an access control, and the endpoints would have become bypasses the
moment anything populated them.

The root cause was one thing: `FEATURE_MIN_TIER` only ever asked about
**tier**, and a signed-out caller resolves to tier `free`, so they
received every free feature — including the complete fair-value analysis,
which is the headline reason to create a Free account.

**Also fixed, unrelated to tiers:** `GET /health` returned `str(e)` on a
database failure. An asyncpg connection error message contains the DSN it
failed to reach, so the one moment the database was down was the one
moment the endpoint published the connection string.

---

## 2. Final signed-out player access

Tier and identity are now separate questions. `ACCOUNT_ONLY_FEATURES` in
`entitlements.py` names the free features that additionally require an
account (`fair_value`, `watchlist`, `trade_journal`,
`portfolio_performance`), and `features_for_tier(tier, authenticated=…)`
subtracts them for anonymous callers. One place, same resolver.

| | Signed out | Free | Elite |
|---|---|---|---|
| Search any card | ✅ | ✅ | ✅ |
| Card identity, rating, version, club | ✅ | ✅ | ✅ |
| Market price | **Approximate only** | Exact live | Exact live |
| Fair value / what it's worth | ❌ | ✅ | ✅ |
| Verdict (buy/wait/avoid) | ❌ | ✅ | ✅ |
| Completed sales & price history | ❌ | ✅ | ✅ |
| Trade plan (entry, target, profit) | ❌ | Only that day's 2 picks | Every card |
| Watchlist / alerts / portfolio | ❌ | 10 / 3 / 5 | 500 / 100 / unlimited |

**Search stays public deliberately.** It is discovery, it is what makes a
shared link worth opening, and making it account-only would remove the top
of the funnel to protect a number.

The public price is **proportionally** approximate — ±500 under 10k, ±5k
under 100k, ±25k under 1M, ±100k above. A flat rounding is wrong at both
ends: round a 3,200-coin card to the nearest 1,000 and you lose a third of
its value; round a 900,000-coin card to the nearest 1,000 and you have not
approximated anything.

Enforcement is **allow-list serialisation**, not subtraction: a field
added to the card payload tomorrow is absent from a public response
without anyone remembering to exclude it. `require_account()` answers
**401, not 402** — what is missing is an account, and one is free, so the
frontend routes it to signup rather than checkout.

Frontend: a dedicated signed-out card page (not the real page with blanks
in it) naming the five things an account adds and returning the visitor to
the same card afterwards; search labels its price `AROUND` rather than
`LIVE PRICE`; `PLAN_ROWS` re-worded, because "Player search & live prices —
included on Free" was true and was also true of no account at all.

---

## 3. AI model, measured usage and cost

**Model:** `gpt-4o-mini`, via `OPENAI_CHAT_MODEL`.
**Model calls per customer message:** up to 3 — the loop calls, runs a
tool, calls again with the result in context. Every round resends the
whole conversation plus a 438-token tool schema.

| | tokens |
|---|---|
| `SYSTEM_PROMPT` | ~306 |
| `TOOLS` schema | ~438 (resent every call) |
| card tool result | ~171 |
| budget search result (5 cards) | ~848 |

**These are estimates and are labelled as such.** `tiktoken`'s BPE table
cannot be downloaded here (the egress proxy denies
`openaipublic.blob.core.windows.net`) and there is no API key in this
environment, so the sizing is character-based (~4.0 chars/token prose,
~3.2 dense JSON), reported at the pessimistic end. The shipped accounting
records **provider-reported** usage, so the first day of real traffic
replaces every number with a measured one.

**No pricing existed anywhere in the repository.** The defaults —
$0.15/1M input, $0.60/1M output — are published list rates for the default
model and are **not verified against this account's invoices**. They are
env-overridable for that reason, and the doc, the API payload and the
deployment checklist all say so. **Verifying them against one real invoice
is on the launch checklist.**

Monthly cost per Elite member against the £3 subscription:

| msgs/day | typical | % of £3 | worst | % of £3 |
|---|---|---|---|---|
| 10 | £0.19 | 6% | £0.46 | 15% |
| 30 | £0.56 | 19% | £1.38 | 46% |
| 50 | £0.93 | 31% | £2.31 | 77% |
| **200** | **£3.72** | **124%** | **£9.22** | **307%** |

**200/day loses money on any member who uses it as permitted.**

### The hole that mattered more than the daily count

`ChatRequest.messages` had `min_length=1` and **no maximum**, and the
client sends the whole conversation every request. History is the dominant
cost term, and it was chosen by the caller:

| history sent | cost/message | at the 6/min limit |
|---|---|---|
| 100 KB | £0.0080 | £2.88/hour |
| 1 MB | £0.0763 | **£27.45/hour** |

One account could spend more in an hour than nine annual subscriptions.
Capping the daily count alone would have left this untouched.

**Answers to the specific questions asked:** failed requests do consume
the rate windows (a request that reached the provider spent money either
way) and are recorded with the tokens they actually used; blocked requests
consume nothing and do not take the concurrency slot; **streaming is not
used**, so there is no partial-response retry path to double-charge;
concurrent requests can no longer bypass the limits.

---

## 4. Final AI limits and reasoning

With history capped at 10 messages the worst case falls to
£0.00070/message, making 30/day ~21% of £3.

| variable | default | why |
|---|---|---|
| `AI_CHAT_RATE_LIMIT_PER_MINUTE` | 6 | unchanged; stops a burst |
| `AI_CHAT_RATE_LIMIT_PER_DAY` | **30** (was 200) | ~21% of £3 at the worst case |
| `AI_CHAT_RATE_LIMIT_PER_MONTH` | **300** | for the member who maxes out daily |
| `AI_CHAT_MAX_CONCURRENT` | **1** | a chat UI sends one at a time; more is a script |
| `AI_CHAT_MAX_HISTORY_MESSAGES` | **10** | five exchanges — enough to follow one card |
| `AI_CHAT_MAX_MESSAGE_CHARS` | **2000** | so one huge message can't replace a long history |
| `AI_CHAT_MAX_OUTPUT_TOKENS` | **600** | makes "keep replies short" enforceable, not requested |
| `AI_CHAT_ENABLED` | **1** | kill switch, read per request — no deploy to switch off |

30/day is a backstop against automation and runaway loops, not rationing.

Accounting (migration 076) stores user id, timestamp, model, token counts,
model calls, estimated cost and `ok`/`failed`/`blocked`. It stores **no**
message text, tool arguments, tool results, Discord identity, email, IP or
user agent. Usage accumulates across the whole tool loop — reading only
the last response would report a third of the real cost. If the provider
stops reporting usage the counts stay at zero, which reads as "we do not
know" rather than as a confident wrong number.

Customers never see a cost figure. Refusals now say **which** limit and
**when** it lifts, so someone who has used their daily allowance is no
longer told to "give it a moment" — which was false and invited retrying.

---

## 5. Admin funnel metrics and activation

`GET /api/admin/funnel?window=today|7d|30d|90d` or `?start=&end=`, plus a
Conversion tab in the existing admin page. Aggregated in SQL — **no raw
funnel rows reach a browser** — in five queries, not twenty round trips.

Reports: signup page views and starts (events), accounts created and
completion rate, onboarding starts/completions/skips, free opportunity
views and limit hits, upgrade prompt and pricing views, checkout
starts/cancellations, returned-from-checkout, **confirmed subscriptions**,
checkout completion rate, signup→Elite rate, first watchlist card / alert
/ trade, activation rate, median hours to first useful action and to
checkout, distinct active Free and Elite members, and a daily trend.

**Activation** is defined in code (`ACTIVATION_EVENTS`) and echoed in the
payload so the label and the number cannot drift: viewed a complete free
pick, added a watchlist card, created an alert, or logged a trade.
**Signing up is not activation.** It is measured as a **cohort** — signups
in the window, activated at any time since — because counting activations
in the window against signups in the window credits this month's signups
with last month's members finally doing something.

Three things it is careful about:
- **People are counted once.** Every user-scoped metric is
  `COUNT(DISTINCT user_id)`; eleven pricing-page visits is one person.
- **Signed-out events are events, not people.** They carry no user id and
  this product has no privacy-safe session identifier, so they are
  reported as event counts and the payload says so. **No fingerprinting
  was added**, and none is possible from what is stored.
- **Empty is not zero.** Rates return `null` rather than `0%` when there
  is nothing to divide by: "no signups yet" and "signups that all failed
  to convert" are different facts. Medians, not means.

---

## 6. Stripe-confirmed conversion

`checkout_completed` is emitted by the page Stripe redirects back to. It
means "a customer returned from checkout", which differs from "Stripe
confirmed a paid subscription" **in both directions**: someone who pays
and closes the tab never fires it, and anyone who knows the URL can fire
it without paying.

Migration 077 adds a `source` column. `subscription_confirmed` is written
**only** by the webhook handlers and is **refused outright** from the
tracking endpoint — dropped rather than stored as `source='client'`,
because a row that existed would have to be filtered out of every revenue
query forever. The dashboard shows both, labelled: confirmed subscriptions
are revenue; returned-from-checkout is intent.

The `subscription.updated` handler emits it **only on the transition into
an entitled state**. Stripe sends that event for renewals, card changes
and cancellation flags; counting each as a conversion would inflate
revenue by whatever Stripe happens to be chatty about.

**Analytics failure is now visible.** The endpoint still always answers
200 — instrumentation must never fail the action that triggered it — but
total silent loss looked exactly like nobody using the product. A counter
tracks attempts and failures, the dashboard shows a banner when recent
attempts are failing, `/health/ready` includes it, and the error **class**
is recorded rather than its message, because an asyncpg error text can
carry a connection string and this is read by an admin page.

---

## 7. Discord reconciliation

The queue from migration 056 is event-driven and correct until a job is
**lost** — an outage long enough to exhaust the retries leaves an account
permanently wrong, because nothing will ever look at it again. A queue
cannot notice it is out of step.

**It is not a second entitlement calculation.** Whether an account should
hold the role is answered by `resolve_user_tier` — the same resolver the
application uses — so it cannot disagree with the rest of the product
about who has paid. That disagreement is the bug Phase 2 removed, and
reintroducing it in a background job would be worse because nobody would
be looking. A test asserts the sweep reads no billing column of its own.

Behaviour: idempotent by construction (reads desired state, reads actual
state, acts on the difference); bounded batches with a politeness delay on
top of the `retry_after` handling `DiscordClient` already does; accounts
with no linked Discord identity are **skipped, never "fixed"** — the job
is not a reason to ask anyone to reconnect anything; someone in the
product but not in the guild is skipped, not failed; one bad account does
not end a pass, but ten consecutive Discord failures do, because a pass
that keeps trying through an outage burns the rate limit and achieves
nothing. Failure reasons are short codes from a fixed set, never exception
text, because an HTTP client's error string can carry a bot token.

Runs every 6h via the project's existing worker pattern (an asyncio task
in the app lifespan guarded by `pg_try_advisory_lock`, held for the whole
pass — the same discipline the alerts poller uses). Manual command:
`scripts/reconcile_discord_roles.py --dry-run | --limit N | --status`.
`GET /api/admin/discord-reconciliation` reports both the most recent pass
and the most recent **successful** one, because "it ran an hour ago" and
"it last worked a week ago" are different facts.

---

## 8. Retention

`funnel_events` grows unbounded and was flagged as such when it shipped.
12 months by default (`FUNNEL_RETENTION_DAYS`), checked daily and acting
on the first of the month, through the same worker pattern and advisory
lock.

Deleted in bounded batches **by primary key**: `DELETE … LIMIT` is not
valid Postgres, and deleting by predicate alone would take one long lock
on the table the tracking endpoint is writing to. A first run against a
large backlog stops at a batch ceiling and reports `partial` rather than
running unbounded; the next pass continues.

**No aggregate roll-up was invented.** The objective says to preserve
longer-term aggregates *if they already exist* — they do not, the
dashboard computes everything live — so this deletes and does not quietly
become a new, untested source of truth about the product's history. A test
asserts the module contains no `INSERT` or `CREATE TABLE`.

Manual command: `scripts/purge_funnel_events.py --count | --dry-run |
--days N`.

---

## 9. Extension and userscript verification

**A correction to the premise first:** neither client runs on FUT.GG. Both
run inside EA's Web App (`www.ea.com`) and call `api.eafc.live`. FUT.GG is
the data source the backend scrapes, not a page the companion is injected
into. The cross-site problem is real either way.

**Verified** (statically and by test): `SameSite=none` + `Secure` in
production; CORS is still an allow-list, not a wildcard, with
`allow_credentials=True`; the extension holds host permission and fetches
from its **MV3 service worker**, not a content script (a content script's
fetch carries the *page's* origin for CORS; the service worker's does not,
and host permissions exempt it); the userscript declares `@connect`;
neither client forwards an EA credential; and each of the four access
states returns what it should.

**Could not be verified here:** whether a browser actually attaches the
cookie. That is a property of Chrome, Safari, Tampermonkey, Violentmonkey
and the iOS Userscripts app, not of this codebase. Two risks: the
third-party cookie phase-out, and the fact that `GM_xmlhttpRequest`'s
`withCredentials` support **differs between managers** — this codebase has
already been bitten by that exact class of difference, documented in
`api-client.js` from a live iOS session.

**So, per the objective, an explicit short-lived token flow** rather than
weakened cookies or CORS. `POST /api/v2/companion/session`, authenticated
by the session cookie on `app.eafc.live` where it is **first-party**,
returns a token scoped `companion:read` with a 7-day default TTL, sent
as a bearer header — which nothing can decline to attach.

It is **not a permanent API key**: it expires, and rotating
`JWT_PRIVATE_KEY` revokes every outstanding one. It is **not the existing
extension token** either — that is scoped `trade:ingest` with a 30-day TTL
and is a *write* credential. Scope is checked explicitly rather than
inferred from the signature, because the signing key is shared. The token
says **who, never what**: entitlements are resolved from stored billing
state on every request, so a token minted while someone was Elite gives
them nothing once the subscription lapses. A live cookie session wins over
a token. The bearer path is companion-only. Storage is host-private
(`chrome.storage.local`, GM storage) so the EA page cannot read it.

Both clients still send the cookie. Whichever the browser honours wins.

`docs/COMPANION_AUTH.md` holds a six-item **real-device checklist** for
the launch smoke test — stated as a checklist, not a claim.

---

## 10. Health checks

`GET /health` — liveness. Now logs the exception and returns 503 with no
detail.
`GET /health/ready` — public: names and statuses only. Enough for a load
balancer, useless for fingerprinting. 503 only when a **required**
dependency is down.
`GET /api/admin/readiness` — admin: reason codes and small details,
because the detail is a map of which integrations exist and which
migrations are pending.

**Four states, not two**, because most of what this product depends on is
optional:

| state | meaning |
|---|---|
| `ok` | working |
| `degraded` | an **optional** feature is impaired; the product serves |
| `unconfigured` | never set up on this deployment — **not a fault** |
| `unavailable` | a **required** dependency is down |

`unconfigured` deliberately does not drag the overall status down: a
staging environment with no Stripe key is correctly configured for what it
is, and reporting it as unhealthy trains people to ignore the check.

Covers core and watchlist databases, migrations 072–078 (checked against
`information_schema`, not a ledger that can itself be out of step with the
schema), Redis, AI provider and kill switch, Stripe, Discord, the
reconciliation sweep, free-pick persistence and funnel-event recording.

Two judgements worth naming: a pending migration is `degraded`, not
`unavailable`, because every feature behind those tables degrades honestly
on its own; and the AI kill switch reports `unconfigured` with reason
`disabled_by_operator`, because being switched off on purpose is not a
fault. Stripe without a webhook secret is `degraded` — without it nothing
confirms a payment server-side, so subscriptions never activate.

No exception text, connection string, host name or environment variable
value appears anywhere; tests plant secrets in the environment and assert
they do not reach the payload.

---

## 11. Tests

**Backend: 1461 passing, 58 skipped** (1227 at the start of this phase,
measured on `3a238bb`). New files:

| file | tests | covers |
|---|---|---|
| `test_public_access.py` | 35 | signed-out allow-list, Free fair-value access, Elite detail, and the **route sweep** |
| `test_ai_usage.py` | 40 | history/output bounds, daily/monthly/concurrent limits, kill switch, accounting without message content, cost calculation |
| `test_funnel_report.py` | 39 | aggregation correctness, admin-only access, Stripe-confirmed conversion, degraded states |
| `test_discord_reconcile.py` | 22 | grant, revoke, skip, retry, dry-run, idempotence, concurrency safety |
| `test_funnel_retention.py` | 18 | bounded batches, count-only, dry-run, configuration, scheduling |
| `test_companion_auth.py` | 30 | token issue/verify/expiry/scope, cookie posture, client configuration |
| `test_billing_plans_cache.py` | 15 | cache, single-flight, Stripe outage, unconfigured, no customer data |
| `test_readiness.py` | 25 | the four states, migration detection, no secrets in any payload |
| additions to `test_funnel_events.py` | +12 | server-only events, tracking-failure visibility |

**Frontend: 156 passing** (136 at phase start). New:
`PublicCardPreview.test.jsx` (9), `Players.public.test.jsx` (5),
`useAiChat.test.js` (6).

**Lint:** 0 errors. **Build:** clean. **Browser:** 49/49 route checks, all
signed-out/Free/Elite state checks, and a new signed-out player-page
harness — all passing at desktop and mobile.

No existing test was weakened. Where a test targeted a moved
implementation (the Phase 2 fair-use tests, the companion viewer
override), the plumbing was updated and the assertions kept or
strengthened.

Two tests in this phase were themselves wrong and were fixed rather than
the code: a docstring-substring false positive (a check matched the
comment explaining the bug it prevents) and a fake Discord client that
did not persist its own writes, which would have let the idempotence test
pass against a double that forgets.

---

## 12. Migrations

All additive and reversible. **Deploy order does not matter** — the
application tolerates every one of these being absent.

| | change | target | rollback | degradation if unapplied |
|---|---|---|---|---|
| 076 | `ai_usage_events` | core | `DROP TABLE ai_usage_events;` | limits still enforced; cost is unrecorded |
| 077 | `funnel_events.source` + index | core | `ALTER TABLE funnel_events DROP COLUMN source;` | revenue cannot be distinguished from intent |
| 078 | `discord_reconciliation_runs` | core | `DROP TABLE discord_reconciliation_runs;` | sweep runs; history is unrecorded |

Migrations 073–075 from Phase 2 are unchanged.

---

## 13. Deployment and rollback

`docs/DEPLOYMENT_CHECKLIST.md` covers the eleven steps requested: backup,
backend deploy, migration verification, frontend deploy, Free and Elite
smoke accounts, Stripe verification, Discord role verification, extension
and userscript checks, analytics recording check, and rollback.

The rollback section is organised **by symptom**, because most of this
phase rolls back by config change rather than redeploy:

| symptom | action |
|---|---|
| AI cost higher than expected | `AI_CHAT_ENABLED=0` (read per request) |
| Discord sweep misbehaving | `DISCORD_RECONCILE_INTERVAL_SECONDS=0` |
| Retention deleting unexpectedly | `FUNNEL_RETENTION_INTERVAL_SECONDS=0` |
| Companion tokens leaked | rotate `JWT_PRIVATE_KEY` |
| `/health` 503, or entitled members cannot see prices | redeploy the previous commit |

Migrations are additive, so the previous application version ignores the
new tables and column entirely. **Nothing was deployed and no destructive
production operation was run.**

---

## 14. Remaining blockers

1. **AI pricing is unverified.** The configured rates are published list
   prices, never checked against a real invoice. Every cost figure in the
   admin view is wrong by whatever margin they are wrong by. **Check one
   invoice against `/api/admin/ai-usage` before relying on the numbers.**
   This is the top item in the checklist's first-24-hours section.
2. **Companion cookie behaviour is unverified on real devices.** The token
   flow exists precisely so this is not blocking, but the six-item
   checklist in `docs/COMPANION_AUTH.md` still needs running — especially
   the iOS Userscripts app.
3. **The AI token estimates are estimates.** Real usage replaces them on
   the first day of traffic; treat the current limit as provisional and
   check the heaviest account after 24 hours.
4. **Email/password auth remains unfinished** (no verification, no
   password reset). Out of scope this phase, as instructed; signup is
   still Discord-only.
5. **No `og:image` exists.** Out of scope this phase, as instructed. Every
   social share of eafc.live is still text-only.
6. **The funnel dashboard has no CSV export.** Deliberate — the objective
   asked for something practical rather than a general analytics platform.
7. **Discord reconciliation has never run against real data.** The first
   production run should be `--dry-run --limit 50`; **a large
   `roles_revoked` on the first pass is a red flag**, and may indicate an
   entitlement resolution problem rather than Discord drift.
8. **The sales collector was falling back to a transport that doesn't
   work, and its heartbeat didn't say so.** This is why every market
   surface went empty. The worker was never down —
   `futgg-sales-sync-v1` runs on its ~2-hourly schedule and ran most
   recently at 14:04 on 31 August. What was happening inside each run,
   from the 31 August container log:

   - `signed_fetch` collects 150–185 cards, then FUT.GG starts returning
     `data_429` and the circuit breaker trips at the 80% threshold.
   - The worker switched to the `page_capture` fallback, which
     effectively stops collecting: `cards_ok=2 failed=18 sales=200 in
     2311.5s (0 cards/min)`, with `page_capture_fetch avg=230s
     max=1292s` — up to 21 minutes for a single card — plus repeated
     Playwright crashes (`Response.body: Page crashed`) at
     `futgg_sales_sync.py:972`.
   - Coverage therefore drained rather than stopped: across four cycles,
     sales coverage went 46.2% → 46.0% and signal-capable cards 39.4% →
     38.9%. Cards age past the 72h freshness gate faster than the worker
     refreshes them, the geometric confidence blend zeroes them, and Home
     reported "32 of 1182 tracked cards have enough fresh data".

   Three fixes shipped for this, in order of what they address:

   - **Visibility.** The collector now writes its own heartbeat every
     cycle, where `ok` means "collecting fast enough to stay inside the
     freshness gate", not "the process is alive" — a tripped breaker or
     a throughput collapse is not ok, however punctually the container
     checks in. Readiness now checks the three deployed collectors *by
     name*, so an absent heartbeat reads as an outage instead of nothing
     to find.
   - **Stop making it worse.** `PAGE_CAPTURE_FALLBACK_ON_THROTTLE`
     (default off) stops the worker downgrading to `page_capture` on a
     429/5xx throttle — the log shows that fallback is currently a
     straight downgrade from "slow but working" to "not working", not a
     cost/reliability tradeoff. A genuine block (403/challenge) still
     switches immediately; only throttling is affected. Flip the env var
     back on once `page_capture`'s own throughput and crash rate are
     fixed.
   - **A real infrastructure bug.** `Fatal server error: (EE) Server is
     already active for display 99` at container start was a stale Xvfb
     lock surviving an unclean previous exit (SIGKILL, OOM) and blocking
     every subsequent start. Both `docker-entrypoint-sales.sh` and
     `docker-entrypoint-sbc.sh` now clear a stale lock (after confirming
     its PID is actually dead) before starting Xvfb.

   A follow-up live incident, found after the above shipped: coverage
   was still only advancing at roughly half the rate the log's own
   productive cycles implied. A 6-hour container log (11:06–17:03)
   showed a repeating pattern — a healthy ~150–175 card cycle, then
   (after the 300s circuit-breaker backoff) a near-instant `cards_ok=0`
   cycle that failed and re-tripped in under 10 seconds, on roughly
   every other cycle throughout the whole window. Root cause:
   `AdaptiveThrottle.consecutive_fully_throttled_batches` — the streak
   the circuit breaker actually trips on — was never reset between
   cycles. A cycle only trips with this counter already at or above
   `CONSECUTIVE_FULL_THROTTLE_BATCHES` (2), so the next cycle inherited
   that count and its very first batch, whether or not FUT.GG's
   rate-limit window had actually cleared during the backoff, could push
   it straight back over the threshold. `batch_size`/`delay` are
   deliberately carried across cycles (that's the point of
   `AdaptiveThrottle`); this streak counter is not meant to be, and now
   resets to 0 at the top of every cycle in `run_forever()`, alongside
   the existing per-cycle `stats` reset. Confirmed live: the next cycle
   after this deployed logged `cards_ok=137 failed=0` — clean, no
   retrip — and coverage/`signal_capable` climbed across three
   consecutive readings over ~2.5 hours (46.8%/38.0% → 47.7%/39.1% →
   48.0%/39.3%) rather than one good stretch.

   A second incident surfaced immediately after that fix shipped, on the
   very next redeploy: `asyncpg.exceptions.InterfaceError: cannot call
   Connection.execute(): connection has been released back to the pool`,
   thrown from the shutdown path's `pg_advisory_unlock` call and
   propagating out as an unhandled `futgg_sales_sync failed` traceback.
   Root cause: a container teardown can cut the DB connection within
   ~150ms of the SIGTERM that triggers shutdown, before the unlock ever
   runs; asyncpg detects the dead connection and auto-detaches it, so
   calling it raises. Harmless in effect (Postgres already drops a
   session-scoped advisory lock the instant its connection dies) but
   noisy — a routine redeploy looked like a crash. Fixed by wrapping the
   unlock call in try/except; a dead connection there now just logs a
   warning.

   A third incident, found from the SAME redeploy's logs, was more
   serious: the advisory-lock handoff between the new and old container
   left **both** stopped. Sequence observed: Railway starts the new
   container while the old one is still mid-cycle → new container's
   single `pg_try_advisory_lock` attempt loses (expected — the old one
   is still running) → old code treated that as "another SalesSync is
   running, exit cleanly" and returned for good → Railway stops that
   now-exited container → moments later the old instance also gets
   SIGTERM as part of the same deploy and shuts down cleanly. Net
   result: nothing left running, and nothing restarts a container that
   exited with code 0. That one-shot check was correct for the old
   bounded/cron-schedule world (the other run would finish and free the
   lock on its own schedule regardless), but a continuous service's
   rolling deploy hits this exact race on *every single deploy*.
   `acquire_advisory_lock_with_retry()` now retries for
   `FUTGG_SALES_LOCK_ACQUIRE_RETRY_SECONDS` (default 90s) before giving
   up — long enough for the outgoing instance to finish its own SIGTERM
   handling and release the lock, which the previous fix confirmed
   completes in well under that. Confirmed live: the manual redeploy
   after this fix came back up and resumed the queue (`resuming
   throttle from a previous run: batch=8 delay=10.24s`) instead of both
   containers exiting.

   A fourth incident, from that same redeploy's logs: a cycle tripped
   the circuit breaker, backed off the full `CIRCUIT_BREAKER_BACKOFF_
   SECONDS` (300s), and the very next cycle tripped again almost
   immediately (`cards_ok=0 failed=9` in 25.3s). This is a different
   failure from the "instant retrip" bug fixed earlier — that fix
   (resetting `consecutive_fully_throttled_batches` at cycle start)
   was working correctly here: the new cycle got several real batches
   in (9 cards across ~2 batches) before genuinely re-tripping, rather
   than tripping on batch one from an inherited counter. This is
   FUT.GG's actual rate-limit window simply outlasting a flat 300s
   wait — retrying into a still-active limit only spends more requests
   against it for nothing. `next_backoff_seconds()` now doubles the
   wait on each consecutive trip with no clean cycle in between (300s →
   600s → 1200s → ...), capped at `FUTGG_SALES_CIRCUIT_BREAKER_MAX_
   BACKOFF_SECONDS` (default 1800s), and resets to the base 300s the
   moment a cycle actually completes clean. A single, isolated trip
   still gets exactly the same 300s as before.

   Re-verified live within the hour, and the escalation itself needed a
   correction: four consecutive trips ran the backoff 300s → 600s →
   1200s → 1800s (the cap) — but two of those trips (`cards_ok=142` at
   24/min, `cards_ok=185` at 31/min) were healthy, substantial cycles
   that simply hit the same recurring adaptive-throttle ceiling again
   after collecting real data, not a sign the prior wait had failed.
   Only one of the four (`cards_ok=0` at 0/min) was the true
   "instant retrip" failure this exists to catch. Escalating on every
   trip alike drove the wait to its 30-minute cap for what was actually
   `AdaptiveThrottle` behaving exactly as designed — ease the batch size
   back up, eventually overshoot the limit, back off, repeat — costing
   real throughput for no reason. Escalation is now gated on the
   tripped cycle's own throughput (the same `HEARTBEAT_MIN_CARDS_PER_MIN`
   floor `write_heartbeat`'s `ok` already uses): a productive-but-tripped
   cycle resets the streak back to the base 300s, since collecting
   real data before hitting the wall again proves the wait before it
   was already enough; only a near-zero-output trip escalates the next
   one. Not yet re-verified against a live log with this correction in
   place.

   **Still not fixed:** whether `signed_fetch` alone, even running at
   full effective throughput, keeps the catalogue inside the 72h
   freshness window — that may still need a lower per-cycle card count,
   a longer inter-batch delay tuned to FUT.GG's actual window, or simply
   accepting a slower refresh cadence for low-priority cards.
9. **The SBC-reward guard's match quality is unverified against live
   data.** A production case (Lamine Yamal 99 RW, live "Summer Stars
   Tournament Winners" SBC) showed the engine recommending a buy on a
   card whose price was being pushed down by SBC-completion supply, not
   underpricing — the trend layer is price-only and had no way to see
   the SBC. `app/services/sbc_reward_match.py` now name-matches live
   SBCs (`market_events`, player DB) to FUT.GG cards (core DB), and
   `futgg_intelligence.py` downgrades a matched card's signal to `watch`
   while its SBC is live (`SBC_REWARD_ACTIVE`) — which removes it from
   Opportunities/Trade Finder/Top Card automatically, since those already
   filter to `signals={"buy", "strong_buy"}`. The match is deliberately
   conservative — exact name match, unique among candidates,
   rating-disambiguated when ambiguous, generic pack/coin/token rewards
   rejected outright — but it has never run against a real EasySBC
   payload in this environment (see `easysbc_sbc_sync.py`'s own
   "genuinely open" section for why `reward_card_id` itself is
   unmapped). **Watch the first live runs after this deploys: check
   `futgg_sbc_reward_watch` picks up real live SBCs and that
   `matched`/`unmatched` counts in the refresher's logs look sane.**
   `FUTGG_SBC_REWARD_GUARD=false` turns the downstream gate off without
   a deploy if the match rate turns out to be poor.
10. **The sales collector was scheduling by rating, while the
    customer-facing staleness signal was judged by adaptive priority
    tier — two axes over the same catalogue that were never
    reconciled.** Live: "Our sales data is behind" kept showing on Home
    even after item 8's fixes, during a 6-hour stretch where coverage
    was climbing steadily and every heartbeat was healthy. Root cause:
    `EmptyFeedReason.jsx`'s staleness gate sums
    `stale_sales_by_priority_tier` — cards judged overdue against
    `card_priority.effective_tier`'s own interval (CRITICAL=15min,
    HIGH=60min, migration 044's adaptive-priority engine, refreshed
    every 15 minutes independently of this worker). But
    `select_due()` had no concept of `card_priority` at all — it
    scheduled purely by rating band, whose fastest tier (95+) only
    ever targeted once per 180 minutes. So CRITICAL (~1,700 cards) and
    HIGH (~4,100 cards) were permanently reported stale by the exact
    metric shown to users, regardless of collector health, because
    nothing in the pipeline ever attempted their interval — not a
    throughput problem, a scheduling-axis mismatch. Migration 044's
    own comments already anticipated this as deferred scope ("a later
    milestone starts writing next_sales_due_at").

    `select_due()` now bands by `card_priority.effective_tier` instead
    of rating, reusing the exact guaranteed-floor-then-overflow
    mechanism from item 8's band-floor fix (`_TIER_MIN_SHARE`/
    `_TIER_WHERE` replace `_BAND_MIN_SHARE`/`_BAND_WHERE`), and
    `sales_interval_minutes()` now takes a tier instead of a rating
    (`SALES_INTERVALS` keys changed from `95_plus`/`85_plus`/`80_84`/
    `75_79`/`under_75` to `CRITICAL`/`HIGH`/`NORMAL`/`LOW`/`DORMANT` —
    the old `FUTGG_SALES_95_PLUS_INTERVAL_MIN`-style env vars are no
    longer read; the new ones are `FUTGG_SALES_CRITICAL_INTERVAL_MIN`
    etc., defaulting to 15/60/240/1440/4000 minutes, mirroring the
    backend's `PRIORITY_ENGINE_CONFIG.tier_sales_interval_minutes`).
    Both sides default an unscored card to `NORMAL`, matching
    `market_data_provider.py`'s `DEFAULT_SALES_TIER`, so scheduling and
    staleness-reporting can no longer quietly disagree about a card
    neither side has classified yet.

    **Confirmed, not just predicted:** re-deployed and watched live for
    ~3 hours. `select_due()` was scheduling by tier correctly (confirmed
    in the startup log: `intervals={'CRITICAL': 15, 'HIGH': 60, ...}`)
    and overall coverage climbed steadily the whole time (any_sales
    8424→9007, signal_capable 7183→7860) — but `CRITICAL`/`HIGH`'s own
    `stale_sales_by_priority_tier` counts stayed flat-to-rising (1714→
    1777, 4104→4639) regardless. Exactly the predicted outcome: 15
    minutes for ~1,700 CRITICAL cards needs ~118 cards/min sustained for
    that tier alone, against a real observed ceiling of ~12-15 cards/min
    even during this healthy stretch. No amount of scheduling
    improvement could have closed that gap — the targets themselves were
    the bug, set in `priority_config.py` before this worker ever
    scheduled by tier at all, with nothing to check them against.

    `tier_sales_interval_minutes` (`priority_config.py`) and its
    `auto_sync` mirror (`SALES_INTERVALS`) both changed: CRITICAL
    15→180min (matching the old rating-band system's *proven*-
    sustainable 95_plus cadence — the one real data point available),
    HIGH 60→720min, NORMAL 240→1440min, LOW 1440→2880min (matching the
    old system's proven under_75 cadence), DORMANT 4320→4000min
    (unchanged in effect, just consistent with the auto_sync default
    already in place). NORMAL/LOW were already comfortably met at their
    old targets (stale counts in the hundreds/low thousands, not
    thousands), so this is a deliberately conservative staircase down
    from the one hard data point (CRITICAL=180min), not a second guess.
    A new test (`test_tier_intervals_equal_the_backend_priority_config`
    in `test_sales_cadence_vs_gate.py`) pins the two configs equal so
    this exact class of drift — one side tuned without the other —
    fails CI going forward.

    **The interval fix alone was not enough, and the follow-up
    confirms why.** Re-checked ~3 hours later: `CRITICAL`/`HIGH`/
    `NORMAL`'s stale counts were all still rising (1777→1857, 4639→
    4976, 1439→1708) despite now-achievable targets, while `DORMANT` —
    the loosest target of all — was the only one actually falling
    (3783→2984). The remaining bug was `_TIER_MIN_SHARE`, the
    guaranteed-cards-per-cycle floor: it still gave `DORMANT` the
    largest reservation (25%) and `CRITICAL` the smallest (5%),
    unchanged since the original rating-band port, where that made
    sense (protect the slow, large tail from the fast bands). It does
    not make sense now that priority tiers replaced rating bands:
    `DORMANT`'s loose 4000-minute target barely needs guaranteed
    throughput to stay met, while `CRITICAL`'s population needs real
    volume to have any chance at its (already loosened) 180-minute
    target — a 20-card floor per cycle was never going to move a
    population in the thousands. Reallocated to `CRITICAL`=0.25,
    `HIGH`=0.25, `NORMAL`=0.10, `LOW`=0.05, `DORMANT`=0.10 (still 25%
    overflow capacity left over). Full test suite green, including a
    replacement for the test that had encoded the old "DORMANT gets the
    biggest floor" assumption as a permanent property rather than a
    rating-band-specific one. **Not yet re-verified live against this
    third correction** — this is the second time a plausible-looking
    fix under-shot; watch `stale_sales_by_priority_tier["CRITICAL"]`/
    `["HIGH"]` specifically over several hours, not just whether they
    stop rising but whether they actually turn around and fall. If they
    still don't, the next lever is whether real throughput can support
    ANY reasonable CRITICAL/HIGH treatment at this catalogue size, not
    another share/interval rebalance.

---

## 15. Files and commits

**7 commits, 58 files, +7,244 / −251.**

| commit | objective |
|---|---|
| `2d7ab8f` | Free membership has a purpose: analysis needs an account, search does not |
| `6146ee3` | AI Chat: costed limits, provider-reported accounting, and an off switch |
| `50e5cb5` | Admin conversion dashboard; Stripe, not the browser, decides revenue |
| `7af82e7` | Periodic Discord reconciliation and funnel-event retention |
| `de24753` | Companion auth: verify what can be verified, stop relying on what cannot |
| `9ae9f7f` | Pricing survives a Stripe outage; readiness that distinguishes its states |
| `8d5a5d9` | Say what the search page does, in the customer's words |

**New backend modules:** `app/services/ai_usage.py`,
`app/services/funnel.py`, `app/services/funnel_retention.py`,
`app/services/discord_reconcile.py`, `app/services/readiness.py`,
`app/auth/companion_token.py`, `app/routers/admin_reporting.py`.

**New scripts:** `scripts/reconcile_discord_roles.py`,
`scripts/purge_funnel_events.py`.

**New migrations:** `076_ai_usage_events.sql`,
`077_funnel_event_source.sql`, `078_discord_reconciliation.sql`.

**New docs:** `docs/AI_CHAT_COST.md`, `docs/COMPANION_AUTH.md`,
`docs/DEPLOYMENT_CHECKLIST.md`.

**Changed backend:** `app/auth/entitlements.py`, `app/auth/access.py`,
`app/routers/v2/futgg_market.py`, `companion.py`, `ai_chat.py`,
`events.py`, `app/routers/players.py`, `main.py`.

**New frontend:** `v2/pages/PlayerPage/PublicCardPreview.jsx`,
`v2/pages/Admin/tabs/ConversionTab.jsx`,
`v2/components/CompanionConnect.jsx`, `v2/hooks/useAdminFunnel.js`, and
their stylesheets and tests.

**Changed frontend:** `PlayerPage.jsx`, `Players.jsx`, `Account.jsx`,
`Chat.jsx`, `PlanComparison.jsx`, `Signup.jsx`, `PublicSite.jsx`,
`useAiChat.js`, `api/aiChat.js`.

**Changed clients:** `eafc-live-extension/manifest.json` and
`src/background.js`; `eafc-live-userscript/src/api-client.js` and
`src/metadata.js`.

**Out of scope, and not touched:** email/password auth, Google login,
magic links, SBC Hub, Market Map, trading algorithms, FC27 cold start,
visual redesign, external analytics, social image, `src/v2` rename.
