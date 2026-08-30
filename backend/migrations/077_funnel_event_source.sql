-- Migration 077: mark where a funnel event came from
-- target: core
-- requires-table: funnel_events
--
-- checkout_completed was emitted by the BROWSER, on the page Stripe
-- redirects back to. That event means "a customer returned from
-- checkout" - which is not the same fact as "Stripe confirmed a paid
-- subscription", and only the second one is revenue.
--
-- The two differ in both directions. A customer who completes payment and
-- closes the tab before the redirect never fires the browser event. A
-- customer who reaches the success URL while the webhook later fails, or
-- whose card is refunded, fires it and is not a subscriber. Anyone who
-- knows the URL can fire it at will.
--
-- So the source is recorded, and the reporting counts the server-side
-- event for revenue and the browser event only as intent.
--
-- Additive and reversible:
--   ALTER TABLE funnel_events DROP COLUMN source;
--   DROP INDEX IF EXISTS funnel_events_event_source_time_idx;
-- Existing rows default to 'client', which is what they were.

ALTER TABLE funnel_events
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'client';

-- 'client'  posted by the browser through POST /api/v2/events/track
-- 'server'  written by trusted server-side code (a Stripe webhook)
ALTER TABLE funnel_events
    DROP CONSTRAINT IF EXISTS funnel_events_source_check;
ALTER TABLE funnel_events
    ADD CONSTRAINT funnel_events_source_check CHECK (source IN ('client', 'server'));

-- Justified by the dashboard's own queries, which always filter by event
-- and period and, for the revenue metrics, additionally by source.
CREATE INDEX IF NOT EXISTS funnel_events_event_source_time_idx
    ON funnel_events (event, source, occurred_at DESC);
