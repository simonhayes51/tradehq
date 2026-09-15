import { useDeferredValue, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, LayoutGrid, List, RotateCcw, Rocket, Search, ShieldCheck, Shirt } from "lucide-react";
import { getReleasePriceRises } from "../../api/releasePriceRises";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import { PageHead } from "../Players/Players";
import "./release-price-rises.css";

const SORTS = [
  { value: "pct_change", label: "Biggest % rise" },
  { value: "net_profit_after_tax", label: "Best coin profit" },
  { value: "net_roi_after_tax", label: "Best ROI after tax" },
  { value: "abs_change", label: "Gross rise" },
  { value: "rating", label: "Rating" },
];
const POSITIONS = ["GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM", "LM", "RM", "LW", "RW", "CF", "ST"];
const RATING_FLOORS = [
  { value: "0", label: "Any rating" },
  { value: "75", label: "75+" },
  { value: "80", label: "80+" },
  { value: "85", label: "85+" },
  { value: "90", label: "90+" },
];
const PRICE_FLOORS = [
  { value: "0", label: "Any price" },
  { value: "500", label: "500+" },
  { value: "1000", label: "1k+" },
  { value: "5000", label: "5k+" },
  { value: "25000", label: "25k+" },
];
const WINDOWS = {
  day1_full: { label: "Day 1 to full release", short: "17 Sep to 24 Sep", early: "2025-09-17", full: "2025-09-24", meaning: "What moved before the wider market arrived." },
  day1_14: { label: "Day 1 to +14 days", short: "17 Sep to 1 Oct", early: "2025-09-17", full: "2025-10-01", meaning: "What kept climbing beyond full release." },
  full_7: { label: "Full release to +7 days", short: "24 Sep to 1 Oct", early: "2025-09-24", full: "2025-10-01", meaning: "What rose after the main player base joined." },
  full_14: { label: "Full release to +14 days", short: "24 Sep to 8 Oct", early: "2025-09-24", full: "2025-10-08", meaning: "What had staying power after launch hype settled." },
};
const PAGE_SIZE = 60;

