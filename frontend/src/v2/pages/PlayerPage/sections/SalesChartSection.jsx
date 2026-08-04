// src/v2/pages/PlayerPage/sections/SalesChartSection.jsx
//
// Real OHLC candles + sale markers, direct v1 endpoints (see the v2
// plan section 1.4) - independently timeframe-toggled, so kept out of
// the aggregated /summary payload on purpose.
import { ResponsiveContainer, ComposedChart, Area, Bar, XAxis, YAxis, Tooltip } from "recharts";
import SectionCard from "../../../components/SectionCard";
import { useSalesCandles, useSalesHistory } from "../../../hooks/useSalesCandles";
import { formatCoins, formatRelativeTime } from "../../../lib/format";

export default function SalesChartSection({ cardId }) {
  const { data: candlesData, isLoading: candlesLoading } = useSalesCandles(cardId);
  const { data: historyData, isLoading: historyLoading } = useSalesHistory(cardId, { limit: 10 });

  const candles = candlesData?.candles || [];
  const chartData = candles.map((c) => ({
    time: new Date(c.time * 1000).toLocaleDateString(),
    close: c.close,
    volume: c.volume,
  }));

  return (
    <SectionCard title="Price History" subtitle="Real completed sales, PS market">
      {candlesLoading ? (
        <p className="text-xs text-[var(--v2-muted)]">Loading...</p>
      ) : chartData.length === 0 ? (
        <p className="text-xs text-[var(--v2-muted)]">No recent sales data for this card.</p>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={chartData}>
            <defs>
              <linearGradient id="salesChartFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--v2-accent)" stopOpacity={0.35} />
                <stop offset="100%" stopColor="var(--v2-accent)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="time" tick={{ fontSize: 10 }} stroke="var(--v2-muted)" />
            <YAxis tick={{ fontSize: 10 }} stroke="var(--v2-muted)" />
            <Tooltip
              contentStyle={{ background: "var(--v2-elevated)", border: "1px solid var(--v2-border)" }}
            />
            <Bar dataKey="volume" fill="var(--v2-border)" yAxisId={0} />
            <Area
              type="monotone"
              dataKey="close"
              stroke="var(--v2-accent)"
              strokeWidth={2}
              fill="url(#salesChartFill)"
              dot={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}

      <div className="mt-4">
        <h4 className="text-xs font-semibold text-[var(--v2-muted)] mb-2">Recent sales</h4>
        {historyLoading ? (
          <p className="text-xs text-[var(--v2-muted)]">Loading...</p>
        ) : (historyData?.sales || []).length === 0 ? (
          <p className="text-xs text-[var(--v2-muted)]">No recent sales.</p>
        ) : (
          <ul className="text-xs divide-y divide-[var(--v2-border)]">
            {(historyData?.sales || []).map((s, i) => (
              <li key={i} className="flex justify-between py-1.5">
                <span>{formatCoins(s.soldPrice)}</span>
                <span className="text-[var(--v2-muted)]">{formatRelativeTime(s.soldAt)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </SectionCard>
  );
}
