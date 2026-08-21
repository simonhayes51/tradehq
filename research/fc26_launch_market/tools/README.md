# Phase 4 local capture tools (run on Windows, not in this sandbox)

This repo's Claude Code session cannot reach `fut.gg` (network egress is
blocked in that environment). These two scripts are meant to be run on
your own Windows PC, where fut.gg loads normally in Chrome.

Both use **ordinary Playwright browser automation only** — no Cloudflare
bypass, no anti-bot evasion, no invented API endpoints. If FUT.GG blocks or
challenges the browser, the scripts will fail those cards and record it,
not work around it.

## What's here

- `build_sample_list.py` — crawls fut.gg's public catalogue listing to
  discover real FC26 base gold cards (common + rare, no specials), reusing
  the already-tested parser in `auto_sync/futgg_common.py`, and writes a
  representative ~30-50 card sample (`sample_cards.csv`) spread across
  position × price-tier buckets.
- `capture_charts.py` — the actual Phase 4 capture utility. Reads a list
  of cards (e.g. `sample_cards.csv`), opens each fut.gg player page,
  selects the "Year" chart range, waits for the chart to render, and saves
  the chart HTML + (for the first few cards) full page HTML + a manifest.

Neither script scrapes prices or touches the Phase 2 extractor — they only
produce raw HTML fixtures for you to send back.

## 1. One-time setup (Windows, PowerShell or Command Prompt)

You need Python 3.10+ and a checkout of this repo (so `auto_sync/` is a
sibling directory the sample-list builder can import from).

```powershell
cd C:\path\to\tradehq
python -m venv .venv-fc26-capture
.\.venv-fc26-capture\Scripts\activate

pip install -r research\fc26_launch_market\tools\requirements.txt
playwright install chromium
```

If `python` isn't found, use `py` instead (`py -3 -m venv ...`).

## 2. Build the representative sample list (~30-50 cards)

```powershell
python research\fc26_launch_market\tools\build_sample_list.py --target 40
```

This writes `research\fc26_launch_market\tools\sample_cards.csv`. It runs
headed (visible browser window) by default so you can watch it work and
confirm nothing looks wrong; add `--headless` once you trust it. It's
deliberately slow (2s between requests by default) — do not lower `--delay`
below a couple of seconds.

Useful flags:
- `--target 30` — smaller sample if 40 is slow/unreliable
- `--max-listing-pages 20` — crawl more catalogue pages if you need a
  bigger candidate pool
- `--delay 3.0` — be even more conservative

Check the console summary at the end — it prints how many cards landed in
each position/price-tier bucket, so you can see if coverage is thin
anywhere (e.g. few "expensive" wingers found).

**Review `sample_cards.csv` before the next step** — feel free to hand-edit
it: swap in specific cards you know are meta/volatile/flat, remove ones
that look wrong, add extras.

## 3. Capture the chart HTML for every card in the sample

```powershell
python research\fc26_launch_market\tools\capture_charts.py research\fc26_launch_market\tools\sample_cards.csv
```

This writes into `fc26_chart_fixtures\` at the repo root:

```
fc26_chart_fixtures\
  264453.html
  <other card_id>.html
  ...
  full_pages\
    264453.html          (first 5 successful cards, full page HTML)
    ...
  manifest.csv            (one row per card: status, circle count, errors)
```

It also runs headed by default, waits ~3s (± jitter) between cards, and
retries a card up to 3 times before giving up and recording the failure —
nothing is silently skipped. **It's resumable**: if you stop it (Ctrl+C) or
it crashes partway through, just re-run the same command — cards already
marked `success` in `manifest.csv` are skipped automatically. Pass `--force`
to re-capture everything anyway.

Useful flags:
- `--delay 4.0` — slower, more conservative
- `--retries 4` — more retry attempts per card
- `--full-page-count 8` — save full page HTML for more than the first 5
- `--headless` — no visible browser window
- `--force` — ignore the existing manifest and recapture every card

You can also point it at a plain `.txt` file (one URL or card ID per line)
or a `.json` file instead of the CSV, if you want to hand-build a list
outside `build_sample_list.py`.

## 4. What to send back

Please send back the whole `fc26_chart_fixtures\` folder (or zip it) —
the per-card HTML files, `full_pages\`, and `manifest.csv`. I'll run the
Phase 2 extractor against every captured file and do the Phase 4 report
from there. I won't start that analysis until you confirm the capture run
is finished (or intentionally stopped).

## Troubleshooting

- **"Could not import auto_sync/futgg_common.py"** (from
  `build_sample_list.py`): run the command from inside the `tradehq`
  checkout so the relative path to `auto_sync/` resolves, and make sure
  `beautifulsoup4` installed correctly (`pip show beautifulsoup4`).
- **Lots of `failed` rows with "chart circles never rendered"**: fut.gg
  may be slow, or Cloudflare may be showing a challenge page. Try
  `--headless` off (default) so you can see what's happening, and increase
  `--timeout-ms`. Do not try to defeat a Cloudflare challenge — if it's
  consistently blocking, stop and let me know rather than working around it.
- **A few `card_id`-only entries in a hand-built list failing with "unverified
  URL guess"**: `capture_charts.py` can only build a full canonical URL
  from a bare card ID by guessing (`fut.gg` URLs also need a player-id/slug
  segment a card ID alone doesn't give). Prefer full URLs — everything
  `build_sample_list.py` produces already has them.
