// src/components/TradeSummaryCardArt.jsx
//
// Pure presentation for the portfolio-summary share card (Today/This
// Week/All-Time) - see pages/internal/TradeSummaryExport.jsx for the
// readiness/screenshot contract (data-trade-summary-export).
const ACCENT = "#39ff8a";
const LOSS = "#ff5f6d";
const MUTED = "#8d8f95";

function formatCoins(n) {
  return Number(n || 0).toLocaleString();
}

export default function TradeSummaryCardArt({ data }) {
  const profit = data.total_profit ?? 0;
  const isProfit = profit >= 0;
  const resultColor = isProfit ? ACCENT : LOSS;
  const best = data.best_sale;

  return (
    <div
      data-trade-summary-export
      style={{
        position: "relative",
        width: 900,
        height: 470,
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        padding: "34px 44px",
        background: `
          radial-gradient(600px 400px at 85% -10%, ${resultColor}22 0%, transparent 60%),
          radial-gradient(500px 350px at -5% 110%, ${resultColor}14 0%, transparent 55%),
          #08090a
        `,
        color: "#f3f3f2",
        fontFamily: "'Segoe UI', Inter, system-ui, -apple-system, sans-serif",
        boxSizing: "border-box",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage:
            "repeating-linear-gradient(135deg, rgba(255,255,255,0.025) 0px, rgba(255,255,255,0.025) 1px, transparent 1px, transparent 26px)",
          pointerEvents: "none",
        }}
      />
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 4,
          background: `linear-gradient(90deg, ${resultColor}, transparent 70%)`,
        }}
      />

      <div style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <div style={{ width: 9, height: 9, borderRadius: 2, background: ACCENT, boxShadow: `0 0 10px ${ACCENT}99` }} />
          <span style={{ fontSize: 15, fontWeight: 800, letterSpacing: "0.08em" }}>
            EAFC<span style={{ color: ACCENT }}>.LIVE</span>
          </span>
        </div>
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.08em",
            color: resultColor,
            border: `1px solid ${resultColor}55`,
            background: `${resultColor}14`,
            borderRadius: 999,
            padding: "5px 14px",
            textTransform: "uppercase",
          }}
        >
          {data.period_label} Recap
        </div>
      </div>

      <div style={{ position: "relative" }}>
        <div style={{ fontSize: 13, color: MUTED, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 8 }}>
          Total profit
        </div>
        <div style={{ fontSize: 58, fontWeight: 800, color: resultColor, lineHeight: 1 }}>
          {isProfit ? "+" : ""}
          {formatCoins(profit)}
        </div>
      </div>

      <div style={{ position: "relative", display: "flex", gap: 14 }}>
        <StatCard label="Record" value={`${data.wins}W – ${data.losses}L`} />
        <StatCard label="Win Rate" value={`${data.win_rate}%`} />
        <StatCard label="Cards Traded" value={data.closed_trades} />
        {best ? (
          <StatCard
            label="Best Sale"
            value={best.player}
            sub={`${best.profit >= 0 ? "+" : ""}${formatCoins(best.profit)}`}
            color={resultColor}
            highlight
          />
        ) : null}
      </div>

      <div style={{ position: "relative", textAlign: "right", fontSize: 12, color: MUTED, letterSpacing: "0.04em" }}>
        app.eafc.live
      </div>
    </div>
  );
}

function StatCard({ label, value, sub, color, highlight }) {
  return (
    <div
      style={{
        flex: 1,
        minWidth: 0,
        padding: "14px 18px",
        borderRadius: 10,
        background: highlight ? `${color}14` : "rgba(255,255,255,0.035)",
        border: highlight ? `1px solid ${color}40` : "1px solid rgba(255,255,255,0.08)",
      }}
    >
      <div style={{ fontSize: 11, fontWeight: 700, color: MUTED, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 6 }}>
        {label}
      </div>
      <div
        style={{
          fontSize: 22,
          fontWeight: 800,
          color: color || "#f3f3f2",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {value}
      </div>
      {sub ? <div style={{ fontSize: 14, fontWeight: 700, color: color || MUTED, marginTop: 2 }}>{sub}</div> : null}
    </div>
  );
}
