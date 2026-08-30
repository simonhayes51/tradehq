# Deployment and rollback checklist

For the Free/Elite launch. Written to be followed in order; each step says
how to tell whether it worked, because "it deployed" is not the same as
"it works".

Nothing here was run against production as part of preparing it.

---

## 0. Before you start

- [ ] Know which commit is currently live. `git rev-parse HEAD` on the
      running deployment, or the platform's release id. **Rollback is not
      possible without this.**
- [ ] Confirm `JWT_PRIVATE_KEY` is set and is not `dev-secret-change-me`.
      The app hard-fails on that in production, but check before deploying,
      not after.
- [ ] New environment variables this phase adds — all have working
      defaults, so none blocks a deploy:

  | variable | default | notes |
  |---|---|---|
  | `AI_CHAT_RATE_LIMIT_PER_DAY` | 30 | was effectively 200 |
  | `AI_CHAT_RATE_LIMIT_PER_MONTH` | 300 | new |
  | `AI_CHAT_MAX_CONCURRENT` | 1 | new |
  | `AI_CHAT_MAX_HISTORY_MESSAGES` | 10 | new |
  | `AI_CHAT_MAX_OUTPUT_TOKENS` | 600 | new |
  | `AI_CHAT_ENABLED` | 1 | kill switch |
  | `AI_CHAT_PRICE_PER_1M_INPUT` / `_OUTPUT` | 0.15 / 0.60 | **unverified list rates** |
  | `COMPANION_TOKEN_TTL_HOURS` | 24 | new |
  | `DISCORD_RECONCILE_INTERVAL_SECONDS` | 21600 | 0 disables |
  | `FUNNEL_RETENTION_DAYS` | 365 | |
  | `FUNNEL_RETENTION_INTERVAL_SECONDS` | 86400 | 0 disables |
  | `BILLING_PLANS_CACHE_TTL` | 600 | |
  | `VITE_COMPANION_EXTENSION_IDS` | — | frontend; enables one-click connect |

---

## 1. Database backup

- [ ] Take a fresh backup of the **core** database and, if separate, the
      **watchlist** database.
- [ ] Verify the backup file is non-empty and its timestamp is from this
      run. An untested backup is a hope, not a rollback plan.

Migrations 073–078 are all additive (`CREATE TABLE`, one `ADD COLUMN`), so
a rollback does not require restoring — but take the backup anyway.

---

## 2. Deploy the backend

- [ ] Deploy.
- [ ] `GET /health` → `200 {"status":"healthy"}`.
      A 503 here means the core database is unreachable: **stop and roll
      back** (step 10).
- [ ] Check the logs for the worker start lines:
      `Discord role reconciliation started`,
      `Funnel-event retention started`.

---

## 3. Verify migrations

- [ ] `GET /api/admin/readiness` as an admin.
- [ ] `checks[].name == "migrations"` is `ok`.
      If it is `degraded` with `reason: "pending"`, `detail.missing` names
      exactly which migrations have not run — apply them and re-check.
- [ ] Confirm the whole report: `core_database` ok; `stripe` ok (not
      `no_webhook_secret`); `discord` ok or a deliberate `unconfigured`;
      `funnel_events` ok.

**Degraded is not necessarily a stop.** Every feature behind a pending
migration degrades honestly: free picks fall back to an in-memory
selection, onboarding returns `degraded: true`, funnel events return
`recorded: false`. The product serves. Decide deliberately; do not drift
into it.

---

## 4. Deploy the frontend

- [ ] Deploy.
- [ ] Load `https://eafc.live` signed out. Pricing shows a real price, not
      "pricing unavailable" — that string means Stripe could not be read
      (see step 6).
- [ ] Load `https://app.eafc.live` and confirm the app shell renders.

---

## 5. Free and Elite smoke accounts

**Signed out**
- [ ] Search a player. Results appear.
- [ ] Prices are labelled **AROUND**, not LIVE PRICE.
- [ ] Open a card. The public preview renders with an approximate price
      and a "Create free account" CTA — not a blank analysis.
- [ ] DevTools → Network → the `/api/v2/players/...` response contains
      **no** `current_bin`, `fair_value`, `signal` or
      `recommended_sell_target`. This is the check that matters: the
      fields must be *absent*, not hidden.

**Free**
- [ ] Sign in with a free smoke account.
- [ ] A card page shows the exact live price and fair value.
- [ ] Opportunities shows the day's free picks complete, everything else
      locked, and the "next picks at 6pm UK" line.
- [ ] Trade Finder shows the Elite lock, not an error.
- [ ] Watchlist accepts up to 10 cards and refuses the 11th with a 402.

