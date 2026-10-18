# FutBot PC and PS5 PriceSync

TradeHQ keeps PC observations in `futbot_pc_bin_history` and console
observations in `futgg_bin_history`. PriceSync can capture both market feeds
from one FUT.GG page load and write them in one cycle. `Dockerfile.price`
uses `FUTGG_BULK_PLATFORM=both` by default.

## One-time database setup

Apply `backend/migrations/101_futbot_pc_bin_history.sql` to the same
**price database** used by the collector and FutBot. The migration is
idempotent. Do not run it against FutBot's application database.

## PriceSync service

Stop the existing separate PC and PS5 PriceSync services, then run one
continuous `futgg_bulk_price_sync` service with the PriceSync image. The
combined worker takes both platform locks at startup and exits if either
lock is still held, so stop both old workers before starting the combined
service.
Set its environment to include:

```dotenv
FUTGG_BULK_PLATFORM=both
FUTGG_GAME_YEAR=27
FUTGG_BULK_INTERVAL_SECONDS=300
```

Keep the working `DATABASE_URL`, browser settings, and FUT.GG discovery
settings on that service. The worker acquires the existing PS5 and PC/year
advisory locks, captures both dynamic feeds on the same page, validates both,
and commits both history-table writes in one database transaction. PC data
does not update TradeHQ's shared `futgg_players` freshness/status fields or
enqueue the existing console price-change events.

If running an explicit single-platform PriceSync for troubleshooting, set
`FUTGG_BULK_PLATFORM=ps5` or `pc`; do not run that alongside a `both`
worker for the same game year.

## FutBot configuration

FutBot reads PS5/Xbox Console prices from `public.futgg_bin_history` and PC
prices from `public.futbot_pc_bin_history`. Keep `GAME_YEAR=27`;
`PLATFORM=ps5` remains the default market for the overview, fodder, movers
and investment pages. `/flip`, player price cards, personal watchlist
entries, charts and alerts can select PC per item. The Discord bot and
dashboard remain read-only against the price database.

After applying the migration and deploying the single PriceSync service,
verify that both tables receive fresh rows with `platform` and `game_year`
set correctly before enabling PC alerts in a live server.
