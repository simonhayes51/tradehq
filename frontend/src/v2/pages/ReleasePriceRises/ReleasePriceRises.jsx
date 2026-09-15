import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDown, ArrowUp, Rocket, Search } from "lucide-react";
import { getReleasePriceRises } from "../../api/releasePriceRises";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import { PageHead } from "../Players/Players";
import "./release-price-rises.css";

// Server does the ranking/filtering (it has the full card set); this page
// re-fetches on every sort/filter change rather than sorting a client-side
// cache, since the trends block (by_position / by_pace_tier) is computed
// over the SAME filtered set server-side and would drift out of sync with
// a client-only sort.
const SORTS = [
  { value: "pct_change", label: "Biggest % rise" },
  { value: "abs_change", label: "Biggest coin rise" },
  { value: "early_price", label: "Highest early price" },
  { value: "full_price", label: "Highest full-release price" },
  { value: "rating", label: "Rating" },
];

export default function ReleasePriceRises() {
  const [sortBy, setSortBy] = useState("pct_change");
  const [order, setOrder] = useState("desc");
  const [position, setPosition] = useState("");
  const [query, setQuery] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    getReleasePriceRises({ sort_by: sortBy, order, position: position || undefined, page_size: 100 })
      .then((res) => { if (!cancelled) setData(res); })
      .catch((err) => { if (!cancelled) setError(err?.response?.data?.detail || "Couldn't load launch price movement."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [sortBy, order, position]);

  const items = useMemo(() => {
    const list = data?.items || [];
    if (!query.trim()) return list;
    const q = query.trim().toLowerCase();
    return list.filter((row) => String(row.name || "").toLowerCase().includes(q));
  }, [data, query]);

  return (
    <main className="v2-destination rpr-page">
      <PageHead
        eyebrow="FC26 LAUNCH"
        title="Who rose the most at launch"
        copy={data
          ? `Price on ${fmtDate(data.early_date)} (early release) vs ${fmtDate(data.full_date)} (full release) for every priced FC${data.game_year} card.`
          : "Comparing prices from early release to full release."}
      />

      {data ? <Trends trends={data.trends} /> : null}

      <div className="rpr-controls">
        <label className="v2-search-box rpr-search">
          <Search size={16} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by name…" />
        </label>
        <select value={position} onChange={(e) => setPosition(e.target.value)} aria-label="Filter by position">
          <option value="">All positions</option>
          {POSITIONS.map((p) => <option key={p} value={p}>{p}</option>)}
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
      </div>

      {loading ? (
        <EmptyState text="Loading launch price movement…" compact />
      ) : error ? (
        <EmptyState text={error} error compact />
      ) : items.length ? (
        <RisesTable items={items} />
      ) : (
        <EmptyState icon={<Rocket />} title="No cards match" text="Try a different position or clear the search." />
      )}
    </main>
  );
}

const POSITIONS = ["GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM", "LM", "RM", "LW", "RW", "CF", "ST"];

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
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
  const values = items.map((i) => Math.abs(Number(i.value) || 0));
  const max = Math.max(1, ...values);
  return (
    <div className="rpr-bars">
      {items.map((item) => (
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
                  {row.image_url ? <img src={row.image_url} alt="" /> : null}
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
