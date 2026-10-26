# SalesSync V2 — one-resource cutover (Coolify)

**No extra resource is required.** Use your existing SalesSync Coolify service,
using the existing `auto_sync/Dockerfile.sales`. V1 is the default; V2 is an
explicit mode switch in the same image/entrypoint. This preserves the existing
Xvfb, restart management, and bundled bulk BIN worker when enabled.

## Already checked

The DB-only V2 shadow on 26 October 2026 returned 1,295 eligible cards,
940 due, and 220 without successful checks. It sends **no requests**.

## Before switching

1. Confirm whether the current SalesSync resource runs bulk prices:
   `FUTGG_RUN_BULK_WITH_SALES=true` means **yes**. Leave this value
   unchanged so BIN updates continue after the change.
2. Check for *any other* separate SalesSync deployments/cron jobs and stop
   them. Only one completed-sales collector should exist.
3. Keep the current SalesSync resource and Dockerfile. Redeploying it in V2
   mode stops V1 and starts V2. Both collectors use the same DB advisory
   lock, preventing concurrent card-selection runs during rolling deployment.

## One production configuration (existing SalesSync service)

In **Coolify > existing SalesSync > Environment Variables**, add:

```dotenv
FUTGG_SALES_WORKER_VERSION=v2
SALES_V2_ENABLE_FETCH=true
SALES_V2_ALLOW_WRITES=true
SALES_V2_MIN_BIN=5000
SALES_V2_PREMIUM_BIN=20000
SALES_V2_TARGET_MINUTES=60
```

Retain the existing `DATABASE_URL`, Playwright/Xvfb settings, throttle
variables and `FUTGG_RUN_BULK_WITH_SALES`. Do **not** increase request
concurrency, lower rate-limit backoffs, or run an additional sales resource.
Keep `FUTGG_SALES_MAX_CYCLES` and `FUTGG_SALES_MAX_RUNTIME_MINUTES`
unset/0 for a continuous production service. Deploy current TradeHQ `main`.

V2 does not promise that every card will be checked hourly: achieving that
for 1,295 cards would require 31,080 successful checks/day, significantly
above the observed ~480 attempts/day. The source's actual limits win.

## Initial logs to verify

* `Sales collector mode: v2 (script=sales_v2_collector.py)`
* `SalesSync V2 fetch ACTIVE ... writes=True`
* `V2 market selection: cards=... premium=... base=...`
* `V2 eligible-market coverage:`
* Normal `cards_ok`, `failed`, `sales`, and 429/backoff reports

If the startup log still says `Starting FUT.GG sales sync with read-only
information shadow...`, you are running the old V1 image or wrong resource.

V2 reuses the existing hardened fetcher, deduplicated sales inserts, failure
backoff, throttle and circuit breaker. It does not delete historical sales or
create a new DB. Unknown tradeability remains unknown; no BIN-only inference.

## Optional one-cycle fetch-only smoke test

If testing against the live source **before enabling writes**, change only
`SALES_V2_ALLOW_WRITES=false` with `FUTGG_SALES_WORKER_VERSION=v2`.
The entrypoint automatically bounds this mode to one cycle **if no runtime
limit is supplied**. Warning: a Coolify restart-always policy can restart a
completed one-cycle container and generate repeated requests. For this reason,
don't run this optional mode unattended or with automatic restarts enabled.
The DB-only `sales_v2_shadow.py` is always safe to run while V1 is active.

## Rollback (same resource, no additional changes)

Set `FUTGG_SALES_WORKER_VERSION=v1`, redeploy. This restores the original
`futgg_sales_shadow_worker.py` while retaining all existing PostgreSQL
sales history and bulk-price process.

## Boundaries

V2 replaces the **selection policy** (market BIN >=5k, any rarity/rating,
20% reserve for 5k-20k, fair oldest-first selection). It deliberately
reuses V1's fetch/parse/write/rate-limit machinery. The source can provide
only sales it exposes; V2 cannot recreate missed older completed auctions.
Collect metrics after deployment before changing intervals or request budgets.
