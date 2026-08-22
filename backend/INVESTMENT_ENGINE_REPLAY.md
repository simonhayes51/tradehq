# FC26 Investment Engine Replay — Validation Report

## What this is

A chronological, day-by-day replay of FC26's launch through the actual
production Investment Engine code (`app/services/investment_engine/`) —
not a second, research-only approximation of it. The replay harness
(`app/services/investment_engine/replay/`) calls the same
`cohorts.classify_cohorts`, `drawdown.compute_drawdown`/
`drawdown_percentile`/`historical_reference_percentile`/
`severity_score_and_class`, `momentum.compute_momentum`,
`state_machine.next_state`, `ranking.rank_candidates`, both strategy
modules' eligibility/target/hold functions, and a pure helper extracted
during this work, `ledger.evaluate_active_signal_exit()`, that the live
runner (`grade_open_signals`) and this replay both call identically.
Nothing about how a cohort, a state, a candidate, or an exit is decided
was reimplemented for this exercise.

**This report now reflects a REAL run against the production
`fc26_historical_daily_prices`/`futgg_players` dataset** — 2,608 real
Gold Common/Rare cards (9 excluded by a data-quality filter, see below),
the actual FC26 launch window (2025-09-17 → 2025-10-17). The first draft
of this report (superseded) used a synthetic stand-in because this
development session had no live database connection; the real run was
executed separately (Coolify backend terminal, which does have DB
access) using the exact CLI this harness ships. A synthetic-fixture
smoke-test summary is kept at the bottom for harness-validation
purposes only — do not read those numbers as FC26 fact.

## How this was run

```
python -m scripts.run_investment_engine_replay --source postgres
```

`--source postgres` defaults to the real FC26 Web App Launch date
through Full Release +21 (no `--start`/`--end` needed for the standard
validation window), and automatically excludes any card
`fc26_historical_extraction_runs` already flags as a failed or
low-confidence chart reconstruction (`calibration_max_pct_err > 20%`) —
9 cards were excluded this way. Output written to
`replay_output/{REPLAY_REPORT.md,daily_states.csv,signals.csv,
replay_result.json}` on whatever machine runs it (the Coolify backend
container in this case — those files are NOT synced back to this repo
automatically, which is why the actual data lives only in this
hand-written report, not in `backend/replay_output/`; that directory
still holds the earlier synthetic run's artifacts as a harness-validation
reference).

## Scale

- Period: 2025-09-17 (real Web App Launch) → 2025-10-17 (real Full
  Release + 21), 31 calendar days.
- 2,608 real cards (2,617 total Gold Common/Rare minus 9 excluded for
  chart-reconstruction quality).
- 1,987 cards actually reached a cohort classification within this
  31-day window (the rest either arrived too late in the window or
  never reached the `min_cohort_size` threshold in their position
  group in time — itself a bootstrap-timing data point, not an error).
- Final cohort sizes: `premium_meta`=196, `mid_tier_meta`=375,
  `desirable_starter`=475, `ordinary`=509, `likely_fodder`=432.

## Did the engine behave sensibly from Day 1? (real data)

### During hype — did it avoid stupid premium purchases?
Yes. `premium_meta` stayed `NORMAL` through Early Access
(2025-09-17 → ~2025-09-23) with zero candidates ever ranked.

### During the crash — did it avoid catching falling knives?
Yes. 8 cohort-days were suppressed while `CRASHING`/`OVERHEATED`
(first 2025-09-24, last 2025-10-12), during which zero signals were
opened. Post-hoc, the cohort fell an **average of 17.0% further**
after each suppressed day (worst case 37.6% further) — smaller in
magnitude than an earlier mis-scoped 343-day pull of the same data, but
this is the correctly-scoped, real number for the actual launch window.

### Near the bottom — a genuine, important finding: the first "bottom" was a fakeout
The real sequence:

| FR offset | State | Drawdown |
|---|---|---|
| FR-2 | CRASHING | 31.8% |
| FR+1 | NORMAL | 38.1% |
| FR+5 | CRASHING | 54.2% |
| FR+8 | CRASHING | 54.1% |
| FR+9 | WATCH | 57.3% |
| FR+10 | BOTTOMING | 56.3% |
| **FR+11** | **INVESTMENT_WINDOW** | 55.2% — 25 signals opened |
| FR+14 | EXIT (forced, max regime age) | 53.1% |
| FR+15 | CRASHING (again) | 56.2% |
| FR+17 | WATCH | 56.3% |
| FR+18 | BOTTOMING | 56.3% |
| **FR+19** | **INVESTMENT_WINDOW** (again) | 55.3% — 25 more signals opened |

