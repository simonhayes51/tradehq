# app/services/investment_engine/
"""
The Investment Engine.

Separate concept from the existing Trade/Opportunity Engine
(app/services/futgg_scanner.py, futgg_intelligence.py, recommendation_
engine_v2.py, trade_finder.py) - that engine answers "is there money in
THIS listing right now" (snipes, mispriced BINs, short flips, single-card
anomalies). This engine answers "is this market SEGMENT likely to
appreciate over the next 1-3 days, with enough supply to build a real
position" (market cycles, broad sell-offs, rebounds, accumulation
windows). Neither engine's logic, tables, or UI mix with the other's -
see backend/INVESTMENT_ENGINE.md for the full architecture writeup.

Module map:
    config.py          - InvestmentEngineConfig: every threshold/flag,
                          env-var overridable, mirrors priority_config.py.
    cohorts.py          - position-relative launch-price-percentile
                          cohort classification. Pure.
    drawdown.py         - dynamic/relative drawdown severity model. Pure.
    momentum.py          - decline-deceleration tracking. Pure.
    state_machine.py      - the regime state machine (NORMAL -> ... ->
                            CLOSED). Pure.
    live_confirmation.py   - FC27-only BIN/sales confirmation features,
                            computed strictly from what futgg_bin_history/
                            futgg_sales_history actually contain.
    ranking.py               - individual-card ranking (not hard
                            filtering) once a cohort regime opens. Pure.
    strategies/               - one module per strategy
                            (PREMIUM_POST_CRASH_REBOUND,
                            LAUNCH_STARTER_ACCUMULATION); new strategies
                            are new modules, not core-engine changes.
    ledger.py                  - the Investment Ledger: opens/grades/
                            closes investment_signals rows, no hindsight.
    runner.py                    - orchestrates one full engine pass
                            (DB I/O; wraps the pure modules above).
    backtest.py                    - runs the same pure modules against
                            FC26's fc26_historical_daily_prices, without
                            live_confirmation (which FC26 has no data
                            for), to validate the research findings still
                            reproduce.

Everything in cohorts.py/drawdown.py/momentum.py/state_machine.py/
ranking.py is pure (no I/O), by design: the same functions run inside
both the live FC27 runner and the FC26 backtest, so "does this logic
still behave the way the research said it should" is directly testable
without a database.
"""
