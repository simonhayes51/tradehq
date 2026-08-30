# Access control, signup, onboarding and conversion

Branch `claude/eafc-live-access-and-conversion`, 11 commits on top of the
completed site audit. No audit commit was modified or squashed.

---

## 1. The final entitlement table

Three states, resolved server-side. There is no fourth, and no "pro".

| | **Signed out** | **Free member** | **Elite** |
|---|---|---|---|
| Opportunities feed | 1 preview card, locked | 2 complete picks/day + previews of the rest | Everything, all day |
| Preview contents | Player, version, verdict, ~price, one reason | same | n/a |
| Trade Finder | No (402) | No (402) | Full filter + sort set |
| Player search & live prices | Yes | Yes | Yes |
| Fair value on a searched card | Yes | Yes | Yes |
| Card detail trade plan | No | Only for that day's picks | Every card |
| Watchlist | No | 10 cards | 500 cards |
| Price alerts | No | 3 active | 100 active |
| Portfolio | No | 5 open trades, 30 days history | Unlimited trades, full history |
| Track record | Headline published results, 30d | Headline results, 30d | Every window, full ledger |
| AI trading assistant | No | No | Yes, 6/min and 200/day fair use |
| Members-only Discord | No | No | Yes |

Every number is a named constant in `backend/app/auth/entitlements.py`,
each overridable by environment variable, and each returned to the client
in `/api/entitlements`'s `limits` block so no component holds its own copy:

```
FREE_WATCHLIST_MAX=10       ELITE_WATCHLIST_MAX=500
FREE_ALERT_MAX=3            ELITE_ALERT_MAX=100
FREE_OPEN_TRADE_MAX=5       ELITE_OPEN_TRADE_MAX=1000
FREE_DAILY_PICKS=2
FREE_TRACK_RECORD_WINDOW_DAYS=30
FREE_PORTFOLIO_HISTORY_DAYS=30
PAST_DUE_GRACE_DAYS=5
AI_CHAT_RATE_LIMIT_PER_MINUTE=6   AI_CHAT_RATE_LIMIT_PER_DAY=200
```

`frontend/src/v2/lib/limits.js` holds display copies for the two surfaces
with no entitlement context (the signed-out pricing page and signup).
They are copy only; a number that drifts there produces wrong wording, not
a bypass.

**One paid-plan name.** The product previously said "Premium" on the
billing page and marketing site, "ELITE" in the sidebar, and could print
"PREMIUM" on the account page. It is Elite everywhere now, including the
Discord row, which is "Members-only Discord — the Elite channel".

**Discord is never a condition of being paid.** `_resolve_tier` reads
plan, tier column, subscription status, and both premium_until grants. A
linked Discord account is not among them, and a test asserts no Discord
signal can enter that function.

---

## 2. Where access is enforced

One resolution path: `compute_entitlements` → `get_viewer` → `Viewer`.
`require_feature` and `require_tier` are built on `Depends(get_viewer)`,
so there is one place a tier is decided and one place tests override.

Endpoints changed this phase:

| Endpoint | Before | Now |
|---|---|---|
| `GET /api/v2/opportunities` | Full feed to anyone | `serialise_opportunities` — Elite whole, others allow-listed previews with the day's picks unlocked |
| `GET /api/v2/trade-finder` | Ungated | `require_feature("trade_finder")` |
| `GET /api/v2/trade-finder/strategy/{s}` | Ungated | same |
| `GET /api/v2/players/{card_id}` | Full trade plan | `serialise_card_detail` |
| `GET /api/v2/companion/card/{id}` | Full trade plan, no auth | `serialise_card_detail` + `locked`/`upgrade_url` |
| `GET /api/v2/dashboard` | Top-ranked BUY complete to anyone | `serialise_dashboard_feed` against the same daily picks |
| `GET /api/v2/track-record` | Any window | Window capped for non-Elite, `window_capped` said out loud |
| `GET /api/v2/track-record/recent` | Ungated | `require_feature("full_track_record")` |
| `POST /api/v2/ai/chat` | 6/min | 6/min + 200/day, tools gated per feature |
| `POST /api/watchlist-alerts` | No limit | `limit_reached` at the tier's alert cap |
| `POST /api/v2/trades` | No limit | `limit_reached` at the open-trade cap |
| `GET /api/v2/trades/history` | All history | Windowed in SQL by `portfolio_history_days` |

