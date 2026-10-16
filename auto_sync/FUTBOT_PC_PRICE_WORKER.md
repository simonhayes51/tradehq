# FutBot PC price worker

TradeHQ's market consumers treat `futgg_bin_history` as the console market. FutBot PC prices therefore go into a dedicated `futbot_pc_bin_history` table; this keeps PC observations from changing existing console signals.

## One-time database setup

Apply `backend/migrations/101_futbot_pc_bin_history.sql` to the same **price database** used by the collector and FutBot. The migration is idempotent. Do not run it against FutBot's application database.

The existing console history table already needs `platform` and `game_year` columns, as shown by the live schema. Keep the existing PS5 worker on `FUTGG_BULK_PLATFORM=ps5`.

## Add a PC worker

Clone the existing `futgg_bulk_price_sync` Coolify service. Keep its image/build, browser settings, database URL, discovery URL, and other working variables. Set these values on the cloned service:

```dotenv
FUTGG_BULK_PLATFORM=pc
FUTGG_GAME_YEAR=27
FUTGG_BULK_INTERVAL_SECONDS=300
```

Keep the console service explicitly set to:

```dotenv
FUTGG_BULK_PLATFORM=ps5
FUTGG_GAME_YEAR=27
FUTGG_BULK_INTERVAL_SECONDS=300
```

Both workers use the same price database and FUT.GG catalogue, but their market-feed markers and history tables differ. A PostgreSQL advisory lock prevents two copies of the same platform/year worker from collecting concurrently. The PC worker does not update TradeHQ's shared `futgg_players` price freshness/status columns or enqueue the existing console price-change events.

## FutBot configuration

FutBot reads PS5/Xbox Console prices from `public.futgg_bin_history` and PC prices from `public.futbot_pc_bin_history`. Keep `GAME_YEAR=27`; `PLATFORM=ps5` remains the default market for the overview, fodder, movers and investment pages. `/flip`, player price cards, personal watchlist entries, charts and alerts can select PC per item. The Discord bot and dashboard remain read-only against the price database.

After applying the migration and deploying the worker, verify that both tables receive fresh rows with `platform` and `game_year` set correctly before enabling PC alerts in a live server.
