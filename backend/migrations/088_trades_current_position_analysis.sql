-- Migration 088: persist current held-position exit analysis.
-- target: core
--
-- current_recommendation_status/reasoning remain the compact compatibility
-- fields used by existing Club UI. The JSONB analysis stores the economics and
-- exact trigger behind that verdict without exploding the trades table into a
-- dozen tightly-coupled columns.

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS current_position_analysis JSONB;

COMMENT ON COLUMN trades.current_position_analysis IS
    'Latest FUT.GG held-position analysis: break-even, current BIN, after-tax P/L, original/live targets, market state and exact HOLD/SELL trigger.';
