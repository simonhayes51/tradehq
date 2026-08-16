"""
One-off repair for UTF-8-as-Latin-1 mojibake in futgg_players text
columns (name/club/league/nation) - e.g. "Kylian MbappÃ©" instead of
"Kylian Mbappé".

BACKGROUND
----------
This is the classic "double-decode" artifact: a body that IS valid UTF-8
bytes gets decoded once as Latin-1/cp1252 before being re-encoded/stored
as UTF-8, so 'é' (UTF-8 bytes 0xC3 0xA9) turns into the two separate
characters 'Ã' + '©'. It is reversible exactly because it's a clean
byte-for-byte substitution: encoding the corrupted string back to
Latin-1 recovers the original UTF-8 bytes, which then decode correctly.

The write path for futgg_players.name (auto_sync/futgg_player_sync.py's
_parse_card_visual(), reading Playwright's page.content()) was audited
and found to already decode correctly - Chromium, unlike a raw HTTP
client trusting a possibly-wrong Content-Type header, renders text
correctly regardless of what the server declares. The most likely
sources of any *already-corrupted* row are therefore (a) a row written
before the current Playwright-based scraper existed, sitting stale
because a later scrape never touched it, or (b) FUT.GG's own page
occasionally rendering this way upstream, faithfully captured. Neither
is fixable by re-running the current scraper, hence this repair script.
As a separate hardening measure (belt-and-braces, not because it was
confirmed as the actual cause here), auto_sync/http_client.py's shared
GET helper now forces UTF-8 decoding rather than trusting a response's
declared/sniffed charset - see that file's own comment.

SAFETY
------
- Pre-filters to rows containing 'Ã' (via `~ 'Ã'`), the tell-tale lead
  byte of this exact corruption. 'Ã' is not a real character in any
  player/club/league/nation name FUT.GG's catalogue is expected to
  contain, so this is a narrow, low-false-positive filter rather than a
  blanket re-encode of every non-ASCII row.
- Only ever proposes a fix when `value.encode("latin-1").decode("utf-8")`
  succeeds cleanly (`errors="strict"` on both sides) - a genuinely
  correct string containing 'Ã' for some other reason will fail this
  round-trip and is left untouched, reported separately as "skipped".
- Defaults to DRY RUN: prints every proposed change and does not write
  anything unless --apply is passed.

Usage:
    python scripts/fix_futgg_name_mojibake.py                # dry run
    python scripts/fix_futgg_name_mojibake.py --apply         # writes
    python scripts/fix_futgg_name_mojibake.py --apply --limit 5000

Requires DATABASE_URL.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Optional

import asyncpg

# Columns known to hold free-text scraped from FUT.GG that could
# plausibly carry an accented character. Deliberately excludes
# controlled-vocabulary columns (rarity, primary_position, price_tier)
# which are drawn from a small ASCII set and have no legitimate reason
# to contain 'Ã'.
TEXT_COLUMNS = ("name", "club", "league", "nation")


def try_fix(value: str) -> Optional[str]:
    """Returns the repaired string, or None if `value` doesn't round-trip
    cleanly as UTF-8-decoded-as-Latin-1 (i.e. isn't this exact corruption)."""
    try:
        fixed = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if fixed == value:
        return None
    return fixed


async def run(dsn: str, apply: bool, limit: int) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        total_candidates = 0
        total_fixed = 0
        total_skipped = 0

        for column in TEXT_COLUMNS:
            rows = await conn.fetch(
                f"""
                SELECT source_card_id, {column} AS value
                FROM futgg_players
                WHERE {column} LIKE '%' || chr(195) || '%'  -- 'Ã' = U+00C3
                ORDER BY source_card_id
                LIMIT $1
                """,
                limit,
            )
            if not rows:
                continue

            print(f"\n=== {column}: {len(rows)} candidate row(s) ===")
            for row in rows:
                total_candidates += 1
                original = row["value"]
                fixed = try_fix(original)
                if fixed is None:
                    total_skipped += 1
                    print(f"  SKIP  source_card_id={row['source_card_id']}: {original!r} (not this corruption)")
                    continue

                print(f"  FIX   source_card_id={row['source_card_id']}: {original!r} -> {fixed!r}")
                total_fixed += 1
                if apply:
                    await conn.execute(
                        f"UPDATE futgg_players SET {column} = $2 WHERE source_card_id = $1",
                        row["source_card_id"],
                        fixed,
                    )

        print(
            f"\n{'APPLIED' if apply else 'DRY RUN'}: "
            f"{total_candidates} candidate(s), {total_fixed} fixed, {total_skipped} skipped."
        )
        if not apply and total_fixed:
            print("Re-run with --apply to write these changes.")
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write fixes (default: dry run only)")
    ap.add_argument("--limit", type=int, default=2000, help="Max rows to scan per column")
    args = ap.parse_args()

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1

    return asyncio.run(run(dsn, args.apply, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
