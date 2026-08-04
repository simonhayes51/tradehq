// src/components/PlayerCardImage.jsx
//
// Single place that decides whether to show the on-demand generated card
// PNG or the composited PlayerCardArt fallback. A card is only "ready to
// show as the generated image" when generated_card_status === 'ready' AND
// generated_card_url is present AND generated_card_flagged is not true -
// any other combination (null/'generating'/'error', or flagged even with a
// URL present) shows the existing fallback so we never display a stale or
// known-wrong generated image.
//
// `player` accepts whatever shape callers already have on hand - the
// snake_case fields the backend sends (card_bg_image, generated_card_url,
// ...) or the camelCase-normalised equivalents some v2 pages already use
// (cardBgImage, generatedCardUrl, ...); both are checked.
import PlayerCardArt from "./PlayerCardArt";
import { usePlayerCardStatus } from "../v2/hooks/usePlayerCardStatus";

function pick(...values) {
  for (const v of values) {
    if (v !== undefined && v !== null) return v;
  }
  return undefined;
}

export default function PlayerCardImage({
  player,
  enablePolling = false,
  imgClassName,
  widthClass = "w-48",
  compact = false,
  showStats = true,
  versionLabel,
  altText,
}) {
  const p = player || {};
  const cardId = pick(p.card_id, p.cardId, p.id);

  const initialStatus = pick(p.generated_card_status, p.generatedCardStatus) ?? null;
  const initialUrl = pick(p.generated_card_url, p.generatedCardUrl) ?? null;
  const initialFlagged = Boolean(pick(p.generated_card_flagged, p.generatedCardFlagged, false));

  const initiallyReady = initialStatus === "ready" && !!initialUrl && !initialFlagged;
  const pollingEnabled = Boolean(enablePolling) && cardId != null && !initiallyReady;

  const polled = usePlayerCardStatus({
    cardId,
    initialStatus,
    enabled: pollingEnabled,
  });

  const status = pollingEnabled ? polled.status : initialStatus;
  const url = pollingEnabled ? polled.generatedCardUrl : initialUrl;
  const flagged = pollingEnabled ? polled.flagged : initialFlagged;

  const isReady = status === "ready" && !!url && !flagged;
  const displayName = pick(p.card_name, p.cardName, p.name);

  if (isReady) {
    return (
      <img
        className={imgClassName || `${widthClass} h-auto object-contain block`}
        src={url}
        alt={altText || displayName || "Player"}
        referrerPolicy="no-referrer"
      />
    );
  }

  return (
    <PlayerCardArt
      bgImage={pick(p.card_bg_image, p.cardBgImage, p.bgImage)}
      cutoutImage={pick(p.card_cutout_image, p.cardCutoutImage, p.cutoutImage)}
      cutoutType={pick(p.card_cutout_type, p.cardCutoutType, p.cutoutType, "special")}
      fallbackImage={pick(p.image_url, p.imageUrl, p.fallbackImage)}
      rating={p.rating}
      position={p.position}
      name={displayName}
      altText={altText || pick(p.name, displayName)}
      stats={
        p.stats || {
          pace: p.pace,
          shooting: p.shooting,
          passing: p.passing,
          dribbling: p.dribbling,
          defending: p.defending,
          physicality: p.physicality,
        }
      }
      nationImage={pick(p.nation_image, p.nationImage)}
      leagueImage={pick(p.league_image, p.leagueImage)}
      clubImage={pick(p.club_image, p.clubImage)}
      versionLabel={versionLabel ?? p.version}
      widthClass={widthClass}
      compact={compact}
      showStats={showStats}
    />
  );
}
