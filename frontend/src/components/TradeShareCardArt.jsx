// src/components/TradeShareCardArt.jsx
//
// Pure presentation for the "Share" button's rendered image - see
// pages/internal/TradeShareExport.jsx for the readiness/screenshot
// contract this attaches to (data-trade-share-export).
//
// Hierarchy (deliberate, not incidental): 1) player/card, 2) profit -
// the dominant element, readable in ~1s, 3) buy -> sell (with quantity
// folded in, not hidden in a pill), 4) ROI, 5) EAFC.Live branding, kept
// subtle. `data.roi` is computed server-side (trade_economics.py) - this
// component never recomputes it. An earlier version divided profit by
// unit buy price alone here, overstating ROI by a factor of quantity for
// every qty>1 trade; that formula no longer exists on the frontend at
// all, so it can't regress the same way twice.
import { DIMENSIONS, formatCoinsCompact, formatCoinsExact, formatRoi, scaledFontSize } from "./shareCardFormat";

const ACCENT = "#39ff8a";
const LOSS = "#ff5f6d";
const MUTED = "#8d8f95";

export default function TradeShareCardArt({ data, format = "wide" }) {
  const { width, height } = DIMENSIONS[format] || DIMENSIONS.wide;
  const isPortrait = format === "portrait";
  const profit = data.profit ?? 0;
  const isProfit = profit >= 0;
  const resultColor = isProfit ? ACCENT : LOSS;
  const quantity = data.quantity || 1;
  const roiText = formatRoi(data.roi);
  const transparency = quantity > 1
    ? `${quantity} cards • after 5% EA tax`
    : "after 5% EA tax";

  return (
    <div
      data-trade-share-export
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

      <Header resultColor={resultColor} isProfit={isProfit} isPortrait={isPortrait} />

      {isPortrait ? (
        <PortraitBody
          data={data}
          profit={profit}
          isProfit={isProfit}
          resultColor={resultColor}
          quantity={quantity}
          roiText={roiText}
          transparency={transparency}
        />
      ) : (
        <WideBody
          data={data}
          profit={profit}
          isProfit={isProfit}
          resultColor={resultColor}
          quantity={quantity}
          roiText={roiText}
          transparency={transparency}
        />
      )}

      <Footer isPortrait={isPortrait} />
    </div>
  );
}

function Header({ resultColor, isProfit, isPortrait }) {
  return (
    <div style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
      <Brand size={isPortrait ? 20 : 15} />
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
        {isProfit ? "Trade Won" : "Trade Closed"}
      </div>
    </div>
  );
}

function Brand({ size }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
      <div style={{ width: size * 0.6, height: size * 0.6, borderRadius: 2, background: ACCENT, boxShadow: `0 0 10px ${ACCENT}99` }} />
      <span style={{ fontSize: size, fontWeight: 800, letterSpacing: "0.08em" }}>
        EAFC<span style={{ color: ACCENT }}>.LIVE</span>
      </span>
    </div>
  );
}

function PlayerBlock({ data, thumbSize, nameSize }) {
  return (
    <div style={{ position: "relative", display: "flex", alignItems: "center", gap: thumbSize * 0.2 }}>
      {data.image_url ? (
        <div style={{ position: "relative", width: thumbSize, height: thumbSize, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div
            style={{
              position: "absolute",
              inset: thumbSize * 0.05,
              background: "radial-gradient(circle, rgba(255,255,255,0.12) 0%, transparent 72%)",
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
            fontSize: data.player.length > 22 ? nameSize * 0.8 : nameSize,
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
        </div>
      </div>
    </div>
  );
}

function ProfitHero({ profit, isProfit, resultColor, size, transparency }) {
  const profitText = `${isProfit ? "+" : ""}${formatCoinsExact(profit)}`;
  const profitFontSize = scaledFontSize(size, profitText, 9);
  return (
    <div style={{ position: "relative", textAlign: "center" }}>
      <div style={{ fontSize: size * 0.16, color: MUTED, letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: size * 0.06 }}>
        {isProfit ? "Profit" : "Loss"}
      </div>
      <div style={{ fontSize: profitFontSize, fontWeight: 800, color: resultColor, lineHeight: 1 }}>
        {profitText}
      </div>
      <div style={{ fontSize: size * 0.14, color: MUTED, marginTop: size * 0.1, letterSpacing: "0.02em" }}>{transparency}</div>
    </div>
  );
}

function BuySellLine({ data, quantity, size }) {
  const qtyPrefix = quantity > 1 ? `${quantity}x @ ` : "";
  return (
    <div style={{ position: "relative", display: "flex", alignItems: "baseline", justifyContent: "center", gap: size * 0.3, flexWrap: "wrap" }}>
      <span style={{ fontSize: size, fontWeight: 700, color: "#d8dade" }}>
        {qtyPrefix}{formatCoinsCompact(data.buy)}
      </span>
      <span style={{ fontSize: size * 0.8, color: MUTED }}>&rarr;</span>
      <span style={{ fontSize: size, fontWeight: 700, color: "#d8dade" }}>
        {qtyPrefix}{formatCoinsCompact(data.sell)}
      </span>
    </div>
  );
}

function WideBody({ data, profit, isProfit, resultColor, quantity, roiText, transparency }) {
  return (
    <>
      <PlayerBlock data={data} thumbSize={172} nameSize={42} />
      <div style={{ position: "relative", display: "flex", gap: 14, alignItems: "stretch" }}>
        <StatCard label="Bought" value={quantity > 1 ? `${quantity}x @ ${formatCoinsCompact(data.buy)}` : formatCoinsExact(data.buy)} />
        <StatCard label="Sold" value={quantity > 1 ? `${quantity}x @ ${formatCoinsCompact(data.sell)}` : formatCoinsExact(data.sell)} />
        <StatCard
          label={isProfit ? "Profit" : "Loss"}
          value={`${isProfit ? "+" : ""}${formatCoinsExact(profit)}`}
          color={resultColor}
          highlight
        />
        {roiText ? <StatCard label="ROI" value={roiText} color={resultColor} /> : null}
      </div>
      <div style={{ position: "relative", textAlign: "right", fontSize: 12, color: MUTED, letterSpacing: "0.02em" }}>{transparency}</div>
    </>
  );
}

function PortraitBody({ data, profit, isProfit, resultColor, quantity, roiText, transparency }) {
  return (
    <div style={{ position: "relative", display: "flex", flexDirection: "column", alignItems: "center", gap: 40 }}>
      <PlayerBlock data={data} thumbSize={220} nameSize={56} />
      <ProfitHero profit={profit} isProfit={isProfit} resultColor={resultColor} size={132} transparency={transparency} />
      <BuySellLine data={data} quantity={quantity} size={40} />
      {roiText ? (
        <div
          style={{
            fontSize: 30,
            fontWeight: 800,
            color: resultColor,
            border: `1px solid ${resultColor}55`,
            background: `${resultColor}14`,
            borderRadius: 999,
            padding: "10px 28px",
          }}
        >
          ROI {roiText}
        </div>
      ) : null}
    </div>
  );
}

function Footer({ isPortrait }) {
  return (
    <div style={{ position: "relative", textAlign: "right", fontSize: isPortrait ? 16 : 12, color: MUTED, letterSpacing: "0.04em" }}>
      app.eafc.live &middot; live market data
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