export default function ReleasePriceRises() {
  const [windowKey, setWindowKey] = useState("day1_full");
  const [sortBy, setSortBy] = useState("pct_change");
  const [order, setOrder] = useState("desc");
  const [position, setPosition] = useState("");
  const [minRating, setMinRating] = useState("0");
  const [minPrice, setMinPrice] = useState("500");
  const [earlyDate, setEarlyDate] = useState("");
  const [fullDate, setFullDate] = useState("");
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [view, setView] = useState("grid");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const selectedWindow = WINDOWS[windowKey] || null;
  const isCustom = windowKey === "custom";
  const requestEarly = isCustom ? earlyDate : selectedWindow?.early;
  const requestFull = isCustom ? fullDate : selectedWindow?.full;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    getReleasePriceRises({
      sort_by: sortBy,
      order,
      position: position || undefined,
      min_rating: Number(minRating) || undefined,
      min_early_price: Number(minPrice) || undefined,
      early_date: requestEarly || undefined,
      full_date: requestFull || undefined,
      q: deferredQuery.trim() || undefined,
      page,
      page_size: PAGE_SIZE,
    })
      .then((res) => { if (!cancelled) setData(res); })
      .catch((err) => { if (!cancelled) setError(err?.response?.data?.detail || "Couldn't load launch movers."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [sortBy, order, position, minRating, minPrice, requestEarly, requestFull, deferredQuery, page]);

  const setFilter = (setter) => (value) => { setPage(1); setter(value); };
  const effectiveEarly = earlyDate || data?.early_date || "";
  const effectiveFull = fullDate || data?.full_date || "";
  const items = data?.items || [];
  const total = Number(data?.total || 0);
  const from = total ? (page - 1) * PAGE_SIZE + 1 : 0;
  const to = Math.min(page * PAGE_SIZE, total);
  const hasNext = to < total;

  return (
    <main className="v2-destination rpr-page">
      <PageHead
        eyebrow="FC26 launch data"
        title="Launch Movers"
        copy="Compare the key launch windows: what moved before full release, what moved after, and what kept going."
      />

      <div className="rpr-windows" role="tablist" aria-label="Launch window">
        {Object.entries(WINDOWS).map(([key, item]) => (
          <button key={key} type="button" className={windowKey === key ? "active" : ""} onClick={() => { setPage(1); setWindowKey(key); }} role="tab" aria-selected={windowKey === key}>
            <strong>{item.label}</strong>
            <span>{item.short}</span>
          </button>
        ))}
        <button type="button" className={isCustom ? "active" : ""} onClick={() => { setPage(1); setWindowKey("custom"); }} role="tab" aria-selected={isCustom}>
          <strong>Custom</strong>
          <span>Pick dates</span>
        </button>
      </div>

      <div className="rpr-datebar">
        {isCustom ? (
          <>
            <label><span>Start</span><input type="date" value={effectiveEarly} onChange={(e) => setFilter(setEarlyDate)(e.target.value)} /></label>
            <ArrowRight size={15} />
            <label><span>End</span><input type="date" value={effectiveFull} onChange={(e) => setFilter(setFullDate)(e.target.value)} /></label>
            <button type="button" onClick={() => { setPage(1); setEarlyDate(""); setFullDate(""); }}><RotateCcw size={13} /> Reset</button>
          </>
        ) : (
          <p><ShieldCheck size={13} /> {selectedWindow?.meaning} Low-confidence chart reads are excluded.</p>
        )}
      </div>

      {data?.playbook ? <KeyRead playbook={data.playbook} windowLabel={selectedWindow?.label || "Custom window"} /> : null}

      {data ? (
        <p className="rpr-summary-line">
          {total} cards matched. Showing {from}-{to}. Comparing {fmtDate(data.early_date)} to {fmtDate(data.full_date)}.
        </p>
      ) : null}

      {data ? <Patterns trends={data.trends} /> : null}

      <div className="rpr-controls">
        <label className="v2-search-box rpr-search">
          <Search size={16} />
          <input value={query} onChange={(e) => { setPage(1); setQuery(e.target.value); }} placeholder="Search player..." />
        </label>
        <select value={position} onChange={(e) => setFilter(setPosition)(e.target.value)} aria-label="Filter by position">
          <option value="">All positions</option>
          {POSITIONS.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={minRating} onChange={(e) => setFilter(setMinRating)(e.target.value)} aria-label="Minimum rating">
          {RATING_FLOORS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
        </select>
        <select value={minPrice} onChange={(e) => setFilter(setMinPrice)(e.target.value)} aria-label="Minimum early price">
          {PRICE_FLOORS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
        </select>
        <select value={sortBy} onChange={(e) => setFilter(setSortBy)(e.target.value)} aria-label="Sort by">
          {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        <button type="button" className="rpr-order-toggle" onClick={() => { setPage(1); setOrder((o) => (o === "desc" ? "asc" : "desc")); }}>
          {order === "desc" ? <ArrowDown size={15} /> : <ArrowUp size={15} />}
          {order === "desc" ? "High first" : "Low first"}
        </button>
        <div className="rpr-view-toggle" role="group" aria-label="Layout">
          <button type="button" className={view === "grid" ? "active" : ""} onClick={() => setView("grid")} aria-label="Card view"><LayoutGrid size={15} /></button>
          <button type="button" className={view === "table" ? "active" : ""} onClick={() => setView("table")} aria-label="Table view"><List size={15} /></button>
        </div>
      </div>

      {loading ? (
        <EmptyState text="Loading launch movers..." compact />
      ) : error ? (
        <EmptyState text={error} error compact />
      ) : items.length ? (
        <>
          {view === "grid" ? <RisesGrid items={items} rankOffset={(page - 1) * PAGE_SIZE} /> : <RisesTable items={items} rankOffset={(page - 1) * PAGE_SIZE} />}
          <Pagination page={page} from={from} to={to} total={total} hasNext={hasNext} onPrev={() => setPage((p) => Math.max(1, p - 1))} onNext={() => setPage((p) => p + 1)} />
        </>
      ) : (
        <EmptyState icon={<Rocket />} title="No cards match" text="Try a lower price/rating floor, a different position, or widen the date range." />
      )}
    </main>
  );
}

function KeyRead({ playbook, windowLabel }) {
  const cards = playbook.cards || [];
  const picked = ["Biggest riser", "Best coin return", "Position angle"].map((label) => cards.find((card) => card.label === label)).filter(Boolean);
  return (
    <section className="rpr-read">
      <div>
        <span>Key read · {windowLabel}</span>
        <h2>{cleanHeadline(playbook.headline)}</h2>
        <p>{playbook.summary}</p>
      </div>
      <div className="rpr-read-stats">
        {picked.map((card) => <ReadStat key={card.label} card={card} />)}
      </div>
    </section>
  );
}

function ReadStat({ card }) {
  const body = <><span>{card.label}</span><strong>{card.value}</strong><small>{card.copy}</small></>;
  if (card.card_id) return <Link to={`/players/${card.card_id}`}>{body}</Link>;
  return <div>{body}</div>;
}

function Patterns({ trends }) {
  const position = (trends?.by_position || []).slice(0, 4);
  const budget = (trends?.by_price_band || []).slice(0, 4);
  const pace = trends?.by_pace_tier || [];
  if (![position, budget, pace].some((rows) => rows.length)) return null;
  return (
    <section className="rpr-patterns">
      <Pattern title="Position" rows={position.map((r) => ({ label: r.position, pct: r.median_pct_change, profit: r.median_profit_after_tax, count: r.card_count }))} />
      <Pattern title="Budget" rows={budget.map((r) => ({ label: r.price_band, pct: r.median_pct_change, profit: r.median_profit_after_tax, count: r.card_count }))} />
      <Pattern title="Pace" rows={pace.map((r) => ({ label: r.label, pct: r.median_pct_change, profit: r.median_profit_after_tax, count: r.card_count }))} />
    </section>
  );
}

function Pattern({ title, rows }) {
  const clean = rows.filter((row) => row.count > 0);
  const max = Math.max(1, ...clean.map((row) => Math.abs(Number(row.pct) || 0)));
  return (
    <div className="rpr-pattern">
      <h3>{title}</h3>
      {clean.length ? clean.map((row) => (
        <div className="rpr-pattern-row" key={row.label}>
          <span>{row.label}</span>
          <i><b style={{ width: `${(Math.abs(Number(row.pct) || 0) / max) * 100}%` }} /></i>
          <strong>{signedPct(row.pct)}</strong>
          <small>{row.profit == null ? `n=${row.count}` : <><CoinValue value={row.profit} signed /> median</>}</small>
        </div>
      )) : <p>No pattern yet.</p>}
    </div>
  );
}

function CardArt({ src, alt, className }) {
  const [failed, setFailed] = useState(false);
  if (src && !failed) return <img src={src} alt={alt} className={className} onError={() => setFailed(true)} />;
  return <div className={`${className} rpr-noimg`}><Shirt size={18} /></div>;
}

function RisesGrid({ items, rankOffset = 0 }) {
  return (
    <section className="rpr-grid">
      {items.map((row, i) => (
        <Link to={`/players/${row.card_id}`} key={row.card_id} className="rpr-card">
          <span className="rpr-rank">#{rankOffset + i + 1}</span>
          <CardArt src={row.image_url} alt="" className="rpr-card-img" />
          <div className="rpr-card-main">
            <strong>{row.name}</strong>
            <p>{row.rating ?? "-"} OVR · {row.position || "-"}{row.pace != null ? ` · ${row.pace} PAC` : ""}</p>
            <div><CoinValue value={row.early_price} /> <ArrowRight size={12} /> <CoinValue value={row.full_price} /></div>
          </div>
          <div className="rpr-card-change">
            <strong className={row.pct_change >= 0 ? "positive" : "negative"}>{signedPct(row.pct_change)}</strong>
            <small><CoinValue value={row.net_profit_after_tax} signed /> after tax</small>
          </div>
        </Link>
      ))}
    </section>
  );
}

function RisesTable({ items, rankOffset = 0 }) {
  return (
    <div className="rpr-table-wrap">
      <table className="rpr-table">
        <thead><tr><th>Card</th><th>Pos</th><th>Early</th><th>Full</th><th>Rise</th><th>After tax</th></tr></thead>
        <tbody>
          {items.map((row, i) => (
            <tr key={row.card_id}>
              <td><Link to={`/players/${row.card_id}`}><span>{rankOffset + i + 1}</span><CardArt src={row.image_url} alt="" className="rpr-table-img" /><strong>{row.name}</strong></Link></td>
              <td>{row.position || "-"}</td>
              <td><CoinValue value={row.early_price} /></td>
              <td><CoinValue value={row.full_price} /></td>
              <td className={row.pct_change >= 0 ? "positive" : "negative"}>{signedPct(row.pct_change)}</td>
              <td><CoinValue value={row.net_profit_after_tax} signed /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Pagination({ page, from, to, total, hasNext, onPrev, onNext }) {
  if (!total) return null;
  return <div className="rpr-pagination"><button type="button" onClick={onPrev} disabled={page <= 1}><ArrowLeft size={14} /> Previous</button><span>{from}-{to} of {total}</span><button type="button" onClick={onNext} disabled={!hasNext}>Next <ArrowRight size={14} /></button></div>;
}

function fmtDate(iso) {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
}

function cleanHeadline(value) {
  return String(value || "Launch pattern").replace(/^Best FC27 watch profile:\s*/i, "");
}

function signedPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "-";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n}%`;
}
