// src/v2/pages/SbcEventDetail/sections/ChallengeBreakdownSection.jsx
import SectionCard from "../../../components/SectionCard";
import CardArtThumb from "../../../components/CardArtThumb";
import { formatCoins, formatCountdown } from "../../../lib/format";

// sbc_details' reward_card_* fields are already flat on the details
// object (get_event()'s LEFT JOIN aliases them directly) - reshape into
// CardArtThumb's expected shape, same as SbcListSection.jsx.
function rewardCard(details) {
  if (!details?.reward_card_name) return null;
  return {
    name: details.reward_card_name,
    rating: details.reward_card_rating,
    version: details.reward_card_version,
    image_url: details.reward_card_image_url,
    card_bg_image: details.reward_card_bg_image,
    card_cutout_image: details.reward_card_cutout_image,
    card_cutout_type: details.reward_card_cutout_type,
    card_name: details.reward_card_card_name,
  };
}

export default function ChallengeBreakdownSection({ event }) {
  if (!event) return null;
  const details = event.sbc_details;
  const challenges = event.sbc_challenges || [];
  const reward = rewardCard(details);

  return (
    <SectionCard title={event.title} subtitle={details?.category}>
      <div className="flex items-start gap-4 mb-4">
        {reward && <CardArtThumb card={reward} widthClass="w-20" />}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 flex-1">
          <div>
            <span className="text-xs text-[var(--v2-muted)]">Total cost</span>
            <p className="text-lg font-semibold">{formatCoins(details?.total_cost_coins)}</p>
          </div>
          <div>
            <span className="text-xs text-[var(--v2-muted)]">Repeatable</span>
            <p className="text-lg font-semibold">{details?.repeatable ? "Yes" : "No"}</p>
          </div>
          <div>
            <span className="text-xs text-[var(--v2-muted)]">Expires</span>
            <p className="text-lg font-semibold">
              {event.ends_at ? formatCountdown(event.ends_at) : "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-[var(--v2-muted)]">Reward</span>
            <p className="text-lg font-semibold">{details?.reward_description || "—"}</p>
          </div>
        </div>
      </div>

      {challenges.length === 0 ? (
        <p className="text-xs text-[var(--v2-muted)]">No challenge breakdown available.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-[var(--v2-border)]">
          {challenges.map((c) => (
            <li key={c.id} className="py-2">
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium">{c.challenge_name}</span>
                <span className="text-[var(--v2-muted)]">{formatCoins(c.estimated_cost_coins)}</span>
              </div>
              {c.requirements && Object.keys(c.requirements).length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-1.5">
                  {Object.entries(c.requirements).map(([k, v]) => (
                    <span
                      key={k}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--v2-elevated)] text-[var(--v2-muted)]"
                    >
                      {k.replace(/_/g, " ")}: {String(v)}
                    </span>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
