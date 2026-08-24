// src/components/TradeShareCardArt.jsx
//
// Pure presentation for the "Share" button's rendered image - see
// pages/internal/TradeShareExport.jsx for the readiness/screenshot
// contract this attaches to (data-trade-share-export).
const ACCENT = "#39ff8a";

function formatCoins(n) {
  return `${Number(n || 0).toLocaleString()}`;
}

export default function TradeShareCardArt({ data }) {
  const profit = data.profit ?? 0;
  const isProfit = profit >= 0;

  return (
    <div
      data-trade-share-export
      style={{
        width: 900,
        height: 470,
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        padding: 40,
        background: "radial-gradient(circle at 15% 15%, #14231b 0%, #0a0b0c 60%)",
        color: "#f3f3f2",
        fontFamily: "Inter, system-ui, -apple-system, sans-serif",
        boxSizing: "border-box",
        border: `1px solid ${ACCENT}33`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 10, height: 10, borderRadius: 2, background: ACCENT }} />
          <span style={{ fontSize: 14, fontWeight: 800, letterSpacing: "0.08em", color: ACCENT }}>
            EAFC.LIVE
          </span>
        </div>
        <span style={{ fontSize: 12, color: "#8d8f95", textTransform: "uppercase", letterSpacing: "0.06em" }}>
          Trade Result
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 32 }}>
        {data.image_url ? (
          <img
            data-trade-share-thumb
            src={data.image_url}
            alt=""
            style={{ width: 150, height: 150, objectFit: "contain", flexShrink: 0 }}
          />
        ) : null}
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 40, fontWeight: 800, lineHeight: 1.05, wordBreak: "break-word" }}>
            {data.player}
          </div>
          <div style={{ fontSize: 18, color: "#8d8f95", marginTop: 6 }}>
            {data.version}
            {data.rating ? ` · ${data.rating} OVR` : ""}
            {data.platform ? ` · ${String(data.platform).toUpperCase()}` : ""}
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 28 }}>
        <Stat label="BOUGHT" value={formatCoins(data.buy)} />
        <Stat label="SOLD" value={formatCoins(data.sell)} />
        <Stat
          label={isProfit ? "PROFIT" : "LOSS"}
          value={`${isProfit ? "+" : ""}${formatCoins(profit)}`}
          color={isProfit ? ACCENT : "#ff6b6b"}
        />
      </div>
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div>
      <div style={{ fontSize: 12, color: "#8d8f95", letterSpacing: "0.06em", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 30, fontWeight: 800, color: color || "#f3f3f2" }}>{value}</div>
    </div>
  );
}
