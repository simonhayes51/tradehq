import TradeResearchCard from "../../../v3/TradeResearchCard";
import PageBanner from "../../../v3/PageBanner";
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
import { ArrowRight, RotateCcw, ShieldAlert, SlidersHorizontal, TrendingUp } from "lucide-react";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import { SkeletonCards } from "../../components/Skeleton";
import MarketFreshness from "../../components/MarketFreshness";
import EmptyFeedReason from "../../components/EmptyFeedReason";
import { useFutggTradeFinder } from "../../hooks/useFutggMarket";
import { buildWhyText } from "../../lib/tradeNarrative";
import { PageHead } from "../Players/Players";
import UpgradePrompt from "../../components/UpgradePrompt";
import { GATED } from "../../lib/gatedFeatures";
import { riskLabel, versionLabel } from "../../lib/format";
import StatusLegend from "../../components/StatusLegend";
import "./trade-finder.css";

const DEFAULTS = {
  budget: "", min_profit: "", min_roi: "", risk: "", min_confidence: "",
  position: "", rarity: "", rating: "", min_liquidity: "", max_price_age: "3", sort: "best",
};
const LIVE_PRICE_MAX_AGE_SECONDS = 3 * 60;

// A raw age field made people do unit math in their head to use the
// filter. These map directly to minutes server-side but are offered as
// plain, understandable choices instead - what "fresh" means to a
// trader, not a raw threshold.
const FRESHNESS_OPTIONS = [
  { value: "3", label: "Launch-safe — under 3 minutes old" },
  { value: "10", label: "Recent — under 10 minutes old" },
  { value: "30", label: "Fairly fresh — under 30 minutes old" },
  { value: "60", label: "Within the last hour" },
  { value: "", label: "Any freshness" },
];

// Human labels for the filter chips row - kept in sync with DEFAULTS by
// key, so a chip never appears for a filter this page doesn't have.
const FILTER_LABELS = {
  budget: (v) => `Budget ≤ ${Number(v).toLocaleString()}`,
  min_profit: (v) => `Min profit ${Number(v).toLocaleString()}`,
  min_roi: (v) => `Min ROI ${v}%`,
  risk: (v) => `${v[0].toUpperCase()}${v.slice(1)} risk`,
  min_confidence: (v) => `Min confidence ${v}%`,
  position: (v) => `Position ${v.toUpperCase()}`,
  rarity: (v) => `Rarity ${v}`,
  rating: (v) => `Rating ${v}`,
  min_liquidity: (v) => `Min liquidity ${v}%`,
  max_price_age: (v) => FRESHNESS_OPTIONS.find((o) => o.value === String(v))?.label || `Price under ${v}m old`,
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
  // 402 is the server refusing on plan grounds, and it is the ONLY thing
  // that decides whether this page is locked. Reading the entitlement
  // context instead would mean the page and the API could disagree - and
  // the API is the one that is right.
  const locked = query.isError && query.error?.response?.status === 402;
  // 401: signed out. Same treatment - explain and offer the free account,
  // rather than an error that reads like a fault.
  const signedOut = query.isError && query.error?.response?.status === 401;

  return <main className="v2-destination trade-finder-page">
      <PageBanner kicker="RESEARCH / TRADE FINDER" title="FIND YOUR EDGE." description="Set your budget, risk and return targets. Explore the full live-priced market using your own filters." action="SHORTLIST" actionTo="/opportunities"/>
    <PageHead eyebrow="TRADE FINDER" title="Search every trade, your way" copy="Set your budget, minimum return, risk, position, rarity and sort order — every current live-priced trade is in scope here, not just the pre-filtered buy signals. Just want today's top picks with no setup? Try Opportunities." />

    <Link className="opp-crosslink" to="/opportunities">
      <TrendingUp size={15} /> Just want the shortlist? Open Opportunities <ArrowRight size={14} />
    </Link>

    {locked || signedOut ? null : <section className="finder-controls trade-finder-controls" aria-label="Trade filters">
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
        <label htmlFor="finder-age">Price freshness</label>
        <select id="finder-age" value={filters.max_price_age} onChange={set("max_price_age")}>
          {FRESHNESS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <small>How recently the price must have been checked</small>
      </div>
      <div className="finder-control">
        <label htmlFor="finder-sort">Sort</label>
        <select id="finder-sort" value={filters.sort} onChange={set("sort")}>
          {SORT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <small>How matching trades are ordered</small>
      </div>
    </section>}

    {locked || signedOut ? null : (
      <>
        {/* Filters apply instantly (no submit step) - the chips row is
            what makes "instantly" legible: every active filter is
            visible and individually removable, right above the results
            it's already affecting. */}
        {filtersActive ? (
          <div className="finder-chips" aria-label="Active filters">
            {Object.keys(DEFAULTS).filter((key) => key !== "sort" && filters[key] !== DEFAULTS[key]).map((key) => (
              <button key={key} className="finder-chip" onClick={() => setFilters((current) => ({ ...current, [key]: DEFAULTS[key] }))}>
                {(FILTER_LABELS[key] || ((v) => v))(filters[key])} <span aria-hidden="true">×</span>
              </button>
            ))}
          </div>
        ) : null}
        <div className="finder-result-head">
          <div><SlidersHorizontal size={16}/><strong>{query.isLoading ? "Scanning for trades…" : `${items.length} matching ${items.length === 1 ? "trade" : "trades"}`}</strong></div>
          {filtersActive ? <button onClick={() => setFilters(DEFAULTS)}><RotateCcw size={14}/> Reset filters</button> : null}
        </div>
      </>
    )}

    {locked || signedOut ? (
      <UpgradePrompt {...GATED.trade_finder} />
    ) : query.isLoading ? (
      <SkeletonCards count={6} gridClassName="finder-grid" cardClassName="" />
    ) : notDeployed ? (
      <EmptyState icon={<ShieldAlert size={25}/>} error text="Trade Finder isn't available right now. Please check back shortly." action={<button onClick={() => query.refetch()}>Try again</button>} />
    ) : query.isError ? (
      <EmptyState icon={<ShieldAlert size={25}/>} error text="Trade Finder could not load results just now. This is a fault on our side, not a limit on your account." action={<button onClick={() => query.refetch()}>Try again</button>} />
    ) : items.length ? (
      <section className="v3-research-grid" aria-label="Matching trade research">
        {items.map(item=><TradeResearchCard key={item.card_id??item.source_card_id} item={item} priceAgeLimit={LIVE_PRICE_MAX_AGE_SECONDS}/>)}
      </section>
    ) : <EmptyFeedReason noun="trades" filtersActive={filtersActive} onClearFilters={() => setFilters(DEFAULTS)} />}
  </main>;
}

function numberOrUndef(value) { const number = Number(value); return value === "" || !Number.isFinite(number) ? undefined : number; }
