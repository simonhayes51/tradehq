# Release Metadata & Market Event Timeline

How EAFC.Live captures FUT.GG's own card-release timestamps and short
player names, and reconstructs a historical content/promo timeline from
them.

---

## 1. What was added

| Field | Meaning |
|---|---|
| `futgg_players.released_at` | FUT.GG's **"Added On"** — when the *card* was added to the game |
| `futgg_players.released_at_raw` | The exact unparsed source string |
| `futgg_players.display_name` | Short user-facing name (`Bruno Fernandes`) |
| `futgg_players.full_name` | Player Information name (`Bruno Miguel Borges Fernandes`) |

`released_at` is **not** `first_seen_at`. `first_seen_at` only records
when *our scraper* discovered a card — for historical FC26 cards that can
be weeks after the real release, which would produce a fictional
timeline. Both are kept; neither is repurposed.

---

## 2. Scraper changes

Everything extends the **existing** pipeline — no parallel scraper.

### `auto_sync/futgg_common.py`
- `FutggCard` gains `display_name`, `full_name`, `released_at`,
  `released_at_raw`.
- `parse_added_on(value, assume_timezone)` — tolerant of abbreviated and
  full month names, with/without a time, and the narrow/non-breaking
  spaces some renderers emit around the meridiem. Returns `None` on
  failure and **never fabricates a timestamp**.
- `_find_display_name(soup, source_player_id)` — finds the short name
  **structurally**, never by Tailwind class: the player-profile URL is
  `/players/{source_player_id}-{slug}/` with *no* card-id segment, which
  is what distinguishes it from the card-version URL. Matching the known
  player id also prevents a navigation or "similar players" link being
  picked up.
- `_parse_player_information` reads the `Added On` row via the existing
  `_find_label_value` label/value helper (the same one `Rarity`/`Squad`
  already use), and records a parse warning — `released_at_not_found`,
  `released_at_unparseable:…`, or `display_name_not_found` — rather than
  failing silently.

### `auto_sync/futgg_player_sync.py`
- **`timezone_id` is now pinned** on the Playwright context (see §3).
- `ensure_schema` gains defensive `ADD COLUMN IF NOT EXISTS` for the four
  new columns, per this worker's existing convention.
- `upsert_card` persists them with `COALESCE(EXCLUDED.x, futgg_players.x)`
  so **a later failed parse can never blank out good data** — this is what
  makes the backfill safe to interrupt and re-run.
- `cards_needing_release_metadata()` / `count_cards_needing_release_metadata()`
  — targeted backfill selection.
- Backfill mode reuses the existing fetch workers, parser, upsert,
  circuit breaker and request delay. The *only* difference is where the
  target list comes from.

---

## 3. Timezone semantics — read this before trusting `released_at`

FUT.GG renders `Added On` as e.g. `Mar 18, 2026, 6:00 PM` with **no
timezone in the markup**. The timezone it represents is therefore decided
by how the page is rendered, not by anything in the string.

**This was not assumed — it was made deterministic.** The player sync
previously set `locale="en-GB"` but **not** `timezone_id`, so Chromium
inherited the *host machine's* timezone. If FUT.GG renders this value
client-side in the viewer's local zone — the common pattern — the scraped
string would silently shift with container configuration.

`futgg_player_sync.py` now pins:

```python
context = await browser.new_context(
    locale="en-GB",
    timezone_id=BROWSER_TIMEZONE,   # FUTGG_BROWSER_TIMEZONE, default "UTC"
    ...
)
```

- If the value is **client-rendered**, pinning makes it UTC *by
  construction*, and `ADDED_ON_ASSUMED_TIMEZONE = timezone.utc` is
  correct.
- If it is **server-rendered** in some fixed zone, pinning changes
  nothing and costs nothing.

Either way the parse is deterministic rather than host-dependent.

**`released_at_raw` preserves the exact source string**, so if FUT.GG is
later shown to render in a different fixed zone, every row can be
re-derived with a single UPDATE — no re-scrape of the catalogue.

> **CONFIRMED against production, twice over.**
>
> 1. A live backfill returned FUTTIES 2 cards at `17:01–17:02 UTC` on
>    31 Jul — i.e. 18:01–18:02 BST, the canonical 6pm UK content slot.
> 2. Conclusively, the reconstructed FC26 timeline shows the **BST→GMT
>    clock change**: TOTW 6 lands at `17:00 UTC` (22 Oct) and TOTW 7 at
>    `18:00 UTC` (29 Oct), with the UK leaving BST on 26 Oct. The event
>    stayed at 18:00 *UK* throughout; only the UTC offset moved. The same
>    step appears on the promo track (Ultimate Scream 1 → 2). That
>    pattern cannot arise by accident.
>
> If a future FUT.GG change ever breaks this, the fix is to change
> `ADDED_ON_ASSUMED_TIMEZONE` and re-derive from `released_at_raw` — no
> re-scrape needed.

