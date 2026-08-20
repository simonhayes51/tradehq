import { useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useSalesCandles } from "../../hooks/useSalesCandles";
import { useFutggPlayerSales } from "../../hooks/useFutggMarket";
import CoinValue from "../../components/CoinValue";
import { formatAge } from "../../components/MarketFreshness";

// No "30d": futgg_market_snapshot's sales window stops at 14 days and the
// sales endpoint caps there too, so a 30d button could only ever redraw
// the 14d picture under a label promising more than exists.
const RANGES = {
  "24h": { days: 1, bucketHours: 1 },
  "7d": { days: 7, bucketHours: 4 },
  "14d": { days: 14, bucketHours: 12 },
};

// FUT.GG's own recent-sales endpoint (futgg_sales_history, keyed on
// source_card_id) is a completely different table/id-space from the
// legacy sales-history/sales-candles endpoints this chart used
// exclusively before - those only ever know about legacy FUTBIN
// card_ids, so every FUT.GG card looked like "not enough completed
// sales" regardless of how much real FUT.GG sales data existed. Those
// sale times are APPROXIMATE (derived from a relative age string, not
// exact) - see the tooltip's "approx." label below.
//
// The range picker asks the SERVER for each window (see
// useFutggPlayerSales' `days`). It used to be a client-side filter over a
// single response that was always the newest 50 sales - a few hours on a
// liquid card - so 24h, 7d and 30d all drew the identical few hours and
// the toggle appeared to do nothing at all.
export default function ModalPriceChart({ cardId, entryPrice, targetPrice, fairValue, source }) {
  const [range, setRange] = useState("7d");
  const config = RANGES[range];
  const isFutgg = source === "futgg";
  const legacyQuery = useSalesCandles(cardId, config, { enabled: !isFutgg });
  const futggQuery = useFutggPlayerSales(isFutgg ? cardId : undefined, config.days);
  const isLoading = isFutgg ? futggQuery.isLoading : legacyQuery.isLoading;
  const isError = isFutgg ? futggQuery.isError : legacyQuery.isError;
  // The FUT.GG response carries coverage metadata alongside the rows -
  // see get_player_sales. Used only to explain an empty window.
  const data = isFutgg ? futggQuery.data : null;

  const points = useMemo(() => {
    if (isFutgg) {
      // No client-side cutoff any more - the server already returned this
      // range, and re-filtering here is what made the picker inert.
      return (futggQuery.data?.items || [])
        .map((s) => ({ time: new Date(s.approximate_sold_at).getTime(), price: Number(s.sold_price), approximate: true }))
        .filter((point) => Number.isFinite(point.price) && Number.isFinite(point.time))
        .sort((a, b) => a.time - b.time);
    }
    return (legacyQuery.data?.candles || []).map((c) => ({
      time: Number(c.time) * 1000,
      price: Number(c.close),
    })).filter((point) => Number.isFinite(point.price));
  }, [isFutgg, futggQuery.data, legacyQuery.data]);

  // Tick format follows the span the data ACTUALLY covers, not the range
  // that was asked for. Every label read "Thu 6" when a whole window sat
  // inside one day, so a dozen identical labels overprinted each other
  // into an unreadable smear.
  const spanMs = points.length > 1 ? points[points.length - 1].time - points[0].time : 0;
  const formatTick = spanMs > 0 && spanMs < 36 * 3600 * 1000 ? formatClock : formatDay;
  // Explicit tick positions rather than trusting recharts'
  // interval="preserveStartEnd" + minTickGap to thin a dense, irregularly-
  // spaced completed-sales series on its own: on a card with sales
  // bursty enough (many points within a short span, e.g. a cluster of 40
  // sales in ten minutes), that combination still emitted far more labels
  // than minTickGap should have allowed, overprinting into an unreadable
  // wall of digits - the same failure shape the "Thu 6" comment above
  // already fixed once for a different cause. A fixed, small number of
  // evenly-spaced ticks can't overcrowd regardless of how the underlying
  // points are distributed.
  const axisTicks = useMemo(() => {
    if (points.length < 2) return undefined;
    const first = points[0].time;
    const last = points[points.length - 1].time;
    if (first === last) return [first];
    const TICK_COUNT = 5;
    return Array.from({ length: TICK_COUNT }, (_, i) => Math.round(first + ((last - first) * i) / (TICK_COUNT - 1)));
  }, [points]);

  // "7d selected but the card only has six hours of history" is a real,
  // common state that otherwise reads as a broken toggle.
  const coverageNote = useMemo(() => {
    if (!isFutgg || points.length < 2) return null;
    const requestedMs = config.days * 24 * 3600 * 1000;
    if (spanMs >= requestedMs * 0.5) return null;
    // "exist for this card" was a claim about the market. What we
    // actually know is how much WE hold: sales are captured in bursts
    // whenever the sales worker visits a card, and between visits -
    // 24 hours to 28 days depending on rating band - nothing is
    // recorded at all. So a 7d chart routinely contains one dense
    // cluster from a single visit, not a week of trading.
    return `We only hold ${humaniseSpan(spanMs)} of completed sales for this card - they are collected in bursts, not continuously.`;
  }, [isFutgg, points.length, spanMs, config.days]);

  // An empty window has two very different causes and this used to state
  // only one of them. "No completed sales in the last 24h" reads as a
  // fact about the market - nobody bought this card - when for most of
  // the catalogue it is a fact about our own coverage: futgg_sales_sync
  // re-reads by rating band, and the lower bands are on intervals longer
  // than the windows offered here, so those cards would report "no sales"
  // essentially always no matter how actively they were being traded.
  const emptyReason = useMemo(() => {
    if (!isFutgg) return `No completed sales in the last ${range}.`;
    if (data?.sales_never_checked) return "We haven't collected sales for this card yet.";
    if (data?.sales_check_stale_for_window) {
      const when = data.sales_checked_at
        ? formatAge((Date.now() - new Date(data.sales_checked_at).getTime()) / 1000)
        : null;
      return when
        ? `We last checked this card's sales ${when} - longer ago than the ${range} window, so there is nothing to show for it yet.`
        : `We haven't checked this card's sales within the last ${range}.`;
    }
    return `No completed sales in the last ${range}.`;
  }, [isFutgg, range, data]);

  return <div className="dash-price-chart">
    <div className="dash-price-chart-head">
      <div><strong>Completed sales</strong>{isFutgg?<small className="dash-chart-approx"> (approximate times)</small>:null}</div>
      <div className="dash-chart-ranges" aria-label="Chart range">
        {Object.keys(RANGES).map(key => <button key={key} className={range === key ? "active" : ""} aria-pressed={range === key} onClick={() => setRange(key)}>{key}</button>)}
      </div>
    </div>
    <div className="dash-chart-frame">
      {isLoading ? <div className="dash-chart-loading"><i/><i/><i/><i/></div>
      : isError || points.length === 0 ? <div className="dash-chart-empty">{emptyReason}</div>
      : <ResponsiveContainer width="100%" height={190}>
          <AreaChart data={points} margin={{ top: 12, right: 8, left: 0, bottom: 0 }}>
            <defs><linearGradient id={`modalPriceFill-${cardId}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#3bffa4" stopOpacity={0.34}/><stop offset="100%" stopColor="#3bffa4" stopOpacity={0}/></linearGradient></defs>
            <CartesianGrid stroke="#2e2e2e" strokeDasharray="3 4" vertical={false}/>
            {/* minTickGap + preserveStartEnd are what stop recharts emitting
                a label per data point and overprinting them into a smear. */}
            <XAxis
              dataKey="time" type="number" domain={["dataMin", "dataMax"]} scale="time"
              ticks={axisTicks}
              tickFormatter={formatTick} tick={{ fill: "#909090", fontSize: 10 }}
              axisLine={false} tickLine={false}
              tickMargin={8}
            />
            <YAxis domain={["auto", "auto"]} width={55} tickFormatter={compactCoins} tick={{ fill: "#909090", fontSize: 10 }} axisLine={false} tickLine={false}/>
            <Tooltip
              content={<PriceTooltip/>}
              cursor={{ stroke: "#e0e0e0", strokeWidth: 1 }}
              allowEscapeViewBox={{ x: false, y: true }}
              wrapperStyle={{ zIndex: 4, pointerEvents: "none" }}
            />
            {/* Three reference lines sharing two corners overprinted each
                other ("SELL" landing on top of "BUY"). Spread across the
                available positions instead. */}
            {entryPrice > 0 && <ReferenceLine y={entryPrice} stroke="#ffd34d" strokeDasharray="5 4" label={{ value: "BUY", fill: "#ffd34d", fontSize: 10, position: "insideBottomRight" }}/>}
            {fairValue > 0 && <ReferenceLine y={fairValue} stroke="#65c7ff" strokeDasharray="3 4" label={{ value: "FAIR", fill: "#65c7ff", fontSize: 10, position: "insideTopLeft" }}/>}
            {targetPrice > 0 && <ReferenceLine y={targetPrice} stroke="#3bffa4" strokeDasharray="5 4" label={{ value: "SELL", fill: "#3bffa4", fontSize: 10, position: "insideTopRight" }}/>}
            <Area type="monotone" dataKey="price" stroke="#3bffa4" strokeWidth={3} fill={`url(#modalPriceFill-${cardId})`} activeDot={{ r: 5 }} dot={false}/>
          </AreaChart>
        </ResponsiveContainer>}
    </div>
    {coverageNote ? <p className="dash-chart-coverage">{coverageNote}</p> : null}
  </div>;
}

function PriceTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return <div className="dash-chart-tooltip"><small>SALE PRICE{point.approximate?" (approx. time)":""}</small><strong><CoinValue value={point.price}/></strong><span>{new Date(point.time).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span></div>;
}
function formatDay(value) { return new Date(value).toLocaleDateString("en-GB", { weekday: "short", day: "numeric" }); }
function formatClock(value) { return new Date(value).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); }
function compactCoins(value) { const n=Number(value); return Number.isFinite(n) ? (n>=1000?`${Math.round(n/1000)}k`:String(Math.round(n))) : ""; }
function humaniseSpan(ms) {
  const hours = ms / 3600000;
  if (hours < 1) return `${Math.max(1, Math.round(ms / 60000))} minutes`;
  if (hours < 36) return `${Math.round(hours)} hours`;
  return `${Math.round(hours / 24)} days`;
}
