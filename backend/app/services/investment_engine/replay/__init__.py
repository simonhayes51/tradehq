# app/services/investment_engine/replay/
"""
FC26 launch-replay validation harness for the production Investment
Engine (app/services/investment_engine/). This is NOT a second, research
approximation of the engine - it drives the exact same pure modules
(cohorts.py, drawdown.py, momentum.py, state_machine.py, ranking.py,
strategies/*, ledger.py's evaluate_active_signal_exit) chronologically
over FC26's reconstructed historical daily prices, one simulated day at a
time, to check whether the production implementation behaves sensibly
when it only ever sees what would genuinely have been known at that
historical moment.

Module map:
  data_provider.py - ReplayDataProvider: the ONE thing standing between
      the orchestrator and the full historical dataset. Every accessor
      takes an `as_of` date and raises if asked for anything at or after
      it - this is the structural no-lookahead guard the product spec
      calls "leakage assertions", not just a convention.
  fixtures.py       - loads real fc26_historical_daily_prices/futgg_players
      rows (production path, requires a DATABASE_URL) OR builds a
      clearly-labelled deterministic synthetic dataset shaped like the
      real FC26 launch (used in this repo/session because no live
      database connection was available to run the real one).
  ledger_store.py   - ReplayLedgerStore: an in-memory stand-in for the
      investment_signals table's lifecycle (open/active/closed),
      isolated per replay run - never touches production Postgres.
  engine.py         - run_full_replay(): the day-by-day orchestrator.
  report.py         - JSON/CSV/Markdown artifact writers.

See backend/INVESTMENT_ENGINE_REPLAY.md for the full validation report,
and backend/scripts/run_investment_engine_replay.py for the CLI entry
point (mirrors research/fc26_launch_market's existing one-off-script
pattern).
"""
