// src/v2/pages/SbcEventDetail/sections/ChallengeBreakdownSection.jsx
import { ExternalLink, RefreshCw } from "lucide-react";
import ContentPlayerStrip from "../../../components/ContentPlayerStrip";
import { formatCoins, formatCountdown } from "../../../lib/format";

// sbc_details' reward_card_* fields are already flat on the details
// object (get_event()'s LEFT JOIN aliases them directly) - reshape into
// CardArtThumb's expected shape, same as SbcListSection.jsx.
export default function ChallengeBreakdownSection({ event }) {
  if (!event) return null;
  const details = event.sbc_details;
  const challenges = event.sbc_challenges || [];
  return <>
    <header className="content-detail-hero"><div><span className="content-kicker">{details?.category || "SBC"}</span><h1>{event.title}</h1><p>{event.description || details?.reward_description || "Live FC27 Squad Building Challenge."}</p>{details?.repeatable ? <small><RefreshCw size={13}/> Repeatable{details.repeat_count ? ` ×${details.repeat_count}` : ""}</small> : null}</div>{details?.image_url ? <img className="sbc-detail-art" src={details.image_url} alt="" /> : null}</header>
    <section className="content-stats"><div><span>Console cost</span><strong>{formatCoins(details?.total_cost_coins)}</strong></div><div><span>PC cost</span><strong>{formatCoins(details?.total_cost_pc)}</strong></div><div><span>Expires</span><strong>{event.ends_at ? formatCountdown(event.ends_at) : "—"}</strong></div><div><span>Challenges</span><strong>{challenges.length}</strong></div></section>
    <section className="content-panel"><div className="content-section-head"><div><span>Verified solutions</span><h2>Challenge breakdown</h2></div><p>{details?.reward_description || "Cheapest tracked solutions from FUT.GG."}</p></div>
      {!challenges.length ? <div className="content-empty">No challenge breakdown is available yet.</div> : <div className="challenge-list">{challenges.map((challenge, index) => {
        const consoleItems = (challenge.solution_items || []).filter((item) => item.platform === "console");
        const pcItems = (challenge.solution_items || []).filter((item) => item.platform === "pc");
        return <article className="challenge-card" key={challenge.id}><header><div><span>Challenge {index + 1}</span><h3>{challenge.challenge_name}</h3></div><div><span>Console</span><strong>{formatCoins(challenge.estimated_cost_coins)}</strong><small>PC {formatCoins(challenge.estimated_cost_pc)}</small></div></header>
          {challenge.requirements && Object.keys(challenge.requirements).length ? <div className="content-tags">{Object.entries(challenge.requirements).map(([key, value]) => <span key={key}>{String(value).replace(/_/g, " ")}</span>)}</div> : null}
          {consoleItems.length ? <><div className="solution-label"><span>Console solution</span>{challenge.solution_console_url ? <a href={challenge.solution_console_url} target="_blank" rel="noreferrer">Open squad <ExternalLink size={12}/></a> : null}</div><ContentPlayerStrip items={consoleItems} /></> : null}
          {pcItems.length ? <><div className="solution-label"><span>PC solution</span>{challenge.solution_pc_url ? <a href={challenge.solution_pc_url} target="_blank" rel="noreferrer">Open squad <ExternalLink size={12}/></a> : null}</div><ContentPlayerStrip items={pcItems} /></> : null}
        </article>;
      })}</div>}
    </section>
  </>;
}
