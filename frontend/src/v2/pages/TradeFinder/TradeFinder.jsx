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
import { ArrowRight, Lock, RotateCcw, ShieldAlert, SlidersHorizontal, TrendingUp } from "lucide-react";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import MarketFreshness from "../../components/MarketFreshness";
import { useFutggTradeFinder } from "../../hooks/useFutggMarket";
import { buildWhyText } from "../../lib/tradeNarrative";
import { PageHead } from "../Players/Players";
import "./trade-finder.css";
// Reuses the locked-banner/locked-card styles Opportunities.jsx already
// defines (.opp-locked-banner/.opp-locked-cta/.opp-card-locked etc.) -
// same visual language for the same Free/Premium concept rather than a
// second copy of the same rules.
import "../Opportunities/opportunities.css";

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

  // Free's daily search allowance ran out - the backend 402s the whole
  // request rather than returning a locked/preview shape (see
  // futgg_market.py's trade_finder), so this needs its own branch rather
  // than reusing the generic query.isError copy below.
  const limitDetail = query.error?.response?.status === 402 ? query.error.response.data?.detail : null;
  // A successful Free response instead carries `locked: true` plus
  // remaining-search counters and preview-shaped items (see that same
  // endpoint's Free branch) - never the full actionable fields.
  const isLockedResult = query.data?.locked === true;

  return <main className="v2-destination trade-finder-page">
    <PageHead eyebrow="TRADE FINDER" title="Search every trade, your way" copy="Set your budget, minimum return, risk, position, rarity and sort order — every current live-priced trade is in scope here, not just the pre-filtered buy signals. Just want today's top picks with no setup? Try Opportunities." />

    <Link className="opp-crosslink" to="/v2/opportunities">
      <TrendingUp size={15} /> Just want the shortlist? Open Opportunities <ArrowRight size={14} />
    </Link>

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

    {isLockedResult ? (
      <div className="opp-locked-banner">
        <Lock size={15} />
        <span>
          {query.data.searchesRemainingToday ?? 0} of {query.data.dailySearchLimit ?? "?"} searches remaining today
        </span>
        <Link className="opp-locked-cta" to="/v2/billing?unlock=Trade%20Finder">Upgrade to Premium</Link>
      </div>
    ) : null}

    <div className="finder-result-head">
      <div><SlidersHorizontal size={16}/><strong>{query.isLoading ? "Scanning for trades…" : `${items.length} matching ${items.length === 1 ? "trade" : "trades"}`}</strong></div>
      {filtersActive ? <button onClick={() => setFilters(DEFAULTS)}><RotateCcw size={14}/> Reset filters</button> : null}
    </div>

    {query.isLoading ? (
      <EmptyState icon={<SlidersHorizontal size={25}/>} text="Checking the latest trade opportunities…" />
    ) : limitDetail ? (
      <EmptyState
        icon={<Lock size={25}/>}
        text={limitDetail.message || `You've used today's ${limitDetail.limit ?? ""} free Trade Finder searches.`}
        action={<Link className="opp-locked-cta" to={limitDetail.upgrade_url || "/v2/billing?unlock=Trade%20Finder"}>Upgrade to Premium — or come back tomorrow</Link>}
      />
    ) : notDeployed ? (
      <EmptyState icon={<ShieldAlert size={25}/>} error text="Trade Finder isn't available right now. Please check back shortly." action={<button onClick={() => query.refetch()}>Try again</button>} />
    ) : query.isError ? (
      <EmptyState icon={<ShieldAlert size={25}/>} error text="Trade Finder could not load the opportunity feed." action={<button onClick={() => query.refetch()}>Try again</button>} />
    ) : items.length ? (
      <section className="finder-grid">
        {items.map((item, index) => {
          if (item.locked) {
            // Free-tier preview item: none of the normal actionable
            // fields exist on this shape (see futgg_market.py's
            // locked_opportunity_preview) - render a locked teaser
            // instead of attempting to read undefined card/price fields.
            return (
              <Link className="finder-card opp-card-locked" key={index} to="/v2/billing?unlock=Trade%20Finder">
                <div className="opp-locked-head">
                  <Lock size={16} />
                  <strong>{item.budgetCategory ? `${item.budgetCategory[0].toUpperCase()}${item.budgetCategory.slice(1)} budget` : "Locked trade"}</strong>
                </div>
                <div className="opp-targets">
                  <div><span>Risk</span><b>{item.riskCategory || "unknown"}</b></div>
                  <div><span>Profit band</span><b>{item.profitBand || "Varies"}</b></div>
                  <div><span>Age</span><b>{item.ageMinutes != null ? `${item.ageMinutes}m` : "—"}</b></div>
                </div>
                <p className="opp-why">{item.message || "Upgrade to Premium to unlock this trade."}</p>
              </Link>
            );
          }
          const cardId = item.card_id ?? item.source_card_id;
          const confidence = item.confidence_score != null ? Math.round(Number(item.confidence_score) * 100) : null;
          const roi = item.expected_roi != null ? Number(item.expected_roi) * 100 : null;
          return <Link className="finder-card" key={cardId} to={`/v2/players/${cardId}`} state={{ from: "trade-finder" }}>
            <div className="finder-card-top">
              <img src={item.composited_image_url || item.image_url || "/img/card-placeholder.png"} alt="" />
              <div><h2>{item.name || "Unknown player"}</h2><div className="v2-meta-pills"><span className={`finder-signal-pill signal-${item.signal || "unknown"}`}>{item.signal === "watch" ? "WATCH" : "BUY"}</span><span>{item.rating ?? "—"} OVR</span><span>{item.position || "—"}</span><span>{item.rarity || "Card"}</span></div><p>{buildWhyText(item)}</p></div>
              <span className="finder-confidence">{confidence == null ? "—" : `${confidence}%`}<small>CONFIDENCE</small></span>
            </div>
            <div className="finder-signals">
              {/* Never fall back to current_bin here. "BUY BELOW <the
                  current asking price>" is not advice, it is the ask
                  echoed back under an advisory label - the circularity
                  engine v2 exists to prevent. No ceiling means we could
                  not value the card, and CoinValue already renders null
                  as an em dash. */}
              {/* The number beside EXPECTED PROFIT must be the one that
                  profit was computed against - expected_profit_basis, the
                  live ask on a buy - not the advised ceiling. Showing the
                  ceiling here overstated profit 3.6x on a live Xhaka.
                  The ceiling is still shown, as a separate limit. */}
              <div className="entry">
                <span>{item.expected_profit_basis != null && item.recommended_buy_max != null
                       && item.expected_profit_basis !== item.recommended_buy_max
                       ? "BUY NOW" : "BUY BELOW"}</span>
                <CoinValue value={item.expected_profit_basis ?? item.recommended_buy_max}/>
                {item.expected_profit_basis != null && item.recommended_buy_max != null
                 && item.expected_profit_basis !== item.recommended_buy_max
                  ? <small>pay up to <CoinValue value={item.recommended_buy_max}/></small>
                  : null}
              </div>
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