The engine correctly detected deceleration around FR+9–11 and opened
positions — but the market then fell into a **second leg down**
(FR+15), and the positions from the first window were force-closed at
FR+14 (48-hour max regime age) essentially right at the edge of that
second decline. Result: premium/meta signals closed in this window,
27.4% win rate, median ROI **-6.72%**.

**This is not a bug — it's a genuine, useful risk finding.** The engine's
discipline worked exactly as designed: it never bought *during* the
crash, and it force-closed rather than holding through the second leg
down indefinitely. But "the decline decelerated for 2 periods" and "this
is the real, final bottom" are not the same claim, and this replay shows
a real historical case where they diverged. The engine then correctly
recognised the *second* bottoming cycle (FR+17–19) and reopened — showing
it can re-engage rather than treating "one crash, one bottom" as a fixed
rule — but the data window ends at FR+21, too soon after FR+19 to see how
that second attempt resolved.

**What this should NOT lead to**: tightening the deceleration
requirement or lengthening the regime-max-age specifically because of
this one window would be tuning the engine to one historical fakeout —
exactly the overfitting the brief explicitly warned against. The
correct response is to carry this forward as a known, real risk pattern
into FC27 shadow-mode monitoring, not to hand-adjust a threshold now.

### Starter market — did it recognise this was a different strategy?
Yes, structurally: `LAUNCH_STARTER_ACCUMULATION` never touched
`CRASHING`/`WATCH`/`BOTTOMING` in this run — its states
(`LAUNCH_WINDOW_OPEN`/`LAUNCH_WINDOW_CLOSED`) stayed disjoint from
`state_machine.ALL_STATES`, opening steadily through the pre-Full-Release
window and closing the window the day after Full Release, exactly per
`starter_launch_window_days=9.0`.

**But its real-data outcome underperformed the research expectation**:
43.7% win rate, median ROI **-5.0%**, wide dispersion (worst -85.1%,
best +311.1%). The data-quality filter changed almost nothing here (only
9 cards excluded platform-wide), so this is not a chart-reconstruction
artifact — it's a real result. The likely honest explanation: with no
FC27-only sales/liquidity data available, this strategy currently ranks
candidates using little more than recent relative price momentum, which
in a genuinely chaotic launch market can just as easily select a card
right after it spikes (about to mean-revert) as one about to climb. This
is a real, worth-investigating concern about the ranking signal available
to this strategy specifically — **not something fixed in this session**,
because doing so now, in response to one historical window, would be
exactly the kind of overfitting the brief said not to do.

## Cohort bootstrap

Cohort composition and freeze-on-first-classification behaviour matched
what the pure-function tests already verify (`test_investment_replay.py`,
`TestCohortBootstrap`): 1,987 of 2,608 real cards were classified within
this window, sizes grew as staggered/late-arriving cards reached their
position group's `min_cohort_size` threshold, and — as production
currently behaves — no card was ever reclassified once assigned. This
is production's existing behaviour being genuinely exercised, not a
hypothetical improved bootstrap policy.

## Bugs found and fixed

**One real reusability defect**, found and fixed while building this
harness: `ledger.grade_open_signals()`'s exit decision (target-hit
check, max-hold check, mark-to-market classification) was inlined and
hard-coupled to `datetime.now(timezone.utc)`, making it impossible for
replay to reuse without either forking the logic or faking the wall
clock. Fixed by extracting `ledger.evaluate_active_signal_exit()` as a
pure function both the live runner and the replay engine now call
identically. Covered by dedicated tests including a direct check against
`trading_math.net_roi()`.

**Two real data-hygiene gaps, found via the real run and fixed in the
harness (not the engine) this session**:
1. The replay CLI defaulted to walking every day the historical table
   had (343 days, well past launch) rather than the specific launch
   window being validated — fixed by defaulting `--source postgres` to
   the real Web App Launch → Full Release +21 window.
2. The replay was not excluding cards `fc26_historical_extraction_runs`
   already flags as low-confidence chart reconstructions — fixed by
   excluding them by default (9 cards this run).

**No defect was found in the engine's decision logic itself** on the
real data — no future leakage, no incorrect state transition, no
impossible signal lifecycle, no duplicate signals, no case of signals
generated while `CRASHING`, no starter-strategy leakage into the premium
crash trigger. The negative outcome numbers above are real, disclosed
results — not evidence of a logic defect, and specifically not something
"fixed" by adjusting thresholds to make this window look better.

