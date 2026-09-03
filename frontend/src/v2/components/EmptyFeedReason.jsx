// src/v2/components/EmptyFeedReason.jsx
//
// Why the feed is empty - which is a different question from "the feed is
// empty", and the one an operator or a paying member actually has.
//
// Every market surface used to answer it the same way: "No current
// opportunities match those filters." That sentence is wrong twice over
// when no filters are set, and actively misleading when the cause is the
// price data being hours behind - it blames the reader for a pipeline
// outage. The three cases are genuinely different and the product knows
// which one it is in, so it should say:
//
//   filters set        -> your filters excluded everything, here's the reset
//   data is behind     -> our market data is stale, this is us, not you
//   neither            -> the engine looked and found nothing it believes in
//
// The third is a real and healthy answer. This engine is allowed to
// return nothing: an empty feed on a quiet market is the product working,
// and dressing it up as a fault would be as dishonest as the reverse.
import { AlertTriangle, RotateCcw, TrendingUp } from "lucide-react";
import EmptyState from "./EmptyState";
import { useFutggMarketFreshness } from "../hooks/useFutggMarket";

// DORMANT is intentionally allowed to run far behind its own ~67-hour
// (4000-minute) sales-refresh interval - see priority_config.py's
// tier_sales_interval_minutes and the "CRITICAL now matches..." comment
// there: DORMANT is the low-liquidity, no-recent-sales, no-interest tail
// this catalogue deliberately stops chasing so real throughput can go to
// CRITICAL/HIGH instead. The candidate scan behind /opportunities also
// orders by sales_count DESC (see futgg_market.py's CANDIDATE_SCAN_ORDER_BY),
// so a DORMANT-tier card was never going to surface as a trade candidate
// regardless of how fresh its sales data is.
//
// Summing DORMANT's own, by-design backlog into "is our sales data
// behind" made the gate trip on a population that was never relevant to
// whether the engine has anything to recommend - confirmed live: a
// 14,946-card catalogue with a healthy CRITICAL/HIGH/NORMAL/LOW picture
// crossed the 50% threshold by only 245 stale rows out of 7,718 summed
// across all tiers, and DORMANT alone accounted for 1,566 of them
// (production `stale_sales_by_priority_tier`: CRITICAL 1096, HIGH 4264,
// NORMAL 792, LOW 0, DORMANT 1566) - a false "our sales data is behind"
// on a feed that had real, sufficiently-fresh candidates to show.
function staleSalesForCandidateTiers(byTier) {
  if (!byTier || typeof byTier !== "object") return null;
  return Object.entries(byTier)
    .filter(([tier]) => tier !== "DORMANT")
    .reduce((total, [, n]) => total + (Number(n) || 0), 0);
}

// The age of the WORST silent collector, not the newest - and "silent"
// per the BACKEND's own `stale` flag on each heartbeat, not a single
// flat threshold recomputed here.
//
// Taking the newest heartbeat hid the outage this was written during:
// three collectors, one of them dead for three weeks, and the healthy
// ones reporting minutes ago made the whole pipeline look fine. A
// pipeline is as fresh as its stalest stage - but "stalest" has to be
// judged against each collector's own expected cadence, not one number
// for all of them. futgg_player_sync is a one-shot DAILY discovery job;
// a same-day gap since it last ran is normal, not an outage. Comparing
// its age against the same flat threshold used for the two continuous
// collectors (which cycle every few minutes) reported it silent for
// roughly 18 of every 24 hours - confirmed live in production - which is
// exactly the false "our sales data is behind" this component exists to
// avoid saying. The backend's own `stale` field (see
// app/services/readiness.py's COLLECTOR_SILENT_HOURS_BY_WORKER) is
// already computed per-worker; trusting it here instead of re-deriving
// a second, flat-threshold answer is what actually fixes that, and
// keeps this file from silently drifting out of sync with the backend's
// own judgement call again.
function silentCollectorHours(heartbeats) {
  const staleAges = (heartbeats || [])
    .filter((h) => h.stale && Number.isFinite(h.age_hours))
    .map((h) => h.age_hours);
  if (!staleAges.length) return null;
  return Math.max(...staleAges);
}

/**
 * @param noun         what this surface calls its rows ("opportunities", "trades")
 * @param filtersActive whether the reader has narrowed the search themselves
 * @param onClearFilters resets them
 */
export default function EmptyFeedReason({ noun = "opportunities", filtersActive, onClearFilters }) {
  const freshness = useFutggMarketFreshness();

  // The reader's own filters come first: if they set them, that IS the
  // explanation, and no amount of pipeline detail is more relevant.
  if (filtersActive) {
    return (
      <EmptyState
        icon={<TrendingUp size={25} />}
        text={`No current ${noun} match those filters.`}
        action={onClearFilters ? <button onClick={onClearFilters}><RotateCcw size={14} /> Clear filters</button> : null}
      />
    );
  }

  const data = freshness.data;
  const priced = Number(data?.cards_priced);
  const stalePrices = Number(data?.cards_stale);
  const staleSales = staleSalesForCandidateTiers(data?.stale_sales_by_priority_tier);
  const silentHours = silentCollectorHours(data?.heartbeats);

  // "We have no priced cards at all" and "most of what we have is stale"
  // are both us, not the market. So is a collector that has stopped.
  const noData = Number.isFinite(priced) && priced === 0;
  const hasPriced = Number.isFinite(priced) && priced > 0;
  // Sales and prices are collected by SEPARATE workers and fail
  // independently - the outage this component was written during had
  // 100% price coverage while the sales collector was rate-limited down
  // to near-zero throughput. Saying "prices are behind" when sales are
  // the problem sends whoever reads it to the wrong worker.
  const salesStale = hasPriced && staleSales != null && staleSales > priced * 0.5;
  const pricesStale = hasPriced && Number.isFinite(stalePrices) && stalePrices / priced > 0.5;

  if (noData || salesStale || pricesStale || silentHours != null) {
    const what = salesStale ? "sales data" : pricesStale ? "price data" : "market data";
    return (
      <EmptyState
        icon={<AlertTriangle size={25} />}
        error
        text={
          noData
            ? "We don't have live prices right now, so there's nothing the engine can score. This is on our side — it should come back on its own."
            : `Our ${what} is behind${
                silentHours != null ? ` (a collector hasn't reported in for about ${Math.round(silentHours)}h)` : ""
              }, so the engine has nothing it can stand behind. This is on our side, not your filters.`
        }
        action={<button onClick={() => freshness.refetch()}><RotateCcw size={14} /> Check again</button>}
      />
    );
  }

  // Prices are current and the engine still found nothing. That is a real
  // answer, and the honest one.
  return (
    <EmptyState
      icon={<TrendingUp size={25} />}
      text={`No ${noun} clear the bar right now. Prices are current — the engine just doesn't see a trade worth making yet.`}
    />
  );
}
