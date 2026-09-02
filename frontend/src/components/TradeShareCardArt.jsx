// src/components/TradeShareCardArt.jsx
//
// Pure presentation for the "Share" button's rendered image - see
// pages/internal/TradeShareExport.jsx for the readiness/screenshot
// contract this attaches to (data-trade-share-export).
//
// Editorial football-graphic system, not a dashboard export: real card
// artwork as the dominant visual, one hero number (profit), everything
// else as running text sized by importance - never four equal boxes.
// `data.roi` is computed server-side (trade_economics.py) - this
// component has no ROI math of its own. An earlier version divided
// profit by unit buy price alone here, overstating ROI by a factor of
// quantity for every qty>1 trade; that formula doesn't exist on the
// frontend at all any more.
import { DIMENSIONS, formatCoinsCompact, formatCoinsExact, formatRoi, scaledFontSize } from "./shareCardFormat";

const ACCENT = "#91db32";
const LOSS = "#ff5f6d";
const MUTED = "#8d8f95";
const INK = "#08090a";

const SIZES = {
  portrait: {
    // Margins tuned to leave headroom for the widest realistic hero
    // string ("+" + up to 3 digits + two thousands-commas, e.g.
    // "+1,522,000") at its scaledFontSize-reduced size - caught in
    // production QA when that exact string pushed the footer ~37px past
    // the 1350px canvas and it got clipped by overflow:hidden. Sizes
    // (font-size, card dimensions) are the approved hierarchy and are
    // left untouched; only vertical spacing was tightened.
    padding: "48px 76px 30px",
    gap: 0,
    brandIcon: 26, brandText: 18,
    kicker: 48,
    cardWidth: 414, cardHeight: 524, cardMarginTop: 10,
    playerLine: 30, playerLineMarginTop: 14,
    profitLabel: 25, profitHero: 192, profitMarginTop: 20,
    priceLabel: 17, priceValue: 60, priceArrow: 56, priceMarginTop: 20, priceGap: 26,
    roiLabel: 18, roiValue: 72, roiMarginTop: 20,
    tax: 20, taxMarginTop: 16,
    pill: 15,
  },
  wide: {
    cardWidth: 210, cardHeight: 266,
    brandIcon: 18, brandText: 13,
    kicker: 27,
    playerLine: 16,
    profitLabel: 13, profitHero: 82,
    priceLabel: 10, priceValue: 25, priceArrow: 22,
    roiLabel: 10, roiValue: 32,
    tax: 12,
    pill: 11,
  },
};

export default function TradeShareCardArt({ data, format = "wide" }) {
  const { width, height } = DIMENSIONS[format] || DIMENSIONS.wide;
  const isPortrait = format === "portrait";
  const s = SIZES[format] || SIZES.wide;

  const profit = data.profit ?? 0;
  const isProfit = profit >= 0;
  const resultColor = isProfit ? ACCENT : LOSS;
  const quantity = data.quantity || 1;
  const roiText = formatRoi(data.roi);
  const profitText = `${isProfit ? "+" : ""}${formatCoinsExact(profit)}`;
  const profitFontSize = scaledFontSize(s.profitHero, profitText, isPortrait ? 9 : 7);
  const transparency = quantity > 1 ? `${quantity} cards · after 5% EA tax` : "After 5% EA tax";
  const boughtValue = quantity > 1 ? `${quantity}×${formatCoinsCompact(data.buy)}` : formatCoinsCompact(data.buy);
  const soldValue = quantity > 1 ? `${quantity}×${formatCoinsCompact(data.sell)}` : formatCoinsCompact(data.sell);

  return (
    <div
      data-trade-share-export
      style={{
        position: "relative",
        width,
        height,
        background: INK,
        color: "#f5f5f4",
        fontFamily: "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif",
        overflow: "hidden",
        boxSizing: "border-box",
      }}
    >
      <Texture />
      {isPortrait ? (
        <PortraitLayout
          data={data} s={s} resultColor={resultColor} isProfit={isProfit}
          profitText={profitText} profitFontSize={profitFontSize} roiText={roiText}
          boughtValue={boughtValue} soldValue={soldValue} transparency={transparency}
        />
      ) : (
        <WideLayout
          data={data} s={s} resultColor={resultColor} isProfit={isProfit}
          profitText={profitText} profitFontSize={profitFontSize} roiText={roiText}
          boughtValue={boughtValue} soldValue={soldValue} transparency={transparency}
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
        backgroundImage:
          "repeating-linear-gradient(135deg, rgba(255,255,255,0.035) 0px, rgba(255,255,255,0.035) 1px, transparent 1px, transparent 30px)",
        pointerEvents: "none",
      }}
    />
  );
}

