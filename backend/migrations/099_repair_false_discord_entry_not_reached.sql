-- Repair Discord result rows created by the short-entry-window bug.
--
-- New opportunity posts persist the live executable price observed by the
-- final pre-send recheck. If that price was already <= the published MAX BUY,
-- entry was available at publication even when futgg_bin_history did not
-- create another row during the following 15 minutes. The old result worker
-- ignored that proof and could write a false entry_not_reached update.
--
-- Remove only those impossible result rows so the normal worker can resume
-- tracking the original opportunity for a genuine target hit. This does not
-- touch the opportunity itself or any target-hit result.
DELETE FROM discord_server_posts u
USING discord_server_posts p
WHERE u.server_id = p.server_id
  AND u.kind = 'opportunity_update'
  AND p.kind IN ('opportunity', 'daily_overview')
  AND u.dedup_key = p.dedup_key
  AND u.payload->>'outcome_status' = 'entry_not_reached'
  AND p.payload ? 'observed_buy_price'
  AND p.payload ? 'buy_price'
  AND (p.payload->>'observed_buy_price') ~ '^[0-9]+([.][0-9]+)?$'
  AND (p.payload->>'buy_price') ~ '^[0-9]+([.][0-9]+)?$'
  AND (p.payload->>'observed_buy_price')::numeric
      <= (p.payload->>'buy_price')::numeric;
