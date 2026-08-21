# Phase 1 — FUT.GG Year chart structure (Van de Ven, card 264453)

Source: user-captured HTML of the "Year" tab price chart widget on the fut.gg
player page for Micky van de Ven (FC26 base gold, item id 264453), captured
2026-08-21. Saved at `fixtures/vdv_264453_year_chart.html`.

## 1. How many data points exist
**339** `<circle class="recharts-dot recharts-area-dot">` elements.

## 2. One point per day?
Yes. `cx` values are perfectly evenly spaced: every consecutive step is
`0.9674556213...` px (constant to 6 decimal places across all 338 gaps).
Total span is exactly `382 - 55 = 327` px = `338 * 0.967456`. The X axis
ticks give real calendar dates at specific `cx` positions:

| tick cx | label |
|---|---|
| 55.00 | Sep 17 |
| 97.57 | Oct 31 |
| 140.14 | Dec 14 |
| 182.70 | Jan 27 |
| 225.27 | Mar 12 |
| 267.84 | Apr 25 |
| 310.41 | Jun 08 |
| 352.98 | Jul 22 |

`(97.568 - 55) / 0.967456 = 44.0` px-steps, and Sep 17 → Oct 31 is exactly 44
calendar days. This holds for every tick pair. So the X axis is a true
**linear day-scale**, first point = Sep 17 2025 (FC26's known launch/Web App
date), one point per calendar day, last point = today (page capture date,
Aug 21 2026 — matches the "current price" timestamp shown above the chart).

## 3. Missing points?
None in this capture — spacing is perfectly uniform for all 338 gaps, so
every day in the range has a rendered point. (The extractor should not
*assume* this always holds — see Phase 2 resilience notes.)

## 4. cx → date mapping
Two independent ways to derive it, and they agree to sub-pixel precision:
- **Point-index method**: `date = start_date + i` where `i` is the circle's
  0-based order and `start_date` is the leftmost X-axis tick's date.
- **Axis-calibration method** (preferred, more robust): linear-regress
  calendar day-offset against `cx` using *all* X-axis tick label/position
  pairs, then invert for any `cx` (including circle positions). This does
  not depend on circle count or ordering, so it degrades gracefully if a
  future chart is missing points or the layout changes.

X-axis labels are `"Mon DD"` with no year, so the extractor must infer year
rollover: whenever a tick's month number is lower than the previous tick's,
increment the year (confirmed here: Dec 14 → Jan 27 crosses into 2026).

## 5. cy → price mapping
The Y axis is **linear**. Six real ticks (a duplicated/garbled block of 6
identical "3.6K" ticks also appeared in the raw captured markup before the
real set — see anomaly note below):

| cy | label | parsed value |
|---|---|---|
| 242.917 | 3.6K | 3,600 |
| 198.750 | 32.3K | 32,300 |
| 154.583 | 61K | 61,000 |
| 110.417 | 89.6K | 89,600 |
| 66.250 | 118K | 118,000 |
| 22.083 | 147K | 147,000 |

Ticks are evenly spaced in `cy` (constant Δ=44.1667) and the price gaps
between them are all ~28.4–29.0K — consistent with a linear scale where
Recharts picked "nice" tick values and the axis labels are the values
**rounded to ~3 significant figures**, not exact boundaries.

Least-squares fit across all 6 ticks: `price = 161195.71 - 648.6469 * cy`.
Residuals vs. the (rounded) tick labels: -128.6 to +222.9 coins, i.e. up to
~0.19% of the tick's value — this is rounding noise in the axis *labels*,
not in the underlying scale, since `cy` itself is rendered with full
floating-point precision (e.g. `144.5118549511855`), reflecting Recharts'
internal (unrounded) data-to-pixel scale.

## 6. Is the Y scale genuinely linear?
Yes — verified two ways: (a) tick `cy` values are exactly evenly spaced
(44.1667 apart), and (b) the implied price-per-tick-interval is consistent
(~28.4–29.0K, differences explained by label rounding, not by a non-linear
mapping). No log-scale or other transform.

## 7. Can we get the exact chart domain (not just rounded axis labels)?
**No, not exactly.** The `cy` pixel values of the *actual data points* are
full-precision, but the only calibration reference available is the axis
**tick labels**, which are abbreviated/rounded (`3.6K`, `147K`, etc). There
is no separate min/max domain label in the markup — only gridlines at
`y=0` and `y=265` (chart top/bottom) with no accompanying text, so the
domain edges themselves aren't independently confirmable.
**Consequence: reconstructed prices carry an inherent ~0.2–1% calibration
uncertainty from tick-label rounding, on top of sub-pixel rendering noise.**
This is why every reconstructed value must be tagged `confidence:
reconstructed`, never `exact`.

## 8. Is the raw data present anywhere else (JSON/hydration/etc.)?
**Not in this capture.** The HTML supplied was the chart *widget's* rendered
markup (SVG + surrounding React output) plus a "Recent Sales" table — it did
not include `<head>`, `<script>` tags, or a `__NEXT_DATA__` block. So this
capture alone can't confirm or rule out an embedded JSON payload elsewhere
on the full page. **Open item**: if you can capture full `view-source:`
HTML of the page (not just the chart widget), that would let us check for
`__NEXT_DATA__` / other embedded JSON properly — worth doing once, since if
raw values are embedded there, we should prefer that over pixel geometry
entirely, per your instructions.

## 9. Hidden FUT.GG API endpoint?
Not investigated yet — this sandbox's network egress to `www.fut.gg` is
blocked (confirmed via direct `curl` and `WebFetch`, both returned
`EGRESS_BLOCKED`/403), so I can't fetch page source or JS bundles myself.
No endpoint is hardcoded anywhere and none will be invented. If you have
access to the site's bundled JS or can share full page source (including
`<script>` tags), I can search it for API/GraphQL/server-action references.

## Anomaly to flag
The raw HTML you pasted contained a **duplicated Y-axis tick block**: six
`<g class="...tick">` entries all showing `"3.6K"` at the same `cy=132.5`,
immediately before the real 6-tick block described above. This looks like
a stale/duplicate paint state captured mid-render (e.g. a pre-hydration or
transition frame), not a real second axis. The extractor **must not** blindly
average all tick text nodes — it should dedupe by `(cy, text)` and/or
require monotonic price vs. `cy` before using ticks for calibration, and
should log a warning if a duplicate/degenerate tick set is detected.

## Sanity checks (self-consistency)
- Reconstructed **peak**: index 126 → **2026-01-21**, price ≈ **146,871**
  (label at that cy is `147K`) — plausible for a January promo spike.
- Reconstructed **last 7 days** (incl. today) all land at cy=242.917 →
  price ≈ **3,629**, vs. the widget's displayed **current price of 3,600**
  and the **Recent Sales** table (last 12h of sales: 3,600–3,700). Match
  within 0.8%, using data that was *not* part of the Y-axis calibration —
  this is a real out-of-sample cross-check, not circular.
- Reconstructed **launch day** (2025-09-17): price ≈ **67,459**.

## Conclusion for Phase 2/3
SVG-coordinate reconstruction is necessary here (no embedded JSON found in
this capture) and looks accurate to within roughly **±0.2–1%** for this
card, based on tick-residual and out-of-sample checks. All extracted values
must be labeled `confidence=reconstructed`, never `exact`, until/unless we
find the raw JSON source.
