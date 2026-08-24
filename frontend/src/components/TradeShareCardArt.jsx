// src/components/TradeShareCardArt.jsx
//
// Pure presentation for the "Share" button's rendered image - see
// pages/internal/TradeShareExport.jsx for the readiness/screenshot
// contract this attaches to (data-trade-share-export).
const ACCENT = "#39ff8a";
const LOSS = "#ff5f6d";
const MUTED = "#8d8f95";

function formatCoins(n) {
  return Number(n || 0).toLocaleString();
}

export default function TradeShareCardArt({ data }) {
  const profit = data.profit ?? 0;
  const isProfit = profit >= 0;
  const resultColor = isProfit ? ACCENT : LOSS;
  const roi = data.buy ? (profit / data.buy) * 100 : null;

  return (
    <div
      data-trade-share-export
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
      {/* Faint diagonal texture, purely decorative - gives the flat
          background some depth without needing an image asset. */}
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

      {/* Header */}
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
          {isProfit ? "Trade Won" : "Trade Closed"}
        </div>
      </div>

      {/* Player block */}
      <div style={{ position: "relative", display: "flex", alignItems: "center", gap: 34 }}>
        {data.image_url ? (
          <div
            style={{
              position: "relative",
              width: 172,
              height: 172,
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <div
              style={{
                position: "absolute",
                inset: 8,
                background: `radial-gradient(circle, ${resultColor}3d 0%, transparent 72%)`,
                filter: "blur(2px)",
              }}
            />
            <img
              data-trade-share-thumb
              src={data.image_url}
              alt=""
              style={{ position: "relative", width: "100%", height: "100%", objectFit: "contain" }}
            />
          </div>
        ) : null}
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              fontSize: data.player.length > 22 ? 34 : 42,
              fontWeight: 800,
              lineHeight: 1.08,
              wordBreak: "break-word",
            }}
          >
            {data.player}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
            <Pill>{data.version}</Pill>
            {data.rating ? <Pill>{data.rating} OVR</Pill> : null}
            {data.platform ? <Pill>{String(data.platform).toUpperCase()}</Pill> : null}
            {data.quantity > 1 ? <Pill>x{data.quantity}</Pill> : null}
          </div>
        </div>
      </div>

      {/* Stats row */}
      <div style={{ position: "relative", display: "flex", gap: 14 }}>
        <StatCard label="Bought" value={formatCoins(data.buy)} />
        <StatCard label="Sold" value={formatCoins(data.sell)} />
        <StatCard
          label={isProfit ? "Profit" : "Loss"}
          value={`${isProfit ? "+" : ""}${formatCoins(profit)}`}
          color={resultColor}
          highlight
        />
        {roi !== null ? (
          <StatCard
            label="ROI"
            value={`${roi >= 0 ? "+" : ""}${roi.toFixed(1)}%`}
            color={resultColor}
          />
        ) : null}
      </div>

      <div
        style={{
          position: "relative",
          textAlign: "right",
          fontSize: 12,
          color: MUTED,
          letterSpacing: "0.04em",
        }}
      >
        app.eafc.live
      </div>
    </div>
  );
}

function Pill({ children }) {
  return (
    <span
      style={{
        fontSize: 14,
        fontWeight: 600,
        color: "#d8dade",
        background: "rgba(255,255,255,0.06)",
        border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: 999,
        padding: "5px 13px",
      }}
    >
      {children}
    </span>
  );
}

function StatCard({ label, value, color, highlight }) {
  return (
    <div
      style={{
        flex: 1,
        padding: "14px 18px",
        borderRadius: 10,
        background: highlight ? `${color}14` : "rgba(255,255,255,0.035)",
        border: highlight ? `1px solid ${color}40` : "1px solid rgba(255,255,255,0.08)",
      }}
    >
      <div style={{ fontSize: 11, fontWeight: 700, color: MUTED, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 26, fontWeight: 800, color: color || "#f3f3f2", whiteSpace: "nowrap" }}>{value}</div>
    </div>
  );
}
