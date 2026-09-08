import { useEffect, useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useSalesCandles } from "../../hooks/useSalesCandles";
import { useFutggPlayerSales } from "../../hooks/useFutggMarket";
import CoinValue from "../../components/CoinValue";
import { formatAge } from "../../components/MarketFreshness";
import { compactCoins, evenTicks, humaniseSpan, salesStats, tickFormatterForSpan } from "../../lib/chartFormat";

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
const MAX_RANGE_DAYS = 14;

function median(values) {
  const sorted = values.slice().sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

// FUT.GG's approximate timestamps legitimately collide: a page capture
// can contain many completed sales whose source age resolves to the same
// minute. Joining every raw row gives the chart vertical needles. The
// range config already defines the intended chart resolution (1h/4h/12h),
// so draw one median point per bucket. Raw sales remain untouched for the
// statistical readout below the plot.
export function bucketApproximateSales(points, bucketHours) {
  if (!Array.isArray(points) || points.length <= 1) return points || [];
  const bucketMs = Math.max(1, Number(bucketHours) || 1) * 3600 * 1000;
  const groups = new Map();
  points.forEach((point) => {
    const key = Math.floor(point.time / bucketMs) * bucketMs;
    const group = groups.get(key) || [];
    group.push(point);
    groups.set(key, group);
  });
  return [...groups.values()].map((group) => {
    const times = group.map((point) => point.time).sort((a, b) => a - b);
    return {
      time: times[Math.floor(times.length / 2)],
      price: median(group.map((point) => point.price)),
      approximate: true,
    };
  }).sort((a, b) => a.time - b.time);
}

export default function ModalPriceChart({ cardId, entryPrice, targetPrice, fairValue, source }) {
  const [range, setRange] = useState("7d");
  const config = RANGES[range];
  const isFutgg = source === "futgg";
  const legacyQuery = useSalesCandles(cardId, config, { enabled: !isFutgg });
  const futggQuery = useFutggPlayerSales(isFutgg ? cardId : undefined, config.days);
  // The widest window, fetched once, purely to answer "does this card
  // have enough history for the 7d and 14d buttons to mean anything?".
  // Cheap: react-query shares the cache entry with the 14d selection, so
  // choosing 14d costs nothing extra. Without it a card holding three
  // days of sales still offered a 7d and a 14d button that silently drew
  // the same three days - or nothing at all - which reads as a broken
  // toggle rather than as a limit of the data.
  const coverageQuery = useFutggPlayerSales(isFutgg ? cardId : undefined, MAX_RANGE_DAYS);
  const isLoading = isFutgg ? futggQuery.isLoading : legacyQuery.isLoading;
  const isError = isFutgg ? futggQuery.isError : legacyQuery.isError;
  // The FUT.GG response carries coverage metadata alongside the rows -
  // see get_player_sales. Used only to explain an empty window.
  const data = isFutgg ? futggQuery.data : null;

  const rawPoints = useMemo(() => {
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
    })).filter((point) => Number.isFinite(point.price) && Number.isFinite(point.time));
  }, [isFutgg, futggQuery.data, legacyQuery.data]);

  // Plot FUT.GG data at the resolution the selected range already
  // declares. Legacy data is pre-bucketed candles, so it stays as-is.
  const points = useMemo(
    () => isFutgg ? bucketApproximateSales(rawPoints, config.bucketHours) : rawPoints,
    [isFutgg, rawPoints, config.bucketHours],
  );

  // Tick format follows the span the data ACTUALLY covers, not the range
  // that was asked for. Every label read "Thu 6" when a whole window sat
  // inside one day, so a dozen identical labels overprinted each other
  // into an unreadable smear.
  const spanMs = rawPoints.length > 1 ? rawPoints[rawPoints.length - 1].time - rawPoints[0].time : 0;
  const formatTick = tickFormatterForSpan(spanMs);
  const axisTicks = useMemo(() => evenTicks(points), [points]);

  // Robust scaling and a typical-range indicator.
  //
  // The chart previously scaled to the raw data min/max, so ONE outlier
  // print - a panic sale far below the market, or a single listing
  // bought at several times the going rate - flattened every real sale
  // into a band a few pixels tall against the bottom of the frame. The
  // domain is now the 5th-95th percentile (never tighter than the
  // interquartile range, never wider than the real data), with the
  // median drawn as a reference line and the interquartile range as a
  // shaded band. Outlying points are still plotted, clipped to the edge,
  // and counted in the note below the chart - disclosed, not hidden.
  // Stats stay based on every real sale, not the display-only buckets.
  const stats = useMemo(() => salesStats(rawPoints.map((p) => p.price)), [rawPoints]);

  // Which range buttons can actually be answered for this card. Derived
  // from the widest window's own data, not guessed.
  const availableSpanMs = useMemo(() => {
    const rows = coverageQuery.data?.items || [];
    if (rows.length < 2) return 0;
    const times = rows
      .map((s) => new Date(s.approximate_sold_at).getTime())
      .filter((t) => Number.isFinite(t));
    if (times.length < 2) return 0;
    return Math.max(...times) - Math.min(...times);
  }, [coverageQuery.data]);
  const coverageKnown = isFutgg && !coverageQuery.isLoading && !coverageQuery.isError;
  // A range is supported when we hold sales older than its start. The
  // 60% allowance keeps a range that is nearly covered usable rather
  // than disabling it on a few missing hours.
  const rangeSupported = (key) => {
    if (!coverageKnown) return true;
    // The narrowest range is always offered: it is the best view of
    // whatever we do hold, and disabling every button would leave no way
    // to see the chart at all.
    if (key === "24h") return true;
    return availableSpanMs >= RANGES[key].days * 24 * 3600 * 1000 * 0.6;
  };

  // If the selected range turns out to be unsupported, fall back to the
  // widest one that is, rather than leaving the reader on an empty chart
  // they cannot get out of.
  useEffect(() => {
    if (!coverageKnown || rangeSupported(range)) return;
    const fallback = Object.keys(RANGES).filter(rangeSupported).pop();
    if (fallback && fallback !== range) setRange(fallback);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coverageKnown, availableSpanMs, range]);

  // "7d selected but the card only has six hours of history" is a real,
  // common state that otherwise reads as a broken toggle.
  const coverageNote = useMemo(() => {
    if (!isFutgg || rawPoints.length < 2) return null;
    const requestedMs = config.days * 24 * 3600 * 1000;
    if (spanMs >= requestedMs * 0.5) return null;
    // "exist for this card" was a claim about the market. What we
    // actually know is how much WE hold: sales are captured in bursts
    // whenever the sales worker visits a card, and between visits -
    // 24 hours to 28 days depending on rating band - nothing is
    // recorded at all. So a 7d chart routinely contains one dense
    // cluster from a single visit, not a week of trading.
    return `We only hold ${humaniseSpan(spanMs)} of completed sales for this card - they are collected in bursts, not continuously.`;
  }, [isFutgg, rawPoints.length, spanMs, config.days]);

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
        {Object.keys(RANGES).map(key => {
          const supported = rangeSupported(key);
          return <button
            key={key}
            className={`${range === key ? "active" : ""}${supported ? "" : " unsupported"}`}
            aria-pressed={range === key}
            disabled={!supported}
            // A disabled control must say why, or it is just broken.
            title={supported ? undefined : `We only hold ${humaniseSpan(availableSpanMs)} of completed sales for this card`}
            onClick={() => setRange(key)}
          >{key}</button>;
        })}
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
            <YAxis domain={stats ? stats.domain : ["auto", "auto"]} allowDataOverflow={Boolean(stats)} width={55} tickFormatter={compactCoins} tick={{ fill: "#909090", fontSize: 10 }} axisLine={false} tickLine={false}/>
            {/* The middle half of sales - what a typical sale actually
                went for - drawn behind the series so an outlier spike
                cannot be mistaken for the going rate. */}
            {stats && stats.q3 > stats.q1
              ? <ReferenceArea y1={stats.q1} y2={stats.q3} fill="#ffffff" fillOpacity={0.045} stroke="none" ifOverflow="hidden"/>
              : null}
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
            {stats
              ? <ReferenceLine y={stats.median} stroke="#c9c9c9" strokeDasharray="2 3" label={{ value: "MEDIAN", fill: "#c9c9c9", fontSize: 10, position: "insideBottomLeft" }} ifOverflow="extendDomain"/>
              : null}
            <Area type="monotone" dataKey="price" stroke="#3bffa4" strokeWidth={3} fill={`url(#modalPriceFill-${cardId})`} activeDot={{ r: 5 }} dot={false}/>
          </AreaChart>
        </ResponsiveContainer>}
    </div>
    {stats ? <p className="dash-chart-typical">
      Typical sale <b>{Math.round(stats.median).toLocaleString("en-GB")}</b> · middle half between{" "}
      {Math.round(stats.q1).toLocaleString("en-GB")} and {Math.round(stats.q3).toLocaleString("en-GB")}
      {stats.outliers ? ` · ${stats.outliers} outlying ${stats.outliers === 1 ? "sale is" : "sales are"} off the top or bottom of this scale` : ""}
    </p> : null}
    {coverageNote ? <p className="dash-chart-coverage">{coverageNote}</p> : null}
  </div>;
}

function PriceTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return <div className="dash-chart-tooltip"><small>SALE PRICE{point.approximate?" (approx. time)":""}</small><strong><CoinValue value={point.price}/></strong><span>{new Date(point.time).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span></div>;
}
