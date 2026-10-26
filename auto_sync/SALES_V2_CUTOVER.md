# SalesSync V2 — controlled cutover

V2 is a **market-value sales collector**, not a new BIN worker. The existing
bulk BIN worker remains the canonical pricing source. V2 uses the existing
SalesSync transports, response parsing, deduplicated database inserts,
rate-limiter/circuit breaker, retry backoff, schema and advisory lock.

## Selection

* FC27 active players with snapshot BIN >= `SALES_V2_MIN_BIN` (default 5000).
* Any rarity/rating, excluding only cards explicitly marked untradeable.
  Unknown tradeability is **not** evidence that a card is tradeable.
* 20% of each selection reserved for 5k–20k cards, remainder for 20k+,
  with unused capacity filled from either group.
* Never-successfully-read cards first; oldest attempted among peers.
* After a successful request, attempt again when the target age has passed.
  Prior V1 success schedules do not extend that target.
* Failed/excluded requests always honour existing backoff due timestamps.
* Reuses V1's conservative request budget; 60 minutes is an **aspiration**,
  not a guaranteed check interval. Real 429s must take precedence.

## Safe evaluation — no external FUT.GG traffic

Run inside a TradeHQ/SalesSync environment with `DATABASE_URL`:

```bash
PYTHONPATH=auto_sync python3 auto_sync/sales_v2_shadow.py
```

This is DB-only. It neither fetches sales nor writes scheduler state.

## Stage 2 — fetch-only test

**Stop V1 first**, including any secondary or scheduled SalesSync instances.
Run this from the existing SalesSync image/environment (it needs headed
Chromium/Xvfb where used by V1), changing its command/entrypoint to execute
`python3 sales_v2_collector.py` from the `auto_sync` working directory.

```
SALES_V2_ENABLE_FETCH=true
SALES_V2_ALLOW_WRITES=false
SALES_V2_MIN_BIN=5000
SALES_V2_PREMIUM_BIN=20000
SALES_V2_TARGET_MINUTES=60
```

V2 takes **the same PostgreSQL advisory lock** as V1. If V1 is still
running, V2 cannot start collecting. Fetch-only still makes real requests:
do not run two source consumers in parallel. Never treat a clean handful
of batches as proof a higher long-term request rate is sustainable.

## Stage 3 — production cutover

Only after controlled throughput/429 and correctness verification:

```
SALES_V2_ALLOW_WRITES=true
```

Keep V1 stopped. The existing shared SalesSync DB schema/history remains
in use and no database reset is required.

## Why target age is not a promise

1296 cards * 24 checks/day = 31,104 successful checks/day for hourly
coverage. Historical observations of ~480 attempts/day are far below
that target; V2 **must not** achieve it by disabling upstream limits.
Measure attempted/succeeded per minute, throttle responses, age-to-last
successful check, distinct new sales inserted and age of latest
completed sale separately. The source may not expose historical sales
beyond its returned window, and no scheduler can recover unobserved prints.

## Rollback

Stop V2, restart the old SalesSync resource/command, leaving the DB and
existing history in place. Both versions deliberately share the same
lock, schema and idempotent sales upserts. Restore its original env.
