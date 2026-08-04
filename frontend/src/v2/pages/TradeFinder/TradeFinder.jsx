// src/v2/pages/TradeFinder/TradeFinder.jsx
//
// FUT.GG migration: switched from the legacy FUTBIN-backed
// /api/v2/recommendations/opportunities feed (useOpportunities, still
// used elsewhere e.g. Home Dashboard) to the new FUT.GG-backed
// GET /api/v2/trade-finder contract, which exposes the full filter/sort
// set below server-side. Backend built in a sibling repo in parallel -
// see the FUTBIN -> FUT.GG migration plan.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, RotateCcw, ShieldAlert, SlidersHorizontal } from "lucide-react";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import MarketFreshness from "../../components/MarketFreshness";
import { useFutggTradeFinder } from "../../hooks/useFutggMarket";
import { PageHead } from "../Players/Players";
import "./trade-finder.css";

const DEFAULTS = {
  budget: "", min_profit: "", min_roi: "", risk: "", min_confidence: "",
  position: "", rarity: "", rating: "", min_liquidity: "", max_price_age: "", sort: "best",
};

const SORT_OPTIONS = [
  { value: "best", label: "Best overall" },
  { value: "profit", label: "Highest profit" },
  { value: "roi", label: "Highest return" },
  { value: "confidence", label: "Best confidence" },
  { value: "liquidity", label: "Most liquid" },
  { value: "newest", label: "Newest listings" },
  { value: "freshest", label: "Freshest price data" },
];