**Serialisation, not redaction.** `preview_opportunity` and
`preview_dashboard_recommendation` are allow-lists: they name the fields
that survive. A premium field added to either shape tomorrow is absent
from a locked payload without anyone remembering to exclude it, and a
test asserts exactly that. The hidden fields are never sent, so there is
nothing in the client's props to blur and no devtools trick that recovers
them.

None of the following is relied on anywhere: hidden buttons, blurred CSS,
disabled navigation, client-side counters, query parameters, local
storage, frontend route guards. `TradeFinder.jsx` derives its locked state
from the server's own 402, not from the entitlement context, so the page
and the API cannot disagree.

---

## 3. Free daily picks

`backend/app/services/free_picks.py`, persisted by migration 073.

- **Deterministic.** The ranking is a pure function; ties break on a
  SHA-256 seeded by the content-day key, so a fresh database re-deriving a
  past day lands on the same answer for the same pool.
- **The same for everyone.** The pool is `free_pick_candidates()` in
  `futgg_market.py` — unfiltered, unpaginated, taking nothing a caller
  controls. `/opportunities` previously resolved the picks from *this
  request's* filtered page, so the first free member of the day to load
  with `risk=low` set decided everyone else's picks from a pool nobody
  else was looking at. Home resolves through the same function, so
  whichever page a member opens first produces the identical answer.
- **Defensible.** Confidence ≥ 0.55, price age ≤ 90 minutes, ≥ 5 recent
  sales, buy/strong_buy only.
- **Not two versions of one player**, and spread across budget bands
  (< 25k, < 150k, above) as a preference. Diversity never reduces the
  count: a second pass backfills, relaxing the band but never the
  one-player rule.
- **Fixed for the day.** Recorded on first resolution with
  `ON CONFLICT DO NOTHING`, so a card cannot vanish from under someone who
  has already been shown it. Read-only `unlocked_pick_ids()` is what the
  card page and companion overlay use — a card page never decides the day.
- **Never fabricated.** A thin day returns one pick, or none, and the UI
  says so: "Only 1 of 2 today — we'd rather show you nothing than a call
  we don't believe in."
- **Next refresh** is a real timestamp in the response, rendered as a
  countdown and "at 6pm UK time".

**Content day**: `backend/app/services/content_day.py`, 18:00
Europe/London, one place. `next_content_day_start` steps the local date
rather than adding 24 hours, so the 23-hour and 25-hour DST days are
correct — verified at both transitions and asserted by test (summer
boundary 17:00Z, winter 18:00Z, countdown never exceeds 25 hours).

---

## 4. Signup and onboarding

**Signup** (`frontend/src/pages/Signup.jsx`) was a heading, three vague
bullets ("Unlock more EAFC Live features as you go") and a Discord button.
It now leads with "Create your free account", states the four concrete
things Free includes, and answers the two objections that stop people
under the button: free forever, no card, Discord only for the account and
never an EA login. Free is never called a trial, and a `<details>` holds
the full plan comparison for anyone who wants it before deciding.

Authentication is unchanged: Discord OAuth, the only method wired end to
end. The email/password routes in `app/routers/auth_email.py` have no
verification and no password reset, so putting them in front of customers
would ship an unfinished auth system — flagged in §10, not enabled.

A failed sign-in now lands on a page. Every failure in the OAuth callback
used to raise `HTTPException`, which — because the customer arrives by
top-level navigation from Discord — rendered raw JSON on a blank page with
no way back, the most common cause being someone pressing Cancel. Browser
flows redirect to `/#/signup?error=<code>` with four allow-listed codes,
carrying the origin so the retry lands where they started. Nothing from
Discord's `error_description` reaches the URL. The CSRF check still fails
closed, before the authorization code is spent.

**Onboarding** (`frontend/src/v2/pages/Onboarding/`, migration 074): two
short steps, skippable, persisted so a member who finishes on desktop is
not asked again on their phone. Only two things are asked — coin budget
and whether they want price alerts — and both are used: the budget
pre-fills Trade Finder, the alert answer decides whether alerts are
offered. Nothing is stored that is never read. It ends on the member's
real free picks with a plain-language explanation of buy below / sell
target / expected profit / risk.

---

## 5. Pricing and upgrade

