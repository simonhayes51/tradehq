"""
Exports rows from fc26_chart_capture_fixtures (populated by
auto_sync/research_fc26_chart_capture.py running against production's
DATABASE_URL) into the local file layout the Phase 2 extractor and Phase 4
report expect:

    fc26_chart_fixtures/
        <card_id>.html
        full_pages/<card_id>.html   (only cards that have one)
        manifest.csv

Run this wherever DATABASE_URL is reachable -- e.g. the same "temporarily
override this Coolify resource's command" pattern used to run the capture
job itself (see tools/README.md), or from any machine/psql client that
already has production DB access. It only SELECTs from
fc26_chart_capture_fixtures; it never writes to the database.

Usage:
    DATABASE_URL=postgresql://... python export_fc26_chart_fixtures.py \
        --out ../../../fc26_chart_fixtures
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
from pathlib import Path

import asyncpg

MANIFEST_FIELDS = [
    "card_id", "url", "name", "rating", "primary_position", "rarity",
    "league", "category_tag", "status", "num_circles", "attempts",
    "error_reason", "has_full_page", "captured_at",
]


async def export(database_url: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    full_pages_dir = out_dir / "full_pages"
    full_pages_dir.mkdir(parents=True, exist_ok=True)

    conn = await asyncpg.connect(database_url)
    try:
        rows = await conn.fetch(
            """
            SELECT card_id, url, name, rating, primary_position, rarity,
                   league, category_tag, status, num_circles, attempts,
                   error_reason, chart_html, full_page_html, captured_at
            FROM fc26_chart_capture_fixtures
            ORDER BY card_id
            """
        )
    finally:
        await conn.close()

    manifest_rows = []
    chart_written = 0
    full_page_written = 0

    for r in rows:
        card_id = r["card_id"]
        if r["chart_html"]:
            (out_dir / f"{card_id}.html").write_text(r["chart_html"], encoding="utf-8")
            chart_written += 1
        has_full_page = "no"
        if r["full_page_html"]:
            (full_pages_dir / f"{card_id}.html").write_text(r["full_page_html"], encoding="utf-8")
            full_page_written += 1
            has_full_page = "yes"

        manifest_rows.append({
            "card_id": card_id,
            "url": r["url"],
            "name": r["name"] or "",
            "rating": r["rating"] if r["rating"] is not None else "",
            "primary_position": r["primary_position"] or "",
            "rarity": r["rarity"] or "",
            "league": r["league"] or "",
            "category_tag": r["category_tag"] or "",
            "status": r["status"],
            "num_circles": r["num_circles"],
            "attempts": r["attempts"],
            "error_reason": r["error_reason"] or "",
            "has_full_page": has_full_page,
            "captured_at": r["captured_at"].isoformat() if r["captured_at"] else "",
        })

    with (out_dir / "manifest.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for row in manifest_rows:
            w.writerow(row)

    success = sum(1 for r in manifest_rows if r["status"] == "success")
    failed = sum(1 for r in manifest_rows if r["status"] == "failed")
    print(
        f"Exported {len(manifest_rows)} rows ({success} success, {failed} failed) to {out_dir}\n"
        f"  chart HTML files written: {chart_written}\n"
        f"  full-page HTML files written: {full_page_written}\n"
        f"  manifest: {out_dir / 'manifest.csv'}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out", type=Path,
        default=Path(__file__).resolve().parents[3] / "fc26_chart_fixtures",
    )
    ap.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    args = ap.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL not set (env var or --database-url)")

    asyncio.run(export(args.database_url, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
