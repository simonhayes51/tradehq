-- Migration 089: persist the entry price behind expected profit.
-- target: core
--
-- CardIntelligence has carried expected_profit_basis since the engine started
-- distinguishing the live executable entry from the advised maximum buy.
-- The mutable current-state table never stored that field, so persisted list
-- surfaces lost it while retaining expected_profit_after_tax. The frontend then
-- paired profit calculated at the live BIN with the higher "buy below" ceiling.
-- Example seen in production: buy below 40,000 + sell 45,500 + profit 17,725,
-- where 17,725 was actually calculated from a 25,500 live BIN.
--
-- Keep the basis beside the profit so persisted intelligence is self-contained
-- and every surface can show internally consistent trade economics.

ALTER TABLE futgg_current_intelligence
    ADD COLUMN IF NOT EXISTS expected_profit_basis NUMERIC;
