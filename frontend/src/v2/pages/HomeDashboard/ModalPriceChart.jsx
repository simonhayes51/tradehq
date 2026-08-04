import { useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useSalesCandles } from "../../hooks/useSalesCandles";
import { useFutggPlayerSales } from "../../hooks/useFutggMarket";
import CoinValue from "../../components/CoinValue";

const RANGES = {
  "24h": { days: 1, bucketHours: 1 },
  "7d": { days: 7, bucketHours: 4 },
  "30d": { days: 30, bucketHours: 12 },
};

// FUT.GG's own recent-sales endpoint (futgg_sales_history, keyed on
// source_card_id) is a completely different table/id-space from the
// legacy sales-history/sales-candles endpoints this chart used
// exclusively before - those only ever know about legacy FUTBIN
// card_ids, so every FUT.GG card looked like "not enough completed
// sales" regardless of how much real FUT.GG sales data existed. FUT.GG
// only ever returns its bounded recent window (<=50 rows/14 days, see
// migrations/038), not a real bucketed time series - the 24h/7d/30d
// picker becomes a client-side filter over that same window rather
// than a different server query per range. Those sale times are
// APPROXIMATE (derived from a relative age string, not exact) - see
// the tooltip's "approx." label below.
export default function ModalPriceChart({ cardId, entryPrice, targetPrice, fairValue, source }) {
  const [range, setRange] = useState("7d");
  const config = RANGES[range];
  const isFutgg = source === "futgg";
  const legacyQuery = useSalesCandles(cardId, config, { enabled: !isFutgg });
  const futggQuery = useFutggPlayerSales(isFutgg ? cardId : undefined);
  const isLoading = isFutgg ? futggQuery.isLoading : legacyQuery.isLoading;
  const isError = isFutgg ? futggQuery.isError : legacyQuery.isError;

  const points = useMemo(() => {
    if (isFutgg) {
      const cutoffMs = Date.now() - config.days * 24 * 60 * 60 * 1000;
      return (futggQuery.data?.items || [])
        .map((s) => ({ time: new Date(s.approximate_sold_at).getTime(), price: Number(s.sold_price), approximate: true }))
        .filter((point) => Number.isFinite(point.price) && Number.isFinite(point.time) && point.time >= cutoffMs)
        .sort((a, b) => a.time - b.time);
    }
    return (legacyQuery.data?.candles || []).map((c) => ({
      time: Number(c.time) * 1000,
      price: Number(c.close),
    })).filter((point) => Number.isFinite(point.price));
  }, [isFutgg, futggQuery.data, legacyQuery.data, config.days]);

  return <div className="dash-price-chart">
    <div className="dash-price-chart-head">
      <div><strong>Completed sales</strong>{isFutgg?<small className="dash-chart-approx"> (approximate times)</small>:null}</div>
      <div className="dash-chart-ranges" aria-label="Chart range">
        {Object.keys(RANGES).map(key => <button key={key} className={range === key ? "active" : ""} onClick={() => setRange(key)}>{key}</button>)}
      </div>
    </div>
    <div className="dash-chart-frame">
      {isLoading ? <div className="dash-chart-loading"><i/><i/><i/><i/></div>
      : isError || points.length === 0 ? <div className="dash-chart-empty">Not enough completed sales yet.</div>
      : <ResponsiveContainer width="100%" height={190}>
          <AreaChart data={points} margin={{ top: 12, right: 8, left: 0, bottom: 0 }}>
            <defs><linearGradient id={`modalPriceFill-${cardId}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#3bffa4" stopOpacity={0.34}/><stop offset="100%" stopColor="#3bffa4" stopOpacity={0}/></linearGradient></defs>
            <CartesianGrid stroke="#2e2e2e" strokeDasharray="3 4" vertical={false}/>
            <XAxis dataKey="time" type="number" domain={["dataMin", "dataMax"]} scale="time" tickFormatter={formatDay} tick={{ fill: "#909090", fontSize: 10 }} axisLine={false} tickLine={false}/>
            <YAxis domain={["auto", "auto"]} width={55} tickFormatter={compactCoins} tick={{ fill: "#909090", fontSize: 10 }} axisLine={false} tickLine={false}/>
            <Tooltip
              content={<PriceTooltip/>}
              cursor={{ stroke: "#e0e0e0", strokeWidth: 1 }}
              allowEscapeViewBox={{ x: false, y: true }}
              wrapperStyle={{ zIndex: 4, pointerEvents: "none" }}
            />
            {entryPrice > 0 && <ReferenceLine y={entryPrice} stroke="#ffd34d" strokeDasharray="5 4" label={{ value: "BUY", fill: "#ffd34d", fontSize: 10, position: "insideTopRight" }}/>} 
            {fairValue > 0 && <ReferenceLine y={fairValue} stroke="#65c7ff" strokeDasharray="3 4" label={{ value: "FAIR", fill: "#65c7ff", fontSize: 10, position: "insideTopLeft" }}/>} 
            {targetPrice > 0 && <ReferenceLine y={targetPrice} stroke="#3bffa4" strokeDasharray="5 4" label={{ value: "SELL", fill: "#3bffa4", fontSize: 10, position: "insideTopRight" }}/>} 
            <Area type="monotone" dataKey="price" stroke="#3bffa4" strokeWidth={3} fill={`url(#modalPriceFill-${cardId})`} activeDot={{ r: 5 }} dot={false}/>
          </AreaChart>
        </ResponsiveContainer>}
    </div>
  </div>;
}

function PriceTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return <div className="dash-chart-tooltip"><small>SALE PRICE{point.approximate?" (approx. time)":""}</small><strong><CoinValue value={point.price}/></strong><span>{new Date(point.time).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span></div>;
}
function formatDay(value) { return new Date(value).toLocaleDateString("en-GB", { weekday: "short", day: "numeric" }); }
function compactCoins(value) { const n=Number(value); return Number.isFinite(n) ? (n>=1000?`${Math.round(n/1000)}k`:String(Math.round(n))) : ""; }
