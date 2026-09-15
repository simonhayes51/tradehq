import { useDeferredValue, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, BarChart3, Flame, LayoutGrid, List, Medal, RotateCcw, Rocket, Search, ShieldCheck, Shirt, Sparkles, Target, TrendingUp } from "lucide-react";
import { getReleasePriceRises } from "../../api/releasePriceRises";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import { PageHead } from "../Players/Players";
import "./release-price-rises.css";

const SORTS = [
  { value: "pct_change", label: "Biggest % rise" },
  { value: "net_profit_after_tax", label: "Best coin profit" },
  { value: "abs_change", label: "Biggest gross rise" },
  { value: "net_roi_after_tax", label: "Best ROI after tax" },
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
const PRICE_FLOORS = [
  { value: "0", label: "Any price" },
  { value: "500", label: "500+ coins" },
  { value: "1000", label: "1,000+ coins" },
  { value: "5000", label: "5,000+ coins" },
  { value: "25000", label: "25,000+ coins" },
];
const PAGE_SIZE = 60;

export default function ReleasePriceRises() {
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
      early_date: earlyDate || undefined,
      full_date: fullDate || undefined,
      q: deferredQuery.trim() || undefined,
      page,
      page_size: PAGE_SIZE,
    })
      .then((res) => { if (!cancelled) setData(res); })
      .catch((err) => { if (!cancelled) setError(err?.response?.data?.detail || "Couldn't load launch movers."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [sortBy, order, position, minRating, minPrice, earlyDate, fullDate, deferredQuery, page]);

  const setFilter = (setter) => (value) => { setPage(1); setter(value); };
  const effectiveEarly = earlyDate || data?.early_date || "";
  const effectiveFull = fullDate || data?.full_date || "";
  const datesAreCustom = Boolean(earlyDate || fullDate);
  const items = data?.items || [];
  const showPodium = page === 1 && sortBy === "pct_change" && order === "desc" && !deferredQuery.trim();
  const podium = showPodium ? items.slice(0, 3) : [];
  const rest = showPodium ? items.slice(3) : items;
  const total = Number(data?.total || 0);
  const from = total ? (page - 1) * PAGE_SIZE + 1 : 0;
  const to = Math.min(page * PAGE_SIZE, total);
  const hasNext = to < total;
  const topCard = items[0];

  return (
    <main className="v2-destination rpr-page">
      <section className="rpr-hero">
        <PageHead
          eyebrow="FC26 LAUNCH DATA"
          title="Launch Movers"
          copy="See which FC26 cards jumped from early access to full release, then turn those patterns into an FC27 launch watchlist."
        />
        <div className="rpr-hero-actions">
          <Link to="/players?game_year=27"><Sparkles size={16} /> Find FC27 matches</Link>
          <Link to="/opportunities" className="secondary"><Target size={16} /> Open live opportunities</Link>
        </div>
      </section>

      {data?.playbook ? <Playbook playbook={data.playbook} topCard={topCard} /> : null}

      <div className="rpr-dates">
        <label>
          <span>{datesAreCustom ? "Start date" : "Early access"}</span>
          <input type="date" value={effectiveEarly} onChange={(e) => setFilter(setEarlyDate)(e.target.value)} />
        </label>
        <ArrowRight size={16} className="rpr-dates-arrow" />
        <label>
          <span>{datesAreCustom ? "End date" : "Full release"}</span>
          <input type="date" value={effectiveFull} onChange={(e) => setFilter(setFullDate)(e.target.value)} />
        </label>
        {datesAreCustom ? (
          <button type="button" className="rpr-dates-reset" onClick={() => { setPage(1); setEarlyDate(""); setFullDate(""); }}>
            <RotateCcw size={13} /> Reset to launch dates
          </button>
        ) : (
          <span className="rpr-dates-hint"><ShieldCheck size={13} /> Clean FC26 early-access to full-release comparison</span>
        )}
      </div>

      {data ? (
        <p className="rpr-summary-line">
          Showing {from}-{to} of {total} cards priced near both {fmtDate(data.early_date)} and {fmtDate(data.full_date)}
          {data.include_uncertain ? "" : " with low-confidence chart extractions excluded"}.
        </p>
      ) : null}

      {data ? <Trends trends={data.trends} /> : null}

      {podium.length ? <Podium items={podium} /> : null}

      <div className="rpr-controls">
        <label className="v2-search-box rpr-search">
          <Search size={16} />
          <input value={query} onChange={(e) => { setPage(1); setQuery(e.target.value); }} placeholder="Search every launch mover…" />
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
        <button
          type="button"
          className="rpr-order-toggle"
          onClick={() => { setPage(1); setOrder((o) => (o === "desc" ? "asc" : "desc")); }}
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
        <EmptyState text="Loading launch movers…" compact />
      ) : error ? (
        <EmptyState text={error} error compact />
      ) : items.length ? (
        <>
          {view === "grid" ? <RisesGrid items={rest} rankOffset={showPodium ? 3 + (page - 1) * PAGE_SIZE : (page - 1) * PAGE_SIZE} /> : <RisesTable items={items} rankOffset={(page - 1) * PAGE_SIZE} />}
          <Pagination page={page} from={from} to={to} total={total} hasNext={hasNext} onPrev={() => setPage((p) => Math.max(1, p - 1))} onNext={() => setPage((p) => p + 1)} />
        </>
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

function Playbook({ playbook, topCard }) {
  const cards = playbook.cards || [];
  return (
    <section className="rpr-playbook">
      <div className="rpr-playbook-copy">
        <span><Rocket size={14} /> FC27 launch playbook</span>
        <h2>{playbook.headline}</h2>
        <p>{playbook.summary}</p>
        <div className="rpr-playbook-actions">
          <Link to="/players?game_year=27"><Search size={15} /> Search FC27 cards</Link>
          {topCard ? <Link to={`/players/${topCard.card_id}`} className="secondary"><ArrowRight size={15} /> Inspect top mover</Link> : null}
        </div>
      </div>
      <div className="rpr-playbook-grid">
        {cards.map((card) => <InsightCard key={`${card.label}-${card.value}`} card={card} />)}
      </div>
    </section>
  );
}

function InsightCard({ card }) {
  const Icon = card.label === "Best coin return" ? Flame : card.label === "Pace premium" ? TrendingUp : BarChart3;
  const content = <>
    <span><Icon size={14} /> {card.label}</span>
    <strong>{card.value}</strong>
    <p>{card.copy}</p>
  </>;
  if (card.card_id) return <Link to={`/players/${card.card_id}`} className="rpr-insight-card">{content}</Link>;
  return <div className="rpr-insight-card">{content}</div>;
}

function fmtDate(iso) {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

function CardArt({ src, alt, className }) {
  const [failed, setFailed] = useState(false);
  if (src && !failed) return <img src={src} alt={alt} className={className} onError={() => setFailed(true)} />;
  return <div className={`${className} rpr-noimg`}><Shirt size={18} /></div>;
}

const MAX_POSITION_ROWS = 6;

function Trends({ trends }) {
  const byPosition = (trends?.by_position || []).slice(0, MAX_POSITION_ROWS);
  const byPace = trends?.by_pace_tier || [];
  const byPrice = trends?.by_price_band || [];
  const byNation = trends?.by_nation || [];
  const byLeague = trends?.by_league || [];
  const byRarity = trends?.by_rarity || [];
  if (![byPosition, byPace, byPrice, byNation, byLeague, byRarity].some((g) => g.length)) return null;
  return (
    <section className="rpr-trends">
      <TrendPanel title="Best positions" intro={trendIntro(byPosition, "position")}>
        <TrendBars items={byPosition.map((r) => ({ label: r.position, value: r.median_pct_change, profit: r.median_profit_after_tax, count: r.card_count }))} />
      </TrendPanel>
      <TrendPanel title="Pace check" intro={paceIntro(byPace)}>
        <TrendBars items={byPace.map((r) => ({ label: r.label, value: r.median_pct_change, profit: r.median_profit_after_tax, count: r.card_count }))} />
      </TrendPanel>
      <TrendPanel title="Budget bands" intro={trendIntro(byPrice, "price_band")}>
        <TrendBars items={byPrice.map((r) => ({ label: r.price_band, value: r.median_pct_change, profit: r.median_profit_after_tax, count: r.card_count }))} />
      </TrendPanel>
      {byRarity.length ? <TrendPanel title="Card types"><TrendChips items={byRarity.map((r) => ({ label: r.rarity, value: r.median_pct_change, profit: r.median_profit_after_tax, count: r.card_count }))} /></TrendPanel> : null}
      {byNation.length ? <TrendPanel title="Top nations"><TrendChips items={byNation.map((r) => ({ label: r.nation, value: r.median_pct_change, profit: r.median_profit_after_tax, count: r.card_count, image: r.image_url }))} /></TrendPanel> : null}
      {byLeague.length ? <TrendPanel title="Top leagues"><TrendChips items={byLeague.map((r) => ({ label: r.league, value: r.median_pct_change, profit: r.median_profit_after_tax, count: r.card_count, image: r.image_url }))} /></TrendPanel> : null}
    </section>
  );
}

function TrendPanel({ title, intro, children }) {
  return <div className="rpr-trend-panel"><h3>{title}</h3>{intro ? <p className="rpr-trend-intro">{intro}</p> : null}{children}</div>;
}

function trendIntro(rows, field) {
  const first = rows?.[0];
  if (!first) return "Not enough clean data for a pattern yet.";
  const label = first[field];
  return `${label} led this view with a ${signedPct(first.median_pct_change)} median rise across ${first.card_count} cards.`;
}

function paceIntro(rows) {
  const high = rows.find((r) => r.group === "high_pace");
  const low = rows.find((r) => r.group === "low_pace");
  if (!high || !low || high.median_pct_change == null || low.median_pct_change == null) return "Pace split builds as more cards qualify.";
  const diff = Number(high.median_pct_change) - Number(low.median_pct_change);
  return diff >= 0 ? `85+ pace beat slower cards by ${diff.toFixed(1)} points.` : `Slower cards beat 85+ pace by ${Math.abs(diff).toFixed(1)} points.`;
}

function TrendChips({ items }) {
  if (!items.length) return <p className="rpr-trend-empty">Not enough priced cards yet to break this down.</p>;
  return (
    <div className="rpr-chip-row">
      {items.map((item, i) => (
        <div className="rpr-chip" key={item.label}>
          <span className="rpr-chip-rank">#{i + 1}</span>
          <ChipImage src={item.image} alt="" />
          <span className="rpr-chip-label">{item.label}</span>
          <span className={`rpr-chip-pct ${Number(item.value) < 0 ? "negative" : "positive"}`}>{signedPct(item.value)}</span>
          {item.profit != null ? <span className="rpr-chip-profit"><CoinValue value={item.profit} signed /></span> : null}
          <span className="rpr-chip-count">{item.count} cards</span>
        </div>
      ))}
    </div>
  );
}

function ChipImage({ src, alt }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return <span className="rpr-chip-noimg" aria-hidden="true" />;
  return <img src={src} alt={alt} onError={() => setFailed(true)} />;
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
            <div className={`rpr-bar-fill ${Number(item.value) < 0 ? "negative" : "positive"}`} style={{ width: `${(Math.abs(Number(item.value) || 0) / max) * 100}%` }} />
          </div>
          <span className="rpr-bar-value">{signedPct(item.value)}</span>
          <span className="rpr-bar-profit">{item.profit == null ? "-" : <CoinValue value={item.profit} signed />}</span>
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
          <span className="rpr-podium-meta">{row.rating} OVR · {row.position || "-"}</span>
          <div className="rpr-podium-prices"><CoinValue value={row.early_price} /> <ArrowRight size={12} /> <CoinValue value={row.full_price} /></div>
          <span className="rpr-podium-pct">{signedPct(row.pct_change)}</span>
          <span className="rpr-podium-profit"><CoinValue value={row.net_profit_after_tax} signed /> after tax</span>
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
          <span className="rpr-grid-rank">#{rankOffset + i + 1}</span>
          <CardArt src={row.image_url} alt="" className="rpr-grid-img" />
          <div className="rpr-grid-body">
            <strong>{row.name}</strong>
            <div className="rpr-meta-pills">
              <span>{row.rating ?? "-"} OVR</span>
              <span>{row.position || "-"}</span>
              {row.pace != null ? <span>{row.pace} PAC</span> : null}
              {row.price_band?.label ? <span>{row.price_band.label}</span> : null}
            </div>
            <div className="rpr-grid-prices">
              <div><small>EARLY</small><CoinValue value={row.early_price} /></div>
              <ArrowRight size={13} className="rpr-grid-arrow" />
              <div><small>FULL</small><CoinValue value={row.full_price} /></div>
            </div>
          </div>
          <div className="rpr-grid-return">
            <span className={`rpr-grid-pct ${row.abs_change > 0 ? "positive" : row.abs_change < 0 ? "negative" : ""}`}>{signedPct(row.pct_change)}</span>
            <small><CoinValue value={row.net_profit_after_tax} signed /> tax net</small>
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
        <thead>
          <tr><th>Card</th><th>Pos</th><th>Pace</th><th>Early</th><th>Full release</th><th>Gross</th><th>After tax</th></tr>
        </thead>
        <tbody>
          {items.map((row, i) => (
            <tr key={row.card_id}>
              <td className="rpr-card-cell"><span className="rpr-rank">{rankOffset + i + 1}</span><Link to={`/players/${row.card_id}`}><CardArt src={row.image_url} alt="" className="rpr-table-img" /><div><strong>{row.name}</strong><small>{row.rating} OVR · {row.rarity}</small></div></Link></td>
              <td>{row.position || "-"}</td>
              <td>{row.pace ?? "-"}</td>
              <td><CoinValue value={row.early_price} /></td>
              <td><CoinValue value={row.full_price} /></td>
              <td className={`rpr-change ${row.abs_change > 0 ? "positive" : row.abs_change < 0 ? "negative" : ""}`}><CoinValue value={row.abs_change} signed /><small>{signedPct(row.pct_change)}</small></td>
              <td className={`rpr-change ${row.net_profit_after_tax > 0 ? "positive" : row.net_profit_after_tax < 0 ? "negative" : ""}`}><CoinValue value={row.net_profit_after_tax} signed /><small>{signedPct(row.net_roi_after_tax)}</small></td>
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

function signedPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "-";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n}%`;
}
