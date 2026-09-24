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
  const activeTags = (set.solution_payload?.tags || set.solution_payload?.activeTags || []).map(String);
  const tags = (set.tags || []).filter((tag) => activeTags.includes(String(tag.tag_id)) || activeTags.includes(tag.name));
  return <main className="content-page">
    <div className="content-detail-nav"><Link to="/gallery"><ArrowLeft size={15}/> Gallery</Link><a href="https://www.fut.gg/fut-gallery/" target="_blank" rel="noreferrer">FUT.GG source <ExternalLink size={14}/></a></div>
    <header className="content-detail-hero"><div><span className="content-kicker">{set.category_name || "FUT GALLERY"}</span><h1>{set.name}</h1><p>Cheapest tracked solution and the cards needed to complete this collection set.</p></div><span className={`gallery-grade gallery-grade-large grade-${(set.best_grade || "d").toLowerCase()}`}>{set.best_grade || "—"}</span></header>
    <section className="content-stats"><div><span>Completion cost</span><strong>{formatCoins(set.total_cost_coins)}</strong></div><div><span>Max tokens</span><strong>{set.max_tokens ?? "—"}</strong></div><div><span>Cards</span><strong>{set.item_count ?? set.items?.length ?? "—"}</strong></div><div><span>Score</span><strong>{set.total_score?.toLocaleString?.() ?? "—"}</strong></div></section>
    {tags.length ? <section className="content-panel"><div className="content-section-head"><div><span>Active bonuses</span><h2>Collection conditions</h2></div></div><div className="content-tags">{tags.map((tag) => <span key={tag.tag_id}>{tag.name}</span>)}</div></section> : null}
    <section className="content-panel"><div className="content-section-head"><div><span>Cheapest solution</span><h2>{set.items?.length || 0} required cards</h2></div><p>Live market prices can move after the solution is captured.</p></div><ContentPlayerStrip items={set.items} metric={(p) => formatCoins(p.price ?? p.current_bin)} /></section>
  </main>;
}
