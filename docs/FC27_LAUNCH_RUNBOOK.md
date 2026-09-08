# FC27 Launch Runbook

Launch target: FC27 Early Access. Treat Early Access as the operational launch, not the worldwide release.

## Launch principles

- Do not change recommendation thresholds, collector budgets or market logic during launch unless there is a confirmed correctness incident.
- Fail closed when market evidence is weak. A high `insufficient_data` rate early in a new game is safer than invented confidence.
- Prefer fixing correctness, freshness and availability over adding features.
- Personal Elite trials renew into the selected paid plan unless cancelled before the trial ends. Trial and cancellation wording must remain explicit anywhere the trial is promoted.

## Acquisition attribution

Partner and campaign links should use short operator-defined codes:

- `https://eafc.live/?ref=<partner-code>`
- `https://eafc.live/?ref=<partner-code>&utm_campaign=fc27-launch`

Examples:

- `https://eafc.live/?ref=server-alpha&utm_campaign=fc27-launch`
- `https://eafc.live/?ref=tiktok&utm_campaign=fc27-early-access`

The frontend stores only the bounded `source` and `campaign` codes for the browser session and includes them in existing first-party funnel events. Do not put usernames, Discord IDs, emails or free text into these parameters.

Core funnel events to compare by source/campaign:

1. `signup_viewed`
2. `signup_started`
3. `signup_completed`
4. `pricing_viewed`
5. `checkout_started`
6. `subscription_confirmed` (authoritative paid conversion; server-side)
7. first-use events: watchlist, alert and portfolio trade

Do not treat `checkout_completed` as authoritative revenue. It means the browser returned from Stripe. `subscription_confirmed` is the paid event.

## Partner launch offer

Default launch arrangement:

- partner receives EAFC.Live bot/server feed access for launch promotion
- keep feed quality high and volume deliberately limited
- EAFC.Live branding and site link remain on feed posts
- partner gives one launch announcement and at least one Early Access reminder
- every partner receives a unique `ref` code

Track signups and paid conversions by partner rather than ranking partners by server member count.

## Content priorities

### Before Early Access

- FC27 starter/value lists
- players likely to be overpriced or extinct
- market education: tax, completed sales, liquidity, fair value, entry ceiling vs executable basis
- product teasers framed around decisions, not architecture
- CTA: track the FC27 market / start the Elite trial on EAFC.Live

### During Early Access

Prioritise reactive evidence over prebuilt generic posts:

- genuine EAFC.Live BUY/STRONG_BUY transitions
- fast market moves
- completed-sale evidence
- successful calls with entry, exit/current price, after-tax result and elapsed time
- failed/invalidated calls when useful for credibility

Never present a stale or expired intelligence state as a fresh call.

## Launch monitoring

### Market ingestion

Watch continuously:

- FC27 cards discovered
- tradeable cards
- cards with current BIN
- cards with completed sales
- BIN age p50/p95
- completed-sales age p50/p95
- collector cycle duration
- FUT.GG failures / throttling
- no-active-market rate

### Intelligence

Watch:

- current-intelligence coverage
- intelligence age
- scanner runtime
- BUY / STRONG_BUY / WATCH counts
- `insufficient_data` rate
- stale or expired current states
- transitions per hour

### Product/API

Watch:

- API latency p50/p95/p99
- 5xx rate
- DB pool saturation
- CPU and memory
- Dashboard latency
- Opportunities latency
- Trade Finder latency
- player-page latency
- login/auth errors
- Stripe checkout/webhook errors
- Discord publisher/command errors

### Commercial

Watch by source/campaign:

- signup views and starts
- completed registrations
- onboarding completion
- pricing views
- checkout starts
- confirmed subscriptions
- partner source conversion rate
- trial starts
- cancellations when available from billing state

## Operational alert thresholds

These are launch defaults, not permanent SLOs. Adjust only from observed production evidence.

### Critical

- scanner has not completed for 15 minutes
- market collector stops or freshness coverage collapses sharply
- sustained API 5xx rate above 2%
- sustained API p95 above 3 seconds on core customer routes
- DB pool exhaustion
- Stripe webhook/checkout path failing broadly
- Discord publisher stops unexpectedly during an active posting window

### Warning

- scanner runtime above 8 seconds repeatedly
- current-intelligence coverage falling materially
- unusual spike in stale/expired current intelligence
- sales/BIN freshness materially outside normal launch baseline
- signup/checkout errors increasing
- funnel-event tracking health degraded or failing

## Launch-day change policy

Allowed during launch:

- correctness fixes
- availability/performance fixes
- broken customer journey fixes
- monitoring/observability fixes
- copy fixes where pricing/trial/renewal information is unclear

Avoid during launch:

- threshold tuning from tiny samples
- increasing SalesSync request budget to chase coverage
- publishing Investment Engine before evidence gates are satisfied
- broad redesigns
- legacy cleanup that is not causing a production problem

## Quick launch smoke test

Run with a fresh, non-admin account:

1. land via a partner `?ref=` link
2. create account
3. finish or skip onboarding
4. open a free opportunity
5. hit a real Free allowance and see the correct upgrade prompt
6. open Billing and verify live Stripe pricing
7. verify the trial explicitly says it renews unless cancelled before the end
8. start checkout
9. confirm entitlement updates after Stripe webhook
10. add watchlist card
11. create alert
12. log a portfolio trade
13. open Opportunities, Trade Finder, Dashboard and player detail on mobile
14. install/use Discord flow
15. verify Admin readiness/data-quality/funnel health

If a smoke-test step fails, treat it as a launch blocker until understood.
