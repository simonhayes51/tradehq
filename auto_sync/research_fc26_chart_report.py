"""
Phase 4 report generator -- runs the same way as the other research_fc26_*
scripts (temporary Coolify command override + manual run), reads every row
already captured into fc26_chart_capture_fixtures, runs the Phase 2 SVG
reconstruction algorithm against each captured chart_html, and prints the
Phase 4 report straight to stdout/logs: per-card summary, independent
current-price validation stats, and a generalisation verdict.

Deliberately self-contained rather than importing
research/fc26_launch_market/scripts/futgg_chart_extractor.py: that module
lives outside auto_sync/, and Dockerfile.player only COPYs the auto_sync/
directory into the image, so it isn't present at runtime here. The
extraction algorithm below is a direct port of that script's logic
(documented in docs/phase1_findings.md and validated in Phase 3) -- same
duplication trade-off already made for dismiss_cookie_banner() in
research_fc26_chart_capture.py.

Makes no network requests -- reads only from fc26_chart_capture_fixtures.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import statistics
import sys
from datetime import date, timedelta

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s INFO %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("research_fc26_chart_report")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found")

# --- Phase 2 extraction algorithm (ported, see module docstring) ----------

CIRCLE_RE = re.compile(
    r'<circle\b[^>]*\bcx="(?P<cx>-?[\d.]+)"[^>]*\bcy="(?P<cy>-?[\d.]+)"[^>]*'
    r'class="[^"]*recharts-area-dot[^"]*"'
)
CIRCLE_RE_ALT = re.compile(
    r'<circle\b[^>]*\bcy="(?P<cy>-?[\d.]+)"[^>]*\bcx="(?P<cx>-?[\d.]+)"[^>]*'
    r'class="[^"]*recharts-dot[^"]*"'
)
X_TICK_RE = re.compile(
    # [^>]* after the class attribute is required: real fut.gg markup has
    # more attributes (text-anchor, fill) between class="..." and the
    # tag's closing '>' -- a bug that survived Phase 1/2 undetected
    # because the only fixture ever tested locally had been hand-
    # simplified (those extra attributes stripped) when it was saved,
    # and only surfaced once this script ran against genuinely raw
    # captured production HTML.
    r'<text\b[^>]*\bx="(?P<x>-?[\d.]+)"[^>]*class="[^"]*recharts-cartesian-axis-tick-value[^"]*"[^>]*'
    r'>\s*<tspan[^>]*>(?P<label>[A-Za-z]{3}\s+\d{1,2})</tspan>'
)
Y_TICK_RE = re.compile(
    r'<text\b[^>]*\by="(?P<y>-?[\d.]+)"[^>]*class="[^"]*recharts-cartesian-axis-tick-value[^"]*"[^>]*'
    r'>\s*<tspan[^>]*>(?P<label>[\d,.]+)\s*(?P<suffix>[KMkm]?)</tspan>'
)
CURRENT_PRICE_RE = re.compile(
    r'text-3xl font-bold text-white tabular-nums">(?P<price>[\d,]+)</span>'
)
CAPTURED_DATE_RE = re.compile(
    r'text-gray-400 text-sm mt-1">(?P<label>[A-Za-z]+ \d{1,2}, \d{4})'
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
FULL_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


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
        raise ChartParseError("no chart data points (<circle> dots) found")
    seen, deduped = set(), []
    for p in pts:
        if p in seen:
            continue
        seen.add(p)
        deduped.append(p)
    deduped.sort(key=lambda p: p[0])
    return deduped


def _extract_x_ticks(html: str, captured_date: date | None) -> list[tuple[float, date]]:
    """Builds ticks using an arbitrary dummy base year first (their
    RELATIVE years -- whether a Dec->Jan rollover happened between two
    ticks -- are always correctly inferable from the labels alone), then
    anchors that relative structure to the real calendar using the
    page's own displayed "as of" date, captured_date. Tick labels never
    carry a year ("Aug 14", "Sep 17"), so a chart whose visible window
    doesn't cross a Dec->Jan boundary gives ZERO signal about which
    actual year it's from -- assuming a fixed year for every chart
    (e.g. FC26's launch year) is only correct for Year-view charts that
    happen to start at the launch date; a Week-view chart from a full
    year later has no such anchor and was silently mis-dated a year
    early until this fix (caught via a live report run where ~35 cards
    all showed dates "2025-08-14..2025-08-21" despite being captured on
    2026-08-21)."""
    raw_ticks = X_TICK_RE.findall(html)
    if not raw_ticks:
        raise ChartParseError("no X-axis date tick labels found")

    DUMMY_BASE_YEAR = 2000
    ticks: list[tuple[float, date]] = []
    year = DUMMY_BASE_YEAR
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
    seen, deduped = set(), []
    for t in ticks:
        if t in seen:
            continue
        seen.add(t)
        deduped.append(t)
    if len(deduped) < 2:
        raise ChartParseError("fewer than 2 usable X-axis ticks")

    if captured_date is not None:
        last_dummy_date = deduped[-1][1]
        for year_guess in (captured_date.year, captured_date.year - 1):
            shift = year_guess - last_dummy_date.year
            try:
                shifted_last = last_dummy_date.replace(year=last_dummy_date.year + shift)
            except ValueError:
                continue  # Feb 29 into a non-leap year; try the other guess
            if shifted_last <= captured_date + timedelta(days=3):
                shifted = []
                for x, d in deduped:
                    try:
                        shifted.append((x, d.replace(year=d.year + shift)))
                    except ValueError:
                        shifted.append((x, d))  # leave unshifted rather than crash
                deduped = shifted
                break

    return deduped


def _longest_monotonic_decreasing_subseq(
    ticks: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """ticks sorted by cy ascending; returns the longest subsequence
    whose price is strictly decreasing (a valid Recharts axis is always
    monotonic, so the longest such run is the best guess at "the real
    ticks" when one or more spurious extra ticks are mixed in)."""
    n = len(ticks)
    if n == 0:
        return []
    lengths = [1] * n
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            if ticks[j][1] > ticks[i][1] and lengths[j] + 1 > lengths[i]:
                lengths[i] = lengths[j] + 1
                prev[i] = j
    best_end = max(range(n), key=lambda i: lengths[i])
    seq = []
    i = best_end
    while i != -1:
        seq.append(ticks[i])
        i = prev[i]
    seq.reverse()
    return seq


def _extract_y_ticks(html: str) -> tuple[list[tuple[float, float]], list[str]]:
    """Returns (ticks, warnings) -- warnings records any malformed/
    duplicate/anomalous tick encountered (Phase 4's explicit ask),
    instead of only silently dropping them. May return a single tick
    (flat/near-flat chart -- see extract_series' special-case handling)
    or a recovered subset with one or more anomalous ticks dropped
    (production evidence: several cards showed 6 evenly-spaced,
    genuinely monotonic ticks PLUS one extra spurious tick breaking
    monotonicity -- the same family of stray/duplicate-tick render
    artifact Phase 1 first found, just not an exact duplicate this
    time)."""
    warnings: list[str] = []
    raw_ticks = Y_TICK_RE.findall(html)
    if not raw_ticks:
        raise ChartParseError("no Y-axis price tick labels found")

    parsed = []
    for y_str, num_str, suffix in raw_ticks:
        try:
            price = _parse_abbrev_number(num_str, suffix)
        except ValueError:
            continue
        parsed.append((float(y_str), price))

    seen, deduped = set(), []
    dup_count = 0
    for t in parsed:
        if t in seen:
            dup_count += 1
            continue
        seen.add(t)
        deduped.append(t)
    if dup_count:
        warnings.append(f"{dup_count} duplicate Y-axis tick entries dropped")

    if len(deduped) < 1:
        raise ChartParseError("no usable Y-axis ticks")

    deduped.sort(key=lambda t: t[0])
    if len(deduped) == 1:
        return deduped, warnings  # flat-chart candidate; extract_series decides

    prices = [t[1] for t in deduped]
    monotonic = all(prices[i] > prices[i + 1] for i in range(len(prices) - 1))
    if monotonic:
        return deduped, warnings

    recovered = _longest_monotonic_decreasing_subseq(deduped)
    if len(recovered) >= 4:
        dropped = len(deduped) - len(recovered)
        warnings.append(
            f"dropped {dropped} anomalous Y-axis tick(s) to restore monotonicity "
            f"(kept {recovered}, full raw set was {deduped!r})"
        )
        return recovered, warnings

    warnings.append(f"Y-axis ticks not monotonic and no recoverable subset: {deduped!r}")
    raise ChartParseError(f"non-monotonic Y-axis ticks: {deduped!r}")


def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        raise ChartParseError("degenerate axis ticks (all at same position)")
    b = num / den
    a = mean_y - b * mean_x
    return a, b


def extract_series(html: str, captured_date: date | None):
    """Returns (points, y_tick_warnings, calibration_max_pct_err) where
    points is a date-sorted list of (date, price) and
    calibration_max_pct_err is the largest residual between a Y-axis tick
    label and the linear fit through all ticks (Phase 4's 'Y-axis
    calibration quality'). captured_date anchors the X-axis tick labels
    (which never carry a year) to the real calendar -- see
    _extract_x_ticks' docstring."""
    circles = _extract_circles(html)
    x_ticks = _extract_x_ticks(html, captured_date)
    y_ticks, warnings = _extract_y_ticks(html)

    x0_date = x_ticks[0][1]
    xs = [t[0] for t in x_ticks]
    day_offsets = [(t[1] - x0_date).days for t in x_ticks]
    a_x, b_x = _linear_fit(xs, day_offsets)

    if len(y_ticks) == 1:
        # Phase 4's explicitly-anticipated edge case: "the chart sits
        # near discard price for long periods" -- only one distinct
        # Y-axis tick renders because the price barely varies across the
        # whole window. Confirm the chart really is flat (all circles'
        # cy clustered near that one tick's cy) before trusting a
        # constant-price fit instead of a 2-point regression.
        tick_cy, tick_price = y_ticks[0]
        cys = [cy for _, cy in circles]
        spread = (max(cys) - min(cys)) if cys else 0.0
        mean_cy = statistics.mean(cys) if cys else tick_cy
        if spread <= 5 and abs(mean_cy - tick_cy) <= 5:
            a_y, b_y = tick_price, 0.0
            warnings.append(
                f"only 1 Y-axis tick found; chart is flat (cy spread={spread:.1f}px) "
                f"-- using constant price {tick_price:.0f}"
            )
        else:
            raise ChartParseError(
                f"only 1 Y-axis tick found and chart is NOT flat (cy spread={spread:.1f}px) "
                "-- cannot calibrate"
            )
    else:
        ys = [t[0] for t in y_ticks]
        prices = [t[1] for t in y_ticks]
        a_y, b_y = _linear_fit(ys, prices)

    max_pct_err = 0.0
    for cy, label_price in y_ticks:
        pred = a_y + b_y * cy
        if label_price:
            max_pct_err = max(max_pct_err, abs(pred - label_price) / label_price * 100)

    points_by_date: dict[date, int] = {}
    for cx, cy in circles:
        day_offset = round(a_x + b_x * cx)
        obs_date = x0_date + timedelta(days=day_offset)
        price = max(0, round(a_y + b_y * cy))
        if obs_date not in points_by_date:
            points_by_date[obs_date] = price

    points = sorted(points_by_date.items())

    # Sanity bound: a date before the chart's own first labeled tick, or
    # after the page's own "as of" capture date, is definitionally
    # impossible -- no FC26 card existed before ~Sep 2025, and no chart
    # can show a date in the future relative to when it was captured.
    # Caught in a real production bulk-load run: the aggregate date range
    # across ~2600 cards briefly showed max=2026-12-01 despite every
    # single capture happening on/before 2026-08-21. Root cause: circles
    # positioned past the last labeled X-axis tick extrapolate via the
    # linear day-offset regression, and per-tick pixel spacing isn't
    # perfectly uniform in every chart, so that extrapolation can drift
    # for cx values far beyond the last real tick. Affects only the
    # (rare) drifted points, not the whole card -- drop just those,
    # don't discard an otherwise-good extraction.
    if captured_date is not None and points:
        lower_bound = x0_date - timedelta(days=2)
        upper_bound = captured_date + timedelta(days=2)
        clean_points = [(d, p) for d, p in points if lower_bound <= d <= upper_bound]
        dropped = len(points) - len(clean_points)
        if dropped:
            warnings.append(
                f"dropped {dropped} point(s) with implausible dates outside "
                f"[{lower_bound}, {upper_bound}] (likely extrapolation drift "
                f"past the last labeled X-axis tick)"
            )
            points = clean_points

    return points, warnings, max_pct_err


def parse_current_price_badge(html: str) -> int | None:
    m = CURRENT_PRICE_RE.search(html)
    if not m:
        return None
    return int(m.group("price").replace(",", ""))


def parse_captured_date(html: str) -> date | None:
    """The page's own displayed 'as of' date (e.g. 'August 21, 2026,
    8:24 AM' -> date(2026, 8, 21)) -- the authoritative anchor for
    resolving X-axis tick labels, which never carry a year themselves."""
    m = CAPTURED_DATE_RE.search(html)
    if not m:
        return None
    label = m.group("label")
    try:
        mon_str, rest = label.split(" ", 1)
        day_str, year_str = rest.split(",")
        month = FULL_MONTHS.get(mon_str.lower())
        if month is None:
            return None
        return date(int(year_str.strip()), month, int(day_str.strip()))
    except (ValueError, IndexError):
        return None


# --- Report ----------------------------------------------------------------

async def main() -> int:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT * FROM fc26_chart_capture_fixtures ORDER BY card_id"
        )
    finally:
        await conn.close()

    if not rows:
        log.error("No rows in fc26_chart_capture_fixtures -- has capture run yet?")
        return 1

    log.info("=" * 100)
    log.info("PHASE 4 REPORT -- %d cards total", len(rows))
    log.info("=" * 100)

    per_card_summaries = []
    validation_pairs = []  # (card_id, reconstructed_last_price, displayed_price, pct_err)
    failures = []

    for row in rows:
        card_id, name = row["card_id"], row["name"]
        position, rating, rarity = row["primary_position"], row["rating"], row["rarity"]

        if row["status"] != "success" or not row["chart_html"]:
            failures.append({
                "card_id": card_id, "name": name, "url": row["url"],
                "error_reason": row["error_reason"] or "(no reason recorded)",
            })
            continue

        html = row["chart_html"]
        # Anchor for resolving X-axis tick labels (which never carry a
        # year): prefer the page's own displayed "as of" date; fall back
        # to this row's DB capture timestamp if that badge wasn't found.
        # NOT a fixed year -- a hardcoded FC26-launch-year assumption
        # silently mis-dated ~35 cards by a full year in a live run (see
        # _extract_x_ticks' docstring).
        captured_date = parse_captured_date(html)
        if captured_date is None and row["captured_at"] is not None:
            captured_date = row["captured_at"].date()
        try:
            points, tick_warnings, calib_max_pct_err = extract_series(html, captured_date)
        except ChartParseError as exc:
            failures.append({
                "card_id": card_id, "name": name, "url": row["url"],
                "error_reason": f"extraction failed: {exc}",
            })
            continue

        if not points:
            failures.append({
                "card_id": card_id, "name": name, "url": row["url"],
                "error_reason": "extraction produced zero points",
            })
            continue

        first_date, first_price = points[0]
        last_date, last_price = points[-1]

        displayed_price = parse_current_price_badge(html)
        pct_err = None
        if displayed_price:
            abs_err = abs(last_price - displayed_price)
            pct_err = abs_err / displayed_price * 100 if displayed_price else None
            validation_pairs.append((card_id, last_price, displayed_price, pct_err, abs_err))

        summary = {
            "card_id": card_id, "name": name, "position": position,
            "rating": rating, "rarity": rarity,
            "num_points": len(points), "first_date": first_date, "last_date": last_date,
            "first_price": first_price, "last_price": last_price,
            "calib_max_pct_err": calib_max_pct_err,
            "tick_warnings": tick_warnings,
            "displayed_price": displayed_price, "pct_err_vs_displayed": pct_err,
        }
        per_card_summaries.append(summary)

        log.info(
            "card_id=%-9s %-28s pos=%-4s rating=%-3s rarity=%-12s points=%-4d "
            "%s(%s) -> %s(%s) calib_max_err=%.2f%% ticks=%s displayed=%s pct_err=%s%s",
            card_id, (name or "")[:28], position or "?", rating or "?", (rarity or "?")[:12],
            len(points), first_price, first_date, last_price, last_date,
            calib_max_pct_err,
            "OK" if not tick_warnings else f"WARN({'; '.join(tick_warnings)})",
            displayed_price if displayed_price is not None else "n/a",
            f"{pct_err:.2f}%" if pct_err is not None else "n/a",
            "  <-- LOW POINT COUNT, verify" if len(points) < 20 else "",
        )

    log.info("-" * 100)
    log.info("FAILURES (%d) -- not silently skipped:", len(failures))
    for f in failures:
        log.info("  card_id=%s name=%r url=%s reason=%s", f["card_id"], f["name"], f["url"], f["error_reason"])

    log.info("-" * 100)
    log.info("STRUCTURAL SUMMARY")
    if per_card_summaries:
        point_counts = [s["num_points"] for s in per_card_summaries]
        log.info(
            "  point counts: min=%d max=%d mean=%.1f median=%.1f (Van de Ven Phase 1 fixture = 339)",
            min(point_counts), max(point_counts),
            statistics.mean(point_counts), statistics.median(point_counts),
        )
        low_count_cards = [s for s in per_card_summaries if s["num_points"] < 20]
        log.info("  cards with <20 points (recently-added OR still-suspect): %d -> %s",
                  len(low_count_cards), [s["card_id"] for s in low_count_cards])
        warned_cards = [s for s in per_card_summaries if s["tick_warnings"]]
        log.info("  cards with Y-axis tick warnings: %d -> %s",
                  len(warned_cards), [(s["card_id"], s["tick_warnings"]) for s in warned_cards])
        calib_errs = [s["calib_max_pct_err"] for s in per_card_summaries]
        log.info("  Y-axis calibration max-tick-residual: mean=%.2f%% median=%.2f%% max=%.2f%%",
                  statistics.mean(calib_errs), statistics.median(calib_errs), max(calib_errs))

    log.info("-" * 100)
    log.info("INDEPENDENT CURRENT-PRICE VALIDATION (reconstructed last point vs displayed badge price, "
              "NOT used in calibration)")
    if validation_pairs:
        errs = [v[4] for v in validation_pairs]
        pcts = [v[3] for v in validation_pairs]
        exact = sum(1 for e in errs if e == 0)
        near = sum(1 for e in errs if e <= 5)
        within_1 = sum(1 for p in pcts if p <= 1)
        within_2 = sum(1 for p in pcts if p <= 2)
        within_5 = sum(1 for p in pcts if p <= 5)
        n = len(validation_pairs)
        log.info("  n compared: %d / %d successful extractions", n, len(per_card_summaries))
        log.info("  exact matches (0 coin diff): %d (%.1f%%)", exact, exact / n * 100)
        log.info("  near matches (<=5 coin diff): %d (%.1f%%)", near, near / n * 100)
        log.info("  mean absolute error: %.1f coins", statistics.mean(errs))
        log.info("  median absolute error: %.1f coins", statistics.median(errs))
        log.info("  mean percentage error: %.2f%%", statistics.mean(pcts))
        log.info("  median percentage error: %.2f%%", statistics.median(pcts))
        log.info("  max percentage error: %.2f%%", max(pcts))
        log.info("  within 1%%: %d (%.1f%%)", within_1, within_1 / n * 100)
        log.info("  within 2%%: %d (%.1f%%)", within_2, within_2 / n * 100)
        log.info("  within 5%%: %d (%.1f%%)", within_5, within_5 / n * 100)
        worst = sorted(validation_pairs, key=lambda v: -v[3])[:5]
        log.info("  worst 5 by pct error: %s",
                  [(w[0], f"recon={w[1]}", f"displayed={w[2]}", f"{w[3]:.1f}%") for w in worst])
    else:
        log.info("  no displayed-price badge found on any successful card -- cannot validate")

    log.info("=" * 100)
    log.info("SUCCESS RATE: %d/%d (%.1f%%)", len(per_card_summaries), len(rows),
              len(per_card_summaries) / len(rows) * 100)
    log.info("=" * 100)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
