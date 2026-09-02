// src/components/TradeSummaryCardArt.jsx
//
// Pure presentation for the portfolio-summary share card (Today/This
// Week/All-Time) - see pages/internal/TradeSummaryExport.jsx for the
// readiness/screenshot contract (data-trade-summary-export).
//
// Hierarchy: total profit is the one thing this card needs to say in
// ~1 second, so it is the dominant element, not one box among equals.
// The record line (wins-losses, win rate) sits right under it at
// near-hero size deliberately - for the All-Time card this IS the
// credibility proof (e.g. "78W - 9L / 89.7%"), and a real loss count
// shown plainly reads as more credible than a card that only ever
// shows wins. "Trades Tracked" (not "Cards Traded"): `closed_trades`
// counts trade-calls, not physical cards - a qty-4 trade is one closed
// trade, not four. Renamed rather than left ambiguous, per the same
// count that data.closed_trades has always held.
import { DIMENSIONS, formatCoinsCompact, formatCoinsExact, scaledFontSize } from "./shareCardFormat";

const ACCENT = "#39ff8a";
const LOSS = "#ff5f6d";
const MUTED = "#8d8f95";

export default function TradeSummaryCardArt({ data, format = "wide" }) {
  const { width, height } = DIMENSIONS[format] || DIMENSIONS.wide;
  const isPortrait = format === "portrait";
  const profit = data.total_profit ?? 0;
  const isProfit = profit >= 0;
  const resultColor = isProfit ? ACCENT : LOSS;
  const best = data.best_sale;
  const recordText = `${data.wins}W – ${data.losses}L`;
  const winRateText = `${data.win_rate}% win rate`;
  const profitText = `${isProfit ? "+" : ""}${formatCoinsExact(profit)}`;
  const profitFontSize = scaledFontSize(isPortrait ? 150 : 58, profitText, isPortrait ? 9 : 12);

  return (
    <div
      data-trade-summary-export
      style={{
        position: "relative",
        width,
        height,
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        padding: isPortrait ? "56px 64px" : "34px 44px",
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
          <span style={{ fontSize: isPortrait ? 20 : 15, fontWeight: 800, letterSpacing: "0.08em" }}>
            EAFC<span style={{ color: ACCENT }}>.LIVE</span>
          </span>
        </div>
        <div
          style={{
            fontSize: isPortrait ? 15 : 11,
            fontWeight: 700,
            letterSpacing: "0.08em",
            color: resultColor,
            border: `1px solid ${resultColor}55`,
            background: `${resultColor}14`,
            borderRadius: 999,
            padding: isPortrait ? "8px 20px" : "5px 14px",
            textTransform: "uppercase",
          }}
        >
          {data.period_label} Recap
        </div>
      </div>

      <div style={{ position: "relative", textAlign: isPortrait ? "center" : "left" }}>
        <div style={{ fontSize: isPortrait ? 20 : 13, color: MUTED, letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: isPortrait ? 14 : 8 }}>
          Total profit
        </div>
        <div style={{ fontSize: profitFontSize, fontWeight: 800, color: resultColor, lineHeight: 1 }}>
          {profitText}
        </div>
        <div
          style={{
            fontSize: isPortrait ? 36 : 20,
            fontWeight: 700,
            color: "#d8dade",
            marginTop: isPortrait ? 24 : 12,
          }}
        >
          {recordText} <span style={{ color: MUTED, fontWeight: 600 }}>&middot; {winRateText}</span>
        </div>
      </div>

      <div style={{ position: "relative", display: "flex", gap: 14 }}>
        <StatCard label="Trades Tracked" value={data.closed_trades} isPortrait={isPortrait} />
        {best ? (
          <StatCard
            label="Best Sale"
            value={best.player}
            sub={`${best.profit >= 0 ? "+" : ""}${formatCoinsCompact(best.profit)}`}
            color={resultColor}
            highlight
            isPortrait={isPortrait}
          />
        ) : null}
      </div>

      <div style={{ position: "relative", textAlign: "right", fontSize: isPortrait ? 16 : 12, color: MUTED, letterSpacing: "0.04em" }}>
        app.eafc.live &middot; live market data
      </div>
    </div>
  );
}

function StatCard({ label, value, sub, color, highlight, isPortrait }) {
  return (
    <div
      style={{
        flex: 1,
        minWidth: 0,
        padding: isPortrait ? "22px 26px" : "14px 18px",
        borderRadius: 10,
        background: highlight ? `${color}14` : "rgba(255,255,255,0.035)",
        border: highlight ? `1px solid ${color}40` : "1px solid rgba(255,255,255,0.08)",
      }}
    >
      <div style={{ fontSize: isPortrait ? 15 : 11, fontWeight: 700, color: MUTED, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: isPortrait ? 10 : 6 }}>
        {label}
      </div>
      <div
        style={{
          fontSize: isPortrait ? 32 : 22,
          fontWeight: 800,
          color: color || "#f3f3f2",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {value}
      </div>
      {sub ? <div style={{ fontSize: isPortrait ? 22 : 14, fontWeight: 700, color: color || MUTED, marginTop: 2 }}>{sub}</div> : null}
    </div>
  );
}