**One price, from Stripe.** `GET /api/billing/plans` reads
`stripe.Price.retrieve` and is the only source. Three contradictory
statements of the price existed (£3 typed into the billing page, £9.99 in
the subscription response, and whatever Stripe actually charged); all
three are gone. `headlinePrice()` returns `null` rather than a guess when
Stripe is unconfigured, and every caller renders nothing rather than
inventing a number. The existing price and billing configuration are
untouched — no new price was created.

**Locked states explain themselves.** `UpgradePrompt` always answers four
things: what the feature does, why it is paid, what you get, what it
costs. `gatedFeatures.js` holds that copy for all seven gated features,
and a test fails the build on "more tools", "enhanced access", "unlock
more" or "premium features" appearing in any of it.

**Contextual, not repetitive.** The prompt renders where the limit is met
— below the opportunities feed after the reader has seen their free picks,
on the empty Trade Finder, at the third alert. There are no site-wide
upgrade banners. On Opportunities exactly one prompt renders, below the
grid, asserted by test.

**Signed-out visitors are not asked to pay.** The prompt routes them to
the free account instead; asking for a card before there is an account
inverts the funnel.

No countdowns, no scarcity, no savings claims. Removed as unsupportable:
"Unlimited Watchlist" (Elite is 500), "Try Premium — 7 days free" beside a
plan that is not a trial, and "Limited Opportunities"/"Basic trade
tracking" as plan descriptions.

**The public site** now builds both plan cards from `PLAN_ROWS` — the same
list the app reads — so a difference stated in marketing is a difference
the product enforces, and adds a concise public track record that renders
nothing at all below the API's own sample threshold rather than quoting a
win rate from four calls.

---

## 6. API bypasses closed

1. **`/api/v2/opportunities`** returned the complete feed — entry, target,
   profit, ROI, reasoning — to any caller. This was the product.
2. **`/api/v2/trade-finder`** and its strategy tabs: entirely ungated.
3. **`/api/v2/companion/card/{id}`**: one card per request, no
   authentication, the full trade plan in the response. Iterating card ids
   rebuilt the paid feed for free, and the extension shipped with
   `credentials: "omit"`, so nothing about the design even suggested a
   session was expected. (The extension and userscript now send
   credentials.)
4. **`/api/v2/players/{card_id}`**: full trade plan on any card.
5. **`/api/v2/track-record/recent`**: the complete ledger, ungated.
6. **`/api/v2/dashboard`**: the current top-ranked BUY, complete, to
   anyone without the feature — signed-out callers included, since the
   endpoint needs no account. An uncounted third free pick that changed as
   the market moved.
7. **AI Chat as a proxy**: the assistant could evaluate any card, find
   trades for any budget and read the full track record for a Free member.
   Gated in the server-side tool layer — a blocked tool returns a result
   telling the model to say so plainly rather than guess.
8. **Checkout parameters from the request body**: `trialDays`
   (`{"trialDays": 100000}` bought roughly 273 years of Elite),
   `success_url` and `cancel_url`. All three are server-side now.
9. **No alert or open-trade limit existed at all**; both are enforced.
10. **`/api/v2/trades/history`** returned all history to everyone; now
    windowed in SQL, not filtered after the fact.

Also fixed, in the opposite direction: a paying Stripe subscriber resolved
to tier `"pro"` while `opportunity_feed` required `"elite"`, so a customer
was refused the feed they had just bought.

---

## 7. Stripe and Discord lifecycle

- Access is granted only from stored server-side state. The success URL is
  built server-side, carries no grant, and `confirmUpgrade()` polls
  `/api/me` for the webhook to land rather than trusting the return.
  Tests assert the checkout handler writes no entitlement state.
- `customer.subscription.created`, `.updated` and `.deleted` each write
  the subscription row and profile flags, invalidate the entitlements
  cache, and enqueue a Discord role reconciliation; cancellation revokes
  the role explicitly.
- `invoice.payment_failed` recorded the payment but did not invalidate the
  cache. Stripe also sends `.updated` for the same failure, but the two
  events arrive independently and in either order, so an entitlement
  cached between them described a billing state that no longer existed.
  Fixed.
- `past_due` keeps access for `PAST_DUE_GRACE_DAYS` (5) while Stripe
  retries. Cutting a paying customer off the instant a card expires, before
  Stripe has finished retrying, is the worse failure.
- `cancel_at_period_end` does not revoke: the customer keeps what they paid
  for until the period ends.
- Entitlements change without a new login. The tier is a pure function of
  stored billing state with no session in its signature; the cache TTL is
  60s and is invalidated on every state change.
