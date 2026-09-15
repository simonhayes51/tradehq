import { useDeferredValue, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, RotateCcw, Rocket, Search, Shirt, SlidersHorizontal } from "lucide-react";
import { getReleasePriceRises } from "../../api/releasePriceRises";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
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
  day1_full: { label: "Early rush", range: "17-24 Sep", early: "2025-09-17", full: "2025-09-24", question: "What rose before full release?", use: "Use this to spot cards the early market wanted before everyone arrived." },
  day1_14: { label: "Two week hold", range: "17 Sep-1 Oct", early: "2025-09-17", full: "2025-10-01", question: "What kept rising after full release?", use: "Use this to find launch demand that lasted beyond the first rush." },
  full_7: { label: "Full release bounce", range: "24 Sep-1 Oct", early: "2025-09-24", full: "2025-10-01", question: "What moved when the main crowd joined?", use: "Use this to separate early hype from wider-player demand." },
  full_14: { label: "Staying power", range: "24 Sep-8 Oct", early: "2025-09-24", full: "2025-10-08", question: "What still worked two weeks later?", use: "Use this to see profiles that held demand after launch settled." },
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
      <header className="rpr-hero">
        <span>FC26 launch data</span>
        <h1>Launch Movers</h1>
        <p>Pick a launch window and see the simple trading read: what type of card moved, why it matters, and which cards prove it.</p>
      </header>

      <section className="rpr-window-panel" aria-label="Launch window">
        <div className="rpr-window-copy">
          <span>Question</span>
          <strong>{isCustom ? "What happened between these dates?" : selectedWindow?.question}</strong>
          <p>{isCustom ? "Use custom dates to test a specific launch period." : selectedWindow?.use}</p>
        </div>
        <div className="rpr-windows" role="tablist" aria-label="Launch window presets">
          {Object.entries(WINDOWS).map(([key, item]) => (
            <button key={key} type="button" className={windowKey === key ? "active" : ""} onClick={() => { setPage(1); setWindowKey(key); }} role="tab" aria-selected={windowKey === key}>
              <strong>{item.label}</strong>
              <span>{item.range}</span>
            </button>
          ))}
          <button type="button" className={isCustom ? "active" : ""} onClick={() => { setPage(1); setWindowKey("custom"); }} role="tab" aria-selected={isCustom}>
            <strong>Custom</strong>
            <span>Pick dates</span>
          </button>
        </div>
      </section>

      {isCustom ? (
        <div className="rpr-datebar">
          <label><span>Start</span><input type="date" value={effectiveEarly} onChange={(e) => setFilter(setEarlyDate)(e.target.value)} /></label>
          <ArrowRight size={15} />
          <label><span>End</span><input type="date" value={effectiveFull} onChange={(e) => setFilter(setFullDate)(e.target.value)} /></label>
          <button type="button" onClick={() => { setPage(1); setEarlyDate(""); setFullDate(""); }}><RotateCcw size={13} /> Reset</button>
        </div>
      ) : null}

      {loading ? (
        <EmptyState text="Loading launch movers..." compact />
      ) : error ? (
        <EmptyState text={error} error compact />
      ) : data ? (
        <>
          <LaunchAnswer data={data} windowLabel={selectedWindow?.label || "Custom"} />
          <details className="rpr-advanced">
            <summary><SlidersHorizontal size={15} /> Refine the proof list</summary>
            <Controls
              query={query}
              setQuery={setQuery}
              position={position}
              setPosition={setFilter(setPosition)}
              minRating={minRating}
              setMinRating={setFilter(setMinRating)}
              minPrice={minPrice}
              setMinPrice={setFilter(setMinPrice)}
              sortBy={sortBy}
              setSortBy={setFilter(setSortBy)}
              order={order}
              setOrder={setOrder}
              resetPage={() => setPage(1)}
            />
          </details>
          <section className="rpr-proof">
            <div className="rpr-proof-head">
              <div>
                <span>Proof list</span>
                <h2>{total} cards matched</h2>
              </div>
              <p>{from}-{to} shown / {fmtDate(data.early_date)} to {fmtDate(data.full_date)} / low-confidence chart reads excluded</p>
            </div>
            {items.length ? (
              <>
                <RisesList items={items} rankOffset={(page - 1) * PAGE_SIZE} />
                <Pagination page={page} from={from} to={to} total={total} hasNext={hasNext} onPrev={() => setPage((p) => Math.max(1, p - 1))} onNext={() => setPage((p) => p + 1)} />
              </>
            ) : (
              <EmptyState icon={<Rocket />} title="No cards match" text="Lower the price/rating floor, change position, or widen the date range." />
            )}
          </section>
        </>
      ) : null}
    </main>
  );
}

function LaunchAnswer({ data, windowLabel }) {
  const playbook = data.playbook || {};
  const cards = playbook.cards || [];
  const biggest = cards.find((card) => card.label === "Biggest riser");
  const profit = cards.find((card) => card.label === "Best coin return");
  const position = topBy(data.trends?.by_position, "position");
  const budget = topBy(data.trends?.by_price_band, "price_band");
  const pace = bestPace(data.trends?.by_pace_tier);

  return (
    <section className="rpr-answer">
      <div className="rpr-answer-main">
        <span>The answer / {windowLabel}</span>
        <h2>{cleanHeadline(playbook.headline)}</h2>
        <p>{plainSummary(playbook.summary)}</p>
      </div>
      <div className="rpr-takeaways" aria-label="How to use this data">
        <Takeaway label="1. Shortlist" value={position?.label || "Best position"} copy="Start with the strongest repeated profile, not one random card." />
        <Takeaway label="2. Budget" value={budget?.label || "Best price band"} copy="Check the range that moved on median rise and after-tax coins." />
        <Takeaway label="3. Confirm" value={pace?.label || "Pace check"} copy={pace?.copy || "Use the card list to confirm the pattern with actual examples."} />
      </div>
      <div className="rpr-examples">
        {biggest ? <ReadStat card={biggest} /> : null}
        {profit ? <ReadStat card={profit} /> : null}
      </div>
    </section>
  );
}

