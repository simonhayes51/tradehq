import { useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Shield } from "lucide-react";
import { addWatch } from "../../../api/watchlist";
import { useFutggPlayer } from "../../hooks/useFutggMarket";
import { AnalysisModal } from "../HomeDashboard/HomeDashboard";
import FutggMarketSection from "./sections/FutggMarketSection";

export default function PlayerPage() {
  const { cardId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  // Single source: the FUT.GG-backed GET /api/v2/players/{cardId}.
  // Every /v2/players/:cardId link (Watchlist, Trade Finder,
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
  const returnPath = origin === "trade-finder" ? "/v2/trade-finder" : "/v2/players";
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
      else if (requestError?.response?.status === 401) navigate("/login");
      else setWatchState("error");
    }
  }

  if (!item && settled) {
    return <div className="quick-analysis-page"><div className="quick-error"><Shield size={26}/><h1>Could not load this card</h1><p>{futggQuery.error?.response?.status === 404 ? "This player could not be found." : "The analysis request failed."}</p><div><button onClick={goBack}>{backLabel}</button><button onClick={() => futggQuery.refetch()}>Try again</button></div></div></div>;
  }
  if (!item) return <div className="quick-analysis-page"><div className="quick-error"><p>Loading the latest analysis…</p><button onClick={goBack}>{backLabel}</button></div></div>;

  return <div className="quick-analysis-page">
    {/* FutggMarketSection adds the BIN history and completed-sales
        detail beneath the headline verdict; both now read the same
        FUT.GG source, so the two can no longer disagree. */}
    <AnalysisModal item={item} onClose={goBack} navigate={navigate} onWatch={watch} watchState={watchState} backLabel={backLabel} extra={<FutggMarketSection cardId={cardId} />}/>
  </div>;
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
    entryPrice: raw.recommended_buy_max ?? raw.current_bin,
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
    reasoning: reasons[0] || "FUT.GG market signal",
    updatedAt: raw.current_bin_captured_at,
    priceAgeSeconds: raw.price_age_seconds,
    player: {
      name: raw.name,
      displayName: raw.name,
      cardName: raw.name,
      nickname: null,
      rating: raw.rating,
      position: raw.position,
      version: raw.rarity || "Card",
      imageUrl: raw.image_url,
      generatedCardUrl: null,
      cardBgImage: null,
      cardCutoutImage: null,
      cardCutoutType: null,
      nationImage: null,
      leagueImage: null,
      clubImage: null,
      stats: {},
    },
  };
}
