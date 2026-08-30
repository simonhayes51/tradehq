import { useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Shield } from "lucide-react";
import { addWatch } from "../../../api/watchlist";
import { useFutggPlayer } from "../../hooks/useFutggMarket";
import { AnalysisModal } from "../HomeDashboard/HomeDashboard";
import FutggMarketSection from "./sections/FutggMarketSection";
import PublicCardPreview from "./PublicCardPreview";
import { versionLabel } from "../../lib/format";

export default function PlayerPage() {
  const { cardId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  // Single source: the FUT.GG-backed GET /api/v2/players/{cardId}.
  // Every /players/:cardId link (Watchlist, Trade Finder,
  // Opportunities, Market, SBC impact) passes a source_card_id, which is
  // exactly what this endpoint keys on.
  const futggQuery = useFutggPlayer(cardId);
  const [watchState, setWatchState] = useState("");
  const item = useMemo(
    // FUT.GG is the ONLY source. The legacy FUTBIN /summary path is gone:
    // it was not merely stale but actively wrong - it served Carles Puyol
    // as 337,000 with an AVOID verdict while FUT.GG, the watchlist and
    // the section below all said 11,250. fair_value_mv, which fed it, is
    // broken on the current player database.
    //
    // Nothing falls back to it any more. A card with no FUT.GG row shows
    // an honest empty state rather than a number from a dead pipeline.
    () => toFutggAnalysisItem(futggQuery.data, cardId),
    [futggQuery.data, cardId],
  );
  const settled = !futggQuery.isLoading;
  const origin = location.state?.from;
  const returnPath = origin === "trade-finder" ? "/trade-finder" : "/players";
  const backLabel = origin === "trade-finder" ? "Back to Trade Finder" : "Back to player search";

  function goBack() {
    navigate(returnPath);
  }

  async function watch(current) {
    if (watchState === "saving" || watchState === "saved") return;
    setWatchState("saving");
    try {
      await addWatch({
        player_name: current.player.cardName || current.player.name,
        card_id: String(current.cardId),
        version: current.player.version || null,
        platform: "ps",
        source: current.source === "futgg" ? "futgg" : "futbin",
      });
      setWatchState("saved");
    } catch (requestError) {
      if (requestError?.response?.status === 409) setWatchState("saved");
      // A shared player-page link is the acquisition loop: a friend
      // clicks Watch while signed out, signs up, and should land back on
      // this exact card, not Home - see AuthContext.login's returnTo.
      else if (requestError?.response?.status === 401) navigate(`/signup?from=${encodeURIComponent(`/players/${cardId}`)}`);
      else setWatchState("error");
    }
  }

  // `requires_account` is the server's own answer (app/auth/access.py's
  // public_card_preview), not something inferred from the auth context:
  // the payload and the page must agree about what was actually sent, and
  // the payload is the one that is right. A 401 from the detail endpoint
  // means the same thing.
  const needsAccount =
    futggQuery.data?.requires_account === true
    || futggQuery.error?.response?.status === 401;

  if (needsAccount) {
    return (
      <PublicCardPreview
        card={futggQuery.data || {}}
        cardId={cardId}
        onBack={goBack}
        backLabel={backLabel}
      />
    );
  }

  if (!item && settled) {
    return <div className="quick-analysis-page"><div className="quick-error"><Shield size={26}/><h1>Could not load this card</h1><p>{futggQuery.error?.response?.status === 404 ? "This player could not be found." : "The analysis request failed."}</p><div><button onClick={goBack}>{backLabel}</button><button onClick={() => futggQuery.refetch()}>Try again</button></div></div></div>;
  }
  if (!item) return <div className="quick-analysis-page"><div className="quick-error"><p>Loading the latest analysis…</p><button onClick={goBack}>{backLabel}</button></div></div>;

  {/* variant="page": this route is a standalone page (own URL, own back
      button, indefinitely long FutggMarketSection content below), not a
      click-triggered overlay - see AnalysisModal's own comment for why
      the default fixed/centred/self-scrolling modal chrome broke here
      (card art rendering behind the sidebar / far down the page). */}
  return <AnalysisModal item={item} onClose={goBack} navigate={navigate} onWatch={watch} watchState={watchState} backLabel={backLabel} variant="page" extra={<FutggMarketSection cardId={cardId} />}/>;
}

// Maps the engine's signal vocabulary onto the verdict the shared
// AnalysisModal renders. Restored after being removed by accident: it sat
// between toAnalysisItem and toFutggAnalysisItem, and deleting the former
// took this with it. The result was a ReferenceError thrown only when a
// card actually rendered - so the build passed, and every player page
// died with "Something went wrong".
const _FUTGG_SIGNAL_RECOMMENDATION = {
  strong_buy: "BUY",
  buy: "BUY",
  avoid: "AVOID",
  hold: "WAIT",
  watch: "WAIT",
  insufficient_data: "WAIT",
};

function toFutggAnalysisItem(raw, cardId) {
  if (!raw) return null;
  const expectedRoi = raw.expected_roi == null ? null : Number(raw.expected_roi) * 100;
  const risk = raw.risk_level ? raw.risk_level.charAt(0).toUpperCase() + raw.risk_level.slice(1) : "Unknown";
  const reasons = Array.isArray(raw.signal_reasons) ? raw.signal_reasons : [];
  return {
    cardId,
    source: "futgg",
    recommendation: _FUTGG_SIGNAL_RECOMMENDATION[raw.signal] || "WAIT",
    // Deliberately NOT `?? raw.current_bin`. entryPrice is meant to be a
    // genuine recommended-entry price - on insufficient_data the engine
    // returns recommended_buy_max: null (there is no such price), and
    // falling back to current_bin here silently turned "we don't have a
    // real target" into "the target IS whatever it costs right now",
    // which is exactly the "BUY BELOW 895,000 beside a blank profit"
    // bug AnalysisModal's `ceiling` was already fixed against (see its
    // own comment) - just reintroduced through this separate field,
    // which fed the "Wait for a better entry below X" verdict text and
    // the chart's BUY reference line directly, bypassing that fix.
    entryPrice: raw.recommended_buy_max ?? null,
    // Kept for AdvancedRationale (Layer 2), which reads the raw FUT.GG
    // field names directly rather than duplicating them onto this shape -
    // see HomeDashboard.jsx's normaliseFutgg for the same pattern.
    raw,
    liquidityScore: raw.liquidity_score,
    trendDescription: raw.trend_description,
    // AnalysisModal's "Buy Below" TILE reads item.buyBelow/
    // item.recommendedBuyMax/item.expectedProfitBasis (its own `ceiling`/
    // `basis` computation), a SEPARATE field path from entryPrice above
    // (which only drives the verdict TEXT via realEntry). Leaving these
    // three unset left the tile permanently blank ("Not enough data to
    // set one") even when entryPrice/the verdict text had a real number -
    // seen live on a 96 Haaland: "Wait for a better entry below 655,000"
    // directly beside a blank Buy Below tile. Mirrors the backend's own
    // field names 1:1 (see futgg_market.py's _serialize_intelligence) so
    // the tile and the verdict text always agree.
    buyBelow: raw.buy_below ?? null,
    recommendedBuyMax: raw.recommended_buy_max ?? null,
    expectedProfitBasis: raw.expected_profit_basis ?? null,
    currentBin: raw.current_bin,
    fairValue: raw.fair_value,
    expectedRoi,
    netRoi: { likely: expectedRoi },
    recommendedSellTarget: raw.recommended_sell_target,
    expectedProfitAfterTax: raw.expected_profit_after_tax,
    confidence: raw.confidence_score == null ? null : Number(raw.confidence_score) * 100,
    popularity: null,
    psPcBinGapPct: null,
    risk,
    holdingPeriod: "Flexible",
    reasoning: reasons[0] || "Market signal",
    updatedAt: raw.current_bin_captured_at,
    priceAgeSeconds: raw.price_age_seconds,
    player: {
      name: raw.name,
      displayName: raw.name,
      cardName: raw.name,
      nickname: null,
      rating: raw.rating,
      position: raw.position || raw.primary_position,
      version: versionLabel(raw.rarity),
      imageUrl: raw.image_url,
      // composited_image_url (FUT.GG's own pre-composited full-card
      // image, mirrored to R2) always wins when present - shown as a
      // plain <img>, never handed to PlayerCardArt's bgImage/cutoutImage
      // compositing, which draws its own rating/position/stats text on
      // top and would double it up since this image already has it baked
      // in. See PlayerCardImage.jsx's own docstring for the same rule.
      generatedCardUrl: raw.composited_image_url || (raw.card_design_image_url ? null : (raw.image_url || raw.player_image_url || null)),
      cardBgImage: raw.card_design_image_url || null,
      cardCutoutImage: raw.image_url || raw.player_image_url || null,
      cardCutoutType: raw.price_tier && raw.price_tier !== "special" ? "base" : "special",
      nationImage: raw.nation_image_url || null,
      leagueImage: raw.league_image_url || null,
      clubImage: raw.club_image_url || null,
      stats: {
        pace: raw.pace,
        shooting: raw.shooting,
        passing: raw.passing,
        dribbling: raw.dribbling,
        defending: raw.defending,
        physicality: raw.physicality,
      },
    },
  };
}
