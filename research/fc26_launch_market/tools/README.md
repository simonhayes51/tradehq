# Phase 4 chart capture — running it on existing infra

You don't need to install Python/Playwright/Chromium anywhere. Phase 4
capture runs as a **one-off job through the same Coolify deployment that
already runs PlayerSync** — no new Dockerfile, no new Coolify resource, no
change to the normal production scraping schedule.

## How this fits the existing setup

- Deployment is Coolify (`auto_sync/COOLIFY.md`); Railway config in
  `auto_sync/` is legacy/dead.
- `Dockerfile.player` already builds from
  `mcr.microsoft.com/playwright/python:v1.61.0-noble` (Chromium
  pre-installed), sets `PLAYWRIGHT_HEADLESS=true`, and `COPY . .`s the
  whole `auto_sync/` directory into the image — so a new script dropped
  into `auto_sync/` is already inside that image with zero Dockerfile
  changes.
- `auto_sync/research_fc26_chart_capture.py` is that new script. It:
  1. Picks a representative ~40-card sample of FC26 base golds **directly
     from the already-populated `futgg_players` table** (joined to the
     latest `futgg_bin_history` row for a price tier) — no fut.gg
     catalogue crawl needed, since PlayerSync already keeps that table
     fresh. Uses `classify_price_tier()` from `futgg_common.py` (the same
     function production code uses) to keep only common/rare base golds.
  2. Visits each card's real `source_url`, selects the "Year" chart
     range, waits for the Recharts points to render, captures the chart
     HTML (+ full page HTML for the first 5 cards).
  3. Writes results into `fc26_chart_capture_fixtures` — a **research-only
     table this script creates itself** (`CREATE TABLE IF NOT EXISTS`,
     same convention `futgg_player_sync.py`'s `ensure_schema()` already
     uses), completely separate from every production `futgg_*` table.
     Safe to `DROP TABLE fc26_chart_capture_fixtures;` once you've
     exported the fixtures below.
  4. Is resumable and never silently skips a failure: reruns skip cards
     already `status='success'` unless you set
     `FUTGG_CHART_CAPTURE_FORCE=true`.

This mirrors the exact "temporarily override this Coolify resource's
start command for one manual run" pattern `COOLIFY.md` already documents
for `futgg_sales_sync.py`'s one-card diagnostic mode (see "One-card
diagnostic mode" / "One supervised manual run before scheduling" in that
file) — nothing new to learn.

## Running it

1. In Coolify, open the **PlayerSync** resource (the one that normally
   runs `python futgg_player_sync.py` on a schedule).
2. **Temporarily** override its start command to:
   ```
   python research_fc26_chart_capture.py
   ```
3. Optionally set any of these env vars on that one run (all have
   production-safe defaults, so none are required):
   - `FUTGG_CHART_CAPTURE_ALL` (default `false`) — **Phase 7 bulk mode.**
     Set to `true` to capture every qualifying Gold Common/Gold Rare card
     instead of a balanced ~40-card sample. Logs the true qualifying-card
     count before starting a single capture, so you see the real scale
     first.
   - `FUTGG_CHART_SAMPLE_TARGET` (default `40`, ignored when `_ALL=true`)
   - `FUTGG_CHART_CAPTURE_CONCURRENCY` (default `3`) — number of Playwright
     pages run in parallel. Keep this conservative; raising it increases
     load on fut.gg as well as this container. Start low, confirm a run
     stays stable, only raise if you need to.
   - `FUTGG_CHART_CAPTURE_DELAY` (default `3.0` seconds, **per worker** —
     total request rate is roughly concurrency ÷ delay)
   - `FUTGG_CHART_CAPTURE_RETRIES` (default `3`)
   - `FUTGG_CHART_CAPTURE_TIMEOUT_MS` (default `20000`)
   - `FUTGG_CHART_FULL_PAGE_COUNT` (default `5`)
   - `FUTGG_CHART_CAPTURE_FORCE` (default `false`)

   For a full bulk run: `FUTGG_CHART_CAPTURE_ALL=true` (and optionally
   raise `FUTGG_CHART_CAPTURE_CONCURRENCY` a little once you've watched
   one run go smoothly). It's fully resumable — if it's interrupted or
   you stop it, just re-run the same command; already-successful cards
   are skipped automatically.
4. Trigger it via the Scheduled Task's manual "Run now" trigger (same
   mechanism `COOLIFY.md` describes for a supervised manual run) — don't
   wait for the daily cron.
5. Watch the logs. Expect one `[i/N] card_id=... name=... (url)` line per
   card, `OK: N circles` or `FAILED: <reason>`, and a final `DONE: X
   success, Y failed` line.
6. **Revert the start command back to `python futgg_player_sync.py`**
   once it's done, so the next scheduled PlayerSync run isn't affected.
   This is why step 2 matters — the schedule/cron trigger itself is never
   touched, only that one run's command.

If it fails outright (e.g. "No qualifying gold cards found in
futgg_players"), that means PlayerSync hasn't populated the table yet on
this database — run PlayerSync normally first.

## Getting the fixtures back out

The capture job only writes to the database (a Coolify container's
filesystem isn't something you'd otherwise retrieve files from). To turn
those rows into the local file layout the Phase 2 extractor expects, run
`export_fc26_chart_fixtures.py` anywhere `DATABASE_URL` is reachable —
your own machine with `pip install asyncpg` and DB access, or the same
Coolify one-off pattern as above with the command temporarily set to
`python export_fc26_chart_fixtures.py --out /path`.

```bash
pip install asyncpg
DATABASE_URL=postgresql://... python research/fc26_launch_market/tools/export_fc26_chart_fixtures.py
```

Writes:
```
fc26_chart_fixtures/
    <card_id>.html
    full_pages/<card_id>.html   (first 5 successful cards)
    manifest.csv
```

Send me that folder (or just confirm the export ran — if this session
ever gets `DATABASE_URL` access, I can run the export myself and skip a
round trip). I won't start the Phase 4 report until the fixtures are
available one way or another.

## What NOT to worry about

- No new Coolify service, no new Dockerfile — this reuses PlayerSync's
  image as-is.
- No change to the production schedule beyond the single manual run (and
  you revert the command right after).
- No Cloudflare bypass — if fut.gg blocks or challenges the container the
  same way it would any other request, cards just fail and get logged;
  the script doesn't try to work around it.
- The job never writes to `futgg_players`, `futgg_bin_history`,
  `futgg_sales_history`, or any other production table — read-only
  against those, and it doesn't extract or interpret any price (that's
  still Phase 2's job, unchanged).

---

## Fallback: local Windows capture (not the primary path anymore)

If for some reason the Coolify route isn't available, `build_sample_list.py`
and `capture_charts.py` in this same folder do the equivalent job from a
local machine that can browse fut.gg normally (originally built before we
confirmed the Coolify path — see git history). They need Python +
Playwright installed locally; see the comments at the top of each script.
Prefer the Coolify path above unless you have a specific reason not to.
