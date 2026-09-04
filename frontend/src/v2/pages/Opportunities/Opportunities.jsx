// src/v2/pages/Opportunities/Opportunities.jsx
//
// FUT.GG migration: wired to GET /api/v2/opportunities (buy/strong_buy
// signal feed, backend built in a sibling repo in parallel - see the
// FUTBIN -> FUT.GG migration plan). Was previously just a redirect to
// /v2/trade-finder (no dedicated opportunities page existed), so this is
// a new page rather than an edit of an existing one.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Lock, RotateCcw, ShieldAlert, SlidersHorizontal, TrendingUp } from "lucide-react";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import { SkeletonCards } from "../../components/Skeleton";
import MarketFreshness from "../../components/MarketFreshness";
import EmptyFeedReason from "../../components/EmptyFeedReason";
import { useFutggOpportunities } from "../../hooks/useFutggMarket";
import { buildWhyText, sellingSpeedLabel } from "../../lib/tradeNarrative";
import { PageHead } from "../Players/Players";
import UpgradePrompt from "../../components/UpgradePrompt";
import { GATED } from "../../lib/gatedFeatures";
import { formatCountdown } from "../../lib/format";
import { EVENTS, track, trackOnce } from "../../../lib/analytics";
import { riskLabel, versionLabel } from "../../lib/format";
import "../TradeFinder/trade-finder.css";
import "./opportunities.css";

const DEFAULTS = { risk: "", min_confidence: "", min_roi: "" };

// Opportunities and Trade Finder both read the FUT.GG-backed market feed,
// which is why they used to look like the same tool twice: same card
// layout, same "Scanning FUT.GG…" copy, an almost-identical filter panel.
// The actual difference is scope, not data source - Opportunities is the
// zero-setup shortlist (server-side pre-filtered to buy/strong-buy
// signals only, GET /api/v2/opportunities), Trade Finder is the full
// search across every signal with the complete filter/sort set. Cut this
// page's filter panel down to the handful of quick refinements that make
// sense on an already-curated list, and say the split out loud instead of
// leaving people to infer it from two near-identical screens.
export default function Opportunities() {
  const [filters, setFilters] = useState(DEFAULTS);
  const query = useFutggOpportunities({
    risk: filters.risk || undefined,
    min_confidence: filters.min_confidence === "" ? undefined : Number(filters.min_confidence) / 100,
    min_roi: filters.min_roi ? Number(filters.min_roi) / 100 : undefined,
    page_size: 40,
  });

  // Both come straight from the server's own serialisation. The client
  // never decides how many of these it is allowed to see.
  const freePicks = query.data?.free_picks ?? null;
  const lockedCount = Number(query.data?.locked_count) || 0;
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
        title="Today's shortlist, no setup needed"
        copy="Trades: cards you can buy and flip now. The current BIN sits meaningfully below recent completed sales, so the edge is available today — not something you hold and wait on. Want full control over budget, position, rarity and sort order instead? That's Trade Finder."
      />

      <div className="opp-crosslinks">
        <Link className="opp-crosslink" to="/trade-finder">
          <SlidersHorizontal size={15} /> Need more control? Open Trade Finder <ArrowRight size={14} />
        </Link>
      </div>

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
        <div><TrendingUp size={16} /><strong>{query.isLoading ? "Scanning for opportunities…" : `${items.length} matching ${items.length === 1 ? "opportunity" : "opportunities"}`}</strong></div>
        {filtersActive ? <button onClick={() => setFilters(DEFAULTS)}><RotateCcw size={14} /> Reset filters</button> : null}
      </div>

      {query.isLoading ? (
        <SkeletonCards count={6} gridClassName="finder-grid opp-grid" cardClassName="opp-card" />
      ) : notDeployed ? (
        <EmptyState icon={<ShieldAlert size={25} />} error text="Opportunities aren't available right now. Please check back shortly." action={<button onClick={() => query.refetch()}>Try again</button>} />
      ) : query.isError ? (
        <EmptyState icon={<ShieldAlert size={25} />} error text="Opportunities could not be loaded." action={<button onClick={() => query.refetch()}>Try again</button>} />
      ) : items.length ? (
        <>
          {freePicks ? <FreePickBanner meta={freePicks} lockedCount={lockedCount} /> : null}
          <section className="finder-grid opp-grid">
            {items.map((item) => <OpportunityCard key={item.card_id ?? item.source_card_id} item={item} />)}
          </section>
          {/* The upgrade prompt sits BELOW the feed, after the reader has
              seen their free picks and the locked ones - which is the
              moment the limit is actually felt, and the only moment the
              prompt is information rather than an interruption. */}
          {lockedCount > 0 ? <UpgradePrompt {...GATED.opportunity_feed} /> : null}
        </>
      ) : (
        <EmptyFeedReason noun="opportunities" filtersActive={filtersActive} onClearFilters={() => setFilters(DEFAULTS)} />
      )}
    </main>
  );
}

/**
 * A locked opportunity.
 *
 * The premium fields are not hidden here - they were never sent. The
 * server serialises a narrower object for viewers who have not paid (see
 * app/auth/access.py), so there is nothing in this component's props to
 * blur, and no devtools trick that recovers them.
 *
 * It still shows a real card, a real verdict and a real reason, because
 * the point of the preview is to prove the calls are about actual cards
 * you could look up - not to tease an empty box.
 */
