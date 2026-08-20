# EAFC Live Discord production setup

## Environment variables

| Variable | Where it goes | Value / format | Secret? | Required? | Purpose |
| --- | --- | --- | --- | --- | --- |
| `DISCORD_CLIENT_ID` | Coolify backend | Discord application ID (snowflake) | No | Required | Existing Discord OAuth login |
| `DISCORD_CLIENT_SECRET` | Coolify backend | Discord OAuth client secret | Yes | Required | Existing OAuth code exchange |
| `DISCORD_REDIRECT_URI` | Coolify backend | `https://<backend-host>/auth/discord/callback` | No | Required | Existing OAuth callback; must exactly match Discord |
| `DISCORD_BOT_TOKEN` | Coolify backend only | Bot token from Developer Portal | Yes | Required | Guild membership checks, Member role sync, channel posts |
| `DISCORD_GUILD_ID` | Coolify backend | EAFC Live server ID | No | Required | Official server to reconcile |
| `DISCORD_MEMBER_ROLE_ID` | Coolify backend | `EAFC.Live Member` role ID | No | Required | The only paid role managed at launch |
| `DISCORD_INVITE_URL` | Coolify backend | Permanent `https://discord.gg/...` invite | No | Required | Account join CTA |
| `DISCORD_OPPORTUNITY_CHANNEL_ID` | Coolify backend | `#opportunity-alerts` channel ID | No | Optional | Enables non-actionable batched opportunity notices |
| `DISCORD_PROOF_CHANNEL_ID` | Coolify backend | `#proof-ledger` channel ID | No | Optional | Enables closed 24-hour outcome proof posts |
| `DISCORD_RECONCILE_INTERVAL_SECONDS` | Coolify backend | Integer seconds; default `60` | No | Optional | Durable role/publication worker interval |
| `DISCORD_OPPORTUNITY_BATCH_MIN` | Coolify backend | Integer; default `3` | No | Optional | Minimum new qualifying cards before a digest |
| `VITE_DISCORD_INVITE_URL` | Coolify frontend build args/env | Same permanent invite URL | No | Required for configured deployment | Public-site, billing, and access-denied Discord links |

Legacy `DISCORD_SERVER_ID` and `DISCORD_PREMIUM_ROLE_ID` remain fallback aliases for one deployment cycle. Set the canonical names above and remove the legacy names after verification. Never put `DISCORD_BOT_TOKEN` or `DISCORD_CLIENT_SECRET` in frontend variables.

## Discord Developer Portal

1. Open the existing EAFC Live application; do not create a second application or identity.
2. In **OAuth2**, retain only the user login scope `identify`. Do not add `email`, `guilds`, or `guilds.join`.
3. Add the exact production `DISCORD_REDIRECT_URI` under OAuth2 Redirects. Keep any genuine local/staging callback separately.
4. In **Bot**, reset/copy the bot token once and place it only in the Coolify backend secret.
5. Privileged Gateway Intents: leave Presence and Message Content off. This integration does not connect to the Gateway or list guild members, so Server Members Intent is not required.
6. Install the existing bot to the EAFC Live server with the `bot` install scope. No slash-command scope is needed at launch.
7. Grant the bot role `Manage Roles`. Do not grant Administrator, Manage Server, Manage Channels, Manage Webhooks, Manage Messages, Kick, Ban, Mention Everyone, or DM-related capabilities.

## Discord server

1. Enable Developer Mode in Discord, then copy the server ID, Member role ID, output channel IDs, and a permanent invite URL into the variables above.
2. Create `EAFC.Live Member`. It controls `#member-desk`, `#opportunity-alerts`, and `#product-feedback` visibility/access.
3. Put the bot's managed role above `EAFC.Live Member` in the role list. It may sit below Team, Moderator, Founding Member, Partner, and Creator because it does not manage those roles.
4. Give the bot explicit `View Channel` and `Send Messages` overrides only in `#opportunity-alerts` and `#proof-ledger`. It does not need Read Message History because it never reads channel messages.
5. Make `#opportunity-alerts` read-only for members: allow View Channel to `EAFC.Live Member`, deny Send Messages, and allow the bot to View/Send.
6. Make `#proof-ledger` read-only for visitors/members: allow View Channel, deny Send Messages, and allow the bot to View/Send.
7. The bot needs no permissions in `#welcome`, `#how-it-works`, `#get-access`, `#market-chat`, `#help-desk`, `#member-desk`, `#product-feedback`, or `#partner-desk`.
8. `#market-pulse` can be created read-only now, but do not configure a channel variable: deterministic pulse publishing is deferred.

## Deploy order

1. Back up the production database.
2. Deploy migration `056_discord_integration.sql` to the core database after all migrations through `055`. With `RUN_MIGRATIONS_ON_BOOT=1`, the backend migration runner applies it once under its advisory lock; otherwise run the normal migration command before backend rollout.
3. Add all required backend values in Coolify and redeploy the backend.
4. Confirm backend logs show the migration pass and no Discord configuration error. Temporary Discord/API failures must appear in the queue and must not fail Stripe handling.
5. Add `VITE_DISCORD_INVITE_URL` as a frontend build variable and rebuild/redeploy the frontend. Vite values are compiled into the bundle, so a container restart alone is not enough.
6. Run the manual entitlement lifecycle test before enabling the two optional channel IDs. This prevents accidental publication while role permissions are being checked.
7. Add `DISCORD_OPPORTUNITY_CHANNEL_ID`, test one sanitized digest, then add `DISCORD_PROOF_CHANNEL_ID` and test a matured post-migration outcome.

Rollback: first remove the optional channel IDs and deploy the prior backend. Only then drop `discord_role_sync_queue`, `discord_publications`, `discord_integration_state`, the payment-intent unique index, and the three new `stripe_events` columns if a database rollback is truly required. Keeping those inert tables/columns is safer and preserves retry/audit state.

## Stripe

Keep the existing webhook endpoint and ensure it subscribes to:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.payment_succeeded`
- `invoice.payment_failed`

No second webhook endpoint or Discord-specific Stripe webhook is required. Stripe/subscription records remain the entitlement source of truth.