function Takeaway({ label, value, copy }) {
  return <div><span>{label}</span><strong>{value}</strong><p>{copy}</p></div>;
}

function ReadStat({ card }) {
  const body = <><span>{card.label}</span><strong>{card.value}</strong><small>{card.copy}</small></>;
  if (card.card_id) return <Link to={`/players/${card.card_id}`}>{body}</Link>;
  return <div>{body}</div>;
}

function Controls({ query, setQuery, position, setPosition, minRating, setMinRating, minPrice, setMinPrice, sortBy, setSortBy, order, setOrder, resetPage }) {
  return (
    <div className="rpr-controls">
      <label className="v2-search-box rpr-search">
        <Search size={16} />
        <input value={query} onChange={(e) => { resetPage(); setQuery(e.target.value); }} placeholder="Search player..." />
      </label>
      <select value={position} onChange={(e) => setPosition(e.target.value)} aria-label="Filter by position">
        <option value="">All positions</option>
        {POSITIONS.map((p) => <option key={p} value={p}>{p}</option>)}
      </select>
      <select value={minRating} onChange={(e) => setMinRating(e.target.value)} aria-label="Minimum rating">
        {RATING_FLOORS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
      </select>
      <select value={minPrice} onChange={(e) => setMinPrice(e.target.value)} aria-label="Minimum early price">
        {PRICE_FLOORS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
      </select>
      <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} aria-label="Sort by">
        {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
      </select>
      <button type="button" className="rpr-order-toggle" onClick={() => { resetPage(); setOrder((o) => (o === "desc" ? "asc" : "desc")); }}>
        {order === "desc" ? <ArrowDown size={15} /> : <ArrowUp size={15} />}
        {order === "desc" ? "High first" : "Low first"}
      </button>
    </div>
  );
}

function CardArt({ src, alt, className }) {
  const [failed, setFailed] = useState(false);
  if (src && !failed) return <img src={src} alt={alt} className={className} onError={() => setFailed(true)} />;
  return <div className={`${className} rpr-noimg`}><Shirt size={18} /></div>;
}

function RisesList({ items, rankOffset = 0 }) {
  return (
    <div className="rpr-list">
      {items.map((row, i) => (
        <Link to={`/players/${row.card_id}`} key={row.card_id} className="rpr-row">
          <span className="rpr-rank">#{rankOffset + i + 1}</span>
          <CardArt src={row.image_url} alt="" className="rpr-card-img" />
          <div className="rpr-row-player">
            <strong>{row.name}</strong>
            <p>{row.rating ?? "-"} OVR / {row.position || "-"}{row.pace != null ? ` / ${row.pace} PAC` : ""}</p>
          </div>
          <div className="rpr-price-path"><CoinValue value={row.early_price} /> <ArrowRight size={12} /> <CoinValue value={row.full_price} /></div>
          <div className="rpr-row-change">
            <strong className={row.pct_change >= 0 ? "positive" : "negative"}>{signedPct(row.pct_change)}</strong>
            <small><CoinValue value={row.net_profit_after_tax} signed /> after tax</small>
          </div>
        </Link>
      ))}
    </div>
  );
}

function Pagination({ page, from, to, total, hasNext, onPrev, onNext }) {
  if (!total) return null;
  return <div className="rpr-pagination"><button type="button" onClick={onPrev} disabled={page <= 1}><ArrowLeft size={14} /> Previous</button><span>{from}-{to} of {total}</span><button type="button" onClick={onNext} disabled={!hasNext}>Next <ArrowRight size={14} /></button></div>;
}

function topBy(rows = [], labelKey) {
  const row = rows.filter((item) => item?.card_count > 0 && item?.median_pct_change != null).sort((a, b) => (b.median_pct_change || 0) - (a.median_pct_change || 0))[0];
  if (!row) return null;
  return { label: row[labelKey], pct: row.median_pct_change, profit: row.median_profit_after_tax };
}

function bestPace(rows = []) {
  const high = rows.find((row) => row.group === "high_pace");
  const low = rows.find((row) => row.group === "low_pace");
  if (!high || !low || high.median_pct_change == null || low.median_pct_change == null) return null;
  const winner = high.median_pct_change >= low.median_pct_change ? high : low;
  return { label: winner.label, copy: `${winner.label} had the stronger median move in this window.` };
}

function fmtDate(iso) {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
}

function cleanHeadline(value) {
  return String(value || "Launch pattern").replace(/^Best FC27 watch profile:\s*/i, "");
}

function plainSummary(value) {
  if (!value) return "Use the historical launch window to shortlist similar cards early, then verify with current FC27 prices before buying.";
  return String(value).replace("Use the FC26 launch window as evidence for what to shortlist early in FC27.", "Use this window to decide what profile deserves attention first.");
}

function signedPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "-";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n}%`;
}
