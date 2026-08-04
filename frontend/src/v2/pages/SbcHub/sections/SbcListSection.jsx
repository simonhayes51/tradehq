// src/v2/pages/SbcHub/sections/SbcListSection.jsx
import { Link } from "react-router-dom";
import SectionCard from "../../../components/SectionCard";
import CardArtThumb from "../../../components/CardArtThumb";
import { formatCoins, formatCountdown } from "../../../lib/format";

// get_events() prefixes the reward card's fields with reward_card_* to
// disambiguate from the event's own columns - reshape into CardArtThumb's
// expected {name, rating, version, image_url, card_bg_image, ...} shape.
// Many SBCs (upgrades, non-reward types) legitimately have no reward
// card at all - that's a real "nothing to show" case, distinct from "art
// not backfilled yet" (which CardArtThumb's own fallbackImage handles).
function rewardCard(ev) {
  if (!ev.reward_card_name) return null;
  return {
    name: ev.reward_card_name,
    rating: ev.reward_card_rating,
    version: ev.reward_card_version,
    image_url: ev.reward_card_image_url,
    card_bg_image: ev.reward_card_bg_image,
    card_cutout_image: ev.reward_card_cutout_image,
    card_cutout_type: ev.reward_card_cutout_type,
    card_name: ev.reward_card_card_name,
  };
}

export default function SbcListSection({ events }) {
  if (!events || events.length === 0) {
    return (
      <SectionCard title="Live SBCs">
        <p className="text-xs text-[var(--v2-muted)]">No SBC sets tracked yet.</p>
      </SectionCard>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {events.map((ev) => {
        const reward = rewardCard(ev);
        return (
          <Link
            key={ev.id}
            to={`/v2/sbc/${ev.id}`}
            className="rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-card)] p-4 flex flex-col gap-2 hover:border-[var(--v2-accent)] transition-colors"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                {reward && <CardArtThumb card={reward} widthClass="w-9" />}
                <span className="text-sm font-semibold text-[var(--v2-text)] truncate">{ev.title}</span>
              </div>
              {ev.category && (
                <span className="text-[10px] uppercase tracking-wide text-[var(--v2-accent)] whitespace-nowrap">
                  {ev.category}
                </span>
              )}
            </div>
            <div className="flex items-center justify-between text-xs text-[var(--v2-muted)]">
              <span>{ev.total_cost_coins ? formatCoins(ev.total_cost_coins) : "—"}</span>
              <span>{ev.ends_at ? `ends in ${formatCountdown(ev.ends_at)}` : "—"}</span>
            </div>
          </Link>
        );
      })}
    </div>
  );
}
