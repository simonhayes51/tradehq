// src/v2/components/CardArtThumb.jsx
//
// Thin wrapper around the shared PlayerCardImage, sized + configured for
// v2 list-row use. `card` is any of the row shapes coming out of the
// backend's card-art-extended endpoints
// ({card_id, name, rating, version, image_url, card_bg_image,
//   card_cutout_image, card_cutout_type, card_name, generated_card_url,
//   generated_card_status, generated_card_flagged}), so every v2 list
// section can pass its item straight through with no reshaping.
//
// Polling is intentionally off here - these render in list rows (SBC hub/
// detail, etc.) many at once, and opening a concurrent poll interval per
// row isn't worth it. Rows just show the fallback until the surrounding
// list naturally refetches (query invalidation, refocus, next page load).
import PlayerCardImage from "../../components/PlayerCardImage";

export default function CardArtThumb({ card, widthClass = "w-10" }) {
  if (!card) return null;
  return (
    <PlayerCardImage
      player={card}
      enablePolling={false}
      compact
      altText={card.card_name || card.name}
      widthClass={widthClass}
    />
  );
}