- A 402 from any endpoint now triggers an entitlement refresh
  (throttled, never a local downgrade). Nothing listened to axios's
  `premium:blocked` event before, so a member whose subscription had just
  ended kept a fully unlocked interface until the five-minute poll caught
  up.
- Account deletion revokes the Discord role, deletes the onboarding row,
  invalidates the cache, and never touches billing history.

---

## 8. Conversion measurement

No third-party provider was added. `frontend/src/lib/analytics.js` plus
`POST /api/v2/events/track` (migration 075) are the whole system.

Sixteen events: `signup_viewed`, `signup_started`, `signup_completed`,
`onboarding_started`, `onboarding_completed`, `onboarding_skipped`,
`free_opportunity_viewed`, `free_limit_reached`, `upgrade_prompt_viewed`,
`pricing_viewed`, `checkout_started`, `checkout_completed`,
`checkout_cancelled`, `first_watchlist_card_added`, `first_alert_created`,
`first_portfolio_trade_added`.

Seven permitted property keys: `tier`, `surface`, `feature`, `plan`,
`step`, `count`, `reason`. Strings truncate at 40 characters; nested
structures are dropped, since a dict under an allowed key is the obvious
way past a key-name filter.

**Never recorded**: player searches, chat text, email addresses, Discord
identities, IP addresses, user agents. `user_id` is the internal id, is
nullable, and every signed-out funnel step records no identity at all.
The allow-list is applied on the server as well as the client — a
client-side filter is a convenience, not a control — and a test asserts
the two lists match, so an event the client fires that the server would
silently discard fails the build.

The endpoint always returns 200: an unknown event, a storage failure and a
missing table are all `recorded: false`. Instrumentation must never fail
the action that triggered it.

**If a provider is adopted later**: keep this endpoint as the only call
site and forward from the server. Every event already passes through one
function with the allow-list applied, which is the part that is hard to
retrofit.

---

## 9. Tests

**Backend: 1227 passing, 58 skipped** (1056 at the start of this phase,
measured on `b4dcb97`). New files:

- `test_access_control.py` (38) — preview allow-lists for both payload
  shapes, the anonymous/free/elite matrix, Home's feed gate, a new
  premium field not leaking by default, OAuth failure routing, account
  deletion.
- `test_free_picks.py` (27) — defensibility gates, determinism, budget
  spread, the one-player rule, thin days, and that the pool takes nothing
  a caller controls.
- `test_content_day.py` (23) — both DST transitions, the 23/25-hour days,
  stability within a day, and that the day seed reaches the tie-break.
- `test_subscription_lifecycle.py` (38) — every Stripe state, the dunning
  grace, Discord-vs-payment in both directions, entitlement change without
  re-login, and that no gated feature names a tier nothing resolves to.
- `test_billing_hardening.py` (9) — the three client-controlled checkout
  parameters and the single price source.
- `test_funnel_events.py` (19) — what the event endpoint stores, what it
  drops, and client/server allow-list parity.
- Additions to `test_companion_router.py` (9 in the file), `test_ai_chat_tools.py`
  (6 fair-use tests; 20 in the file).

**Frontend: 136 passing** (83 at the start of this phase). New: `UpgradePrompt.test.jsx` (7),
`Opportunities.locked.test.jsx` (8), `TradeFinder.locked.test.jsx` (5),
`HomeDashboard.locked.test.jsx` (6), `Signup.test.jsx` (14),
`EntitlementsContext.stale.test.jsx` (5), plus 8 added to
`PublicSite.test.jsx`.

**Browser**: a Playwright harness renders the built app as signed out,
free and elite at 1440×900 and 390×844 across Opportunities, Trade Finder,
Billing and Signup with stubbed APIs, asserting that no executable number
appears on any locked card, that each lock explains itself, that Elite
sees no lock, and that nothing overflows horizontally. All checks pass, as
do 49/49 route checks.

No existing test was weakened and no entitlement result is hardcoded to
satisfy a fixture. When 25 tests failed after the gating went in, they
were fixed by giving each test an explicit viewer via
`dependency_overrides`, not by loosening the gate. One genuine test-infra
fix: `test_billing_hardening.py` imported `main` without setting the
environment `main` validates at import, so it passed or failed depending
on which other module in the run had happened to export `DATABASE_URL`.
It now sets its own placeholders; no assertion changed.

---

## 10. Migrations, deployment and blockers

