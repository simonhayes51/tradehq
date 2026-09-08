// src/components/TradeShareCalledItArt.jsx
//
// Social-first trade-result art. This component is presentation only:
// profit/ROI/tax economics are computed server-side and arrive in `data`.
import { useEffect, useState } from "react";
import { DIMENSIONS, formatCoinsCompact, formatRoi, scaledFontSize } from "./shareCardFormat";

const ACCENT = "#91db32";
const LOSS = "#ff5f6d";
const BG = "#08090a";
const TEXT = "#f5f5f4";
const MUTED = "#8d8f95";
const PLACEHOLDER_CARD_ART = "/img/card-placeholder.png";

export default function TradeShareCalledItArt({ data, format = "wide" }) {
  const { width, height } = DIMENSIONS[format] || DIMENSIONS.wide;
  const portrait = format === "portrait";
  const profit = Number(data.profit || 0);
  const isProfit = profit >= 0;
  const resultColor = isProfit ? ACCENT : LOSS;
  const quantity = Number(data.quantity || 1);
  const profitText = `${isProfit ? "+" : ""}${formatCoinsCompact(profit)}`;
  const roiText = formatRoi(data.roi);
  const bought = quantity > 1 ? `${quantity}×${formatCoinsCompact(data.buy)}` : formatCoinsCompact(data.buy);
  const sold = quantity > 1 ? `${quantity}×${formatCoinsCompact(data.sell)}` : formatCoinsCompact(data.sell);
  const footer = quantity > 1 ? `${quantity} cards · after 5% EA tax` : "After 5% EA tax";

  return (
    <div
      data-trade-share-export
      style={{
        position: "relative",
        width,
        height,
        overflow: "hidden",
        boxSizing: "border-box",
        background: BG,
        color: TEXT,
        fontFamily: "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif",
      }}
    >
      <Texture />
      {portrait ? (
        <Portrait
          data={data}
          isProfit={isProfit}
          resultColor={resultColor}
          profitText={profitText}
          roiText={roiText}
          bought={bought}
          sold={sold}
          footer={footer}
        />
      ) : (
        <Wide
          data={data}
          isProfit={isProfit}
          resultColor={resultColor}
          profitText={profitText}
          roiText={roiText}
          bought={bought}
          sold={sold}
          footer={footer}
        />
      )}
    </div>
  );
}

function Texture() {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        backgroundImage: "repeating-linear-gradient(135deg, rgba(255,255,255,0.032) 0px, rgba(255,255,255,0.032) 1px, transparent 1px, transparent 30px)",
        pointerEvents: "none",
      }}
    />
  );
}

function Brand({ centered = false, large = false }) {
  return (
    <div style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: centered ? "center" : "flex-start", gap: large ? 11 : 8 }}>
      <img
        src="/icons/icon-72x72-v2.png"
        alt=""
        style={{ width: large ? 28 : 18, height: large ? 28 : 18, borderRadius: 5, display: "block" }}
      />
      <span style={{ fontSize: large ? 20 : 13, fontWeight: 800, letterSpacing: "0.11em", color: MUTED }}>
        EAFC<span style={{ color: ACCENT }}>.LIVE</span>
      </span>
    </div>
  );
}

function CardArt({ data, width, height }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [data.image_url]);
  const src = !data.image_url || failed ? PLACEHOLDER_CARD_ART : data.image_url;

  return (
    <div style={{ position: "relative", width, height, flexShrink: 0 }}>
      <img
        data-trade-share-thumb
        src={src}
        alt=""
        onError={() => setFailed(true)}
        style={{ width: "100%", height: "100%", objectFit: "contain", display: "block", filter: "drop-shadow(0 22px 34px rgba(0,0,0,0.52))" }}
      />
    </div>
  );
}

