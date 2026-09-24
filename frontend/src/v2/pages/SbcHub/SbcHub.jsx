// src/v2/pages/SbcHub/SbcHub.jsx
import { useMemo, useState } from "react";
import { ExternalLink, Search } from "lucide-react";
import { useSbcEvents, useSbcExposure } from "../../hooks/useSbcEvents";
import { formatCoins } from "../../lib/format";
import ContentPlayerStrip from "../../components/ContentPlayerStrip";
import SbcListSection from "./sections/SbcListSection";
import "../content-intelligence.css";

export default function SbcHub() {
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("live");
  const [sort, setSort] = useState("newest");
  const filters = useMemo(() => ({ q, category, status, sort }), [q, category, status, sort]);
  const { data, isLoading, error } = useSbcEvents(filters);
  const exposure = useSbcExposure();
  const cheapest = [...(data?.items || [])].filter((item) => item.total_cost_coins != null).sort((a, b) => a.total_cost_coins - b.total_cost_coins)[0];

  return <main className="content-page">
    <header className="content-hero"><div><span className="content-kicker">FC27 // LIVE CONTENT</span><h1>SBC Market</h1><p>Every live challenge, its cheapest verified solution and the cards those solutions are pulling into demand.</p></div><a className="content-source" href="https://www.fut.gg/sbc/" target="_blank" rel="noreferrer">Open FUT.GG <ExternalLink size={15}/></a></header>
    <section className="content-stats" aria-label="SBC summary"><div><span>Live sets</span><strong>{data?.count ?? "—"}</strong></div><div><span>Categories</span><strong>{data?.categories?.length ?? "—"}</strong></div><div><span>Lowest entry</span><strong>{cheapest ? formatCoins(cheapest.total_cost_coins) : "—"}</strong></div><div><span>Source</span><strong>FUT.GG</strong></div></section>
    <section className="content-panel"><div className="content-section-head"><div><span>Solution exposure</span><h2>Cards used across live SBCs</h2></div><p>A supporting demand signal, not a buy recommendation.</p></div>{exposure.isLoading ? <div className="content-loading">Reading cheapest solutions…</div> : <ContentPlayerStrip items={exposure.data?.items} metric={(p) => `${p.sbc_count} SBC${p.sbc_count === 1 ? "" : "s"}`} />}</section>
    <section className="content-panel">
      <div className="content-toolbar"><label className="content-search"><Search size={16}/><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search SBCs" /></label><select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="SBC status"><option value="live">Live</option><option value="expired">Expired</option><option value="all">All</option></select><select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort SBCs"><option value="newest">Newest</option><option value="expiring">Expiring soon</option><option value="cheapest">Lowest cost</option><option value="costliest">Highest cost</option></select></div>
      <div className="content-chips"><button className={!category ? "active" : ""} onClick={() => setCategory("")}>All</button>{(data?.categories || []).map((item) => <button key={item.category} className={category === item.category ? "active" : ""} onClick={() => setCategory(item.category)}>{item.category} <span>{item.count}</span></button>)}</div>
      {isLoading ? <div className="content-loading">Loading live SBCs…</div> : error ? <div className="content-error">The SBC feed is temporarily unavailable.</div> : <SbcListSection events={data?.items} />}
    </section>
  </main>;
}
