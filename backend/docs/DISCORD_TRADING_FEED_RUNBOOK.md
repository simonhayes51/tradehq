# Discord trading feed — staging/production smoke test runbook

This covers running the smoke test for the multi-server Discord trading
feed (migration 069 + `app/services/discord_trading_feed.py` +
`app/routers/discord_admin.py` + `app/routers/discord_interactions.py`).

**Cannot be run from a Claude Code Remote sandbox session**: that
environment's egress proxy explicitly does not support raw-TCP database
connections, and `discord.com` is not on its allowed egress list. Run this
from somewhere with real network access to both your database and Discord
— your own machine, a Railway shell, or a CI job.

## 1. Environment variables

```
export DATABASE_URL=...              # same one the live app uses
export DISCORD_BOT_TOKEN=...
export DISCORD_APPLICATION_ID=...    # "Application ID" / client ID, Developer Portal → General Information
export DISCORD_PUBLIC_KEY=...        # "Public Key", same page
export DISCORD_GUILD_ID=...          # your official server's guild ID
export DISCORD_TEST_CHANNEL_ID=...   # a dedicated #trading-feed-test channel — NOT a real channel
export DISCORD_ADMIN_ID=...          # optional: your user/Discord id, for audit-log attribution
```

Do **not** set `DISCORD_TRADING_FEED_ENABLED` — leave it unset (or `0`).
Both scripts below refuse to continue if it's `1`.

## 2. Apply migration 069 + run the smoke test

```
cd backend
python scripts/discord_trading_feed_smoke_test.py
```

This single script performs, in order:

1. Applies any pending core migrations (including 069) via
   `scripts/run_migrations.py`'s own `run()` — the same code path your
   deploy already runs on every boot — and confirms `discord_servers`
   exists and `069_discord_trading_feed.sql` is recorded.
2. Registers (or finds) your official server as a `discord_servers` row,
   points its `feed_channel_id` at `DISCORD_TEST_CHANNEL_ID`, and sets
   `status='active'` **only for the duration of this run**.
3. Confirms `DISCORD_TRADING_FEED_ENABLED` is not `1` and aborts if it is.
4. Sends a synthetic Free daily overview (fixed fixture numbers, not a
   real query) to the test channel.
5. Sends a synthetic Paid-Partner-style opportunity post (fixed fixture
   card, clearly labelled `[TEST]`) to the test channel.
6. Sends a synthetic target-hit update and a synthetic closed/loss
   update, both fixture-based.
7. Exercises duplicate prevention, the daily-allowance/cooldown counters,
   pause/resume, and paid-partner expiry gating — all against real
   database round-trips, using disposable rows it cleans up itself
   (except the real posts from steps 4-6, left for you to inspect in the
   test channel).
8. Runs a real player autocomplete query and every slash command's
   actual reply-building logic (`/price /fairvalue /watchlist /alerts
   /results /account /help`) directly against the database — this does
   **not** require the Interactions endpoint to be deployed, since this
   logic never calls out to Discord itself, only the database and the
   opportunity engine — and confirms an unlinked Discord id gets the
   "link your account" message rather than any account data. Also drives
   the per-user rate limiter past its threshold and confirms it engages.
   The live in-Discord half (Discord actually calling the deployed
   endpoint for autocomplete-as-you-type and the deferred-reply
   round-trip) still needs the endpoint deployed and reachable — see
   §4 below.
9. Reads (never writes) `discord_role_sync_queue`, `discord_publications`,
   and `discord_daily_proof_publications` row counts, to make explicit
   that this run touched none of the pre-existing role-sync / opportunity
   digest / Proof Ledger tables.
10. Resets the server's `status` back to `'paused'` and deletes only its
    own disposable `smoketest-*` rows, so nothing is left active.

It prints a full report to stdout as it goes; redirect to a file if you
want to keep it (`python scripts/discord_trading_feed_smoke_test.py | tee smoke_test_report.txt`).

If any assertion fails, the script stops there (it does not attempt to
auto-fix or continue past a failure) — the last printed section name
tells you which step failed.

## 3. Register the slash commands with Discord

```
python scripts/register_discord_commands.py
```

Registers `/price /fairvalue /watchlist /alerts /results /account /help`
as **guild-scoped** commands (visible in `DISCORD_GUILD_ID` within
seconds — global commands can take up to an hour to propagate). Safe to
re-run any time; it replaces the guild's command set idempotently.

To roll back (remove them again):

```
python scripts/register_discord_commands.py --clear
```

## 4. Configure the Interactions Endpoint URL (Developer Portal)

This step needs the branch actually deployed somewhere reachable over
HTTPS first — do this only once you've reviewed and deployed the code,
not as part of the smoke test above.

1. Deploy this branch so `/api/discord/interactions` is reachable at your
   API's public URL (e.g. `https://api.eafc.live/api/discord/interactions`).
2. In the Discord Developer Portal → your application → **General
   Information** → **Interactions Endpoint URL**, enter that URL and
   save.
