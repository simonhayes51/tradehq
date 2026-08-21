# FC26 PRODUCTION ENGINE REPLAY

- Data source: **SYNTHETIC FIXTURE (not real FC26 data - built because no live database connection was available in this session; see fixtures.py module docstring)**
- Period: 2025-09-17 .. 2025-10-17 (31 days)
- Cards in dataset: 320
- Engine config version: `investment-1.0`

## Premium/Meta timeline

| Date | FR offset | State | Drawdown | Severity | Signals opened |
|---|---|---|---|---|---|
| 2025-09-17 | FR-9 | NORMAL | 0.0% | normal | 0 |
| 2025-09-22 | FR-4 | CRASHING | 0.0% | severe | 0 |
| 2025-10-01 | FR+5 | WATCH | 50.1% | extreme | 0 |
| 2025-10-02 | FR+6 | BOTTOMING | 50.9% | extreme | 0 |
| 2025-10-03 | FR+7 | WATCH | 52.7% | extreme | 0 |
| 2025-10-05 | FR+9 | BOTTOMING | 51.0% | severe | 0 |
| 2025-10-06 | FR+10 | INVESTMENT_WINDOW | 52.2% | severe | 25 |
| 2025-10-09 | FR+13 | EXIT | 49.3% | elevated | 0 |
| 2025-10-10 | FR+14 | NORMAL | 50.0% | elevated | 0 |
| 2025-10-15 | FR+19 | CRASHING | 52.4% | severe | 0 |
| 2025-10-16 | FR+20 | WATCH | 54.1% | extreme | 0 |

**Falling-knife check**: 10 cohort-days suppressed while CRASHING/OVERHEATED (first 2025-09-22, last 2025-10-15). Average further decline after a suppressed day: 30.1%; worst case: 54.6%.

**Signal outcomes** (29 closed):
- win rate 69.0%, median ROI 1.26%, mean ROI 1.43%, p25 -0.62%, p10 -2.43%, worst -3.93%, best 6.81%
- median forward return by horizon (post-hoc only): 1d=-2.71%, 2d=-0.04%, 3d=2.09%, 5d=-0.95%, 7d=-4.04%

## Starter accumulation timeline

| Date | FR offset | State | Drawdown | Severity | Signals opened |
|---|---|---|---|---|---|
| 2025-09-17 | FR-9 | LAUNCH_WINDOW_OPEN | — | — | 25 |
| 2025-09-27 | FR+1 | LAUNCH_WINDOW_CLOSED | — | — | 0 |

**Falling-knife check**: cohort never entered CRASHING/OVERHEATED in this replay.

**Signal outcomes** (95 closed):
- win rate 81.1%, median ROI 7.69%, mean ROI 2.45%, p25 2.38%, p10 -22.79%, worst -34.02%, best 14.87%
- median forward return by horizon (post-hoc only): 1d=-3.04%, 2d=-1.34%, 3d=-0.52%, 5d=3.04%, 7d=7.16%

## Cohort bootstrap

- first day (2025-09-17): {'likely_fodder': 51, 'mid_tier_meta': 50, 'premium_meta': 27, 'desirable_starter': 61, 'ordinary': 63}
- midpoint (2025-10-02): {'likely_fodder': 64, 'mid_tier_meta': 60, 'premium_meta': 35, 'desirable_starter': 84, 'ordinary': 77}
- last day (2025-10-17): {'likely_fodder': 64, 'mid_tier_meta': 60, 'premium_meta': 35, 'desirable_starter': 84, 'ordinary': 77}
- total classification events: 320