**Migrations** — all three additive and reversible, no existing table or
column altered:

| | Table | Target DB | Rollback |
|---|---|---|---|
| 073 | `free_daily_picks` | core | `DROP TABLE free_daily_picks;` |
| 074 | `member_onboarding` | core | `DROP TABLE member_onboarding;` |
| 075 | `funnel_events` | core | `DROP TABLE funnel_events;` |

Each degrades honestly if unapplied: free picks fall back to an in-memory
selection that is still deterministic for the day; onboarding returns
`needsOnboarding: false, degraded: true` rather than trapping anyone in a
loop; funnel events return `recorded: false`. **Deploy order does not
matter** — the application tolerates all three tables being absent.

**Environment variables** — every one has a working default; none is
required to deploy. `AI_CHAT_RATE_LIMIT_PER_DAY` (200) is new this phase.
`REDIS_URL`, if set, gives the AI chat limits a fleet-wide ceiling instead
of a per-worker one.

**Blockers and things left deliberately undone:**

1. **Email/password auth is unfinished.** `app/routers/auth_email.py` has
   no email verification and no password reset. Signup offers Discord
   only. Finishing it is a separate piece of work; putting it in front of
   customers as-is would be shipping an incomplete auth system.
2. **No `og:image` exists in the repo.** `index.html` omits it rather than
   pointing at a made-up asset. Every social share of eafc.live is
   currently text-only. Needs a real image.
3. **Discord role reconciliation is queue-based and best-effort.** It is
   enqueued on every subscription change and retried, but there is no
   periodic sweep that reconciles every account against billing state, so
   an account whose queue entry was dropped during an extended Discord
   outage stays out of step until its next billing event. Recommended, not
   built here — it is a background job, not an access-control change.
4. **`funnel_events` has no retention policy.** It grows unbounded. A
   monthly delete of rows older than a year is the obvious follow-up; it
   is not urgent at current volume and adding an unrequested scheduled job
   was out of scope.
5. **SBC Hub and Market Map were not touched**, as instructed.
6. **The public track record renders nothing until there is published
   data above `PROOF_MIN_SAMPLE`.** This is correct, but it does mean the
   marketing page has one fewer section until the Discord proof ledger has
   enough graded calls. Worth knowing before launch rather than
   discovering it live.

---

## 11. Files changed

67 files, +6390 / −370, across 11 commits.

**Backend — access model**: `app/auth/entitlements.py`,
`app/auth/access.py` (new), `app/services/content_day.py` (new),
`app/services/free_picks.py` (new).

**Backend — endpoints**: `app/routers/v2/futgg_market.py`,
`dashboard.py`, `companion.py`, `recommendations.py`, `ai_chat.py`,
`trades.py`, `onboarding.py` (new), `events.py` (new), `__init__.py`,
`app/routers/watchlist.py`, `main.py`.

**Backend — migrations**: `073_free_daily_picks.sql`,
`074_member_onboarding.sql`, `075_funnel_events.sql` (all new).

**Backend — tests**: `test_access_control.py`, `test_free_picks.py`,
`test_content_day.py`, `test_subscription_lifecycle.py`,
`test_billing_hardening.py`, `test_funnel_events.py` (all new);
`test_companion_router.py`, `test_ai_chat_tools.py`,
`test_futgg_market_router.py`, `test_strategy_finder.py` (updated).

**Frontend — new**: `lib/analytics.js`, `v2/lib/limits.js`,
`v2/lib/gatedFeatures.js`, `v2/components/UpgradePrompt.jsx`,
`v2/components/PlanComparison.jsx`, `v2/hooks/useBillingPlans.js`,
`v2/hooks/useOnboarding.js`, `v2/pages/Onboarding/`, `pages/signup.css`,
`public/robots.txt`, and six test files.

**Frontend — changed**: `PublicSite.jsx`, `pages/Signup.jsx`,
`context/AuthContext.jsx`, `context/EntitlementsContext.jsx`,
`v2/App.v2.jsx`, `App.jsx`, `v2/components/Sidebar.jsx`,
`v2/pages/Opportunities/`, `TradeFinder/`, `Billing/`, `Account/`,
`Chat/`, `HomeDashboard/`, and their stylesheets.

**Clients**: `eafc-live-extension/src/background.js` and
`eafc-live-userscript/src/api-client.js` now send credentials, so the
overlay authenticates instead of relying on an open endpoint.
