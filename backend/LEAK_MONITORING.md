# EAFC.Live FC27 Leak Monitoring

Single X API polling pipeline with two outputs from the same read:

1. raw tweet -> Discord webhook;
2. structured leak signal -> EAFC.Live internal market intelligence.

The product-side boundary intentionally contains no tweet text, X username, tweet URL or media URL.

## Database

Apply `migrations/090_leak_monitoring.sql` to the core `DATABASE_URL`.

It creates:

- `leak_sources` - tracked X accounts and `since_id` state;
- `leak_tweets` - raw ingest/audit copy used only by the monitor;
- `leak_signals` - parsed player/rating/card-type signal;
- `leak_discovery_candidates` - lower-trust search discoveries;
- `active_leak_signals` - structured-only internal view;
- `futgg_current_intelligence.leak_context` - structured context attached to a matched market card.

## Required environment variables

```text
X_BEARER_TOKEN=...
LEAK_X_ACCOUNTS=account_one,account_two,account_three
LEAK_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
DATABASE_URL=postgresql://...
```

Do not put these secret values in Git.

## Recommended variables

```text
LEAK_POLL_SECONDS=180
LEAK_DISCORD_USERNAME=EAFC.Live Leak Feed
LEAK_DISCORD_AVATAR_URL=https://...
LEAK_DISCORD_ROLE_MENTION=<@&ROLE_ID>

# Optional discovery. Off by default because direct timelines are cheaper/cleaner.
LEAK_DISCOVERY_ENABLED=false
LEAK_DISCOVERY_INTERVAL_SECONDS=14400
LEAK_DISCOVERY_QUERIES=FC27 leak,FUT leak

# Optional OCR + fallback NLP. Regex remains the first pass.
LEAK_OPENAI_ENABLED=false
OPENAI_API_KEY=...
LEAK_OPENAI_MODEL=gpt-4o-mini
```

## Run

Run this as its own long-lived worker/service from the `backend` directory:

```bash
python -m scripts.run_leak_monitor
```

Do not run multiple replicas against the same database unless leader election is added. The tweet unique constraint prevents duplicate persistence, but two workers could still race on Discord delivery before one sees the other's insert.

## Poll behaviour

For each active source:

- numeric X user ID is resolved once and stored;
- timeline call requests text, public metrics and media in the same request;
- `since_id` is always supplied after the first successful poll;
- only new tweets are processed;
- tweets are processed oldest-first;
- a Discord failure does not cause the tweet to be fetched again;
- X source state advances only after the returned timeline batch has been ingested.

## Parsing

Order of operations:

1. regex/keyword parser;
2. optional OCR for attached images;
3. optional lightweight OpenAI fallback if regex confidence is below 0.70;
4. optional player/card match against the FC27 `futgg_players` catalogue.

Structured fields are:

- player name;
- rating;
- card type/promo;
- parse confidence;
- reliability score;
- confidence tag: `high`, `medium`, `rumor`.

No market direction is inferred from a leak. The monitor supplies content context only; the existing market engine remains responsible for BUY/WATCH/SELL decisions.

## Reliability

Reliability combines:

- historical source accuracy (50%);
- follower scale (20%);
- tweet engagement (15%);
- parse confidence (15%).

The formula is deliberately saturating so a huge account or viral tweet cannot swamp poor historical accuracy.

`leak_sources.historical_accuracy`, `verified_leaks` and `correct_leaks` are designed to be updated as leaks are confirmed against shipped FC27 content. False outcomes should not be auto-invented just because a card has not appeared yet.

## Internal pipeline boundary

`active_leak_signals` is the safe internal view. It excludes all raw source content.

`app.services.leak_pipeline_bridge.sync_leak_context()` copies the highest-confidence recent signal for each matched card into `futgg_current_intelligence.leak_context`. This makes the structured leak available to Trade Finder / Opportunities intelligence without changing the existing market recommendation itself.

Example context:

```json
{
  "active": true,
  "player_name": "Player Name",
  "rating": 91,
  "card_type": "TOTY",
  "promo_name": "TOTY",
  "confidence": "high",
  "reliability_score": 0.84,
  "parse_confidence": 0.95,
  "observed_at": "2026-09-16T11:30:00+00:00"
}
```

There is intentionally no tweet text/link/source account/image in that object.

## Discovery

Discovery is secondary and disabled by default. When enabled, recent search runs no more often than `LEAK_DISCOVERY_INTERVAL_SECONDS` and stores candidates only after aggressive parse/follower/engagement filtering. It never automatically promotes an account to the core tracked list.

## Scaling

The current implementation is deliberately optimised for 2-3 accounts. When the core list grows beyond roughly 3-4 accounts, switch the timeline source to one X List timeline if the configured X API tier exposes that endpoint. Keep the downstream ingest function unchanged so Discord and structured parsing still fan out from one response.