**Elite**
- [ ] Sign in with an Elite smoke account.
- [ ] Opportunities shows the full feed with no locked cards.
- [ ] Trade Finder loads results.
- [ ] AI Chat answers one question.

---

## 6. Stripe verification

Use **test mode** if at all possible. If verifying in production, use a
real card and refund immediately.

- [ ] `GET /api/billing/plans` returns `any_configured: true` and
      `stale: false`.
- [ ] Complete one checkout end to end.
- [ ] The account becomes Elite **without signing out and in again**
      (entitlements resolve from stored state per request, 60s cache).
- [ ] `GET /api/admin/funnel?window=today` shows
      `upgrade.subscriptions_confirmed` incremented — this is the
      webhook-confirmed number, not the browser's.
- [ ] Cancel the test subscription. Access persists to the period end.

---

## 7. Discord roles

- [ ] `GET /api/admin/discord-reconciliation` responds.
- [ ] Dry run first, and read it before doing anything:

      python -m scripts.reconcile_discord_roles --dry-run --limit 50

- [ ] `roles_granted` / `roles_revoked` are plausible. **A large
      `roles_revoked` on the first run is a red flag** — investigate
      before running for real; it can mean an entitlement resolution
      problem, not a Discord drift problem.
- [ ] Then, if it looks right:

      python -m scripts.reconcile_discord_roles --limit 50
      python -m scripts.reconcile_discord_roles --status

---

## 8. Extension and userscript

Per `docs/COMPANION_AUTH.md`, cookie behaviour cannot be verified from a
test suite. Check on real devices:

- [ ] Chrome + extension, Elite account signed in → overlay shows the
      trade plan.
- [ ] Signed out → overlay shows the locked card and a signup prompt, not
      an error.
- [ ] Account page → **Connect companion** issues a token; if the
      extension is installed it reports "Sent to your installed
      extension".
- [ ] Tampermonkey with the token pasted in → overlay shows the trade plan.
- [ ] iOS Userscripts app with the token → same. **This is the client most
      likely to need the token**, because `GM_xmlhttpRequest`
      `withCredentials` support is manager-dependent.

---

## 9. Analytics recording

- [ ] Visit the signup page signed out, then check
      `GET /api/admin/funnel?window=today`:
      `signed_out.signup_page_views` incremented.
- [ ] `tracking.status` is `ok`, not `failing`.

`failing` means events are being silently dropped — the endpoint returns
200 by design, so this is the only place it is visible.

---

## 10. Rollback

Judged per symptom, because most of this phase degrades rather than breaks.

**Application rollback** (redeploy the previous commit) if:
- `/health` returns 503 after deploy
- signed-in members cannot see prices they are entitled to
- checkout fails or subscriptions do not activate

Redeploy the commit recorded in step 0. **Do not roll back migrations** —
they are additive, and the previous application version ignores the new
tables and the new `funnel_events.source` column entirely.

**Targeted rollback, no redeploy.** Prefer these; each is a config change:

| symptom | action |
|---|---|
| AI cost higher than expected | `AI_CHAT_ENABLED=0` — switches the assistant off, read per request |
| AI limits too tight for real use | raise `AI_CHAT_RATE_LIMIT_PER_DAY`, check `/api/admin/ai-usage` first |
| Discord sweep behaving badly | `DISCORD_RECONCILE_INTERVAL_SECONDS=0` |
| Retention deleting unexpectedly | `FUNNEL_RETENTION_INTERVAL_SECONDS=0` |
| Companion tokens leaked | rotate `JWT_PRIVATE_KEY` — revokes every outstanding token (also invalidates extension trade-ingest tokens) |
| Pricing showing stale | it is *meant* to; check `stale` in `/api/billing/plans` and fix Stripe |

**Migration rollback**, only if genuinely required — each drop loses that
feature's data:

```sql
DROP TABLE discord_reconciliation_runs;          -- 078
ALTER TABLE funnel_events DROP COLUMN source;    -- 077
DROP TABLE ai_usage_events;                      -- 076
DROP TABLE funnel_events;                        -- 075
DROP TABLE member_onboarding;                    -- 074
DROP TABLE free_daily_picks;                     -- 073
```

---

## 11. First 24 hours

- [ ] `GET /api/admin/ai-usage?days=1` — check `heaviest_user_cost`
      against the £3 subscription. **This is the number that decides
      whether the daily limit is right**, not the average.
- [ ] **Verify the AI pricing against one real provider invoice.** The
      configured rates are unverified list prices; every cost figure in
      the admin view is wrong by whatever margin they are wrong by.
- [ ] `GET /api/admin/funnel?window=today` — signups, activation rate,
      confirmed subscriptions.
- [ ] `GET /api/admin/readiness` — still `ok` or a known `degraded`.
