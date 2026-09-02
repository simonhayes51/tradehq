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
import { useEffect, useState } from "react";
import { DIMENSIONS, formatCoinsCompact, formatRoi, scaledFontSize } from "./shareCardFormat";

const ACCENT = "#91db32";
const LOSS = "#ff5f6d";
const MUTED = "#8d8f95";
const INK = "#08090a";

const SIZES = {
  portrait: {
    // Vertical rhythm tuned so the ROI/tax/attribution cluster at the
    // bottom gets real breathing room while still guaranteeing the
    // footer never clips: the hero is compact-formatted now (max ~8
    // chars - "+487.32M" - so it renders at its full, predictable
    // height almost every time, unlike the old full-integer hero whose
    // height varied with digit count). Portrait is the primary format;
    // these values are not constrained by what wide needs.
    paddingX: 76,
    padding: "36px 76px 26px",
    gap: 0,
    brandIcon: 26, brandText: 18,
    kicker: 48,
    cardWidth: 414, cardHeight: 524, cardMarginTop: 4,
    playerLine: 30, playerLineMarginTop: 6,
    profitLabel: 25, profitHero: 192, profitMarginTop: 10,
    priceLabel: 17, priceValue: 60, priceArrow: 56, priceMarginTop: 12, priceGap: 26,
    roiLabel: 18, roiValue: 72, roiMarginTop: 32,
    tax: 20, taxMarginTop: 24,
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
  // Compact, not the full integer: a hero string this short (max ~8
  // chars, "+487.32M") can't realistically overflow the canvas the way
  // an exact multi-comma integer could - scaledFontSize below is a
  // backstop for the extreme case, not the primary defence.
  const profitText = `${isProfit ? "+" : ""}${formatCoinsCompact(profit)}`;
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

const PLACEHOLDER_CARD_ART = "/img/card-placeholder.png";

function CardFrame({ data, width, height, ratingSize }) {
  // A remote card photo can fail at render time (the source CDN pruned
  // an old asset, a transient 404, CORS) even when data.image_url is
  // set - without this, that showed as a blank box with a tiny browser
  // broken-image glyph inside the card's own border: recognisable as
  // "this graphic is broken," not "this card has no photo." Missing and
  // failed now look identical - both fall back to the same placeholder
  // every other page in the app already uses for this exact situation -
  // and the rating overlay (which the real photo already shows) reappears
  // whenever the placeholder is what's actually on screen, failure
  // included, not just for a genuinely absent URL.
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => { setImageFailed(false); }, [data.image_url]);
  const usingPlaceholder = !data.image_url || imageFailed;

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
        src={usingPlaceholder ? PLACEHOLDER_CARD_ART : data.image_url}
        alt=""
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block", opacity: 0.94 }}
        onError={() => setImageFailed(true)}
      />
      {usingPlaceholder && data.rating ? (
        <div style={{ position: "absolute", top: height * 0.03, left: width * 0.04, fontSize: ratingSize, fontWeight: 900, color: ACCENT, letterSpacing: "-0.02em" }}>
          {data.rating}
        </div>
      ) : null}
    </div>
  );
}

function PlayerLine({ data, size, marginTop = 0, maxWidth }) {
  // Just the name - version/rating/platform are already visible on the
  // card art itself, and repeating them here as "· TOTS" duplicated
  // information the photo already shows. A real player's name can still
  // run long (double-barrelled surnames, longer non-English names);
  // scaledFontSize shrinks it first, and the hard nowrap+ellipsis is the
  // backstop if a name is long enough that even the floor scale would
  // still overflow the card's width.
  const fontSize = scaledFontSize(size, data.player || "", 16);
  return (
    <div
      style={{
        position: "relative",
        marginTop,
        maxWidth,
        fontSize,
        fontWeight: 700,
        letterSpacing: "0.06em",
        color: "#d8dade",
        textTransform: "uppercase",
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
        // A flex item (this sits in a flex-direction:column layout) with
        // any overflow other than visible gets its automatic minimum
        // main-axis size forced to 0 per the flexbox spec - if the
        // column ever runs even slightly over its budget, Chromium
        // collapses THIS element to zero height first (silently, with
        // no visual overflow at all) rather than any of its siblings.
        // flexShrink:0 opts it out: any real overflow now shows up as
        // actual clipping, which is easy to catch, instead of the name
        // just vanishing.
        flexShrink: 0,
      }}
    >
      {data.player}
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

      <PlayerLine data={data} size={s.playerLine} marginTop={s.playerLineMarginTop} maxWidth={DIMENSIONS.portrait.width - s.paddingX * 2} />
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
          <PlayerLine data={data} size={s.playerLine} marginTop={6} maxWidth="100%" />
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
