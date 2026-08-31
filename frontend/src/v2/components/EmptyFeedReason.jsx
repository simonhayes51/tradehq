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
import { STALE_THRESHOLD_SECONDS } from "./MarketFreshness";

// Anything older than this and the scan is working from prices nobody
// should trade against - which is why the feed is empty, and which is
// worth saying out loud rather than leaving the reader to guess.
const STALE_HOURS = Math.round(STALE_THRESHOLD_SECONDS / 3600);

function sumValues(byTier) {
  if (!byTier || typeof byTier !== "object") return null;
  return Object.values(byTier).reduce((total, n) => total + (Number(n) || 0), 0);
}

// The age of the WORST collector, not the newest.
//
// Taking the newest hid the outage this was written during: three
// collectors, one of them dead for three weeks, and the healthy ones
// reporting minutes ago made the whole pipeline look fine. A pipeline is
// as fresh as its stalest stage.
function silentCollectorHours(heartbeats) {
  const ages = (heartbeats || [])
    .map((h) => (h.last_run_at ? (Date.now() - new Date(h.last_run_at).getTime()) / 3_600_000 : null))
    .filter((h) => Number.isFinite(h));
  if (!ages.length) return null;
  const worst = Math.max(...ages);
  return worst > STALE_HOURS ? worst : null;
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
  const staleSales = sumValues(data?.stale_sales_by_priority_tier);
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