function PlayerName({ name, size, width }) {
  const fontSize = scaledFontSize(size, name || "", 18, 0.55);
  return (
    <div style={{ maxWidth: width, fontSize, fontWeight: 800, letterSpacing: "0.11em", color: TEXT, textTransform: "uppercase", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
      {name}
    </div>
  );
}

function Portrait({ data, isProfit, resultColor, profitText, roiText, bought, sold, footer }) {
  const profitSize = scaledFontSize(184, profitText, 8, 0.6);
  return (
    <div style={{ position: "relative", width: "100%", height: "100%", boxSizing: "border-box", padding: "34px 68px 28px", display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center" }}>
      <Brand centered large />

      <div style={{ marginTop: 22, fontSize: 68, lineHeight: 0.96, fontWeight: 950, letterSpacing: "-0.025em", color: isProfit ? TEXT : LOSS, textTransform: "uppercase" }}>
        {isProfit ? "WE CALLED IT." : "TRADE RESULT"}
      </div>
      <div style={{ marginTop: 9, fontSize: 15, fontWeight: 800, letterSpacing: "0.28em", color: MUTED, textTransform: "uppercase" }}>
        EAFC.Live trade result
      </div>

      <div style={{ marginTop: 22 }}>
        <CardArt data={data} width={410} height={505} />
      </div>

      <div style={{ marginTop: 2 }}>
        <PlayerName name={data.player} size={31} width={900} />
      </div>

      <div style={{ marginTop: 22, fontSize: profitSize, lineHeight: 0.86, fontWeight: 950, letterSpacing: "-0.045em", color: resultColor }}>
        {profitText}
      </div>
      <div style={{ marginTop: 8, fontSize: 18, fontWeight: 800, letterSpacing: "0.24em", color: MUTED, textTransform: "uppercase" }}>
        {isProfit ? "Profit" : "Loss"}
      </div>

      <div style={{ marginTop: 24, display: "flex", alignItems: "center", justifyContent: "center", gap: 25 }}>
        <Price value={bought} label="Bought" size={54} />
        <div style={{ paddingTop: 22, fontSize: 56, lineHeight: 1, fontWeight: 800, color: ACCENT }}>→</div>
        <Price value={sold} label="Sold" size={54} />
      </div>

      {roiText ? (
        <div style={{ marginTop: 25, fontSize: 57, lineHeight: 1, fontWeight: 900, color: resultColor }}>
          {roiText} <span style={{ fontSize: 20, color: MUTED, letterSpacing: "0.16em", verticalAlign: "middle" }}>ROI</span>
        </div>
      ) : null}

      <div style={{ marginTop: 18, fontSize: 19, color: MUTED, letterSpacing: "0.025em" }}>{footer}</div>
      <div style={{ marginTop: "auto" }}><Attribution /></div>
    </div>
  );
}

function Wide({ data, isProfit, resultColor, profitText, roiText, bought, sold, footer }) {
  const profitSize = scaledFontSize(88, profitText, 7, 0.6);
  return (
    <div style={{ position: "relative", width: "100%", height: "100%", boxSizing: "border-box", padding: "24px 30px" }}>
      <div style={{ position: "absolute", top: 22, left: 30 }}><Brand /></div>
      <div style={{ position: "absolute", bottom: 18, right: 30 }}><Attribution small /></div>

      <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", gap: 32 }}>
        <CardArt data={data} width={218} height={286} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 32, fontWeight: 950, letterSpacing: "-0.015em", textTransform: "uppercase" }}>
            {isProfit ? "WE CALLED IT." : "TRADE RESULT"}
          </div>
          <div style={{ marginTop: 5 }}><PlayerName name={data.player} size={18} width={560} /></div>
          <div style={{ marginTop: 14, fontSize: profitSize, lineHeight: 0.88, fontWeight: 950, letterSpacing: "-0.04em", color: resultColor }}>{profitText}</div>
          <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 14 }}>
            <Price value={bought} label="Bought" size={27} />
            <div style={{ paddingTop: 15, fontSize: 28, fontWeight: 800, color: ACCENT }}>→</div>
            <Price value={sold} label="Sold" size={27} />
          </div>
          <div style={{ marginTop: 11, display: "flex", alignItems: "baseline", gap: 14 }}>
            {roiText ? <div style={{ fontSize: 34, fontWeight: 900, color: resultColor }}>{roiText} ROI</div> : null}
            <div style={{ fontSize: 12, color: MUTED }}>{footer}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Price({ label, value, size }) {
  return (
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: Math.max(12, size * 0.3), fontWeight: 800, letterSpacing: "0.19em", color: MUTED, textTransform: "uppercase" }}>{label}</div>
      <div style={{ marginTop: 2, fontSize: size, lineHeight: 1, fontWeight: 850, color: TEXT }}>{value}</div>
    </div>
  );
}

function Attribution({ small = false }) {
  return (
    <div style={{ fontSize: small ? 10 : 14, fontWeight: 750, letterSpacing: "0.09em", color: MUTED, border: "1px solid rgba(255,255,255,0.13)", borderRadius: 999, padding: small ? "5px 10px" : "7px 15px" }}>
      DATA FROM <span style={{ color: ACCENT }}>EAFC.LIVE</span>
    </div>
  );
}
