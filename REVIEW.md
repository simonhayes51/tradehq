# Code review

## The headline: it's not millions of lines

Total real source across all three repos (`.py`/`.js`/`.jsx`/`.ts`/`.tsx`/`.sql`,
excluding `.git`, `node_modules`, `dist`):

```
backend      32,390 lines
frontend     24,002 lines
auto_sync    15,216 lines  (18,116 before the deletions below)
```

~72,000 lines total. Dense in places — a lot of the length is explanatory
comments recording *why* a decision was made, usually because it was made
the hard way (a production incident, a wrong first attempt). That's a
maintenance asset, not bloat, and I left it alone. There is no evidence of
a "millions of lines" problem. If something specific felt bloated, point
me at it and I'll look at that file directly rather than re-sweep everything.

## Deleted in this pass (auto_sync only, 6 files, ~2,900 lines)

All six were confirmed unreferenced by anything still running before
deletion — no live worker imports them, none are Procfile entries other
than the ones being removed:

- `futbin_full_sync.py`, `bin_sales_history_sync.py` — the original FUTBIN
  scraper pipeline. Fully superseded by `futgg_player_sync.py` /
  `futgg_price_sync.py` / `futgg_bulk_price_sync.py` / `futgg_sales_sync.py`.
- `bin_worker.py`, `sales_worker.py`, `metadata_worker.py` — a
  "queue-driven redesign" that shipped in shadow mode ("deployed alongside
  history_worker, not replacing it yet") and was never promoted before the
  FUT.GG migration made the whole FUTBIN source obsolete. Still FUTBIN-sourced.
- `futgg_bulk_probe.py` — one-shot diagnostic from the bulk-feed transport
  investigation. Served its purpose; the finding is now encoded in
  `futgg_bulk_price_sync.py` itself.

`Procfile` updated to match — it now only lists the FUT.GG pipeline.

Nothing in `backend` or `frontend` was deleted. Both have real findings
below, but they're not clean deletions — see why under each.

## Not deleted: backend's six legacy FUTBIN services

`fair_value.py`, `analytics_engine.py`, `recommendation_engine.py`,
`recommendation_engine_v2.py`, `ml_feature_pipeline.py`, `ml_label_filler.py`
each have a guard (`_legacy_futbin_enabled()`) that no-ops their refresh
loop unless `ENABLE_LEGACY_FUTBIN` is set, which it isn't in production.
So they're inert — not executing — but still imported by `main.py` and
still shipped.

I didn't delete them because of the next finding, which changes what
"dead" means here.

## The actual finding: a live v1 product surface reads from data that stopped updating

`main.py` also still mounts `fair_value_router`, plus `players.py`,
`dashboard.py`, `admin.py`, `v2/recommendations.py`, and `v2/dashboard.py` —
all of which query `fair_value_mv` and/or `recommendations_latest`. Those
two materialized views are written *only* by the six services above.

And the frontend's v1 pages are still routed and still call them:
`src/App.jsx:128` routes `/watchlist` to the v1 `Watchlist.jsx`, and
`TradeFinder.jsx`, `BestBuys.jsx`, `SmartBuyerAI.jsx`, `Demo.jsx`,
`PlayerSearch.jsx`, `BacktestPanel.jsx` and others all call the v1
endpoints backed by those views.

So this isn't dead code in the usual sense — it's a live, reachable part
of the product, serving data from views that silently stopped refreshing
whenever `ENABLE_LEGACY_FUTBIN` was unset. Nobody visiting `/watchlist` or
the v1 Trade Finder gets an error; they get an increasingly stale number
with no indication it's frozen.

**This needs a decision, not a deletion**, because both directions are
real options:

1. **Retire v1 entirely** — remove the routes, the six services, the two
   routers that read the frozen views, and the v1 frontend pages. This is
   the bigger cleanup, and it's the one that would actually move the line
   count. But it's a product change: anyone still using `/watchlist` (v1)
   or the v1 Trade Finder loses that page, full stop.
2. **Re-enable the six services** (`ENABLE_LEGACY_FUTBIN=1`) if v1 is still
   meant to be live. This keeps the surface working but re-introduces the
   FUTBIN dependency you asked to remove.

I didn't pick one on your behalf. Say the word on either direction and
I'll execute it as its own reviewable change — it touches routing,
so I'd rather you see the list of removed routes/pages before it happens
than find out after.

## Lower-confidence, not investigated to completion (out of scope for this pass)

- `auto_sync/futbin_card_art_backfill.py`, `futbin_rarity_backfill.py`,
  `futbin_sbc_sync.py` — one-off backfill/SBC scripts, not in `Procfile`,
  but cross-referenced by `easysbc_sbc_sync.py`, `promo_event_detector.py`
  and `http_client.py`. Might be manually-triggered tools still in use.
  Didn't touch these — would need to know if you still run them by hand.
- `backend/app/futbin_client.py` — referenced only in a comment inside the
  now-deleted `bin_sales_history_sync.py`. Worth a targeted look, not
  swept in this pass.

## Databases: not touched

Three logical databases (core / player / watchlist pools in `backend`,
plus whatever `auto_sync` writes to) weren't consolidated. That's a
migration with real risk — connection pooling, blast radius if one goes
down, and whatever isolation reasons led to the split originally, which I
haven't verified are obsolete. If you want that looked at, it's a
separate, scoped task with its own review, not a side effect of a repo
tidy-up.