function LockedOpportunityCard({ item }) {
  const cardId = item.source_card_id;
  return (
    <Link className="finder-card opp-card locked-opportunity" to={`/players/${cardId}`} state={{ from: "opportunities" }}>
      <div className="finder-card-top">
        <img src={item.composited_image_url || item.player_image_url || "/img/card-placeholder.png"} alt="" />
        <div>
          <h2>{item.display_name || item.name || "Unknown player"}</h2>
          <div className="v2-meta-pills">
            <span>{item.rating ?? "—"} OVR</span>
            <span>{item.primary_position || item.position || "—"}</span>
            <span>{versionLabel(item.rarity)}</span>
          </div>
          <p>
            {item.current_bin_approx != null
              ? `Around ${Number(item.current_bin_approx).toLocaleString()} coins`
              : "Price unavailable"}
          </p>
        </div>
        <span className={`finder-signal-pill signal-${item.signal || "unknown"}`}>
          {item.signal === "strong_buy" ? "STRONG BUY" : "BUY"}
        </span>
      </div>

      <div className="finder-signals">
        <div className="entry"><span>BUY MAX</span><i className="locked-figure" aria-hidden="true" /></div>
        <ArrowRight size={18} />
        <div className="profit"><span>EXPECTED PROFIT</span><i className="locked-figure" aria-hidden="true" /></div>
      </div>

      {item.headline_reason ? <p className="opp-why">{item.headline_reason}</p> : null}

      <div className="finder-card-foot">
        <span className={`risk-${String(item.risk_level || "unknown").toLowerCase()}`}>{riskLabel(item.risk_level)}</span>
        <span className="locked-opportunity-note"><Lock size={12} /> Exact prices on Elite</span>
      </div>
    </Link>
  );
}

/**
 * What a Free member is told about their allowance, above the feed.
 *
 * Says the real refresh time from the server (18:00 UK, DST-correct) and
 * is honest when the day produced fewer than the allowance rather than
 * letting a short list look like a bug.
 */
function FreePickBanner({ meta, lockedCount }) {
  const unlocked = Number(meta.unlocked) || 0;
  useEffect(() => {
    if (unlocked > 0) track(EVENTS.FREE_OPPORTUNITY_VIEWED, { count: unlocked, surface: "opportunities" });
    if (lockedCount > 0) trackOnce(EVENTS.FREE_LIMIT_REACHED, { surface: "opportunities" }, "opportunities");
  }, [unlocked, lockedCount]);

  return (
    <div className="free-pick-banner">
      <strong>
        {unlocked > 0
          ? `Your ${unlocked} free ${unlocked === 1 ? "pick" : "picks"} for today`
          : "No free pick has cleared our bar yet today"}
      </strong>
      <span>
        {meta.fewerThanAllowance && unlocked > 0
          ? `Only ${unlocked} of ${meta.allowance} today — we'd rather show you nothing than a call we don't believe in. `
          : ""}
        {meta.nextRefreshAt
          ? <>Next picks in <b>{formatCountdown(meta.nextRefreshAt)}</b>, at 6pm UK time.</>
          : null}
      </span>
    </div>
  );
}

function OpportunityCard({ item }) {
  // `locked` is the server's own answer, on the row. Never re-derived
  // from the entitlement context, which can be a few minutes stale and
  // would then disagree with the payload it is describing.
  if (item.locked) return <LockedOpportunityCard item={item} />;
  const cardId = item.card_id ?? item.source_card_id;
  const confidence = item.confidence_score != null ? Math.round(Number(item.confidence_score) * 100) : null;
  const liquidityPct = item.liquidity_score != null ? Math.round(Number(item.liquidity_score) * 100) : null;
  const roi = item.expected_roi != null ? Number(item.expected_roi) * 100 : null;
  // The simple layer says "Good"/"Slow" selling speed, not a raw percentage
  // - the exact number lives one click away on the player's own Advanced
  // rationale panel, matching the same split there.
  const sellingSpeed = sellingSpeedLabel(item.liquidity_score);
  const why = buildWhyText(item);
  // Not hidden - the product wants low-budget moves kept available, just
  // clearly told apart from a strong trade so a 90-coin flip doesn't read
  // as an equally-recommended pick beside a 20,000-coin one. Absolute
  // profit, not ROI%, since a small entry can post a huge percentage on
  // a trivial coin amount.
  const profitValue = item.expected_profit_after_tax != null ? Number(item.expected_profit_after_tax) : null;
  const isLowBudget = profitValue != null && profitValue > 0 && profitValue < 500;

  return (
    <Link className="finder-card opp-card" to={`/players/${cardId}`} state={{ from: "opportunities" }}>
      <div className="finder-card-top">
        <img src={item.composited_image_url || item.image_url || "/img/card-placeholder.png"} alt="" />
        <div>
          <h2>{item.name || "Unknown player"}{isLowBudget ? <span className="opp-budget-tag" title="A real, low-effort profit - small in absolute coins">Low-budget</span> : null}</h2>
          <div className="v2-meta-pills">
            <span>{item.rating ?? "—"} OVR</span>
            <span>{item.position || "—"}</span>
            <span>{versionLabel(item.rarity)}</span>
          </div>
          <p>{[item.club, item.league].filter(Boolean).join(" · ") || "Club and league unavailable"}</p>
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
        <div><span>Selling speed</span><b title={liquidityPct == null ? undefined : `${liquidityPct}% liquidity`}>{sellingSpeed}</b></div>
      </div>

      {why ? <p className="opp-why">{why}</p> : null}

      <div className="finder-card-foot">
        <span className={`risk-${String(item.risk_level || "unknown").toLowerCase()}`}>{riskLabel(item.risk_level)}</span>
        <MarketFreshness priceAgeSeconds={item.price_age_seconds} checkedAt={item.current_bin_checked_at || item.bin_checked_at || item.current_bin_captured_at} compact />
      </div>
    </Link>
  );
}