---

## 4. Name behaviour

`futgg_players.name` is **unchanged** — it still holds the Player
Information full name. 97 backend call sites read that column and there
is a `lower(name)` search index on `futgg_market_snapshot`, so a rename
would be a breaking change for no benefit.

User-facing surfaces prefer the short name:

- **`_serialize_player()`** in `app/routers/v2/futgg_market.py` — the single
  serialization choke point every player-facing response passes through —
  sets `name = display_name` when one exists. Applied there rather than
  in ~69 individual queries.
- **`investments.py`** selects `COALESCE(p.display_name, p.name) AS name`.
- **`futgg_market_snapshot`** (migration 065) now carries `display_name`,
  `full_name` and `released_at` so no extra join is needed.

Fallback is always `display_name → name`, so a row that has not been
backfilled yet behaves exactly as it does today — never null.

---

## 5. Backfill

Targeted, so normal daily runs never re-scrape the historical catalogue
(which is what `FUTGG_REFRESH_EXISTING=true` would do).

```bash
FUTGG_BACKFILL_RELEASE_METADATA=true \
FUTGG_BACKFILL_LIMIT=500 \
FUTGG_BACKFILL_GAME_YEAR=26 \
python futgg_player_sync.py
```

| Variable | Default | Purpose |
|---|---|---|
| `FUTGG_BACKFILL_RELEASE_METADATA` | `false` | Enable backfill mode |
| `FUTGG_BACKFILL_LIMIT` | `500` | Cards per run |
| `FUTGG_BACKFILL_GAME_YEAR` | *(all)* | Restrict to one cycle |
| `FUTGG_BROWSER_TIMEZONE` | `UTC` | Pinned render timezone |

**Selection:** rows where `released_at IS NULL OR display_name IS NULL OR
full_name IS NULL`, with `source_url IS NOT NULL` (a row with no URL can
never be re-fetched and would be a permanent queue blocker), ordered
`released_at NULLS FIRST, first_seen_at`.

**Properties:**
- *Resumable* — each run re-runs the same query; completed cards drop out
  naturally.
- *Idempotent* — COALESCE upserts; re-running changes nothing.
- *Safe to interrupt* — a failed parse preserves existing values.
- *Polite* — reuses the existing concurrency, per-request delay and
  circuit breaker. No bypass or evasion of any kind.
- *Sized before it starts* — logs remaining count and an estimated
  duration at the configured concurrency/delay.

Scope is **all cards**, not just specials: base golds have useful release
timestamps too, and the display-name cleanup benefits every card.

---

## 6. Market events

### Tables (migration 064)
- **`market_events`** — `game_year`, `event_type`, `event_key`, `title`,
  `rarity`, `squad`, `released_at`, `card_count`, `event_scale`, `source`,
  `confidence`, `metadata`, timestamps.
- **`market_event_cards`** — `(event_id, source_card_id, source_player_id)`.

`event_type` has **no CHECK constraint** enumerating promo types — so SBC
releases/expiries, Evolutions, rewards, pack changes and promo start/end
markers can be added later without a schema change. Only
`promo_release` and `totw_release` are emitted today.

### Grouping rule
**`(game_year, squad, release cluster)` — never rarity alone.**

Rarity is the promo *family* (`Team of the Week`); squad is the specific
release (`TOTW 27`). Grouping by rarity would collapse a whole season of
weekly TOTW squads into one meaningless mega-event.

**Cluster, not exact timestamp — this was forced by real data.** FUT.GG
stamps a single promo drop across several adjacent minutes: a live
FUTTIES 2 sample landed cards at 17:01, 17:02 and 17:03 UTC. Exact-match
grouping shattered that one drop into three events, two of which were
then mislabelled `mini` because each fragment held only part of the squad.

Cards in the same squad within `RELEASE_CLUSTER_TOLERANCE` (6h) of the
previous card now form one event, stamped with the **earliest** timestamp
in the cluster — the moment the drop began. The observed jitter is
recorded in `metadata.release_span_seconds` rather than smoothed away.

