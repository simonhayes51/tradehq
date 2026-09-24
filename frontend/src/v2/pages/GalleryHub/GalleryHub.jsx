import { useMemo, useState } from "react";
import { ExternalLink, Search } from "lucide-react";
import { Link } from "react-router-dom";
import { useGalleryExposure, useGallerySets } from "../../hooks/useGallery";
import { formatCoins } from "../../lib/format";
import ContentPlayerStrip from "../../components/ContentPlayerStrip";
import "../content-intelligence.css";

export default function GalleryHub() {
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [grade, setGrade] = useState("");
  const [sort, setSort] = useState("tokens");
  const filters = useMemo(() => ({ q, category, grade, sort }), [q, category, grade, sort]);
  const { data, isLoading, error } = useGallerySets(filters);
  const exposure = useGalleryExposure();
  const summary = data?.summary || {};

  return <main className="content-page">
    <header className="content-hero"><div><span className="content-kicker">FC27 // COLLECTION INTELLIGENCE</span><h1>FUT Gallery</h1><p>Every Gallery set ranked by tokens, completion cost and solution quality—with the underlying card demand made visible.</p></div><a className="content-source" href="https://www.fut.gg/fut-gallery/" target="_blank" rel="noreferrer">Open FUT.GG <ExternalLink size={15}/></a></header>
    <section className="content-stats"><div><span>Gallery sets</span><strong>{summary.sets ?? "—"}</strong></div><div><span>Available tokens</span><strong>{summary.tokens?.toLocaleString?.() ?? "—"}</strong></div><div><span>All-set cost</span><strong>{formatCoins(summary.total_cost)}</strong></div><div><span>Game</span><strong>FC27</strong></div></section>
    <section className="content-panel"><div className="content-section-head"><div><span>Gallery exposure</span><h2>Cards appearing across solutions</h2></div><p>Repeated appearances can flag collection-driven demand.</p></div>{exposure.isLoading ? <div className="content-loading">Reading Gallery solutions…</div> : <ContentPlayerStrip items={exposure.data?.items} metric={(p) => `${p.set_count} sets`} />}</section>
    <section className="content-panel">
      <div className="content-toolbar"><label className="content-search"><Search size={16}/><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search Gallery sets" /></label><select value={grade} onChange={(e) => setGrade(e.target.value)} aria-label="Solution grade"><option value="">All grades</option>{["S","A","B","C","D"].map((value) => <option value={value} key={value}>Grade {value}</option>)}</select><select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort Gallery"><option value="tokens">Most tokens</option><option value="cheapest">Lowest cost</option><option value="costliest">Highest cost</option><option value="grade">Best grade</option><option value="name">Name</option></select></div>
      <div className="content-chips"><button className={!category ? "active" : ""} onClick={() => setCategory("")}>All</button>{(data?.categories || []).map((item) => <button key={item.slug} className={category === item.slug ? "active" : ""} onClick={() => setCategory(item.slug)}>{item.name} <span>{item.count}</span></button>)}</div>
      {isLoading ? <div className="content-loading">Sorting the Gallery…</div> : error ? <div className="content-error">The Gallery feed is temporarily unavailable.</div> : !data?.items?.length ? <div className="content-empty">No Gallery sets match these filters.</div> : <div className="gallery-table">
        <div className="gallery-row gallery-head"><span>Set</span><span>Grade</span><span>Cost</span><span>Tokens</span><span></span></div>
        {data.items.map((set) => <Link className="gallery-row" to={`/gallery/${set.set_id}`} key={set.set_id}><div><strong>{set.name}</strong><small>{set.category_name || "Gallery"} · {set.item_count || 0} cards</small></div><span className={`gallery-grade grade-${(set.best_grade || "d").toLowerCase()}`}>{set.best_grade || "—"}</span><b>{formatCoins(set.total_cost_coins)}</b><b className="gallery-token">{set.max_tokens ?? "—"}</b><span className="gallery-open">View</span></Link>)}
      </div>}
    </section>
  </main>;
}
