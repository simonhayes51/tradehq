import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDown, ArrowRight, ArrowUp, LayoutGrid, List, Medal, RotateCcw, Rocket, Search, Shirt } from "lucide-react";
import { getReleasePriceRises } from "../../api/releasePriceRises";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import { PageHead } from "../Players/Players";
import "./release-price-rises.css";

// Server does the ranking (it has the full card set) and the data-quality
// filtering (which cards' chart extraction it trusts); this page re-fetches
// on every sort/filter/date change rather than sorting a client-side cache,
// since the trends block is computed over the SAME server-side set and
// would drift out of sync with a client-only sort. Name search and the
// rating floor stay client-side, narrowing within the fetched top page
// rather than round-tripping for a plain text filter.
const SORTS = [
  { value: "pct_change", label: "Biggest % rise" },
  { value: "abs_change", label: "Biggest coin rise" },
  { value: "early_price", label: "Highest early price" },
  { value: "full_price", label: "Highest full-release price" },
  { value: "rating", label: "Rating" },
];
const POSITIONS = ["GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM", "LM", "RM", "LW", "RW", "CF", "ST"];
const RATING_FLOORS = [
  { value: "0", label: "Any rating" },
  { value: "75", label: "75+ OVR" },
  { value: "80", label: "80+ OVR" },
  { value: "85", label: "85+ OVR" },
  { value: "90", label: "90+ OVR" },
];
// Coin-price floor. Distinct from the rating floor: a low-rated card can
// still cost a lot, and a high-rated one can be dirt cheap - this is what
// actually keeps cheap fodder (whose tiny price base turns a 2,000-coin
// move into a spectacular-looking 600%+) out of the "biggest riser" list.
const PRICE_FLOORS = [
  { value: "0", label: "Any price" },
  { value: "500", label: "500+ coins" },
  { value: "1000", label: "1,000+ coins" },
  { value: "5000", label: "5,000+ coins" },
  { value: "25000", label: "25,000+ coins" },
];