**`MAX_CLUSTER_SPAN` (6h) stops gap-based linkage chaining.** Real FC26
data proved this necessary: TOTY releases in waves (Attackers,
Midfielders, Defenders, GK) roughly a day apart, and each wave sits
within the gap tolerance of the previous one — so 24 TOTY cards merged
into one "event" spanning **seven days**. Thunderstruck 1 (56 cards)
chained across 48 hours the same way. Capping the total span splits these
into the separate market moments they actually were, while leaving the
slowest genuine single drop measured (Joga Bonito, 39 cards, 1h47m)
intact.

The tolerance is hours, not days, so cards **added later to an existing
promo** still form their own event — which is what actually happened in
the market. Clustering is within a squad only, so two different promos
dropping in the same hour never merge.

### Never inventing an event
- No `released_at` → skipped. A timeline built on a missing timestamp is
  a fabrication.
- No `squad` → skipped. Without one there is no way to separate releases
  sharing a rarity, so grouping them would invent events. (Base golds
  therefore create no events; they remain fully queryable.)

### Scale / confidence
| Scale | Cards | Confidence | Typical |
|---|---|---|---|
| `broad` | ≥ 6 | high | A real promo team |
| `mini` | 2–5 | medium | Small batch / late additions |
| `isolated` | 1 | low | SBC or objective card, **not** a promo |

`confidence` is about whether this is a coherent *event* — not about any
market effect.

### Running it
```bash
python -m scripts.reconstruct_market_events --game-year 26 --dry-run
python -m scripts.reconstruct_market_events --game-year 26
```
Idempotent via the `(game_year, event_key, released_at)` unique index —
safe to re-run as the backfill progresses and more timestamps appear.

---

## 7. Event study

```bash
python -m scripts.market_event_study --game-year 26 --min-cards 6
```

Reports median per-card % change by cohort at T−7/−5/−3/−2/−1/0/+1/+2/+3/+5/+7
days, relative to T−1. Cohorts come from the Investment Engine's *own*
`investment_cohort_memberships`, so "premium_meta" means the same thing in
a study as in an engine decision.

**Two disciplines enforced in the output wording:**
1. **Association, not causation.** "Around the Cornerstones 1 release,
   premium golds moved −4.2%" — never "caused". Promos overlap with each
   other, with SBC cycles, with weekend league and with ordinary drift.
2. **Daily resolution for FC26.** Release timestamps are exact to the
   minute, but `fc26_historical_daily_prices` has one observation per card
   per day, so a release at 18:00 and that day's price cannot be ordered
   within the day. Day-relative offsets only; **no intraday reaction is
   fabricated**. Day 0 should be read with caution — the T−1 → T+1
   transition is more meaningful.

For FC27 the exact timestamp is retained, so the same events can later
support genuine hourly/intraday studies once high-resolution live data
exists.

---

## 8. FC27 — automatic

No manual event entry is needed. When a new FUT.GG card appears, the
normal player sync parses Rarity, Squad and Added On and stores
`released_at`. Running the reconstruction (on a schedule or on demand)
then creates or updates the corresponding event. FC26 and FC27 events are
isolated by `game_year` throughout — pinned by test.

---

## 9. Deployment

1. Apply migrations **063** (columns + indexes), **064** (event tables),
   **065** (snapshot view rebuild — a DROP+CREATE, so expect a refresh
   cycle before the view repopulates).
2. Deploy `auto_sync` so the parser and pinned timezone take effect.
3. Run the backfill in batches (§5). Check the first batch's parsed
   values before running the rest.
4. Run the reconstruction with `--dry-run`, then for real.
5. Optionally run the event study.

---

## 10. Limitations

- **Timezone: CONFIRMED correct against production data.** A live
  backfill returned FUTTIES 2 at `17:01–17:02 UTC` on 31 Jul, i.e.
  18:01–18:02 BST — the canonical 6pm UK content slot. The pinned-UTC
  assumption holds.
- **FC26 price data is daily** — no intraday event analysis is possible
  for FC26, and none is fabricated.
- **Isolated cards are not promos.** Single-card releases are recorded but
  flagged `isolated`/`low`; analysis should generally exclude them.
- **Cards without squad or release timestamp form no events** — by design,
  but it does mean event coverage depends on backfill completeness.
- **Backfill partially run.** A 20-card production sample parsed cleanly
  (0 failures, 0 unparseable dates). ~16,040 FC26 cards remain; measured
  throughput is ~0.7s/card, so a full backfill is roughly 3.1 hours.
- **Broader display-name rollout** covers the main serialization choke
  point, the Investments router and the snapshot view. Other surfaces
  (Discord messages, admin tools, some v1 routers) still read `name`
  directly and will show the full name until migrated — a deliberate
  scope boundary, not an oversight.
