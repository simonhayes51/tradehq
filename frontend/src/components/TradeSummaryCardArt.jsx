// src/components/TradeSummaryCardArt.jsx
//
// Pure presentation for the portfolio-summary share card (Today/This
// Week/All-Time) - see pages/internal/TradeSummaryExport.jsx for the
// readiness/screenshot contract (data-trade-summary-export). Same
// editorial system as TradeShareCardArt.jsx: total profit is the one
// hero element (there is no single card to anchor to on this graphic,
// so the number itself carries that role), everything else is running
// text sized by importance, wins and losses always rendered in the same
// weight - the All-Time card's credibility is the honest record, not a
// curated one.
import { useEffect, useState } from "react";
import { DIMENSIONS, formatCoinsCompact, scaledFontSize } from "./shareCardFormat";

const ACCENT = "#91db32";
const LOSS = "#ff5f6d";
const MUTED = "#8d8f95";
const INK = "#08090a";
const PLACEHOLDER_CARD_ART = "/img/card-placeholder.png";

const SIZES = {
  portrait: {
    paddingX: 84,
    padding: "68px 84px 52px",
    brandIcon: 34, brandText: 22,
    period: 34,
    heroLabel: 27, hero: 224, heroMarginTop: 22,
    recordMarginTop: 66, line1: 60, line2: 52, line2MarginTop: 12,
    // Thumbnail bumped up from 52 so the card it names stays
    // identifiable at actual phone feed size, not just full-res.
    supportMarginTop: 34, thumb: 64, thumbRadius: 8, bestTradeText: 34, completedTrades: 22, completedMarginTop: 16,
    pill: 15,
  },
  wide: {
    paddingX: 40,
    padding: "30px 40px",
    brandIcon: 20, brandText: 14,
    period: 18,
    heroLabel: 13, hero: 94, heroMarginTop: 8,
    recordMarginTop: 16, line1: 27, line2: 23, line2MarginTop: 4,
    supportMarginTop: 14, thumb: 36, thumbRadius: 6, bestTradeText: 16, completedTrades: 13, completedMarginTop: 5,
    pill: 11,
  },
};

export default function TradeSummaryCardArt({ data, format = "wide" }) {
  const { width, height } = DIMENSIONS[format] || DIMENSIONS.wide;
  const s = SIZES[format] || SIZES.wide;
  const profit = data.total_profit ?? 0;
  const isProfit = profit >= 0;
  const resultColor = isProfit ? ACCENT : LOSS;
  const best = data.best_sale;
  // Compact, not the full integer - an all-time total summed across a
  // power user's whole history can run to 8-9 digits; scaledFontSize
  // below is a backstop for the rare extreme case, not the primary
  // defence against clipping the canvas.
  const heroText = `${isProfit ? "+" : ""}${formatCoinsCompact(profit)}`;
  const heroFontSize = scaledFontSize(s.hero, heroText, format === "portrait" ? 9 : 6);
  const bestTradeMaxWidth = width - s.paddingX * 2;

  return (
    <div
      data-trade-summary-export
      style={{
        position: "relative",
        width,
        height,
        background: INK,
        color: "#f5f5f4",
        fontFamily: "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif",
        overflow: "hidden",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: s.padding,
      }}
    >
      <Texture />

      <div style={{ position: "relative", width: "100%" }}>
        <Brand iconSize={s.brandIcon} textSize={s.brandText} />
      </div>

      <div style={{ position: "relative", width: "100%", margin: "auto 0", textAlign: "center" }}>
        <div style={{ fontSize: s.period, fontWeight: 800, letterSpacing: "0.16em", color: ACCENT, textTransform: "uppercase" }}>
          {data.period_label}
        </div>

        <div style={{ marginTop: s.heroMarginTop }}>
          <div style={{ fontSize: heroFontSize, fontWeight: 900, lineHeight: 0.94, letterSpacing: "-0.03em", color: resultColor }}>{heroText}</div>
          <div style={{ marginTop: s.heroLabel * 0.35, fontSize: s.heroLabel, fontWeight: 800, letterSpacing: "0.22em", color: MUTED, textTransform: "uppercase" }}>
            Realized Profit
          </div>
        </div>

        <div style={{ marginTop: s.recordMarginTop }}>
          <div style={{ fontSize: s.line1, fontWeight: 800, letterSpacing: "-0.005em", color: "#f5f5f4" }}>
            {data.wins} Wins &middot; {data.losses} Losses
          </div>
          <div style={{ marginTop: s.line2MarginTop, fontSize: s.line2, fontWeight: 800, letterSpacing: "-0.005em", color: "#d8dade" }}>
            {data.win_rate}% Win Rate
          </div>
        </div>

        <div style={{ marginTop: s.supportMarginTop }}>
          {best ? (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: s.thumb * 0.3, maxWidth: bestTradeMaxWidth, margin: "0 auto" }}>
              <BestTradeThumb src={best.image_url} s={s} />
              <div
                style={{
                  fontSize: scaledFontSize(s.bestTradeText, `Best trade: ${best.player} +${formatCoinsCompact(best.profit)}`, 24),
                  fontWeight: 700,
                  color: "#c7c9cf",
                  minWidth: 0,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                Best trade: <b style={{ color: ACCENT, fontWeight: 800 }}>{best.player} {best.profit >= 0 ? "+" : ""}{formatCoinsCompact(best.profit)}</b>
              </div>
            </div>
          ) : null}
          <div style={{ marginTop: s.completedMarginTop, fontSize: s.completedTrades, fontWeight: 600, color: "#7d7f85", letterSpacing: "0.01em" }}>
            {data.closed_trades} completed trades
          </div>
        </div>
      </div>

      <div style={{ position: "relative", width: "100%", display: "flex", justifyContent: "center" }}>
        <AttributionPill size={s.pill} />
      </div>
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

function BestTradeThumb({ src, s }) {
  // Same fallback-on-failure treatment as TradeShareCardArt's
  // CardFrame: a remote image_url that 404s or CORS-fails at render
  // time must not sit there as a blank bordered box - it degrades to
  // the same placeholder a missing URL already uses.
  const [failed, setFailed] = useState(false);
  useEffect(() => { setFailed(false); }, [src]);
  const resolvedSrc = !src || failed ? PLACEHOLDER_CARD_ART : src;
  return (
    <img
      data-trade-summary-thumb
      src={resolvedSrc}
      alt=""
      onError={() => setFailed(true)}
      style={{ width: s.thumb, height: s.thumb * 1.27, borderRadius: s.thumbRadius, objectFit: "cover", border: "1px solid rgba(255,255,255,0.14)", opacity: 0.92, flexShrink: 0 }}
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