export default function ReleasePriceRises() {
  const [sortBy, setSortBy] = useState("pct_change");
  const [order, setOrder] = useState("desc");
  const [position, setPosition] = useState("");
  const [minRating, setMinRating] = useState("0");
  const [minPrice, setMinPrice] = useState("500");
  const [earlyDate, setEarlyDate] = useState("");
  const [fullDate, setFullDate] = useState("");
  const [query, setQuery] = useState("");
  const [view, setView] = useState("grid");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    getReleasePriceRises({
      sort_by: sortBy,
      order,
      position: position || undefined,
      min_early_price: Number(minPrice) || undefined,
      early_date: earlyDate || undefined,
      full_date: fullDate || undefined,
      page_size: 100,
    })
      .then((res) => { if (!cancelled) setData(res); })
      .catch((err) => { if (!cancelled) setError(err?.response?.data?.detail || "Couldn't load launch price movement."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [sortBy, order, position, minPrice, earlyDate, fullDate]);

  // Once the server has answered, its own early_date/full_date become the
  // controlled value for the date inputs - keeps them showing the actual
  // dates in effect (including the server's default) rather than staying
  // blank until someone touches them.
  const effectiveEarly = earlyDate || data?.early_date || "";
  const effectiveFull = fullDate || data?.full_date || "";
  const datesAreCustom = Boolean(earlyDate || fullDate);

  const items = useMemo(() => {
    let list = data?.items || [];
    const floor = Number(minRating) || 0;
    if (floor) list = list.filter((row) => Number(row.rating) >= floor);
    const q = query.trim().toLowerCase();
    if (q) list = list.filter((row) => String(row.name || "").toLowerCase().includes(q));
    return list;
  }, [data, query, minRating]);

  // Podium only makes sense on the default "biggest riser" framing - a
  // rating/price sort or the "lowest first" toggle isn't a launch-movers
  // ranking, it's a different question, so the spotlight hides itself
  // instead of misleadingly medalling whatever happens to be first.
  const showPodium = sortBy === "pct_change" && order === "desc" && !query.trim();
  const podium = showPodium ? items.slice(0, 3) : [];
  const rest = showPodium ? items.slice(3) : items;

  return (
    <main className="v2-destination rpr-page">
      <PageHead
        eyebrow="FC26 LAUNCH"
        title="Who rose the most at launch"
        copy="Compare card prices between any two dates in FC26's launch window - early release, full release, or your own range."
      />

      <div className="rpr-dates">
        <label>
          <span>Early date</span>
          <input type="date" value={effectiveEarly} onChange={(e) => setEarlyDate(e.target.value)} />
        </label>
        <ArrowRight size={16} className="rpr-dates-arrow" />
        <label>
          <span>Full date</span>
          <input type="date" value={effectiveFull} onChange={(e) => setFullDate(e.target.value)} />
        </label>
        {datesAreCustom ? (
          <button type="button" className="rpr-dates-reset" onClick={() => { setEarlyDate(""); setFullDate(""); }}>
            <RotateCcw size={13} /> Reset to launch dates
          </button>
        ) : (
          <span className="rpr-dates-hint">Showing FC26's confirmed early-release → full-release window</span>
        )}
      </div>

      {data ? (
        <p className="rpr-summary-line">
          {data.total} cards priced near both {fmtDate(data.early_date)} and {fmtDate(data.full_date)}
          {data.include_uncertain ? "" : " (low-confidence chart extractions excluded)"}.
        </p>
      ) : null}

      {data ? <Trends trends={data.trends} /> : null}

      {podium.length ? <Podium items={podium} /> : null}

      <div className="rpr-controls">
        <label className="v2-search-box rpr-search">
          <Search size={16} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by name…" />
        </label>
        <select value={position} onChange={(e) => setPosition(e.target.value)} aria-label="Filter by position">
          <option value="">All positions</option>
          {POSITIONS.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={minRating} onChange={(e) => setMinRating(e.target.value)} aria-label="Minimum rating">
          {RATING_FLOORS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
        </select>
        <select value={minPrice} onChange={(e) => setMinPrice(e.target.value)} aria-label="Minimum price">
          {PRICE_FLOORS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
        </select>
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} aria-label="Sort by">
          {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        <button
          type="button"
          className="rpr-order-toggle"
          onClick={() => setOrder((o) => (o === "desc" ? "asc" : "desc"))}
          title={order === "desc" ? "Highest first" : "Lowest first"}
        >
          {order === "desc" ? <ArrowDown size={15} /> : <ArrowUp size={15} />}
          {order === "desc" ? "Highest first" : "Lowest first"}
        </button>
        <div className="rpr-view-toggle" role="group" aria-label="Layout">
          <button type="button" className={view === "grid" ? "active" : ""} onClick={() => setView("grid")} aria-label="Card view"><LayoutGrid size={15} /></button>
          <button type="button" className={view === "table" ? "active" : ""} onClick={() => setView("table")} aria-label="Table view"><List size={15} /></button>
        </div>
      </div>

      {loading ? (
        <EmptyState text="Loading launch price movement…" compact />
      ) : error ? (
        <EmptyState text={error} error compact />
      ) : items.length ? (
        view === "grid" ? <RisesGrid items={rest} rankOffset={podium.length} /> : <RisesTable items={items} />
      ) : (
        <EmptyState
          icon={<Rocket />}
          title="No cards match"
          text="Try a lower price/rating floor, a different position, or widen the date range."
        />
      )}
    </main>
  );
}

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

function CardArt({ src, alt, className }) {
  if (src) return <img src={src} alt={alt} className={className} />;
  return <div className={`${className} rpr-noimg`}><Shirt size={18} /></div>;
}

function Trends({ trends }) {
  const byPosition = trends?.by_position || [];
  const byPace = trends?.by_pace_tier || [];
  if (!byPosition.length && !byPace.length) return null;
  return (
    <section className="rpr-trends">
      <div className="rpr-trend-panel">
        <h3>Median rise by position</h3>
        <TrendBars items={byPosition.map((r) => ({ label: r.position, value: r.median_pct_change, count: r.card_count }))} />
      </div>
      <div className="rpr-trend-panel">
        <h3>Pace vs the rest</h3>
        <TrendBars items={byPace.map((r) => ({ label: r.label, value: r.median_pct_change, count: r.card_count }))} />
      </div>
    </section>
  );
}

function TrendBars({ items }) {
  const withValues = items.filter((i) => i.count > 0);
  if (!withValues.length) return <p className="rpr-trend-empty">Not enough priced cards yet to break this down.</p>;
  const values = withValues.map((i) => Math.abs(Number(i.value) || 0));
  const max = Math.max(1, ...values);
  return (
    <div className="rpr-bars">
      {withValues.map((item) => (
        <div className="rpr-bar-row" key={item.label}>
          <span className="rpr-bar-label">{item.label}</span>
          <div className="rpr-bar-track">
            <div
              className={`rpr-bar-fill ${Number(item.value) < 0 ? "negative" : "positive"}`}
              style={{ width: `${(Math.abs(Number(item.value) || 0) / max) * 100}%` }}
            />
          </div>
          <span className="rpr-bar-value">{item.value == null ? "—" : `${item.value > 0 ? "+" : ""}${item.value}%`}</span>
          <span className="rpr-bar-count">n={item.count}</span>
        </div>
      ))}
    </div>
  );
}

const MEDAL_CLASS = ["gold", "silver", "bronze"];

function Podium({ items }) {
  return (
    <section className="rpr-podium" aria-label="Top launch risers">
      {items.map((row, i) => (
        <Link to={`/players/${row.card_id}`} key={row.card_id} className={`rpr-podium-card ${MEDAL_CLASS[i]}`}>
          <div className="rpr-podium-rank"><Medal size={16} /> #{i + 1}</div>
          <CardArt src={row.image_url} alt="" className="rpr-podium-img" />
          <strong className="rpr-podium-name">{row.name}</strong>
          <span className="rpr-podium-meta">{row.rating} OVR · {row.position || "—"}</span>
          <div className="rpr-podium-prices">
            <CoinValue value={row.early_price} /> <ArrowRight size={12} /> <CoinValue value={row.full_price} />
          </div>
          <span className="rpr-podium-pct">{row.pct_change > 0 ? "+" : ""}{row.pct_change}%</span>
        </Link>
      ))}
    </section>
  );
}

function RisesGrid({ items, rankOffset = 0 }) {
  if (!items.length) return null;
  return (
    <section className="rpr-grid">
      {items.map((row, i) => (
        <Link to={`/players/${row.card_id}`} key={row.card_id} className="rpr-grid-card">
          <span className="rpr-grid-rank">{rankOffset + i + 1}</span>
          <CardArt src={row.image_url} alt="" className="rpr-grid-img" />
          <div className="rpr-grid-body">
            <strong>{row.name}</strong>
            <div className="rpr-meta-pills">
              <span>{row.rating ?? "—"} OVR</span>
              <span>{row.position || "—"}</span>
              {row.pace != null ? <span>{row.pace} PAC</span> : null}
            </div>
            <div className="rpr-grid-prices">
              <div><small>EARLY</small><CoinValue value={row.early_price} /></div>
              <ArrowRight size={13} className="rpr-grid-arrow" />
              <div><small>FULL</small><CoinValue value={row.full_price} /></div>
            </div>
          </div>
          <span className={`rpr-grid-pct ${row.abs_change > 0 ? "positive" : row.abs_change < 0 ? "negative" : ""}`}>
            {row.pct_change == null ? "—" : `${row.pct_change > 0 ? "+" : ""}${row.pct_change}%`}
          </span>
        </Link>
      ))}
    </section>
  );
}

function RisesTable({ items }) {
  return (
    <div className="rpr-table-wrap">
      <table className="rpr-table">
        <thead>
          <tr>
            <th>Card</th>
            <th>Pos</th>
            <th>Pace</th>
            <th>Early price</th>
            <th>Full-release price</th>
            <th>Change</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => (
            <tr key={row.card_id}>
              <td className="rpr-card-cell">
                <span className="rpr-rank">{i + 1}</span>
                <Link to={`/players/${row.card_id}`}>
                  <CardArt src={row.image_url} alt="" className="rpr-table-img" />
                  <div><strong>{row.name}</strong><small>{row.rating} OVR · {row.rarity}</small></div>
                </Link>
              </td>
              <td>{row.position || "—"}</td>
              <td>{row.pace ?? "—"}</td>
              <td><CoinValue value={row.early_price} /></td>
              <td><CoinValue value={row.full_price} /></td>
              <td className={`rpr-change ${row.abs_change > 0 ? "positive" : row.abs_change < 0 ? "negative" : ""}`}>
                <CoinValue value={row.abs_change} signed />
                <small>{row.pct_change == null ? "" : `${row.pct_change > 0 ? "+" : ""}${row.pct_change}%`}</small>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
