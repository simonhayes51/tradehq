// src/components/PlayerCardImage.jsx
//
// Single place that decides whether to show a fully-rendered card image
// as a plain <img> or fall back to PlayerCardArt's own bgImage+cutoutImage
// compositing (which draws its own rating/position/stats text on top -
// never hand it an image that already has that baked in, see
// composited_image_url below).
//
// Two independent "fully rendered, ready to show as-is" sources, checked
// in this priority order:
//   1. generated_card_url/generatedCardUrl - our own bespoke Playwright-
//      rendered PNG (app/services/player_card_generation.py, fut_players-
//      backed), gated on generated_card_status === 'ready' and not
//      generated_card_flagged.
//   2. composited_image_url/compositedImageUrl - FUT.GG's own pre-
//      composited full-card image, mirrored to our R2 storage
//      (app/services/futgg_card_image_mirror.py, futgg_players-backed).
//      No status/flagged gating - the backend only ever populates this
//      once mirrored successfully.
// Only when NEITHER is available does this fall back to PlayerCardArt's
// bgImage/cutoutImage/fallbackImage compositing.
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

  const generatedReady = status === "ready" && !!url && !flagged;
  const compositedUrl = pick(p.composited_image_url, p.compositedImageUrl) ?? null;
  const readyUrl = generatedReady ? url : compositedUrl;
  const isReady = !!readyUrl;
  const displayName = pick(p.card_name, p.cardName, p.name);

  if (isReady) {
    return (
      <img
        className={imgClassName || `${widthClass} h-auto object-contain block`}
        src={readyUrl}
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