function Brand({ iconSize, textSize }) {
  return (
    <div style={{ position: "relative", display: "flex", alignItems: "center", gap: iconSize * 0.4 }}>
      <img src="/icons/icon-72x72.png" alt="" style={{ width: iconSize, height: iconSize, borderRadius: iconSize * 0.22, display: "block" }} />
      <span style={{ fontSize: textSize, fontWeight: 800, letterSpacing: "0.1em", color: MUTED }}>
        EAFC<span style={{ color: ACCENT }}>.LIVE</span>
      </span>
    </div>
  );
}

function CardFrame({ data, width, height, ratingSize }) {
  return (
    <div
      style={{
        position: "relative",
        width,
        height,
        borderRadius: width * 0.045,
        overflow: "hidden",
        flexShrink: 0,
        background: "#111214",
        border: "1px solid rgba(255,255,255,0.08)",
        boxShadow: "0 24px 48px rgba(0,0,0,0.5)",
      }}
    >
      <img
        data-trade-share-thumb
        src={data.image_url || "/img/card-placeholder.png"}
        alt=""
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block", opacity: 0.94 }}
      />
      {!data.image_url && data.rating ? (
        <div style={{ position: "absolute", top: height * 0.03, left: width * 0.04, fontSize: ratingSize, fontWeight: 900, color: ACCENT, letterSpacing: "-0.02em" }}>
          {data.rating}
        </div>
      ) : null}
    </div>
  );
}

function PlayerLine({ data, size, marginTop = 0 }) {
  return (
    <div style={{ position: "relative", marginTop, fontSize: size, fontWeight: 700, letterSpacing: "0.06em", color: "#d8dade", textTransform: "uppercase" }}>
      {data.player}
      <span style={{ color: "#5c5e64", margin: `0 ${size * 0.33}px` }}>&middot;</span>
      {data.version}
    </div>
  );
}

function ProfitBlock({ isProfit, profitText, profitFontSize, resultColor, labelSize, marginTop = 0, align = "center" }) {
  return (
    <div style={{ position: "relative", marginTop, textAlign: align }}>
      <div style={{ fontSize: labelSize, fontWeight: 800, letterSpacing: "0.22em", color: MUTED, textTransform: "uppercase" }}>
        {isProfit ? "Profit" : "Loss"}
      </div>
      <div style={{ marginTop: labelSize * 0.08, fontSize: profitFontSize, fontWeight: 900, lineHeight: 0.94, letterSpacing: "-0.03em", color: resultColor }}>
        {profitText}
      </div>
    </div>
  );
}

function PriceLine({ boughtValue, soldValue, s, marginTop = 0, justify = "center" }) {
  return (
    <div style={{ position: "relative", marginTop, display: "flex", alignItems: "center", justifyContent: justify, gap: s.priceGap || s.priceLabel * 1.4 }}>
      <div style={{ textAlign: "center" }}>
        <div style={{ fontSize: s.priceLabel, fontWeight: 800, letterSpacing: "0.18em", color: MUTED, textTransform: "uppercase" }}>Bought</div>
        <div style={{ marginTop: 2, fontSize: s.priceValue, fontWeight: 800, color: "#f5f5f4" }}>{boughtValue}</div>
      </div>
      <div style={{ fontSize: s.priceArrow, fontWeight: 700, color: ACCENT, marginTop: s.priceLabel * 1.1 }}>&rarr;</div>
      <div style={{ textAlign: "center" }}>
        <div style={{ fontSize: s.priceLabel, fontWeight: 800, letterSpacing: "0.18em", color: MUTED, textTransform: "uppercase" }}>Sold</div>
        <div style={{ marginTop: 2, fontSize: s.priceValue, fontWeight: 800, color: "#f5f5f4" }}>{soldValue}</div>
      </div>
    </div>
  );
}

