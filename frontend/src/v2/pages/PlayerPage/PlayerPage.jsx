import { useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Shield } from "lucide-react";
import { addWatch } from "../../../api/watchlist";
import { useFutggPlayer } from "../../hooks/useFutggMarket";
import { AnalysisModal } from "../HomeDashboard/HomeDashboard";
import ExitEvidence from "./sections/ExitEvidence";
import FutggMarketSection from "./sections/FutggMarketSection";
import PublicCardPreview from "./PublicCardPreview";
import { versionLabel } from "../../lib/format";

export default function PlayerPage() {
  const { cardId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const futggQuery = useFutggPlayer(cardId);
  const [watchState, setWatchState] = useState("");
  const item = useMemo(() => toFutggAnalysisItem(futggQuery.data, cardId), [futggQuery.data, cardId]);
  const settled = !futggQuery.isLoading;
  const origin = location.state?.from;
  const returnPath = origin === "trade-finder" ? "/trade-finder" : "/players";
  const fromDiscordFeed = new URLSearchParams(location.search).get("src") === "discord_feed";
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
      else if (requestError?.response?.status === 401) navigate(`/signup?from=${encodeURIComponent(`/players/${cardId}`)}`);
      // A quota 402 has already raised the shell-level LimitReachedPrompt.
      // Reset the button instead of turning a deliberate Free-plan limit
      // into a generic failed-save state underneath that prompt.
      else if (requestError?.response?.data?.detail?.error === "limit_reached") setWatchState("");
      else setWatchState("error");
    }
  }

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
        fromDiscordFeed={fromDiscordFeed}
      />
    );
  }

  if (!item && settled) {
    return <div className="quick-analysis-page"><div className="quick-error"><Shield size={26}/><h1>Could not load this card</h1><p>{futggQuery.error?.response?.status === 404 ? "This player could not be found." : "The analysis request failed."}</p><div><button onClick={goBack}>{backLabel}</button><button onClick={() => futggQuery.refetch()}>Try again</button></div></div></div>;
  }
  if (!item) return <div className="quick-analysis-page"><div className="quick-error"><p>Loading the latest analysis…</p><button onClick={goBack}>{backLabel}</button></div></div>;

  return <AnalysisModal item={item} onClose={goBack} navigate={navigate} onWatch={watch} watchState={watchState} backLabel={backLabel} variant="page" extra={<><ExitEvidence evidence={futggQuery.data?.exit_quality}/><FutggMarketSection cardId={cardId}/></>}/>;
}

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
    entryPrice: raw.recommended_buy_max ?? null,
    raw,
    liquidityScore: raw.liquidity_score,
    trendDescription: raw.trend_description,
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
    valuationSource: raw.valuation_source,
    evidenceQuality: raw.evidence_quality,
    binSampleCount: raw.bin_sample_count,
    binHistorySpanHours: raw.bin_history_span_hours,
    player: {
      name: raw.name,
      displayName: raw.name,
      cardName: raw.name,
      nickname: null,
      rating: raw.rating,
      position: raw.position || raw.primary_position,
      version: versionLabel(raw.rarity),
      imageUrl: raw.image_url,
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
