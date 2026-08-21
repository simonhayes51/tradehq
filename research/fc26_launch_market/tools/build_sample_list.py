"""
Phase 4 sample-list builder.

Runs LOCALLY (on a machine that can actually reach fut.gg — this repo's
sandboxed sessions cannot, see docs/phase1_findings.md). Crawls FUT.GG's
public player catalogue listing (/players/?page=N — the same listing
partition auto_sync/futgg_player_sync.py already uses in production, see
its DEFAULT_FULL_SCAN_PARTITIONS) to discover real FC26 base gold cards,
then visits a subset of candidates to read rating/position/rarity/league
via the EXISTING, already-tested parser in auto_sync/futgg_common.py
(parse_futgg_card, classify_price_tier) rather than re-implementing that
parsing here.

No repo catalogue of FC26 cards exists to seed this from (checked: only
FC25/FUTBIN scratch data and synthetic test fixtures are present), so this
script performs real (but conservative, delayed) discovery against the
live site. It does NOT invent any FUT.GG API/endpoint — it only follows
ordinary public listing pages and card pages a normal browser would load.

Output: sample_cards.csv in the --out-dir, with columns:
    card_id, url, name, rating, position, rarity, league, club, nation,
    lowest_bin, price_tier, category_tag

Usage (see ../tools/README.md for full Windows instructions):
    python build_sample_list.py --target 40 --out-dir ../../fc26_chart_fixtures
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
AUTO_SYNC_DIR = REPO_ROOT / "auto_sync"
if str(AUTO_SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(AUTO_SYNC_DIR))

try:
    from futgg_common import BASE_URL, CARD_URL_RE, classify_price_tier, parse_futgg_card
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Could not import auto_sync/futgg_common.py. Run this script from a "
        "checkout of the tradehq repo so ../../../auto_sync is reachable, "
        "and pip install beautifulsoup4 (see tools/requirements.txt)."
    ) from exc

from playwright.sync_api import sync_playwright

# Position buckets used for Phase 4's category matrix. Primary positions
# not covered here (GK, CDM handled under CM, etc.) fall through to
# "other" and are still eligible for selection but not a target bucket.
POSITION_BUCKETS = {
    "CB": {"CB"},
    "FULLBACK": {"LB", "RB", "LWB", "RWB"},
    "MIDFIELD": {"CM", "CDM", "CAM"},
    "WINGER": {"LW", "RW", "LM", "RM"},
    "STRIKER": {"ST", "CF"},
}

# Heuristic price-tier bucketing for SAMPLE DIVERSITY only -- these
# thresholds are not FUT.GG ground truth, just a way to spread the sample
# across cheap/mid/expensive cards. Adjust freely.
def price_bucket(lowest_bin: int | None) -> str:
    if lowest_bin is None:
        return "unknown"
    if lowest_bin <= 1000:
        return "discard"
    if lowest_bin <= 5000:
        return "cheap"
    if lowest_bin <= 50000:
        return "mid"
    return "expensive"


def position_bucket(primary_position: str | None) -> str:
    if not primary_position:
        return "other"
    pos = primary_position.strip().upper()
    for bucket, members in POSITION_BUCKETS.items():
        if pos in members:
            return bucket
    return "other"


def collect_candidate_urls(page, max_pages: int, delay: float, target_pool: int) -> list[str]:
    """Paginates the public catalogue listing, same partition path
    production code uses (/players/?page=N), collecting canonical card
    URLs matching CARD_URL_RE. Stops once target_pool is reached or
    max_pages is exhausted or a page returns no new cards."""
    seen: dict[str, None] = {}
    for page_number in range(1, max_pages + 1):
        url = f"{BASE_URL}/players/?page={page_number}"
        print(f"[listing] page {page_number}: {url}")
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if not resp or resp.status != 200:
            print(f"[listing] HTTP {resp.status if resp else 'none'} on page {page_number} -- stopping")
            break

        hrefs = page.eval_on_selector_all(
            "a[href*='/players/']", "els => els.map(e => e.getAttribute('href'))"
        )
        new_count = 0
        for href in hrefs:
            if not href:
                continue
            m = CARD_URL_RE.match(href.split("?")[0])
            if not m:
                continue
            canonical = f"{BASE_URL}{href.split('?')[0]}"
            if canonical not in seen:
                seen[canonical] = None
                new_count += 1

        print(f"[listing] page {page_number}: +{new_count} new, {len(seen)} total")
        if new_count == 0 and page_number > 1:
            print("[listing] no new cards on this page -- stopping pagination")
            break
        if len(seen) >= target_pool:
            print(f"[listing] reached target pool size {target_pool} -- stopping pagination")
            break
        time.sleep(delay)

    return list(seen.keys())


def build_sample(
    urls: list[str],
    page,
    target: int,
    delay: float,
    max_detail_visits: int,
) -> list[dict]:
    """Visits candidate card pages (capped at max_detail_visits), parses
    each with the existing production parser, keeps only common/rare gold
    (skips specials per Phase 4 instructions), buckets by position x price
    tier, and greedily selects up to `target` cards spread across buckets."""
    random.shuffle(urls)
    buckets: dict[tuple[str, str], list[dict]] = {}
    visited = 0

    for url in urls:
        if visited >= max_detail_visits:
            print(f"[detail] reached max_detail_visits={max_detail_visits} -- stopping")
            break
        visited += 1
        print(f"[detail] ({visited}/{max_detail_visits}) {url}")
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if not resp or resp.status != 200:
                print(f"  HTTP {resp.status if resp else 'none'} -- skipping")
                continue
            html = page.content()
        except Exception as exc:  # noqa: BLE001
            print(f"  navigation failed: {exc!r} -- skipping")
            continue

        try:
            card = parse_futgg_card(html, url)
        except Exception as exc:  # noqa: BLE001
            print(f"  parse failed: {exc!r} -- skipping")
            time.sleep(delay)
            continue

        tier = classify_price_tier(card)
        if tier not in ("gold_common", "gold_rare"):
            print(f"  tier={tier} -- not a base gold, skipping")
            time.sleep(delay)
            continue

        pos_bucket = position_bucket(card.primary_position)
        pr_bucket = price_bucket(card.lowest_bin)
        key = (pos_bucket, pr_bucket)
        buckets.setdefault(key, []).append({
            "card_id": card.source_card_id,
            "url": url,
            "name": card.name or "",
            "rating": card.rating if card.rating is not None else "",
            "position": card.primary_position or "",
            "rarity": card.rarity or "",
            "league": card.league or "",
            "club": card.club or "",
            "nation": card.nation or "",
            "lowest_bin": card.lowest_bin if card.lowest_bin is not None else "",
            "price_tier": pr_bucket,
            "category_tag": f"{pos_bucket}/{pr_bucket}/{tier}",
        })
        time.sleep(delay)

    # Greedy round-robin selection across buckets so the sample stays
    # spread across the Phase 4 category matrix rather than clumping.
    selected: list[dict] = []
    bucket_keys = list(buckets.keys())
    random.shuffle(bucket_keys)
    idx = 0
    while len(selected) < target and any(buckets[k] for k in bucket_keys):
        key = bucket_keys[idx % len(bucket_keys)]
        if buckets[key]:
            selected.append(buckets[key].pop())
        idx += 1
        if idx > 10_000:  # safety valve
            break

    print("\n[summary] bucket coverage in final sample:")
    from collections import Counter
    counts = Counter(row["category_tag"] for row in selected)
    for tag, n in sorted(counts.items()):
        print(f"  {tag}: {n}")

    return selected


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=int, default=40, help="target sample size (default 40)")
    ap.add_argument("--max-listing-pages", type=int, default=15)
    ap.add_argument("--target-pool", type=int, default=250, help="candidate URLs to collect before detail visits")
    ap.add_argument("--max-detail-visits", type=int, default=180, help="cap on individual card-page visits")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between requests")
    ap.add_argument("--headless", action="store_true", default=False)
    ap.add_argument(
        "--out", type=Path,
        default=REPO_ROOT / "research" / "fc26_launch_market" / "tools" / "sample_cards.csv",
    )
    args = ap.parse_args()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ))
        try:
            urls = collect_candidate_urls(
                page, args.max_listing_pages, args.delay, args.target_pool
            )
            print(f"\n[listing] {len(urls)} candidate card URLs collected\n")
            selected = build_sample(urls, page, args.target, args.delay, args.max_detail_visits)
        finally:
            browser.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "card_id", "url", "name", "rating", "position", "rarity", "league",
        "club", "nation", "lowest_bin", "price_tier", "category_tag",
    ]
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in selected:
            w.writerow(row)

    print(f"\nWrote {len(selected)} cards to {args.out}")
    if len(selected) < args.target:
        print(
            f"WARNING: only found {len(selected)}/{args.target} qualifying cards. "
            "Try increasing --max-listing-pages / --target-pool / --max-detail-visits."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