3. Discord immediately sends a `PING` to that URL to verify it. Our
   handler in `app/routers/discord_interactions.py` responds `PONG` only
   after verifying the request's ed25519 signature against
   `DISCORD_PUBLIC_KEY` — if the Portal shows a green checkmark / saves
   without an error, signature verification against the *real* Discord
   signing key succeeded end-to-end. If it shows an error, check:
   - `DISCORD_PUBLIC_KEY` is set in the deployed environment and matches
     the Developer Portal's "Public Key" exactly (64 hex characters).
   - The endpoint is reachable (no auth/proxy in front of it blocking
     Discord's IPs) and returns within Discord's timeout.
4. Once that's green, typing any of the seven commands in your server is
   the final live check — each should reply within a few seconds, with
   `/watchlist`, `/alerts`, `/results`, and `/account` visible only to
   you (ephemeral).

## 5. Activating your official server for real (only when you approve it)

The smoke test above leaves your official server `status='paused'` and
`DISCORD_TRADING_FEED_ENABLED` unset. To actually turn the feed on:

1. Set `DISCORD_TRADING_FEED_ENABLED=1` in the deployed environment (the
   global kill switch — nothing posts anywhere while this is unset/`0`,
   regardless of any server's own status).
2. Through an authenticated admin session, call:
   - `PUT /api/admin/discord/servers/{server_id}/channel` with the real
     channel(s) you want (`feed_channel_id`, and `proof_channel_id` if
     different from the existing Proof Ledger channel).
   - `PUT /api/admin/discord/servers/{server_id}/allowance` to set
     `live_opportunities_enabled: true` plus whatever
     `max_posts_per_day` / `min_confidence` / `min_expected_profit` /
     `cooldown_minutes` / `platform` you want for live opportunities (all
     already default to conservative values from migration 069 if you
     skip this).
   - `POST /api/admin/discord/servers/{server_id}/resume` — this is the
     switch that sets `status='active'`.
3. Confirm with `GET /api/admin/discord/servers/{server_id}/status` and
   `GET /api/admin/discord/servers/{server_id}/posts` shortly after — the
   first real daily overview / opportunity post will appear on the next
   worker pass (`DISCORD_TRADING_FEED_POLL_SECONDS`, default 300s).

To pause again at any time: `POST /api/admin/discord/servers/{server_id}/pause`,
or simply unset `DISCORD_TRADING_FEED_ENABLED` to stop every server at once.

## 6. Registering a Paid Partner server (for later, not part of this smoke test)

`POST /api/admin/discord/servers` with `access_level: "paid_partner"`,
then the same channel/allowance/resume sequence as above — except
`PUT .../allowance` will refuse to activate a Paid Partner server without
an `expires_at` set, per the "manual enable with an expiry date"
requirement.

## 7. Self-service `/feed` command group (server owners)

As an alternative to the admin API above, a server owner with the
**Manage Server** permission can set up and manage their own paid feed
entirely from Discord via `/feed setup`, `/feed test`, `/feed activate`,
`/feed status`, `/feed channel`, `/feed preferences`, `/feed pause`,
`/feed resume`, `/feed billing`, and `/feed help`. This is billing-backed
(Stripe subscriptions: "Server Feed" £9.99/mo up to 6 posts/day, "Server
Feed+" £19.99/mo up to 12/day) and fully independent of the admin flow
above — either can register/manage a `discord_servers` row for a given
guild, but a guild only ever has one row.

**New environment variables required before `/feed setup` can create a
real checkout session:**

```
STRIPE_SERVER_FEED_PRICE_ID=price_...       # create this Price in Stripe Dashboard first
STRIPE_SERVER_FEED_PLUS_PRICE_ID=price_...  # £9.99/mo and £19.99/mo recurring subscription Prices
```

These are **not** created for you — set up two recurring subscription
Prices in the Stripe Dashboard (test mode first) and put their IDs here.
Without them, `/feed setup` replies with a friendly "billing isn't fully
configured yet" message rather than failing silently.

**Re-run command registration** after pulling this change — `/feed` is a
new top-level command with subcommands, registered alongside the existing
seven by the same script:

```
python scripts/register_discord_commands.py
```

**Stripe webhook**: no new endpoint — `/feed setup`'s checkout sessions
and subscriptions are tagged `metadata.kind = "discord_feed"` and routed
inside the existing `/api/webhooks/stripe` handler in `main.py`, so no
new webhook needs registering in the Stripe Dashboard; your existing
`STRIPE_WEBHOOK_SECRET` already covers it.

**What to smoke-test for `/feed`** (same network caveats as §1 apply —
run from an environment with real Discord + Stripe + DB access):
- `/feed setup` as a non-Manage-Server member → permission-denied reply.
- `/feed setup` in a DM → "server-only command" reply.
- `/feed setup` with a channel the bot can't post in → rejected before
  anything is saved.
- `/feed setup` happy path → ephemeral summary + a real Stripe Checkout
  link (test mode); complete payment there.
- `/feed test` before and after payment (spec allows it before payment).
- `/feed activate` before a test post / before payment → clear rejection
  reasons; after both → succeeds.
- `/feed status` shows plan, subscription state, channel, platform,
  budget, posts used/allowance, paused/active, paid-through date.
- `/feed pause` / `/feed resume`, `/feed billing` (opens the Stripe
  customer portal), `/feed preferences` (try a budget mix that would
  exceed the plan's daily cap — must be rejected).
- Cancel the Stripe subscription from the portal and confirm a
  `customer.subscription.deleted` webhook pauses the feed automatically.

Keep `DISCORD_TRADING_FEED_ENABLED` unset throughout — `/feed activate`
only flips that one guild's own `status`/`live_opportunities_enabled`
flags; the global switch still gates all real posting everywhere.
