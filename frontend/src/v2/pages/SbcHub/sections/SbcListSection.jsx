// src/v2/pages/SbcHub/sections/SbcListSection.jsx
import { Link } from "react-router-dom";
import { ArrowRight, RefreshCw } from "lucide-react";
import { formatCoins, formatCountdown } from "../../../lib/format";

// get_events() prefixes the reward card's fields with reward_card_* to
// disambiguate from the event's own columns - reshape into CardArtThumb's
// expected {name, rating, version, image_url, card_bg_image, ...} shape.
// Many SBCs (upgrades, non-reward types) legitimately have no reward
// card at all - that's a real "nothing to show" case, distinct from "art
// not backfilled yet" (which CardArtThumb's own fallbackImage handles).
export default function SbcListSection({ events = [] }) {
  if (!events.length) return <div className="content-empty">No SBC sets match these filters.</div>;
  return <div className="content-card-grid">{events.map((event) => <Link className="content-set-card" to={`/sbc/${event.id}`} key={event.id}>
    <div className="content-set-art">{event.image_url ? <img src={event.image_url} alt="" loading="lazy" /> : <span>SBC</span>}</div>
    <div className="content-set-body"><div className="content-set-meta"><span>{event.category || "SBC"}</span><b>{event.ends_at ? `${formatCountdown(event.ends_at)} left` : "No expiry"}</b></div><h3>{event.title}</h3><p>{event.challenge_count || 0} challenge{event.challenge_count === 1 ? "" : "s"} · {event.solution_item_count || 0} solution cards</p><div className="content-price-row"><div><span>Console</span><strong>{formatCoins(event.total_cost_coins)}</strong></div><div><span>PC</span><strong>{formatCoins(event.total_cost_pc)}</strong></div><ArrowRight size={18}/></div>{event.repeatable ? <small><RefreshCw size={12}/> Repeatable{event.repeat_count ? ` ×${event.repeat_count}` : ""}</small> : null}</div>
  </Link>)}</div>;
}