## Known limitations (must not be minimised)

- **One prior cycle only.** This is FC26's launch, once. A real,
  disclosed negative result in this window (premium fakeout, starter
  underperformance) is informative but is not a statistically robust
  verdict on the strategies — that requires FC27 shadow-mode data,
  ideally across future launches too.
- **`require_investable=False` in replay.** FC26's reconstructed daily
  chart data has no sales-count, tradeability, or price-freshness
  fields at all. `ranking.rank_candidates()`'s own docstring calls
  investability "the one genuinely hard requirement" separating
  Investment-Engine-worthy cards from Trade-Engine snipes; replay
  deliberately passes `require_investable=False` (an existing,
  documented parameter, not a fork of the ranking logic) because every
  FC26 candidate would otherwise show `sales_count_recent=None` and be
  excluded entirely. This means **replay validates the cohort-state and
  candidate-ranking logic upstream of the investability gate, but cannot
  validate the investability gate itself** — every replay signal
  honestly reports `investability="limited"`/`liquidity="low"`, which
  shows up as a real confidence discount, not a fabricated FC27-style
  liquidity read.
- **No live-confirmation validation.** Every replay signal is tagged
  `UNAVAILABLE_HISTORICAL_REPLAY`; `live_confirmation.py`'s BIN/sales
  logic is entirely unvalidated by any FC26-based exercise and can only
  be checked against real FC27 live data.
- **The starter strategy's underperformance is flagged, not resolved.**
  See above — a real concern worth watching closely once FC27 shadow
  data exists, deliberately left unmodified here.
- **The premium "fakeout bottom" pattern is flagged, not resolved.**
  A genuine risk the engine handled with appropriate discipline
  (no holding through the second leg indefinitely) but did not avoid
  entirely (the first entry still lost money). Worth explicit attention
  during FC27 shadow-mode monitoring.
- **Full history vs. launch window.** `fc26_historical_daily_prices`
  keeps accumulating well past launch (343+ days observed); an
  unscoped run answers a different, much bigger question than "how did
  the engine handle the launch," and revealed along the way that
  cohort membership, frozen for a whole season, can drift compositionally
  over many months (median price shifting as new, differently-priced
  cards join a cohort) — a real methodological consideration for any
  future full-season analysis, not addressed in this session.

## Tests

101 tests pass (`python3 -m pytest tests/test_investment_*.py -q`), 24
of them specific to the replay harness (leakage via prefix-stability,
cohort bootstrap/freeze, unavailable-live-confirmation tagging,
deterministic replay, ledger isolation, tax/exit correctness,
falling-knife suppression, strategy distinctness). These are pure-logic
tests using synthetic fixtures — the real-data run above was a separate,
manual execution against the production database, not part of the
automated test suite (that dataset isn't available in CI).

## FC27 readiness verdict

# READY FOR FC27 SHADOW MODE

The production Investment Engine's structural safety properties held on
the real FC26 launch data: it never bought during hype or an active
crash, it recognised deceleration through genuine multi-period evidence
rather than a single good day, it force-closed rather than holding
indefinitely, and it correctly re-engaged for a second bottoming cycle
rather than treating "one crash, one bottom" as fixed. The two
strategies remained behaviourally distinct throughout.

**This explicitly is not a claim of profitability** — quite the
opposite: this real run's own numbers were mixed-to-negative (a losing
premium "fakeout" entry, starter underperformance), and that is
disclosed here plainly rather than smoothed over. That is exactly why
shadow mode — not controlled publication — is the right next step:
FC27's own live data, watched without publishing anything to users, is
what actually determines whether these strategies work going forward.
Nothing in this report should be read as license to tune the engine's
thresholds to this one historical window; the honest path is to carry
these findings (the fakeout-bottom risk, the starter strategy's weak
signal without live data) into shadow-mode monitoring and let real
forward data decide.

---

## Appendix: earlier synthetic-fixture smoke test (harness validation only)

Before real database access was available, the harness was validated
end-to-end against a deterministic synthetic fixture (320 cards, same
31-day window) purely to confirm the harness itself works — leakage
protection, bootstrap mechanics, deterministic reruns, ledger isolation,
tax math. Those numbers (69% premium win rate, 81% starter win rate,
etc.) are **not FC26 data** and are superseded entirely by the real run
above. Artifacts from that synthetic run remain at
`backend/replay_output/` as a harness-validation reference; they should
not be cited as FC26 findings.
