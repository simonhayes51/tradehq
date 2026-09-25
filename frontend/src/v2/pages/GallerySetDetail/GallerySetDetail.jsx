import { useState } from "react";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useGallerySet } from "../../hooks/useGallery";
import { formatCoins } from "../../lib/format";
import ContentPlayerStrip from "../../components/ContentPlayerStrip";
import "../content-intelligence.css";

export default function GallerySetDetail() {
  const { setId } = useParams();
  const { data: set, isLoading, error } = useGallerySet(setId);
  const [selectedGrade, setSelectedGrade] = useState("");
  if (isLoading) return <main className="content-page"><div className="content-loading">Loading Gallery solution…</div></main>;
  if (error || !set) return <main className="content-page"><div className="content-error">This Gallery set could not be loaded.</div></main>;
  const tiers = set.tiers?.length ? set.tiers : [{ grade: set.selected_grade || set.best_grade, cost: set.total_cost_coins, tokens: set.max_tokens, score: set.total_score, items: set.items || [] }];
  const selectedTier = tiers.find((tier) => tier.grade === selectedGrade) || tiers[0];
  const activeTags = (set.solution_payload?.tags || set.solution_payload?.activeTags || []).map(String);
  const tags = (set.tags || []).filter((tag) => activeTags.includes(String(tag.tag_id)) || activeTags.includes(tag.name));
  return <main className="content-page">
    <div className="content-detail-nav"><Link to="/gallery"><ArrowLeft size={15}/> Gallery</Link><a href="https://www.fut.gg/fut-gallery/" target="_blank" rel="noreferrer">FUT.GG source <ExternalLink size={14}/></a></div>
    <header className="content-detail-hero"><div><span className="content-kicker">{set.category_name || "FUT GALLERY"}</span><h1>{set.name}</h1><p>Compare every FUT.GG solution tier, its cost and the cards required.</p></div><span className={`gallery-grade gallery-grade-large grade-${(selectedTier.grade || "d").toLowerCase()}`}>{selectedTier.grade || "—"}</span></header>
    <section className="content-stats"><div><span>Solution cost</span><strong>{formatCoins(selectedTier.cost)}</strong></div><div><span>Tokens</span><strong>{selectedTier.tokens ?? set.max_tokens ?? "—"}</strong></div><div><span>Required cards</span><strong>{selectedTier.items?.length ?? "—"}</strong></div><div><span>Score</span><strong>{selectedTier.score?.toLocaleString?.() ?? set.total_score?.toLocaleString?.() ?? "—"}</strong></div></section>
    {tags.length ? <section className="content-panel"><div className="content-section-head"><div><span>Active bonuses</span><h2>Collection conditions</h2></div></div><div className="content-tags">{tags.map((tag) => <span key={tag.tag_id}>{tag.name}</span>)}</div></section> : null}
    <section className="content-panel">
      <div className="content-section-head"><div><span>Solution tiers</span><h2>Choose cost versus reward</h2></div><p>Live market prices can move after the solution is captured.</p></div>
      <div className="content-chips">{tiers.map((tier) => <button type="button" className={selectedTier.grade === tier.grade ? "active" : ""} onClick={() => setSelectedGrade(tier.grade)} key={tier.grade || tier.cost}>Grade {tier.grade || "—"} · {formatCoins(tier.cost)} · {tier.items?.length || 0} cards</button>)}</div>
      <div className="content-section-head"><div><span>Grade {selectedTier.grade || "—"} solution</span><h2>{selectedTier.items?.length || 0} required cards</h2></div></div>
      {selectedTier.items?.length ? <ContentPlayerStrip items={selectedTier.items} metric={(p) => formatCoins(p.price ?? p.current_bin)} /> : <div className="content-empty">FUT.GG provides the cost for this tier but not its exact card list.</div>}
    </section>
  </main>;
}
