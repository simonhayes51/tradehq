// src/v2/pages/Opportunities/Opportunities.jsx
//
// FUT.GG migration: wired to GET /api/v2/opportunities (buy/strong_buy
// signal feed, backend built in a sibling repo in parallel - see the
// FUTBIN -> FUT.GG migration plan). Was previously just a redirect to
// /v2/trade-finder (no dedicated opportunities page existed), so this is
// a new page rather than an edit of an existing one.
import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, RotateCcw, ShieldAlert, TrendingUp } from "lucide-react";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import MarketFreshness from "../../components/MarketFreshness";
import { useFutggOpportunities } from "../../hooks/useFutggMarket";
import { PageHead } from "../Players/Players";
import "../TradeFinder/trade-finder.css";
import "./opportunities.css";

const DEFAULTS = { risk: "", min_confidence: "", min_roi: "" };

export default function Opportunities() {
  const [filters, setFilters] = useState(DEFAULTS);
  const query = useFutggOpportunities({
    risk: filters.risk || undefined,
    min_confidence: filters.min_confidence === "" ? undefined : Number(filters.min_confidence) / 100,
    min_roi: filters.min_roi ? Number(filters.min_roi) / 100 : undefined,
    page_size: 40,
  });

  const items = Array.isArray(query.data?.items) ? query.data.items
    : Array.isArray(query.data?.results) ? query.data.results
    : Array.isArray(query.data) ? query.data
    : [];

  const set = (key) => (event) => setFilters((current) => ({ ...current, [key]: event.target.value }));
  const filtersActive = Object.keys(DEFAULTS).some((key) => filters[key] !== DEFAULTS[key]);
  const notDeployed = query.isError && [404, undefined].includes(query.error?.response?.status);

  return (
    <main className="v2-destination trade-finder-page opportunities-page">
      <PageHead
        eyebrow="OPPORTUNITIES"
        title="Real buy signals, live from FUT.GG"
        copy="Cards where the current BIN sits meaningfully below recent completed sales, filtered to real buy/strong-buy signals only."
      />

      <section className="finder-controls opp-controls" aria-label="Opportunity filters">
        <div className="finder-control">
          <label htmlFor="opp-risk">Risk</label>
          <select id="opp-risk" value={filters.risk} onChange={set("risk")}>
            <option value="">Any risk</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
          <small>Filter by the card's risk level</small>
        </div>
        <div className="finder-control">
          <label htmlFor="opp-confidence">Min confidence</label>
          <input id="opp-confidence" type="number" min="0" max="100" step="5" placeholder="Any" value={filters.min_confidence} onChange={set("min_confidence")} />
          <small>Minimum confidence score (0–100)</small>
        </div>
        <div className="finder-control">
          <label htmlFor="opp-roi">Min ROI %</label>
          <input id="opp-roi" type="number" min="0" step="1" placeholder="Any" value={filters.min_roi} onChange={set("min_roi")} />
          <small>Minimum expected return after tax</small>
        </div>
      </section>

      <div className="finder-result-head">
        <div><TrendingUp size={16} /><strong>{query.isLoading ? "Scanning FUT.GG opportunities…" : `${items.length} matching ${items.length === 1 ? "opportunity" : "opportunities"}`}</strong></div>
        {filtersActive ? <button onClick={() => setFilters(DEFAULTS)}><RotateCcw size={14} /> Reset filters</button> : null}
      </div>

      {query.isLoading ? (
        <EmptyState icon={<TrendingUp size={25} />} text="Checking the latest FUT.GG opportunities…" />
      ) : notDeployed ? (
        <EmptyState icon={<ShieldAlert size={25} />} error text="The FUT.GG opportunities feed isn't live yet — this endpoint hasn't been deployed on the backend." action={<button onClick={() => query.refetch()}>Try again</button>} />
      ) : query.isError ? (
        <EmptyState icon={<ShieldAlert size={25} />} error text="Opportunities could not be loaded." action={<button onClick={() => query.refetch()}>Try again</button>} />
      ) : items.length ? (
        <section className="finder-grid opp-grid">
          {items.map((item) => <OpportunityCard key={item.card_id ?? item.source_card_id} item={item} />)}
        </section>
      ) : (
        <EmptyState icon={<TrendingUp size={25} />} text="No current opportunities match those filters." action={filtersActive ? <button onClick={() => setFilters(DEFAULTS)}>Clear filters</button> : null} />
      )}
    </main>
  );
}

function OpportunityCard({ item }) {
  const cardId = item.card_id ?? item.source_card_id;
  const confidence = item.confidence_score != null ? Math.round(Number(item.confidence_score) * 100) : null;
  const liquidity = item.liquidity_score != null ? Math.round(Number(item.liquidity_score) * 100) : null;
  const roi = item.expected_roi != null ? Number(item.expected_roi) * 100 : null;
  const reasons = Array.isArray(item.signal_reasons) ? item.signal_reasons : [];

  return (
    <Link className="finder-card opp-card" to={`/v2/players/${cardId}`} state={{ from: "opportunities" }}>
      <div className="finder-card-top">
        <img src={item.image_url || "/img/card-placeholder.png"} alt="" />
        <div>
          <h2>{item.name || "Unknown player"}</h2>
          <div className="v2-meta-pills">
            <span>{item.rating ?? "—"} OVR</span>
            <span>{item.position || "—"}</span>
            <span>{item.rarity || "Card"}</span>
          </div>
          <p>{item.club || "—"} · {item.league || "—"}</p>
        </div>
        <span className="finder-confidence">{confidence == null ? "—" : `${confidence}%`}<small>CONFIDENCE</small></span>
      </div>

      <div className="finder-signals">
        <div className="entry"><span>CURRENT BIN</span><CoinValue value={item.current_bin} /></div>
        <ArrowRight size={18} />
        <div className="profit"><span>EXPECTED PROFIT</span><CoinValue value={item.expected_profit_after_tax} signed /><small>{roi == null ? "—" : `${roi > 0 ? "+" : ""}${roi.toFixed(1)}%`} after tax</small></div>
      </div>

      <div className="opp-targets">
        <div><span>Buy max</span><CoinValue value={item.recommended_buy_max} /></div>
        <div><span>Sell target</span><CoinValue value={item.recommended_sell_target} /></div>
        <div><span>Liquidity</span><b>{liquidity == null ? "—" : `${liquidity}%`}</b></div>
      </div>

      {reasons.length ? (
        <ul className="opp-reasons">
          {reasons.slice(0, 3).map((reason, index) => <li key={index}>{reason}</li>)}
        </ul>
      ) : null}

      <div className="finder-card-foot">
        <span className={`risk-${String(item.risk_level || "unknown").toLowerCase()}`}>{item.risk_level || "unknown"} risk</span>
        <MarketFreshness priceAgeSeconds={item.price_age_seconds} capturedAt={item.current_bin_captured_at} compact />
      </div>
    </Link>
  );
}
