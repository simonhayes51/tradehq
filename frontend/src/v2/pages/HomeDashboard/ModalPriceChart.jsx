import { useEffect, useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useSalesCandles } from "../../hooks/useSalesCandles";
import { useFutggPlayerSales } from "../../hooks/useFutggMarket";
import CoinValue from "../../components/CoinValue";
import { formatAge } from "../../components/MarketFreshness";
import { compactCoins, evenTicks, humaniseSpan, salesStats, tickFormatterForSpan } from "../../lib/chartFormat";

const RANGES = {
  "24h": { days: 1, bucketHours: 1 },
  "7d": { days: 7, bucketHours: 4 },
  "14d": { days: 14, bucketHours: 12 },
};

const MAX_RANGE_DAYS = 14;

function median(values) {
  const sorted = values.slice().sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2) return sorted[middle];
  return (sorted[middle - 1] + sorted[middle]) / 2;
}

// FUT.GG sale times are approximate and many rows legitimately share the
// same captured/derived timestamp. Connecting every raw sale therefore
// produces vertical needles that make the chart look broken on a phone.
// The range config already defines the intended time resolution, so plot
// one median sale per bucket while keeping raw rows for the actual sales
// statistics shown below the chart.
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
  const coverageQuery = useFutggPlayerSales(isFutgg ? cardId : undefined, MAX_RANGE_DAYS);
  const isLoading = isFutgg ? futggQuery.isLoading : legacyQuery.isLoading;
  const isError = isFutgg ? futggQuery.isError : legacyQuery.isError;
  const data = isFutgg ? futggQuery.data : null;

  const rawPoints = useMemo(() => {
    if (isFutgg) {
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

  const points = useMemo(
    () => isFutgg ? bucketApproximateSales(rawPoints, config.bucketHours) : rawPoints,
    [isFutgg, rawPoints, config.bucketHours],
  );

  const spanMs = rawPoints.length > 1 ? rawPoints[rawPoints.length - 1].time - rawPoints[0].time : 0;
  const formatTick = tickFormatterForSpan(spanMs);
  const axisTicks = useMemo(() => evenTicks(points), [points]);
  const stats = useMemo(() => salesStats(rawPoints.map((p) => p.price)), [rawPoints]);

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
  const rangeSupported = (key) => {
    if (!coverageKnown) return true;
    if (key === "24h") return true;
    return availableSpanMs >= RANGES[key].days * 24 * 3600 * 1000 * 0.6;
  };

  useEffect(() => {
    if (!coverageKnown || rangeSupported(range)) return;
    const fallback = Object.keys(RANGES).filter(rangeSupported).pop();
    if (fallback && fallback !== range) setRange(fallback);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coverageKnown, availableSpanMs, range]);

  const coverageNote = useMemo(() => {
    if (!isFutgg || rawPoints.length < 2) return null;
    const requestedMs = config.days * 24 * 3600 * 1000;
    if (spanMs >= requestedMs * 0.5) return null;
    return `We only hold ${humaniseSpan(spanMs)} of completed sales for this card - they are collected in bursts, not continuously.`;
  }, [isFutgg, rawPoints.length, spanMs, config.days]);

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
            <XAxis
              dataKey="time" type="number" domain={["dataMin", "dataMax"]} scale="time"
              ticks={axisTicks}
              tickFormatter={formatTick} tick={{ fill: "#909090", fontSize: 10 }}
              axisLine={false} tickLine={false}
              tickMargin={8}
            />
            <YAxis domain={stats ? stats.domain : ["auto", "auto"]} allowDataOverflow={Boolean(stats)} width={55} tickFormatter={compactCoins} tick={{ fill: "#909090", fontSize: 10 }} axisLine={false} tickLine={false}/>
            {stats && stats.q3 > stats.q1
              ? <ReferenceArea y1={stats.q1} y2={stats.q3} fill="#ffffff" fillOpacity={0.045} stroke="none" ifOverflow="hidden"/>
              : null}
            <Tooltip
              content={<PriceTooltip/>}
              cursor={{ stroke: "#e0e0e0", strokeWidth: 1 }}
              allowEscapeViewBox={{ x: false, y: true }}
              wrapperStyle={{ zIndex: 4, pointerEvents: "none" }}
            />
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