export default function TradeFinder() {
  const [filters, setFilters] = useState(DEFAULTS);

  const params = useMemo(() => ({
    budget: numberOrUndef(filters.budget),
    min_profit: numberOrUndef(filters.min_profit),
    min_roi: filters.min_roi === "" ? undefined : Number(filters.min_roi) / 100,
    risk: filters.risk || undefined,
    min_confidence: numberOrUndef(filters.min_confidence),
    position: filters.position || undefined,
    rarity: filters.rarity || undefined,
    rating: numberOrUndef(filters.rating),
    min_liquidity: filters.min_liquidity === "" ? undefined : Number(filters.min_liquidity) / 100,
    max_price_age: numberOrUndef(filters.max_price_age),
    sort: filters.sort,
    page_size: 50,
  }), [filters]);

  const query = useFutggTradeFinder(params);
  const items = Array.isArray(query.data?.items) ? query.data.items
    : Array.isArray(query.data?.results) ? query.data.results
    : Array.isArray(query.data) ? query.data
    : [];

  const set = (key) => (event) => setFilters((current) => ({ ...current, [key]: event.target.value }));
  const filtersActive = Object.keys(DEFAULTS).some((key) => filters[key] !== DEFAULTS[key]);
  const notDeployed = query.isError && [404, undefined].includes(query.error?.response?.status);

  return <main className="v2-destination trade-finder-page">
    <PageHead eyebrow="TRADE FINDER" title="Find a move that fits you" copy="Set your budget, minimum return and risk. We’ll only show current FUT.GG-priced trades that match." />

    <section className="finder-controls trade-finder-controls" aria-label="Trade filters">
      <div className="finder-control">
        <label htmlFor="finder-budget">Budget</label>
        <div className="finder-coin-input"><input id="finder-budget" inputMode="numeric" type="number" min="0" step="100" placeholder="Any budget" value={filters.budget} onChange={set("budget")}/></div>
        <small>The most you can pay for one card</small>
      </div>
      <div className="finder-control">
        <label htmlFor="finder-profit">Minimum profit</label>
        <div className="finder-coin-input"><input id="finder-profit" inputMode="numeric" type="number" min="0" step="100" placeholder="Any profit" value={filters.min_profit} onChange={set("min_profit")}/></div>
        <small>Expected coins after EA tax</small>
      </div>
      <div className="finder-control">
        <label htmlFor="finder-roi">Minimum ROI %</label>
        <input id="finder-roi" type="number" min="0" step="1" placeholder="Any" value={filters.min_roi} onChange={set("min_roi")} />
        <small>Minimum expected return</small>
      </div>
      <div className="finder-control">
        <label htmlFor="finder-risk">Risk</label>
        <select id="finder-risk" value={filters.risk} onChange={set("risk")}>
          <option value="">Any risk</option>
          <option value="low">Low only</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </select>
        <small>How much uncertainty you’ll accept</small>
      </div>
      <div className="finder-control">
        <label htmlFor="finder-confidence">Min confidence</label>
        <input id="finder-confidence" type="number" min="0" max="100" step="5" placeholder="Any" value={filters.min_confidence} onChange={set("min_confidence")} />
        <small>Minimum confidence score</small>
      </div>
      <div className="finder-control">
        <label htmlFor="finder-liquidity">Min liquidity %</label>
        <input id="finder-liquidity" type="number" min="0" max="100" step="5" placeholder="Any" value={filters.min_liquidity} onChange={set("min_liquidity")} />
        <small>How easily the card sells</small>
      </div>
      <div className="finder-control">
        <label htmlFor="finder-position">Position</label>
        <input id="finder-position" type="text" placeholder="e.g. ST" value={filters.position} onChange={set("position")} />
        <small>Exact position code</small>
      </div>
      <div className="finder-control">
        <label htmlFor="finder-rarity">Rarity</label>
        <input id="finder-rarity" type="text" placeholder="e.g. FUTTIES" value={filters.rarity} onChange={set("rarity")} />
        <small>Card rarity/version</small>
      </div>
      <div className="finder-control">
        <label htmlFor="finder-rating">Rating</label>
        <input id="finder-rating" type="number" min="0" max="99" placeholder="Any" value={filters.rating} onChange={set("rating")} />
        <small>Exact overall rating</small>
      </div>
      <div className="finder-control">
        <label htmlFor="finder-age">Max price age (s)</label>
        <input id="finder-age" type="number" min="0" placeholder="Any" value={filters.max_price_age} onChange={set("max_price_age")} />
        <small>Exclude stale price data</small>
      </div>
      <div className="finder-control">
        <label htmlFor="finder-sort">Sort</label>
        <select id="finder-sort" value={filters.sort} onChange={set("sort")}>
          {SORT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <small>How matching trades are ordered</small>
      </div>
    </section>

    <div className="finder-result-head">
      <div><SlidersHorizontal size={16}/><strong>{query.isLoading ? "Scanning FUT.GG trades…" : `${items.length} matching ${items.length === 1 ? "trade" : "trades"}`}</strong></div>
      {filtersActive ? <button onClick={() => setFilters(DEFAULTS)}><RotateCcw size={14}/> Reset filters</button> : null}
    </div>

    {query.isLoading ? (
      <EmptyState icon={<SlidersHorizontal size={25}/>} text="Checking the latest FUT.GG trade opportunities…" />
    ) : notDeployed ? (
      <EmptyState icon={<ShieldAlert size={25}/>} error text="The FUT.GG trade finder feed isn't live yet — this endpoint hasn't been deployed on the backend." action={<button onClick={() => query.refetch()}>Try again</button>} />
    ) : query.isError ? (
      <EmptyState icon={<ShieldAlert size={25}/>} error text="Trade Finder could not load the opportunity feed." action={<button onClick={() => query.refetch()}>Try again</button>} />
    ) : items.length ? (
      <section className="finder-grid">
        {items.map((item) => {
          const cardId = item.card_id ?? item.source_card_id;
          const confidence = item.confidence_score != null ? Math.round(Number(item.confidence_score) * 100) : null;
          const roi = item.expected_roi != null ? Number(item.expected_roi) * 100 : null;
          return <Link className="finder-card" key={cardId} to={`/v2/players/${cardId}`} state={{ from: "trade-finder" }}>
            <div className="finder-card-top">
              <img src={item.image_url || "/img/card-placeholder.png"} alt="" />
              <div><h2>{item.name || "Unknown player"}</h2><div className="v2-meta-pills"><span>{item.rating ?? "—"} OVR</span><span>{item.position || "—"}</span><span>{item.rarity || "Card"}</span></div><p>{(item.signal_reasons || [])[0] || "Current price and completed sales support this move."}</p></div>
              <span className="finder-confidence">{confidence == null ? "—" : `${confidence}%`}<small>CONFIDENCE</small></span>
            </div>
            <div className="finder-signals">
              <div className="entry"><span>BUY BELOW</span><CoinValue value={item.recommended_buy_max ?? item.current_bin}/></div>
              <ArrowRight size={18}/>
              <div className="profit"><span>EXPECTED PROFIT</span><CoinValue value={item.expected_profit_after_tax} signed/><small>{roi == null ? "—" : `${roi > 0 ? "+" : ""}${roi.toFixed(1)}%`} after tax</small></div>
            </div>
            <div className="finder-card-foot">
              <span className={`risk-${String(item.risk_level || "unknown").toLowerCase()}`}>{item.risk_level || "unknown"} risk</span>
              <MarketFreshness priceAgeSeconds={item.price_age_seconds} capturedAt={item.current_bin_captured_at} compact />
            </div>
          </Link>;
        })}
      </section>
    ) : <EmptyState icon={<SlidersHorizontal size={25}/>} text="No current trades match those filters." action={filtersActive ? <button onClick={() => setFilters(DEFAULTS)}>Clear filters</button> : null} />}
  </main>;
}

function numberOrUndef(value) { const number = Number(value); return value === "" || !Number.isFinite(number) ? undefined : number; }