function RoiBlock({ roiText, s, marginTop = 0, align = "center" }) {
  if (!roiText) return null;
  return (
    <div style={{ position: "relative", marginTop, textAlign: align }}>
      <div style={{ fontSize: s.roiLabel, fontWeight: 800, letterSpacing: "0.18em", color: MUTED, textTransform: "uppercase" }}>ROI</div>
      <div style={{ marginTop: 2, fontSize: s.roiValue, fontWeight: 900, letterSpacing: "-0.01em", color: ACCENT }}>{roiText}</div>
    </div>
  );
}

function PortraitLayout({ data, s, resultColor, isProfit, profitText, profitFontSize, roiText, boughtValue, soldValue, transparency }) {
  return (
    <div style={{ position: "relative", width: "100%", height: "100%", display: "flex", flexDirection: "column", alignItems: "center", padding: s.padding, boxSizing: "border-box" }}>
      <div style={{ position: "relative", width: "100%" }}>
        <Brand iconSize={s.brandIcon} textSize={s.brandText} />
        <div style={{ marginTop: 8, fontSize: s.kicker, fontWeight: 900, letterSpacing: "0.03em", color: "#f5f5f4", textTransform: "uppercase" }}>
          Trade Result
        </div>
      </div>

      <div style={{ position: "relative", marginTop: s.cardMarginTop, width: "100%", display: "flex", justifyContent: "center" }}>
        <CardFrame data={data} width={s.cardWidth} height={s.cardHeight} ratingSize={38} />
      </div>

      <PlayerLine data={data} size={s.playerLine} marginTop={s.playerLineMarginTop} />
      <ProfitBlock isProfit={isProfit} profitText={profitText} profitFontSize={profitFontSize} resultColor={resultColor} labelSize={s.profitLabel} marginTop={s.profitMarginTop} />
      <PriceLine boughtValue={boughtValue} soldValue={soldValue} s={s} marginTop={s.priceMarginTop} />
      <RoiBlock roiText={roiText} s={s} marginTop={s.roiMarginTop} />

      <div style={{ position: "relative", marginTop: s.taxMarginTop, fontSize: s.tax, color: "#7d7f85", letterSpacing: "0.02em" }}>{transparency}</div>

      <div style={{ position: "relative", marginTop: "auto", width: "100%", display: "flex", justifyContent: "center" }}>
        <AttributionPill size={s.pill} />
      </div>
    </div>
  );
}

function WideLayout({ data, s, resultColor, isProfit, profitText, profitFontSize, roiText, boughtValue, soldValue, transparency }) {
  return (
    <div style={{ position: "relative", width: "100%", height: "100%", boxSizing: "border-box", padding: "26px 32px" }}>
      <div style={{ position: "absolute", top: 24, left: 32 }}>
        <Brand iconSize={s.brandIcon} textSize={s.brandText} />
      </div>
      <div style={{ position: "absolute", bottom: 20, right: 32 }}>
        <AttributionPill size={s.pill} />
      </div>

      <div style={{ position: "relative", width: "100%", height: "100%", display: "flex", alignItems: "center", gap: 30 }}>
        <CardFrame data={data} width={s.cardWidth} height={s.cardHeight} ratingSize={26} />
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", alignItems: "flex-start" }}>
          <div style={{ fontSize: s.kicker, fontWeight: 900, letterSpacing: "0.03em", color: "#f5f5f4", textTransform: "uppercase" }}>Trade Result</div>
          <PlayerLine data={data} size={s.playerLine} marginTop={6} />
          <ProfitBlock isProfit={isProfit} profitText={profitText} profitFontSize={profitFontSize} resultColor={resultColor} labelSize={s.profitLabel} marginTop={12} align="left" />
          <PriceLine boughtValue={boughtValue} soldValue={soldValue} s={s} marginTop={14} justify="flex-start" />
          <div style={{ display: "flex", alignItems: "flex-end", gap: 20, marginTop: 12 }}>
            <RoiBlock roiText={roiText} s={s} align="left" />
            <div style={{ fontSize: s.tax, color: "#7d7f85", letterSpacing: "0.02em" }}>{transparency}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function AttributionPill({ size }) {
  return (
    <div
      style={{
        fontSize: size,
        fontWeight: 700,
        letterSpacing: "0.08em",
        color: "#7d7f85",
        border: "1px solid rgba(255,255,255,0.14)",
        borderRadius: 999,
        padding: `${size * 0.47}px ${size * 1.07}px`,
      }}
    >
      DATA FROM EAFC.LIVE
    </div>
  );
}
