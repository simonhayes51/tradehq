import { ArrowLeft, ExternalLink } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useGallerySet } from "../../hooks/useGallery";
import { formatCoins } from "../../lib/format";
import ContentPlayerStrip from "../../components/ContentPlayerStrip";
import "../content-intelligence.css";

export default function GallerySetDetail() {
  const { setId } = useParams();
  const { data: set, isLoading, error } = useGallerySet(setId);
  if (isLoading) return <main className="content-page"><div className="content-loading">Loading Gallery solution…</div></main>;
  if (error || !set) return <main className="content-page"><div className="content-error">This Gallery set could not be loaded.</div></main>;
  const recommendation = set.recommendation || { grade: set.selected_grade || set.best_grade, item_count: set.items?.length || 0, items: set.items || [] };
  const tiers = set.tiers?.length ? set.tiers : [{ grade: recommendation.grade, cost: set.total_cost_coins, tokens: set.max_tokens, score: set.total_score }];
  const activeTags = (set.solution_payload?.tags || set.solution_payload?.activeTags || []).map(String);
  const tags = (set.tags || []).filter((tag) => activeTags.includes(String(tag.tag_id)) || activeTags.includes(tag.name));
  return <main className="content-page">
    <div className="content-detail-nav"><Link to="/gallery"><ArrowLeft size={15}/> Gallery</Link><a href="https://www.fut.gg/fut-gallery/" target="_blank" rel="noreferrer">FUT.GG source <ExternalLink size={14}/></a></div>
    <header className="content-detail-hero"><div><span className="content-kicker">{set.category_name || "FUT GALLERY"}</span><h1>{set.name}</h1><p>FUT.GG's published recommendation is shown once. Grade targets below are cost/reward estimates, not separate card lists.</p></div><span className={`gallery-grade gallery-grade-large grade-${(recommendation.grade || "d").toLowerCase()}`}>{recommendation.grade || "—"}</span></header>
    <section className="content-stats"><div><span>Recommendation cost</span><strong>{recommendation.cost != null ? formatCoins(recommendation.cost) : formatCoins(set.total_cost_coins)}</strong></div><div><span>Max tokens</span><strong>{set.max_tokens ?? "—"}</strong></div><div><span>Recommended cards</span><strong>{recommendation.item_count || "—"}</strong></div><div><span>Recommendation score</span><strong>{recommendation.score?.toLocaleString?.() ?? set.total_score?.toLocaleString?.() ?? "—"}</strong></div></section>
    {tags.length ? <section className="content-panel"><div className="content-section-head"><div><span>Active bonuses</span><h2>Collection conditions</h2></div></div><div className="content-tags">{tags.map((tag) => <span key={tag.tag_id}>{tag.name}</span>)}</div></section> : null}
    <section className="content-panel">
      <div className="content-section-head"><div><span>Grade targets</span><h2>Cost versus reward</h2></div><p>Targets are FUT.GG estimates. The card grid is only attached to the published recommendation grade.</p></div>
      <div className="content-targets">{tiers.map((tier) => <div className={tier.is_recommendation ? "content-target active" : "content-target"} key={tier.grade || tier.cost}><span>Grade {tier.grade || "—"}</span><strong>{tier.cost != null ? formatCoins(tier.cost) : "—"}</strong><small>{tier.tokens ?? "—"} tokens{tier.is_recommendation ? " · shown below" : ""}</small></div>)}</div>
      <div className="content-section-head"><div><span>FUT.GG recommendation · Grade {recommendation.grade || "—"}</span><h2>{recommendation.item_count || 0} card collection</h2></div><p>Different versions of the same player are separate cards and may be used for Multiples bonuses.</p></div>
      {recommendation.items?.length ? <ContentPlayerStrip items={recommendation.items} metric={(p) => formatCoins(p.price ?? p.current_bin)} /> : <div className="content-empty">FUT.GG has not published a card recommendation for this set.</div>}
    </section>
  </main>;
}
