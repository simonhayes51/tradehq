"""
Phase 2 — standalone single-card FUT.GG "Year" chart extractor.

Reconstructs a daily price series from the rendered Recharts SVG markup of
a fut.gg player page's price chart widget (Year tab). This is a RESEARCH
tool, isolated from production auto_sync / backend code. It does not fetch
anything itself in this environment (network egress to fut.gg is blocked
in this sandbox) — it operates on already-captured HTML.

See docs/phase1_findings.md for how the chart geometry was reverse
engineered and why every value here is tagged confidence="reconstructed"
rather than "exact": there is no embedded JSON payload in the captures
seen so far, and the only y-axis calibration available is abbreviated,
rounded tick labels ("3.6K", "147K", ...), not the true chart domain.

Output rows (one per reconstructed day):
    card_id | date | price | source | confidence
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

CIRCLE_RE = re.compile(
    r'<circle\b[^>]*\bcx="(?P<cx>-?[\d.]+)"[^>]*\bcy="(?P<cy>-?[\d.]+)"[^>]*'
    r'class="[^"]*recharts-area-dot[^"]*"'
)
# Also tolerate cy-before-cx attribute ordering, and dot classes without
# "area-dot" (older markup variants).
CIRCLE_RE_ALT = re.compile(
    r'<circle\b[^>]*\bcy="(?P<cy>-?[\d.]+)"[^>]*\bcx="(?P<cx>-?[\d.]+)"[^>]*'
    r'class="[^"]*recharts-dot[^"]*"'
)

X_TICK_RE = re.compile(
    # [^>]* after the class attribute is required: real fut.gg markup has
    # more attributes (text-anchor, fill) between class="..." and the
    # tag's closing '>'. This bug survived the original Phase 1/3
    # validation undetected because fixtures/vdv_264453_year_chart.html
    # was hand-simplified when saved (those extra attributes stripped),
    # so it never exercised this path -- only surfaced once
    # auto_sync/research_fc26_chart_report.py ran this same algorithm
    # against genuinely raw captured production HTML. Fixed there and
    # ported back here to keep the two extractors in sync.
    r'<text\b[^>]*\bx="(?P<x>-?[\d.]+)"[^>]*class="[^"]*recharts-cartesian-axis-tick-value[^"]*"[^>]*'
    r'>\s*<tspan[^>]*>(?P<label>[A-Za-z]{3}\s+\d{1,2})</tspan>'
)
Y_TICK_RE = re.compile(
    r'<text\b[^>]*\by="(?P<y>-?[\d.]+)"[^>]*class="[^"]*recharts-cartesian-axis-tick-value[^"]*"[^>]*'
    r'>\s*<tspan[^>]*>(?P<label>[\d,.]+)\s*(?P<suffix>[KMkm]?)</tspan>'
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


@dataclass
class PricePoint:
    card_id: str
    obs_date: date
    price: int
    source: str
    confidence: str


class ChartParseError(RuntimeError):
    pass


def _parse_abbrev_number(raw: str, suffix: str) -> float:
    val = float(raw.replace(",", ""))
    suffix = suffix.upper()
    if suffix == "K":
        val *= 1_000
    elif suffix == "M":
        val *= 1_000_000
    return val


def _extract_circles(html: str) -> list[tuple[float, float]]:
    pts = [(float(m["cx"]), float(m["cy"])) for m in CIRCLE_RE.finditer(html)]
    if not pts:
        pts = [(float(m["cx"]), float(m["cy"])) for m in CIRCLE_RE_ALT.finditer(html)]
    if not pts:
        raise ChartParseError("no chart data points (<circle> dots) found in HTML")
    # De-dup exact (cx, cy) repeats (defensive against duplicated markup).
    seen = set()
    deduped = []
    for p in pts:
        if p in seen:
            continue
        seen.add(p)
        deduped.append(p)
    deduped.sort(key=lambda p: p[0])
    return deduped


def _extract_x_ticks(html: str, reference_start_year: int) -> list[tuple[float, date]]:
    raw_ticks = X_TICK_RE.findall(html)
    if not raw_ticks:
        raise ChartParseError("no X-axis date tick labels found in HTML")

    ticks: list[tuple[float, date]] = []
    year = reference_start_year
    prev_month = None
    for x_str, label in raw_ticks:
        mon_str, day_str = label.split()
        month = MONTHS.get(mon_str.lower()[:3])
        if month is None:
            continue
        if prev_month is not None and month < prev_month:
            year += 1
        prev_month = month
        try:
            d = date(year, month, int(day_str))
        except ValueError:
            continue
        ticks.append((float(x_str), d))

    # De-dup identical (x, date) pairs; keep first-seen order.
    seen = set()
    deduped = []
    for t in ticks:
        if t in seen:
            continue
        seen.add(t)
        deduped.append(t)

    if len(deduped) < 2:
        raise ChartParseError("fewer than 2 usable X-axis ticks; cannot calibrate date scale")
    return deduped


def _extract_y_ticks(html: str) -> list[tuple[float, float]]:
    raw_ticks = Y_TICK_RE.findall(html)
    if not raw_ticks:
        raise ChartParseError("no Y-axis price tick labels found in HTML")

    parsed = []
    for y_str, num_str, suffix in raw_ticks:
        try:
            price = _parse_abbrev_number(num_str, suffix)
        except ValueError:
            continue
        parsed.append((float(y_str), price))

    # De-dup by (cy, price) exactly.
    seen = set()
    deduped = []
    for t in parsed:
        if t in seen:
            continue
        seen.add(t)
        deduped.append(t)

    if len(deduped) < 2:
        raise ChartParseError("fewer than 2 usable Y-axis ticks; cannot calibrate price scale")

    # Defensive check for the "duplicated stale tick block" anomaly seen in
    # Phase 1: if many ticks collapse onto the same cy/price (a degenerate,
    # non-monotonic set), that's a corrupted/partial paint capture, not a
    # real axis. Drop tick groups that don't have unique cy AND unique price
    # each monotonic vs the other, keep the largest such consistent run.
    deduped.sort(key=lambda t: t[0])
    cys = [t[0] for t in deduped]
    prices = [t[1] for t in deduped]
    monotonic_price_desc = all(prices[i] > prices[i + 1] for i in range(len(prices) - 1))
    if not monotonic_price_desc:
        raise ChartParseError(
            "Y-axis ticks are not monotonic (cy increasing, price decreasing) "
            f"after dedup: {deduped!r} — likely a corrupted/partial capture; "
            "refusing to calibrate on inconsistent ticks"
        )
    return deduped


def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Least-squares y = a + b*x. Returns (a, b)."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        raise ChartParseError("degenerate axis ticks (all at same position); cannot fit a scale")
    b = num / den
    a = mean_y - b * mean_x
    return a, b


def extract(html: str, card_id: str, reference_start_year: int,
            source: str = "futgg_chart") -> list[PricePoint]:
    """Parse one fut.gg Year-chart HTML capture into daily reconstructed prices."""
    circles = _extract_circles(html)
    x_ticks = _extract_x_ticks(html, reference_start_year)
    y_ticks = _extract_y_ticks(html)

    # date = start_date + (cx - x0) / px_per_day, fit via ordinal-day regression
    x0_date = x_ticks[0][1]
    xs = [t[0] for t in x_ticks]
    day_offsets = [(t[1] - x0_date).days for t in x_ticks]
    a_x, b_x = _linear_fit(xs, day_offsets)  # day_offset = a_x + b_x * cx

    ys = [t[0] for t in y_ticks]
    prices = [t[1] for t in y_ticks]
    a_y, b_y = _linear_fit(ys, prices)  # price = a_y + b_y * cy

    points: list[PricePoint] = []
    seen_dates: dict[date, PricePoint] = {}
    for cx, cy in circles:
        day_offset = round(a_x + b_x * cx)
        obs_date = x0_date + timedelta(days=day_offset)
        price = a_y + b_y * cy
        price_int = max(0, round(price))
        pp = PricePoint(
            card_id=card_id,
            obs_date=obs_date,
            price=price_int,
            source=source,
            confidence="reconstructed",
        )
        # If two circles map to the same calendar day (shouldn't happen for
        # a true daily series, but guard against it), keep the first and
        # note nothing silently — caller can inspect for duplicates via
        # len(points) vs len(seen_dates).
        if obs_date not in seen_dates:
            seen_dates[obs_date] = pp
            points.append(pp)

    points.sort(key=lambda p: p.obs_date)
    return points


def format_rows(points: list[PricePoint]) -> str:
    lines = []
    for p in points:
        lines.append(
            f"{p.card_id} | {p.obs_date.isoformat()} | {p.price} | {p.source} | {p.confidence}"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("html_path", type=Path, help="path to captured chart HTML")
    ap.add_argument("--card-id", required=True, help="fut.gg card/item id, e.g. 264453")
    ap.add_argument(
        "--start-year", type=int, required=True,
        help="calendar year of the chart's leftmost X-axis tick (e.g. 2025 for FC26 launch)",
    )
    ap.add_argument("--csv-out", type=Path, default=None, help="optional CSV output path")
    args = ap.parse_args()

    html = args.html_path.read_text(encoding="utf-8")
    try:
        points = extract(html, card_id=args.card_id, reference_start_year=args.start_year)
    except ChartParseError as e:
        print(f"PARSE FAILURE for {args.card_id}: {e}", file=sys.stderr)
        return 1

    print(format_rows(points))
    print(f"\n# {len(points)} points, {points[0].obs_date} .. {points[-1].obs_date}", file=sys.stderr)

    if args.csv_out:
        with args.csv_out.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["card_id", "date", "price", "source", "confidence"])
            for p in points:
                w.writerow([p.card_id, p.obs_date.isoformat(), p.price, p.source, p.confidence])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
