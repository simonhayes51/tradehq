import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Activity, ArrowRight, Bell, ChevronRight, Lock, Search, Star, Target, X, Zap } from "lucide-react";
import { useAuth } from "../../../context/AuthContext";
import { isPublicHost, PUBLIC_HOSTNAME } from "../../../lib/domains";
import { useDashboard } from "../../hooks/useDashboard";
import { useIsMobile } from "../../hooks/useIsMobile";
import { buildWhyText, sellingSpeedLabel } from "../../lib/tradeNarrative";
import { formatCountdown, versionLabel } from "../../lib/format";
import AdvancedRationale from "../../components/AdvancedRationale";
import { useFutggMarketFreshness, useFutggOpportunities, useStrategyFinder } from "../../hooks/useFutggMarket";
import { useGeneratedCardImages } from "../../hooks/useRecommendationFeeds";
import PlayerCardArt from "../../../components/PlayerCardArt";
import { addWatch } from "../../../api/watchlist";
import ModalPriceChart from "./ModalPriceChart";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import { SkeletonBlock, SkeletonLine } from "../../components/Skeleton";
import EmptyFeedReason from "../../components/EmptyFeedReason";
import MarketFreshness from "../../components/MarketFreshness";
import TrackRecordSummary from "../../components/TrackRecordSummary";
import StatusLegend from "../../components/StatusLegend";
import NotificationsPanel from "../../components/NotificationsPanel";
// The shared locked-state styles (.locked-figure, .locked-opportunity-note)
// live with the other destination pages; Home renders the same locked
// card treatment, so it needs them too.
import "../../styles/v2-destinations.css";
import "../../styles/terminal.css";
import "../../styles/dashboard-v2.css";
import "../../styles/dashboard-analysis-modal.css";
import "../../styles/mobile-polish.css";

const EMPTY={marketRegime:{label:"Unknown",confidence:0,summary:"",metrics:{}},todaysOpportunities:[],highConfidenceInvestments:[],cardsToAvoid:[],biggestMovers:[],watchlistAlerts:[],watchedCount:0,nextPromo:null,latestMarketEvents:[],latestSbcImpact:[],freePicks:null,locked:{opportunityFeed:false,previewCount:0}};
// Strategy Finder used to hit the legacy FUTBIN engine's
// /api/v2/recommendations/strategy/{name} - that pipeline's fair_value_mv
// is broken (see the comment above baseItems below), so every tab always
// came back empty. Rebuilt on the same /api/v2/trade-finder endpoint the
// rest of this page already uses for live FUT.GG data. Tabs are limited to
// what trade-finder can actually answer honestly - it has no holding-
// period or SBC-relevance signal, so "2-3 days"/"Long hold"/"SBC plays"
// were dropped rather than faked onto an unrelated sort.
//
// Redesigned from the original 5 (For you/Quick flips/Low risk/Best
// ROI/Most confident) after investigation showed: "For you" carried no
// user-specific signal at all (same params for every visitor, signed in
// or not) - renamed rather than left misleading. "Low risk" and "Most
// confident" shared the exact same sort key and differed only by a risk
// filter, making "Most confident" a near-superset of "Low risk" - merged
// into one axis, replaced with "Big profit" (a genuinely different
// need: sort by absolute profit, not confidence, still gated on
// confidence/risk so it can't surface a reckless outlier). "Best ROI"
// had no absolute-profit floor, so an 85-coin flip at 42% ROI ranked
// beside a 60,000-coin one at the same percentage - replaced with
// "Budget picks" (cheapest entry among trades already clearing a
// meaningful, price-relative profit floor - see meaningful_profit on
// the backend). "Quick flips" now requires that same meaningful-profit
// floor and excludes "watch" (not buyable at a sensible price right
// now) so it means "do this now", not "keep an eye on it".
// Keys must match app/services/strategy_definitions.py's
// STRATEGY_DEFINITIONS exactly - the backend now owns every filter
// threshold (min profit, min confidence, risk cap, etc.) for each tab,
// enforced server-side via GET /api/v2/trade-finder/strategy/{key}. This
// file used to send its own raw filter params (STRATEGY_PARAMS) straight
// to /trade-finder - that's how "Big profit" ended up with no real
// minimum coin floor: nothing server-side enforced one, and this was the
// only place that decided what "Big profit" meant. Labels/blurbs stay
// here as static UI copy since they change less often than thresholds
// and a single extra fetch just for tab labels isn't worth it - the
// backend's own `description` field (in strategyQuery.data) is rendered
// as a fallback if a label here ever falls out of sync.
const STRATEGIES=[
  ["best_overall","Best overall"],
  ["quick_flips","Quick flips"],
  ["low_risk","Low risk"],
  ["big_profit","Big profit"],
  ["budget","Budget picks"],
];
// Short, plain-language purpose shown under the active tab - no finance
// jargon, no internal metric names (confidence/liquidity/ROI stay inside
// Advanced Rationale, not here).
const STRATEGY_BLURBS={
  best_overall:"The strongest all-round mix of profit, value and confidence right now.",
  quick_flips:"Cards selling regularly with enough room for a worthwhile flip.",
  low_risk:"More stable prices and strong sales data, so you're less likely to get caught in a drop.",
  big_profit:"The biggest coin gains, still limited to trades we trust.",
  budget:"Good trades without needing a huge coin balance.",
};
// Strategy-specific empty-state copy - per the product rule that an
// honest empty section beats a weakened one, "Big profit" in particular
// must say plainly that the floor was not lowered to fill the tab.
const STRATEGY_EMPTY_TEXT={
  big_profit:"No live opportunities currently meet the Big Profit criteria. We won't lower the standard just to fill this section.",
};
const DEFAULT_STRATEGY_EMPTY_TEXT="No opportunities currently meet this strategy's live-entry requirements. We won't lower the standard just to fill this section.";

// Mobile section tabs (<=4, per the mobile IA rebuild). "Overview" is
// the decision-first default: the Top Card verdict plus a compact
// alerts row, with no further scrolling needed to know whether to buy.
// Everything else is a deliberate choice, not left over from desktop:
// Opportunities groups "what else is worth a look right now" (queue +
// Strategy Finder), Track Record is its own large, distinct concern,
// and Market groups broader context that doesn't bear on today's
// single top pick (Market Map + the full Alerts & Content feed).
// Three, not four. "Track record" was its own full tab because the panel
// it held was enormous; it is now a four-number strip that belongs with
// the rest of the decision context on Overview, and "Market" no longer
// has to carry a Market Map that duplicated Opportunities.
const MOBILE_SECTIONS=[
  ["overview","Overview"],
  ["opportunities","Opportunities"],
  ["market","Your feed"],
];

export default function HomeDashboard(){
  const {isAuthenticated,isLoading:authLoading}=useAuth();
  // Home is the trading app, not the marketing site - a signed-out
  // visitor lands here only via a stale bookmark/redirect (the real
  // public entry point is eafc.live, a separate lightweight page - see
  // PublicSite.jsx). Bounce them there with a real cross-origin
  // navigation rather than showing a stripped-down app shell with
  // marketing bolted on. Deep links (a shared /players/:id) are a
  // different route entirely and are never touched by this - only
  // Home itself redirects.
  useEffect(()=>{
    if(!authLoading&&!isAuthenticated&&!isPublicHost()){
      window.location.href=`https://${PUBLIC_HOSTNAME}/`;
    }
  },[authLoading,isAuthenticated]);
  const dashboardQuery=useDashboard();
  const dashboard=dashboardQuery.data??EMPTY;
  const navigate=useNavigate();
  const {pathname}=useLocation();
  const [strategy,setStrategy]=useState("best_overall");
  // Mobile-only content grouping - the previous mobile layout simply
  // stacked every desktop panel (Top Card, Market Pulse, Track Record,
  // Next Best Moves, Market Map, Alerts, Strategy Finder) into one
  // enormous column, burying the actual buy/watch decision under
  // dozens of secondary stats. Desktop is untouched: isMobile only ever
  // gates the tab bar and which section groups mount - the sections
  // themselves, their data hooks and their calculations are identical
  // on both, so there's no second implementation to drift.
  const isMobile=useIsMobile();
  const [mobileSection,setMobileSection]=useState("overview");
  const [watchStates,setWatchStates]=useState({});
  const [alertsOpen,setAlertsOpen]=useState(false);
  // Once the alerts panel has fetched, its count is authoritative -
  // /api/v2/dashboard's copy can be up to a cache-window stale.
  const [panelUnread,setPanelUnread]=useState(null);
  const [selected,setSelected]=useState(null);
  const strategyQuery=useStrategyFinder(strategy);
  // FUT.GG migration: /api/v2/dashboard itself is still legacy FUTBIN-
  // backed (a bigger, riskier change than wiring the frontend to a new
  // endpoint - see the Opportunities/Trade Finder pages for the same
  // pattern already used there). Home's "Top Card"/"Next best moves"/
  // "Market Map" instead blend in real FUT.GG opportunities client-side,
  // normalised into the same item shape normalise() already produces -
  // so this dashboard isn't empty just because the legacy engine's data
  // has gone stale/negative while FUT.GG pricing is still ramping up.
  const futggOppsQuery=useFutggOpportunities({page_size:12});
  const freshnessQuery=useFutggMarketFreshness();

  const legacyBaseItems=useMemo(()=>{const map=new Map();[...dashboard.todaysOpportunities,...dashboard.highConfidenceInvestments,...dashboard.cardsToAvoid,...dashboard.biggestMovers].forEach(raw=>{const item=normalise(raw);if(item?.cardId&&!map.has(String(item.cardId)))map.set(String(item.cardId),item)});return[...map.values()]},[dashboard]);
  const futggBaseItems=useMemo(()=>(futggOppsQuery.data?.items??[]).map(normaliseFutgg).filter(Boolean),[futggOppsQuery.data]);
  // FUT.GG goes first: Market Map/Ticker/Top Card all just take the
  // front slice of this combined pool (items.slice(0,N)), and the
  // migration plan is explicit that live FUT.GG data should be
  // preferred over the legacy FUTBIN pipeline once it has coverage -
  // legacy-first here was exactly why Market Map kept showing old
  // FUTBIN movers even after real FUT.GG opportunities existed. FUT.GG
  // also wins the (extremely unlikely - unrelated id spaces) chance of
  // a cardId collision now, for the same reason.
  const baseItems=useMemo(()=>{const map=new Map();[...futggBaseItems,...legacyBaseItems].forEach(item=>{if(item?.cardId&&!map.has(String(item.cardId)))map.set(String(item.cardId),item)});return[...map.values()]},[legacyBaseItems,futggBaseItems]);
  const strategyRaw=(strategyQuery.data?.items??[]).map(normaliseFutgg).filter(Boolean);
  // Separate, explicitly non-actionable section - server-filtered by
  // classify_watch_candidate() (tolerance/target/break-even/real-time
  // profit all enforced backend-side). Never merged into strategyItems.
  const watchRaw=(strategyQuery.data?.watch_items??[]).map(normaliseFutgg).filter(Boolean);
  const ids=[...baseItems,...strategyRaw,...watchRaw].map(x=>x.cardId);
  const imageQuery=useGeneratedCardImages(ids);
  const images=imageQuery.data?.images??{};
  const hydrate=(item)=>item?{...item,player:{...item.player,generatedCardUrl:images[String(item.cardId)]||item.player?.generatedCardUrl}}:null;
  const items=baseItems.map(hydrate);
  // The legacy FUTBIN pipeline's fair_value_mv is broken on the current
  // player DB (fut_players there is missing basic columns - that worker
  // has never successfully written to this database), so its computed
  // recommendations/movers are not trustworthy live data, just whatever
  // stale/fallback values the legacy dashboard aggregation happens to
  // return. TOP CARD / NEXT BEST MOVES / MARKET MAP / the ticker all
  // restrict to FUT.GG-sourced items only until that pipeline is either
  // fixed or retired - better an honest "still building" empty state
  // than presenting broken legacy numbers as live market data.
  const futggItems=items.filter(x=>x.source==="futgg");
  // Ranked by expected profit, not left in whatever order the feed
  // returned them - that order was confidence/discount-led, so a 92%-
  // confidence flip worth 85 coins could sit ahead of a 78%-confidence
  // one worth 12,000+. Every item here already cleared the BUY bar, so
  // confidence is already accounted for; profit is what actually makes
  // one BUY a better feature than another. TOP CARD and the "Next best
  // moves" queue both use this order, so the queue stops burying its
  // biggest wins at #4/#5 behind smaller ones at #2/#3.
  // cardId as a final tiebreak: two items tied on expected profit used to
  // fall back to array order, which could differ between fetches even
  // with no real market change (the backend candidate pool order is not
  // guaranteed stable), making Top Card appear to flip at random.
  const opportunities=futggItems.filter(x=>x.recommendation==="BUY").slice().sort((a,b)=>(expectedProfit(b)??-Infinity)-(expectedProfit(a)??-Infinity)||String(a.cardId).localeCompare(String(b.cardId)));
  // The lead slot is the one card Home puts numbers on, so it must be
  // one the viewer is actually entitled to. A Free member whose picks
  // have not resolved yet gets no lead rather than a hero card with
  // every figure blanked out.
  const lead=opportunities.find(x=>!x.locked)??null;
  // The three states Top Card can be in when there is no lead, kept
  // apart deliberately. This panel used to render one EmptyState whose
  // copy ("Today's picks haven't landed yet - the cards below are ready
  // when they do") directly contradicted the numbered locked previews
  // sitting underneath it: those cards HAD landed, they were simply not
  // this viewer's to see. And because .decision-stage is a two-column
  // grid, an ~80px empty state next to the full-height Market Pulse
  // column stretched into an enormous empty rectangle.
  const feedLoading=dashboardQuery.isLoading||futggOppsQuery.isLoading;
  const firstLocked=opportunities.find(x=>x.locked)??null;
  // "Locked, not absent": the day HAS produced picks, this viewer's
  // free allowance just doesn't cover them.
  const lockedOnly=!feedLoading&&!lead&&opportunities.length>0;
  const queue=opportunities.filter(x=>String(x.cardId)!==String(lead?.cardId)).slice(0,6);
  const shown=new Set([lead,...queue].filter(Boolean).map(x=>String(x.cardId)));
  const strategyItems=strategyRaw.map(hydrate).filter(x=>!shown.has(String(x.cardId))).slice(0,8);
  const watchItems=watchRaw.map(hydrate).filter(x=>!shown.has(String(x.cardId))).slice(0,6);
  // Near Entry is explicitly non-actionable (WATCH, not BUY) - a long
  // column of them on mobile reads like a recommendation list by sheer
  // visual weight. Cap what's shown up front; the rest are one tap away
  // via Trade Finder's own full, filterable feed.
  const visibleWatchItems=isMobile?watchItems.slice(0,3):watchItems;
  const buyItems=futggItems.filter(x=>x.recommendation==="BUY");
  const buyCount=buyItems.length;
  // Separate "no live buy signals" from "signals exist but haven't priced
  // a profit yet" - both used to render as an identical "+0", which read
  // as a contradiction next to a positive buy-signal count (3 buy
  // signals, 0 coin potential looks like a calculation bug even when it
  // is really just missing per-card profit data on those rows).
  const buyItemsWithProfit=buyItems.filter(x=>expectedProfit(x)!=null);
  const score=Math.round(Number(dashboard.marketRegime?.confidence||0));
  // "Unknown" is the backend's explicit "we are not calling this market",
  // not a low score - see the Market Pulse comment below.
  const hasMarketVerdict=Boolean(dashboard.marketRegime?.label)&&dashboard.marketRegime.label!=="Unknown";
  const valuedCards=dashboard.marketRegime?.metrics?.valuedCards??null;
  // Prefer the backend's own expected_profit_after_tax (correct EA price-
  // increment rounding) over the generic profit() recompute, same reason
  // AnalysisModal's `gain`/`target` do below.
  const potential=buyItemsWithProfit.reduce((sum,item)=>sum+Math.max(0,expectedProfit(item)||0),0);

  // Removed: this auto-scrolled to the "Next best moves" queue when Home
  // was rendered at the Opportunities URL, back when /opportunities was a
  // redirect INTO this dashboard. Opportunities has been its own page for
  // a while, so the condition could never be true from here - and if it
  // somehow were, Home silently jumping the viewport on load is not
  // behaviour worth keeping.

  async function watchItem(item){const id=String(item?.cardId||"");if(!item||watchStates[id]==="saving"||watchStates[id]==="saved")return;setWatchStates(current=>({...current,[id]:"saving"}));try{await addWatch({player_name:nameOf(item.player),card_id:id,version:item.player?.version??null,platform:"ps",source:item.source==="futgg"?"futgg":"futbin"});setWatchStates(current=>({...current,[id]:"saved"}))}catch(error){if(error?.response?.status===401)navigate(`/signup?from=${encodeURIComponent(pathname)}`);else if(error?.response?.status===409)setWatchStates(current=>({...current,[id]:"saved"}));else setWatchStates(current=>({...current,[id]:"error"}))}}

  if(!authLoading&&!isAuthenticated&&!isPublicHost()){
    // Redirect is already in flight (the effect above) - render nothing
    // rather than flashing the full app shell first.
    return null;
  }

  // Which section groups mount: on desktop everything mounts, same as
  // before this rebuild. On mobile, only the active tab's group mounts -
  // not merely hidden with CSS - so a phone never pays to render the
  // Track Record ledger, Market Map and Strategy Finder simultaneously
  // just to show the Top Card decision.
  const showOverview=!isMobile||mobileSection==="overview";
  const showOpportunities=!isMobile||mobileSection==="opportunities";
  const showTrackRecord=!isMobile||mobileSection==="overview";
  const showMarket=!isMobile||mobileSection==="market";
  const topAlerts=(dashboard.watchlistAlerts||[]).slice(0,2);
  const unreadAlerts=panelUnread==null?Number(dashboard.unreadAlertCount||0):panelUnread;
  // One shape for the feed, built from two sources, each row carrying its
  // own destination. Previously these were rendered from whichever of
  // `title`/`message`/`estimatedMarketImpact` happened to exist, with a
  // final fallback of the literal string "Market update" / "New market
  // activity" - a row that told the reader nothing at all, and nothing to
  // click through to.
  const feedRows=[
    ...(dashboard.watchlistAlerts||[]).slice(0,3).map(alert=>({
      key:`alert-${alert.id}`,
      title:alert.title,
      detail:[alert.movement,alert.age].filter(Boolean).join(" · "),
      href:alert.href||`/players/${alert.cardId}`,
    })),
    ...(dashboard.latestSbcImpact||[]).slice(0,3).filter(x=>x.title).map(x=>({
      key:`sbc-${x.eventId}`,
      title:`${x.title} is moving the cards it uses`,
      detail:`${percent(x.estimatedMarketImpact)} average price change across the cards this SBC draws on`,
      href:"/opportunities",
    })),
  ].slice(0,5);

  return <div className="fut-dashboard-shell">
    <main className="fut-main home-in-shell">
      <header className="fut-topbar">
        <button className="fut-search" onClick={()=>navigate("/players")}><Search size={19}/><span>Search player, card or price</span><kbd>/</kbd></button>
        <div className="feed-live"><i/> LIVE</div>
        {/* The badge is the server's real unread count (undismissed AND
            unread rows), not the length of the 5-row preview list, which
            is what it used to be - a number that never decreased and
            matched nothing on screen. Once the panel has loaded its own
            count it takes over, so the two can never disagree. */}
        <div className="bell-wrap">
          <button
            className="bell-btn"
            onClick={()=>setAlertsOpen(open=>!open)}
            aria-expanded={alertsOpen}
            aria-haspopup="dialog"
            aria-label={unreadAlerts?`Alerts, ${unreadAlerts} unread`:"Alerts"}
          >
            <Bell size={18}/>{unreadAlerts?<span className="bell-badge">{unreadAlerts>9?"9+":unreadAlerts}</span>:null}
          </button>
          <NotificationsPanel open={alertsOpen} onClose={()=>setAlertsOpen(false)} onUnreadCount={setPanelUnread}/>
        </div>
      </header>
      <Ticker items={futggItems} events={dashboard.latestMarketEvents}/>
      {isMobile?<nav className="mobile-section-tabs" aria-label="Dashboard sections">{MOBILE_SECTIONS.map(([key,label])=><button key={key} type="button" role="tab" aria-selected={mobileSection===key} className={mobileSection===key?"active":""} onClick={()=>setMobileSection(key)}>{label}</button>)}</nav>:null}

      {showOverview?<>
        <section className="decision-stage">
          <div className={`lead-trade${lead?"":" is-pending"}`}><div className="lead-label"><Zap size={15}/> TOP CARD</div>{lead?<div className="lead-layout"><button className="lead-card player-open-button" onClick={()=>setSelected(lead)} aria-label={`Open ${nameOf(lead.player)} analysis`}><CardImage player={lead.player} featured/></button><div className="lead-copy"><div className="lead-head"><div><PlayerMeta player={lead.player} source={lead.source}/><h1>{nameOf(lead.player)}</h1></div><div className="lead-status"><b className={`call ${String(lead.recommendation).toLowerCase()}`}>{lead.recommendation}</b><StatusLegend/><ConfidenceRing value={lead.confidence}/></div></div>{lead.source==="futgg"?<MarketFreshness priceAgeSeconds={lead.priceAgeSeconds} checkedAt={lead.updatedAt}/>:null}<p>{reason(lead)}</p><div className="lead-numbers"><Metric label="Buy below" value={<CoinValue value={lead.entryPrice??lead.currentBin}/>} sub="Maximum entry" tone="entry"/><Metric label="Expected profit" value={<CoinValue value={expectedProfit(lead)} signed/>} sub="After EA tax" tone="positive"/><Metric label="Risk level" value={lead.risk||"Unknown"} sub={`${sellingSpeedLabel(lead.liquidityScore)} selling speed`}/></div><div className="lead-actions"><button onClick={()=>setSelected(lead)}>View move <ArrowRight size={17}/></button><button className="secondary" onClick={()=>watchItem(lead)} aria-label="Add top card to watchlist"><Star size={16} fill={watchStates[String(lead.cardId)]==="saved"?"currentColor":"none"}/>{watchStates[String(lead.cardId)]==="saved"?"Watching":"Watch"}</button></div></div></div>:<TopCardPending loading={feedLoading} lockedOnly={lockedOnly} preview={firstLocked} freePicks={dashboard.freePicks} navigate={navigate}/>}</div>
          {/* Two states, never blended. When the backend declines to
              characterise the market (too few cards valued - see
              _compute_live_market_regime), it returns label "Unknown" and
              confidence 0. Rendering that as a "0/100" gauge beside a
              confident BUY recommendation is what made the page look
              broken: 0 there does not mean "terrible market", it means
              "no verdict", and a progress bar cannot say that. So when
              there is no verdict we show the reason instead of a score.
              The per-card numbers below stay - a market-wide read being
              withheld does not invalidate an individual card that DID
              have enough data to evaluate, and the copy now says which
              is which rather than leaving the reader to reconcile them. */}
          <aside className="market-panel">
            <div className="panel-label"><Activity size={15}/> MARKET PULSE</div>
            {hasMarketVerdict?<>
              <div className="pulse-head">
                <div><h2>{mood(dashboard.marketRegime?.label,score)}</h2><p>{dashboard.marketRegime?.summary}</p></div>
                <strong>{score}<small>/100</small></strong>
              </div>
              <div className="pulse-track"><span style={{width:`${Math.max(5,score)}%`}}/></div>
            </>:<div className="pulse-head no-verdict">
              <div>
                <h2>No market-wide read yet</h2>
                <p>{dashboard.marketRegime?.summary||"Prices and completed sales are being checked now."}</p>
              </div>
            </div>}
            <div className="pulse-stats">
              <Metric label="Buy signals" value={buyCount} sub="Cards that passed the filters now"/>
              <Metric
                label="Coin potential"
                value={buyCount>0&&buyItemsWithProfit.length===0?"—":<CoinValue value={potential} signed/>}
                sub={buyCount>0&&buyItemsWithProfit.length===0
                  ?"Profit not priced for these yet"
                  :buyItemsWithProfit.length<buyCount
                    ?`Combined, after EA tax · ${buyItemsWithProfit.length} of ${buyCount} priced`
                    :"Combined, after EA tax"}
              />
              {/* Two separate facts, shown as two separate facts. This
                  tile used to ADD the regime's candidate count to the
                  price-worker's priced-card count and present the sum as
                  "Tracked" - two different populations, double-counted
                  into one number that described neither. */}
              <Metric label="Cards tracked" value={count(freshnessQuery.data?.cards_priced)} sub={valuedCards!=null?`${count(valuedCards)} have a fresh enough price to score`:"Live price coverage"}/>
            </div>
            <PulseFooter nextPromo={dashboard.nextPromo} sbcImpact={dashboard.latestSbcImpact}/>
          </aside>
        </section>
        {isMobile&&topAlerts.length?<div className="mobile-top-alerts"><div className="section-head compact"><div><span>ALERTS</span><h2>Affecting your watchlist</h2></div><Link to="/watchlist">Open</Link></div>{topAlerts.map((x,index)=><Link className="feed-row compact" key={x.id||index} to={x.href||`/players/${x.cardId}`}><i/><div><strong>{x.title}</strong><span>{[x.movement,x.age].filter(Boolean).join(" · ")}</span></div></Link>)}</div>:null}
      </>:null}

      {showTrackRecord?<TrackRecordSummary/>:null}

      {showOpportunities?<>
        <section className="queue-section" id="queue"><div className="section-head"><div><span>LIVE OPPORTUNITIES</span><h2>Next best moves</h2></div><Link to="/players">Find a player <ArrowRight size={15}/></Link></div><FreePicksNote meta={dashboard.freePicks} lockedPreviews={queue.filter(x=>x.locked).length}/><div className="queue-grid">{queue.length?queue.map((item,index)=><QueueCard key={item.cardId} item={item} rank={index+(lead?2:1)} onOpen={setSelected} onWatch={watchItem} watchState={watchStates[String(item.cardId)]}/>):<EmptyFeedReason noun="opportunities" filtersActive={false}/>}{dashboard.locked?.opportunityFeed&&dashboard.locked?.previewCount>0?<LockedPreviewCard count={dashboard.locked.previewCount} navigate={navigate}/>:null}</div></section>
        <section className="strategy-section"><div className="section-head"><div><span>STRATEGY FINDER</span><h2>Find a different angle</h2></div></div><div className="strategy-tabs">{STRATEGIES.map(([key,label])=><button key={key} className={strategy===key?"active":""} onClick={()=>setStrategy(key)}>{label}</button>)}</div><p className="strategy-blurb">{STRATEGY_BLURBS[strategy]||strategyQuery.data?.description}</p><div className="strategy-grid">{strategyQuery.isLoading?Array.from({length:4},(_,i)=><div className="v2-skel-card strategy-card-new" key={i} aria-hidden="true"><SkeletonBlock style={{height:60}}/><div><SkeletonLine width="70%"/><SkeletonLine width="45%"/></div></div>):strategyItems.length?strategyItems.map(item=><StrategyCard key={item.cardId} item={item} onOpen={setSelected} onWatch={watchItem} watchState={watchStates[String(item.cardId)]}/>):<EmptyState compact text={STRATEGY_EMPTY_TEXT[strategy]||DEFAULT_STRATEGY_EMPTY_TEXT}/>}</div>
          {!strategyQuery.isLoading&&watchItems.length?<div className="strategy-watch-section"><div className="strategy-watch-head"><span>NEAR ENTRY</span><p>Not yet actionable - within reach of entry, still profitable at today's price. Not a current recommendation.</p></div><div className="strategy-watch-grid">{visibleWatchItems.map(item=><WatchCard key={item.cardId} item={item} onOpen={setSelected} onWatch={watchItem} watchState={watchStates[String(item.cardId)]}/>)}</div>{watchItems.length>visibleWatchItems.length?<Link className="strategy-watch-more" to="/trade-finder">View {watchItems.length-visibleWatchItems.length} more near-entry cards <ArrowRight size={14}/></Link>:null}</div>:null}
        </section>
      </>:null}

      {/* MARKET MAP REMOVED.
          It rendered the top 7 items of `futggItems` - the same pool
          Top Card and "Next best moves" are drawn from - as a name, a bar
          scaled to |expected ROI| and the same ROI figure already printed
          on those cards. It answered no question the sections above it
          did not already answer, under a heading ("Where coins are
          moving") promising a segment-level view of the market that the
          data behind it never contained: it was seven individual cards,
          not a map. A real market map - which promo/rating band/price tier
          is gaining or losing demand, and whether a move is broad or one
          outlier - needs per-segment aggregates over price history that
          this deployment does not compute today, so it is removed rather
          than restyled. See the audit notes for what building it properly
          would require. */}
      {showMarket?<section className="activity-section"><div className="activity-feed"><div className="section-head compact"><div><span>YOUR FEED</span><h2>Alerts & content</h2></div><Link to="/watchlist">Manage alerts</Link></div>{feedRows.map((row,index)=><Link className="feed-row" key={row.key||index} to={row.href}><i/><div><strong>{row.title}</strong><span>{row.detail}</span></div></Link>)}{!feedRows.length?<EmptyState compact text={dashboard.watchedCount>0?"No price alerts yet. We'll let you know when a watched card hits one of your alert conditions.":"Nothing on your watchlist yet. Add a card and we'll track its price for you."} action={dashboard.watchedCount>0?null:<button onClick={()=>navigate("/players")}>Find a player</button>}/>:null}</div></section>:null}

      <p className="fut-disclaimer">Recent sales and live listings are used to estimate opportunities. Profit is never guaranteed and EA charges 5% on sales.</p>
    </main>
    {selected&&<AnalysisModal item={selected} onClose={()=>setSelected(null)} navigate={navigate} onWatch={watchItem} watchState={watchStates[String(selected.cardId)]}/>}
  </div>;
}

// `page` variant: PlayerPage.jsx renders this directly as a standalone
// route (/players/:cardId), not as a popup over other content. The
// default "modal" rendering is a position:fixed, viewport-centred
// backdrop with its own internal max-height/scroll - correct for a
// click-triggered overlay, but on a real page it fought the persistent
// sidebar (fixed-position centring ignores the sidebar's reserved
// width, so the card art rendered partly behind it) and put the whole
// analysis inside its own tiny scroll box instead of the normal page
// scroll, which is also what let the sticky card art column read as
// "floating halfway down the page" once FutggMarketSection's long
// `extra` content pushed that inner box far taller than the viewport.
// `page` drops the fixed backdrop/centring/own-scrollbar entirely and
// renders in normal flow, inside .v2-shell-content like every other
// route, so the sidebar layout and page scrolling both work the same
// way they do everywhere else.
export function AnalysisModal({item,onClose,navigate,onWatch,watchState,backLabel,extra,variant="modal"}){const isPage=variant==="page";useEffect(()=>{if(isPage)return;const previous=document.body.style.overflow;document.body.style.overflow="hidden";const close=(event)=>event.key==="Escape"&&onClose();window.addEventListener("keydown",close);return()=>{document.body.style.overflow=previous;window.removeEventListener("keydown",close)}},[onClose,isPage]);const isFutgg=item.source==="futgg";
  // `entry` is what a user would actually PAY - the live ask - and is
  // used for chart reference lines and the portfolio hand-off.
  const entry=Number(item.entryPrice??item.currentBin)||0;
  // The un-fallbacked counterpart to `entry` above: null when there is
  // no genuine recommended-entry price (e.g. an insufficient_data
  // card), rather than silently substituting the live ask. `entry`
  // itself must keep falling back to currentBin - it is legitimately
  // "the live ask" for the AVOID/break-even-target and portfolio-
  // handoff paths below - but the WAIT/BUY verdict text and the chart's
  // BUY reference line must be able to tell "no real target" apart from
  // "the target happens to equal the live price", which `entry` alone
  // cannot express. See PlayerPage.jsx's toFutggAnalysisItem for where
  // item.entryPrice stops being contaminated with current_bin.
  const realEntry=item.entryPrice==null?null:Number(item.entryPrice);
  // The advised CEILING is a different number and must never fall back
  // to the live ask. On a WATCH the engine deliberately returns no
  // executable entry ("not buyable at a sensible price right now"), and
  // this panel used to substitute currentBin under a "Maximum entry"
  // label - reintroducing exactly the circularity engine v2 removed:
  // "the maximum you should pay" becoming "whatever it currently costs".
  // Seen on a 98 Haaland with no usable sales: "BUY BELOW 895,000"
  // beside a blank profit and a blank sell target - a confident-looking
  // ceiling that was just the asking price echoed back.
  const ceiling=isFutgg
    ?(item.buyBelow??item.recommendedBuyMax??null)
    :(item.entryPrice??item.currentBin??null);
  // The price the profit figure is actually computed against. On a BUY
  // that is the live ask; on a WATCH it is the trigger price. Showing
  // the CEILING here instead - while the profit assumed the ask - is how
  // a live Xhaka read "BUY BELOW 85,000" directly above "+25,650": the
  // 25,650 assumed a 66,500 entry that appeared nowhere on the card, and
  // buying at the 85,000 actually advertised would have returned 7,150.
  // Whatever number sits beside the profit must be the one it assumes.
  const basis=isFutgg?(item.expectedProfitBasis??null):null;
  const buyPrice=isFutgg?(basis??ceiling):ceiling;
  // Only worth showing separately when it is genuinely a different
  // number - on a watch the trigger IS the basis.
  const showCeiling=isFutgg&&ceiling!=null&&basis!=null&&ceiling!==basis;
  const gain=expectedProfit(item);const negativeCall=isNegativeCall(item);
  // On a negativeCall the label promises "Break-even sale / Minimum
  // sale to cover tax" - that must be the actual break-even price
  // (modalTarget's entry/.95 math), never the backend's normal
  // recommendedSellTarget. That field is "the price that gives the
  // full expected profit", a materially higher number than break-even
  // - showing it under a "minimum to cover tax" label was the second
  // half of the AVOID-card contradiction (an Erling Haaland avoid read
  // "sell above 690,000 just to cover tax" when 690,000 was actually
  // the full-profit target; the true break-even was ~637,000).
  const target=(!negativeCall&&isFutgg&&item.recommendedSellTarget!=null)?Number(item.recommendedSellTarget):modalTarget(item,entry,gain);const confidence=item.confidence==null?null:Math.max(0,Math.min(100,Math.round(Number(item.confidence))));const fair=Number(item.fairValue)||0;
  // Never inferred from anything else (a number, a risk tier, a
  // confidence score all also differ on a BIN-derived call, but none of
  // those differences is the actual signal a user must see) - only this
  // one field, set by the backend, ever decides whether this badge shows.
  // See app/services/futgg_bin_valuation.py's module docstring.
  const isBinEstimate=item.evidenceQuality==="estimated";
  const reasons=[fair>entry?`${percent((fair-entry)/entry*100)} below recent value`:reason(item),negativeCall?"The recommendation is to avoid this card at the current price - the figures above are hypothetical, not a target":(gain>0?<><CoinValue value={gain} signed/> estimated after EA tax</>:`Current price does not leave enough margin`),`${item.risk||"Unknown"} risk · ${sellingSpeedLabel(item.liquidityScore)} selling speed`];
  if(isBinEstimate)reasons.push("Based on recent asking-price movement, not confirmed sales - treat this one with extra caution.");

  // One navigation model per surface. The page variant previously
  // rendered BOTH a circular X in the corner AND a "Back to player
  // search" link, which are two different mental models of where you
  // are (an overlay you dismiss vs a page you navigate away from) shown
  // at the same time. A page gets the back link; an overlay gets the X.
  const isWait=String(item.recommendation||"").toUpperCase()==="WAIT";
  const logTradeHref=`/portfolio?card_id=${item.cardId}&buy=${entry}&sell=${target}&quantity=1&player=${encodeURIComponent(nameOf(item.player))}`;
  const watchButton=(className)=><button className={className} onClick={()=>onWatch(item)}><Star size={17} fill={watchState==="saved"?"currentColor":"none"}/>{watchState==="saved"?(isWait?"Watching for entry":"Watching"):(isWait?"Watch for entry":"Add to watchlist")}</button>;
  // The primary action must not contradict the verdict. A large bright
  // "LOG TRADE" beside a WAIT recommendation tells the reader to do the
  // exact thing the page just advised against; logging a purchase they
  // have ALREADY made is still a legitimate thing to want, so it stays -
  // as the secondary action, labelled as what it is.
  const actions=negativeCall
    ? <><button onClick={()=>navigate("/trade-finder")}><Target size={17}/>Find a better trade</button>{watchButton("secondary watch-action")}</>
    : isWait
      ? <>{watchButton("watch-action")}<button className="secondary" onClick={()=>navigate(logTradeHref)}><Target size={17}/>I already bought this — log it</button></>
      : <><button onClick={()=>navigate(logTradeHref)}><Target size={17}/>Log trade</button>{watchButton("secondary watch-action")}</>;

  // The chart + summary block. On the standalone page it is rendered as
  // a full-width row BELOW both columns rather than inside the right-
  // hand copy column: the card-art column is short and the copy column
  // is very long, so everything under the card image sat beside a tall
  // empty left-hand gutter. On the popup it stays where it was.
  const lower=<div className="dash-modal-lower">
    <ModalPriceChart cardId={item.cardId} entryPrice={negativeCall?entry:realEntry} targetPrice={target} fairValue={fair} source={item.source}/>
    <div className="dash-modal-summary">
      <div className={`dash-confidence${confidence==null?" no-data":""}`}><div><span>Confidence</span><strong>{confidence==null?"—":`${confidence}%`}</strong></div><i><b style={{width:`${confidence??0}%`}}/></i></div>
      {item.popularity!=null?<div className="dash-confidence"><div><span>In-game popularity</span><strong>{item.popularity}%</strong></div><i><b style={{width:`${item.popularity}%`}}/></i></div>:null}
      {item.psPcBinGapPct!=null?<p className="ps-pc-gap">{item.psPcBinGapPct>0?`${Math.abs(item.psPcBinGapPct).toFixed(1)}% cheaper listed on PS`:`${Math.abs(item.psPcBinGapPct).toFixed(1)}% cheaper listed on PC`}</p>:null}
      <ul>{reasons.map((text,index)=><li key={index}><span>✓</span><span className="reason-copy">{text}</span></li>)}</ul>
      {item.raw?<div className="dash-modal-advanced"><AdvancedRationale item={item.raw}/></div>:null}
      <div className="dash-modal-actions">{actions}</div>
    </div>
  </div>;

  return <div className={isPage?"dash-page-shell":"dash-analysis-backdrop"} onMouseDown={isPage?undefined:(event)=>event.target===event.currentTarget&&onClose()}>
    <section className={`dash-analysis-modal${extra?" has-extra":""}${isPage?" page-mode":""}`} role={isPage?undefined:"dialog"} aria-modal={isPage?undefined:"true"} aria-label={isPage?undefined:`${nameOf(item.player)} quick analysis`}>
      {isPage
        ? (backLabel?<button className="dash-modal-back" onClick={onClose}><ArrowRight size={16}/>{backLabel}</button>:null)
        : <button className="dash-modal-close" onClick={onClose} aria-label="Close analysis"><X size={20}/></button>}
      <div className="dash-modal-card"><CardImage player={item.player} featured/></div>
      <div className="dash-modal-copy">
        <div className="dash-modal-heading"><div><PlayerMeta player={item.player} source={item.source}/><h2>{nameOf(item.player)}</h2></div><span className="dash-modal-call"><b className={`call ${String(item.recommendation||"WATCH").toLowerCase()}`}>{item.recommendation||"WATCH"}</b>{isBinEstimate?<span className="bin-estimate-badge" title="Based on listing-price movement, not confirmed sales">BIN history estimate</span>:null}<StatusLegend/></span></div>
        {/* The ONE live-price freshness line on this surface. The market
            section below used to render a second one from the same
            fields, so the page told you twice how old the price was. */}
        {isFutgg?<MarketFreshness priceAgeSeconds={item.priceAgeSeconds} checkedAt={item.updatedAt} className="dash-modal-freshness"/>:null}
        <ModalVerdict item={item} entry={entry} realEntry={realEntry} target={target}/>
        <div className="dash-modal-numbers">
          <Metric label={negativeCall?"Current price":(showCeiling?"Buy now":"Buy below")} value={<CoinValue value={negativeCall?entry:buyPrice}/>} sub={negativeCall?"Current market entry":(buyPrice==null?"Not enough data to set one":(showCeiling?<>Pay up to <CoinValue value={ceiling}/></>:"Maximum entry"))} tone="entry"/>
          <Metric label={negativeCall?"Return after tax":"Expected profit"} value={<CoinValue value={gain} signed/>} sub={negativeCall?"Hypothetical - not a recommendation":"After EA tax"} tone={negativeCall?"":(gain==null?"":gain>=0?"positive":"negative")}/>
          <Metric label={negativeCall?"Break-even sale":"Sell target"} value={<CoinValue value={target}/>} sub={negativeCall?"Minimum sale to cover tax":"List around this price"} tone="target"/>
        </div>
        {isPage?null:lower}
      </div>
      {isPage?<div className="dash-modal-wide">{lower}</div>:null}
      {extra?<div className="dash-modal-extra">{extra}</div>:null}
    </section>
  </div>;
}
function ModalVerdict({item,entry,realEntry,target}){if(isNegativeCall(item))return <p className="dash-modal-verdict">Avoid at <CoinValue value={entry}/>. It needs to sell above <CoinValue value={target}/> just to cover EA tax.</p>;
  // realEntry (not entry) below: entry silently falls back to the live
  // ask, which read as "wait for a better price than the price it's
  // currently at" - a real threshold or nothing, never the live price
  // relabelled as one. See AnalysisModal's realEntry comment.
  if(realEntry==null)return <p className="dash-modal-verdict">Not enough data yet to set a target for this card - check back once more sales are recorded.</p>;
  if(String(item.recommendation||"").toUpperCase()==="WAIT")return <p className="dash-modal-verdict">Wait for a better entry below <CoinValue value={realEntry}/> before considering this card.</p>;
  return <p className="dash-modal-verdict">Buy below <CoinValue value={realEntry}/>. Aim to sell around <CoinValue value={target}/>.</p>}
function isNegativeCall(item){return ["AVOID","SELL"].includes(String(item?.recommendation||"").toUpperCase())}
// null (not 0) when there's no real entry/gain to compute a target from -
// a fabricated "0" here looks like a real "sell for nothing" instruction.
function modalTarget(item,entry,gain){if(!entry)return null;if(isNegativeCall(item))return Math.ceil(entry/.95/250)*250;return gain!=null?Math.ceil((entry+gain)/.95/250)*250:null}
// Replaces the old raw dump of the 3 newest market_events titles - that
// was every SBC the collector had seen, whether or not it had done
// anything to the market, next to zero context (a bare title like "5 of
// 10 83+ Player Pick" doesn't say what it did to prices). Two real,
// specific things instead, each only shown when there's real data
// behind it: which SBCs have a MEASURED price effect (server already
// gates this to a minimum sample size and a minimum move - see
// dashboard.py's HAVING clause), and when the next promo actually is
// (admin-entered, never a guessed/invented date - see /api/events/next).
function PulseFooter({nextPromo,sbcImpact}){
  const impacts=(sbcImpact||[]).slice(0,2);
  if(!nextPromo&&!impacts.length)return null;
  return <div className="pulse-events">
    {nextPromo?<div className="pulse-promo"><i/><span><b>Next: {nextPromo.name}</b> · {formatCountdown(nextPromo.startAt)}</span></div>:null}
    {impacts.map((s,index)=>{const up=Number(s.estimatedMarketImpact)>=0;return <div key={s.eventId||index}><i className={up?"up":"down"}/><span><b>{up?"+":""}{s.estimatedMarketImpact}%</b> avg price move on cards used in "{s.title}"</span></div>})}
  </div>;
}
function Metric({label,value,sub,tone}){return <div className={`metric ${tone||""}`}><span>{label}</span><strong>{value}</strong>{sub?<small>{sub}</small>:null}</div>}
function ConfidenceRing({value,compact=false}){const score=value==null?null:Math.max(0,Math.min(100,Math.round(Number(value))));return <div className={`confidence-ring ${compact?"compact":""}${score==null?" no-data":""}`} style={{"--confidence":`${(score??0)*3.6}deg`}}><span>{score==null?"—":score}{score==null?null:<small>%</small>}</span></div>}
// A locked opportunity preview.
//
// Rebuilt as its own component with its own layout instead of being
// squeezed through the unlocked card's 7-column grid, which it never
// fitted: it renders six children where that grid expects seven, and
// `.queue-card.locked-opportunity { display: flex }` then abandoned the
// grid altogether, so the name, the reason, the two placeholder figures
// and the "Exact prices on Elite" note all competed for one flex row and
// overlapped, clipped and compressed into each other.
//
// The content rule is unchanged and still enforced server-side: the
// premium fields were never sent, so there is nothing here to blur. What
// changes is that the card now says plainly WHAT is withheld and offers
// one action, instead of showing two striped rectangles under labels.
function LockedQueueCard({item,rank}){
  return <Link className="queue-card locked-opportunity" to={`/players/${item.cardId}`} state={{from:"home"}}>
    <span className="rank">#{rank}</span>
    <div className="queue-art"><CardImage player={item.player} compact/></div>
    <div className="locked-body">
      <strong className="locked-name">{nameOf(item.player)}</strong>
      <PlayerMeta player={item.player} source={item.source}/>
      <p className="locked-reason">{item.reasoning||"Market signal"}</p>
      <p className="locked-price">{item.currentBin!=null?`Around ${count(item.currentBin)} coins right now`:"Price unavailable"}</p>
    </div>
    <div className="locked-panel">
      <span className="locked-panel-title"><Lock size={12}/> Elite only</span>
      <ul className="locked-panel-list">
        <li>Exact buy price</li>
        <li>Sell target</li>
        <li>Expected profit</li>
      </ul>
      <span className="locked-panel-cta">Unlock <ChevronRight size={14}/></span>
    </div>
  </Link>;
}

// What a Free member is told about their allowance on Home. Same numbers
// and the same refresh time as the Opportunities page, because they are
// the same picks - both read the server's own free_picks block.
//
// `lockedPreviews` exists because the old copy could say "No free pick
// has cleared our bar yet today" directly above six numbered preview
// cards. Both halves were true and together they read as a bug: picks
// existed, none of them were this viewer's. The note now says which.
export function FreePicksNote({meta,lockedPreviews=0}){
  if(!meta)return null;
  const unlocked=Number(meta.unlocked)||0;
  let lead;
  if(unlocked>0)lead=`Your ${unlocked} free ${unlocked===1?"pick is":"picks are"} unlocked below.`;
  else if(lockedPreviews>0)lead="No free pick has cleared our bar yet today. The cards below are previews of Elite picks - the player and the reason are real, the entry and target prices are not included.";
  else lead="No free pick has cleared our bar yet today.";
  return <p className="home-free-picks">{lead}{meta.nextRefreshAt?<> Next picks in <b>{formatCountdown(meta.nextRefreshAt)}</b>, at 6pm UK time.</>:null}</p>;
}

// The Top Card panel when there is no lead to show.
//
// One purposeful state per real cause, instead of one empty state whose
// copy could contradict the cards below it. Collapsed to its own height
// (see .lead-trade.is-pending in dashboard-v2.css) rather than stretching
// to match the Market Pulse column beside it.
export function TopCardPending({loading,lockedOnly,preview,freePicks,navigate}){
  // Matches .lead-layout's own shape (card art left, name + copy +
  // three metrics right) rather than a bare "loading" line, so the
  // dashboard's hero slot doesn't blank-flash before the top pick lands.
  if(loading)return <div className="lead-layout" aria-busy="true"><SkeletonBlock style={{width:230,maxWidth:"100%",height:310,justifySelf:"center"}}/><div className="lead-copy"><SkeletonLine width="55%"/><SkeletonBlock style={{height:34,width:"75%",marginTop:8}}/><SkeletonLine width="95%" className="v2-skel-line" /><div style={{marginTop:10}}><SkeletonLine width="80%"/></div><div className="lead-numbers">{Array.from({length:3},(_,i)=>(<div className="metric" key={i}><SkeletonLine width="70%"/><SkeletonBlock style={{height:22,width:"60%",marginTop:8}}/></div>))}</div></div></div>;

  const nextAt=freePicks?.nextRefreshAt;
  // The refresh time is the server's own (18:00 UK, DST-correct, from
  // the free_picks block) - never a guessed schedule.
  const nextLine=nextAt
    ? <p className="lead-pending-when">Next picks in <b>{formatCountdown(nextAt)}</b>, at 6pm UK time.</p>
    : null;

  if(lockedOnly){
    return <div className="lead-pending">
      <p className="lead-pending-eyebrow"><Lock size={13}/> TODAY'S PICKS ARE ELITE</p>
      <p className="lead-pending-title">
        {preview?<>Today's top call is <b>{nameOf(preview.player)}</b>.</>:"Today's calls have landed."}
      </p>
      <p className="lead-pending-copy">
        The player and the reason are shown below. The exact buy price, sell target and expected
        profit are part of Elite.
      </p>
      {nextLine}
      <div className="lead-pending-actions">
        <button onClick={()=>navigate(`/billing?unlock=${encodeURIComponent("today's picks")}`)}>Unlock exact buy and sell prices <ArrowRight size={16}/></button>
        <button className="secondary" onClick={()=>navigate("/opportunities")}>See the previews</button>
      </div>
    </div>;
  }

  // Nothing has cleared the bar. Not a failure - the product's own rule
  // is that an honest empty section beats a weakened one - so say that,
  // and say when to come back.
  return <div className="lead-pending">
    <p className="lead-pending-eyebrow">NO PICK YET</p>
    <p className="lead-pending-title">No trade has cleared our bar today.</p>
    <p className="lead-pending-copy">We would rather show you nothing than a call we don't believe in.</p>
    {nextLine}
    <div className="lead-pending-actions">
      <button className="secondary" onClick={()=>navigate("/players")}>Search a player <ArrowRight size={16}/></button>
    </div>
  </div>;
}

function LockedPreviewCard({count,navigate}){return <button className="queue-card locked-preview" onClick={()=>navigate(`/billing?unlock=${encodeURIComponent("the full Opportunities feed")}`)}><div className="locked-preview-copy"><strong>{count} more {count===1?"opportunity":"opportunities"} today</strong><span>Upgrade to see the full live feed, updated as the market moves.</span></div><ChevronRight className="queue-arrow" size={18}/></button>}
function QueueCard({item,rank,onOpen,onWatch,watchState}){
  // A locked row has no entry price, sell target or profit on it - not
  // hidden, absent. Rendering it through the normal signal tiles would
  // print "—" three times, which reads as a data fault rather than as the
  // free allowance working. Say what it is instead.
  if(item.locked)return <LockedQueueCard item={item} rank={rank}/>;
  const open=()=>onOpen(item);return <div className="queue-card" role="button" tabIndex={0} onClick={open} onKeyDown={event=>(event.key==="Enter"||event.key===" ")&&open()}><span className="rank">#{rank}</span><div className="queue-art"><CardImage player={item.player} compact/></div><div className="queue-name"><strong>{nameOf(item.player)}</strong>{item.evidenceQuality==="estimated"?<span className="bin-estimate-badge" title="Based on listing-price movement, not confirmed sales">BIN estimate</span>:null}<PlayerMeta player={item.player} source={item.source}/><small>{reason(item)}</small>{item.source==="futgg"?<MarketFreshness priceAgeSeconds={item.priceAgeSeconds} checkedAt={item.updatedAt} compact/>:null}</div><button className={`queue-watch ${watchState==="saved"?"saved":""}`} onClick={event=>{event.stopPropagation();onWatch(item)}} aria-label={watchState==="saved"?`${nameOf(item.player)} is on your watchlist`:`Add ${nameOf(item.player)} to watchlist`}><Star size={17} fill={watchState==="saved"?"currentColor":"none"}/></button><div className="queue-reason"><b>WHY THIS MOVE</b><span>{reason(item)}</span></div><div className="queue-signals"><TradeSignal label="BUY BELOW" value={item.entryPrice??item.currentBin} tone="entry"/><TradeSignal label="EXPECTED PROFIT" value={expectedProfit(item)} tone="profit" signed sub={`${roi(item)} after tax`}/></div><ConfidenceRing value={item.confidence} compact/><ChevronRight className="queue-arrow" size={18}/></div>}
// Live-entry-state badge - the server-computed answer to "can I act on
// this right now" (app/services/strategy_definitions.py's
// _live_entry_state). Never derived client-side: the whole point is
// that the frontend is not the source of truth for whether a price is
// still valid.
const ENTRY_STATE_COPY={
  buy_now:{text:"Buy now",tone:"buy-now"},
  watch:{text:"Watch",tone:"watch"},
  stale:{text:"Stale · updating",tone:"stale"},
  expired:{text:"Expired",tone:"expired"},
};
function EntryStateBadge({state}){if(!state)return null;const copy=ENTRY_STATE_COPY[state]||{text:state,tone:"watch"};return <span className={`entry-state-badge ${copy.tone}`}>{copy.text}</span>}

function StrategyCard({item,onOpen,onWatch,watchState}){
  const open=()=>onOpen(item);
  const entryState=item.liveEntryState;
  const actionable=entryState==="buy_now";
  return <div className={`strategy-card-new${actionable?" is-actionable":""}`} role="button" tabIndex={0} onClick={open} onKeyDown={event=>(event.key==="Enter"||event.key===" ")&&open()}>
    <div className="strategy-card-art"><CardImage player={item.player} compact/></div>
    <div className="strategy-card-copy">
      <div className="strategy-card-title"><strong>{nameOf(item.player)}</strong><EntryStateBadge state={entryState}/><button className={`queue-watch ${watchState==="saved"?"saved":""}`} onClick={event=>{event.stopPropagation();onWatch(item)}} aria-label={`Add ${nameOf(item.player)} to watchlist`}><Star size={16} fill={watchState==="saved"?"currentColor":"none"}/></button></div>
      <PlayerMeta player={item.player}/>
      <p>{item.risk||"Unknown"} risk · {sellingSpeedLabel(item.liquidityScore)} selling speed{item.confidence!=null?` · ${Math.round(item.confidence)}% confidence`:""}</p>
      {item.source==="futgg"?<MarketFreshness priceAgeSeconds={item.priceAgeSeconds} checkedAt={item.updatedAt} compact/>:null}
      {item.whyThisFits?<p className="strategy-why">{item.whyThisFits}</p>:null}
    </div>
    <div className="strategy-trade">
      <TradeSignal label="CURRENT PRICE" value={item.currentBin} tone="current"/>
      {/* A price already above its buy threshold must never sit next to
          a "buy below" label implying it is still an entry - the badge
          above already says watch/stale/expired, so this stays the plain
          threshold number rather than re-labelling it "buy now" itself. */}
      <TradeSignal label="BUY BELOW" value={item.entryPrice??item.currentBin} tone="entry"/>
      <TradeSignal label="TARGET" value={item.recommendedSellTarget} tone="target"/>
      <TradeSignal label="EXPECTED PROFIT" value={expectedProfit(item)} tone="profit" signed sub={`${roi(item)} after tax`}/>
    </div>
    <ChevronRight className="strategy-arrow" size={18}/>
  </div>;
}

// Compact card for the separate Watch section - deliberately smaller
// and less prominent than StrategyCard, and never shows "BUY BELOW"/
// "EXPECTED PROFIT" phrased as if it were current: every number here is
// explicitly the real-time-from-current-price figure
// (classify_watch_candidate on the backend), not the engine's
// prospective trigger-price estimate.
function WatchCard({item,onOpen,onWatch,watchState}){
  const open=()=>onOpen(item);
  return <div className="watch-card" role="button" tabIndex={0} onClick={open} onKeyDown={event=>(event.key==="Enter"||event.key===" ")&&open()}>
    <div className="watch-card-art"><CardImage player={item.player} compact/></div>
    <div className="watch-card-copy">
      <div className="watch-card-title"><strong>{nameOf(item.player)}</strong><EntryStateBadge state="watch"/><button className={`queue-watch ${watchState==="saved"?"saved":""}`} onClick={event=>{event.stopPropagation();onWatch(item)}} aria-label={`Add ${nameOf(item.player)} to watchlist`}><Star size={14} fill={watchState==="saved"?"currentColor":"none"}/></button></div>
      <p>{item.aboveEntryPct!=null?`${item.aboveEntryPct.toFixed(1)}% above entry`:"Near entry"} · would still net <CoinValue value={item.realTimeExpectedProfitAfterTax} signed/> after tax at today's price</p>
    </div>
  </div>;
}
function TradeSignal({label,value,tone,signed=false,sub}){return <div className={`trade-signal ${tone}`}><span>{label}</span><strong><CoinValue value={value} signed={signed}/></strong>{sub?<small>{sub}</small>:null}</div>}
function PlayerMeta({player={}}){return <div className="player-meta-pills"><span>{player.rating||"—"} OVR</span><span>{player.position||"—"}</span><span>{versionLabel(player.version)}</span></div>}
// Plain-language names for the market_events.kind discriminator. The
// ticker previously rendered every event as `${title} — EVENT`, which is
// where "Lionel Messi — EVENT" came from: `EVENT` is not a description,
// it is the absence of one, and `kind` (the field that actually says what
// happened) was never read. An unmapped kind is skipped entirely rather
// than falling back to printing the raw enum at a customer.
const EVENT_KIND_NOUN={sbc:"SBC",promo:"Promo",objective:"Objective",evolution:"Evolution",pack:"Store pack"};
// Separate mid-sentence forms: "SBC" is an acronym and must stay
// upper-case, so a generic .toLowerCase() produced "New sbc".
const EVENT_KIND_NOUN_MID={sbc:"SBC",promo:"promo",objective:"objective",evolution:"Evolution",pack:"store pack"};
const NEW_EVENT_WINDOW_MS=48*60*60*1000;

export function eventTickerRow(event){
  const noun=EVENT_KIND_NOUN[String(event?.kind||"").toLowerCase()];
  if(!noun||!event?.title)return null;
  const now=Date.now();
  const ends=event.endsAt?new Date(event.endsAt).getTime():null;
  const starts=event.startsAt?new Date(event.startsAt).getTime():null;
  const firstSeen=event.firstSeenAt?new Date(event.firstSeenAt).getTime():null;
  // Expiring soonest is the most actionable thing a ticker can say, so it
  // wins over "new" when both are true.
  if(ends&&ends>now&&ends-now<=NEW_EVENT_WINDOW_MS)
    return{label:`${noun} ends in ${formatCountdown(event.endsAt)}`,value:event.title,tone:"event"};
  if(ends&&ends<=now)return null;                       // finished: not news
  if(firstSeen&&now-firstSeen<=NEW_EVENT_WINDOW_MS)
    return{label:`New ${EVENT_KIND_NOUN_MID[String(event.kind).toLowerCase()]||noun}`,value:event.title,tone:"event"};
  if(starts&&starts>now)
    return{label:`${noun} starts in ${formatCountdown(event.startsAt)}`,value:event.title,tone:"event"};
  return{label:`${noun} live now`,value:event.title,tone:"event"};
}

function Ticker({items,events}){
  // Every row now says what happened and to which card, rather than a
  // bare name next to an unexplained number. Card rows are labelled as
  // an expected return so they cannot be misread as a price change,
  // which is a different measurement entirely.
  // A "0.0% expected return" ticker row next to an SBC headline reads as
  // broken - it says nothing happened. Every item here already cleared
  // the BUY bar server-side, so a genuinely near-zero return is the
  // exception, not the rule; require a real, rounds-to-non-zero return
  // before it's worth a scrolling headline.
  const cardRows=items.slice(0,6)
    .filter(x=>Number.isFinite(Number(x.expectedRoi))&&Math.abs(Number(x.expectedRoi))>=0.5)
    .map(x=>({
      label:`${nameOf(x.player)}${x.player?.rating?` (${x.player.rating})`:""}`,
      value:`${roi(x)} expected return`,
      tone:Number(x.expectedRoi)>=0?"up":"down",
    }));
  const eventRows=(events||[]).map(eventTickerRow).filter(Boolean);
  // Deduplicated: the same SBC can appear more than once in the feed
  // (re-ingested under a new external_id), and a scrolling strip repeating
  // one title reads as broken.
  const seen=new Set();
  const rows=[...eventRows,...cardRows].filter(row=>{
    const key=`${row.label}|${row.value}`;
    if(seen.has(key))return false;
    seen.add(key);
    return true;
  }).slice(0,10);
  if(!rows.length)return null;
  return <div className="fut-ticker"><div>{[...rows,...rows].map((x,index)=><span key={`${x.label}-${x.value}-${index}`} className={x.tone}><b>{x.label}</b> — {x.value}</span>)}</div></div>;
}
function CardImage({player={},featured,compact}){if(player.generatedCardUrl)return <img className={`ft-card-img ${featured?"featured":compact?"compact":""}`} src={player.generatedCardUrl} alt={nameOf(player)}/>;return <PlayerCardArt compact={compact} featured={Boolean(featured)} bgImage={player.cardBgImage} cutoutImage={player.cardCutoutImage} cutoutType={player.cardCutoutType||"special"} fallbackImage={player.imageUrl} rating={player.rating} position={player.position} name={nameOf(player)} altText={nameOf(player)} stats={player.stats} nationImage={player.nationImage} leagueImage={player.leagueImage} clubImage={player.clubImage} showStats={Boolean(featured)} widthClass={featured?"w-52":"w-16"}/>}
// GET /api/v2/opportunities items are already server-side filtered to
// buy/strong_buy signals only, so recommendation is always "BUY" here -
// unlike normalise() this never has to interpret a status/score. expected_roi
// and confidence_score arrive as 0-1 fractions from the API; this dashboard's
// profit()/ConfidenceRing both expect 0-100 scale, same convention normalise()
// already uses via toPct().
function normaliseFutgg(raw){if(!raw)return null;const cardId=raw.card_id??raw.source_card_id;if(cardId==null)return null;const expectedRoi=raw.expected_roi==null?null:Number(raw.expected_roi)*100;const risk=raw.risk_level?raw.risk_level.charAt(0).toUpperCase()+raw.risk_level.slice(1):"Unknown";const reasons=Array.isArray(raw.signal_reasons)?raw.signal_reasons:[];return{cardId,source:"futgg",recommendation:"BUY",expectedRoi,netRoi:{likely:expectedRoi},
  // `locked` is the server's own answer on the row (app/auth/access.py).
  // A locked row has no buy target, sell target or profit in it at all -
  // there is nothing here to hide, only a card to render honestly.
  locked:raw.locked===true,currentBin:raw.current_bin??raw.current_bin_approx??null,fairValue:raw.fair_value,
  // Kept for the Advanced rationale panel (Layer 2), which reads the raw
  // FUT.GG field names directly rather than duplicating them onto this
  // normalised shape - see AdvancedRationale.jsx.
  raw,liquidityScore:raw.liquidity_score,trendDescription:raw.trend_description,
  entryPrice:raw.recommended_buy_max??raw.current_bin,
  // AnalysisModal's "Buy Below" tile reads item.buyBelow/
  // item.recommendedBuyMax/item.expectedProfitBasis (its own ceiling/
  // basis computation) - a SEPARATE field path from entryPrice above.
  // Leaving these unset left the tile blank ("Not enough data to set
  // one") even on a card with a real recommended_buy_max. See
  // PlayerPage.jsx's toFutggAnalysisItem for the same fix and fuller
  // explanation - both build the FUT.GG-sourced items this modal renders.
  buyBelow:raw.buy_below??null,recommendedBuyMax:raw.recommended_buy_max??null,expectedProfitBasis:raw.expected_profit_basis??null,
  recommendedSellTarget:raw.recommended_sell_target,expectedProfitAfterTax:raw.expected_profit_after_tax,breakEvenPrice:null,sales24h:raw.recent_sale_count,holdingPeriod:"Flexible",risk,confidence:raw.confidence_score==null?null:Number(raw.confidence_score)*100,updatedAt:raw.current_bin_checked_at||raw.bin_checked_at||raw.current_bin_captured_at,priceAgeSeconds:raw.price_age_seconds,
  // Only present on Strategy Finder items (GET /trade-finder/strategy/*)
  // - the main Opportunities feed doesn't compute these. See
  // app/routers/v2/futgg_market.py's _live_entry_state() for the state
  // machine: buy_now/watch/stale/expired, never "buy_now" once the price
  // is too old to trust.
  liveEntryState:raw.live_entry_state??null,whyThisFits:raw.why_this_fits??null,
  // Only present on watch_items - real-time numbers computed from the
  // CURRENT price (classify_watch_candidate), never the prospective
  // trigger-price number evaluate_card() uses for expectedProfitAfterTax.
  aboveEntryPct:raw.above_entry_pct??null,realTimeExpectedProfitAfterTax:raw.real_time_expected_profit_after_tax??null,realTimeExpectedRoi:raw.real_time_expected_roi==null?null:Number(raw.real_time_expected_roi)*100,
  // Evidence provenance (app/services/futgg_bin_valuation.py) - see
  // PlayerPage.jsx's toFutggAnalysisItem for the same fields and fuller
  // explanation. AnalysisModal reads evidenceQuality to decide whether to
  // show the "BIN history estimate" badge - never inferred from anything
  // else, since a BIN observation must never be presented as a sale.
  valuationSource:raw.valuation_source,evidenceQuality:raw.evidence_quality,
  reasoning:reasons[0]||raw.headline_reason||"Market signal",player:{name:raw.name,displayName:raw.name,cardName:raw.name,nickname:null,rating:raw.rating,version:raw.rarity||"Card",position:raw.position||raw.primary_position,imageUrl:raw.image_url,cardBgImage:raw.card_design_image_url||null,cardCutoutImage:raw.image_url||raw.player_image_url||null,cardCutoutType:raw.price_tier&&raw.price_tier!=="special"?"base":"special",/* composited_image_url (background+cutout+rating+stats baked in by FUT.GG, mirrored to R2) always wins when present - it's shown as a plain <img> via CardImage()'s player.generatedCardUrl check, never composited/overlaid by PlayerCardArt, which would double the baked-in rating/stats text. Falls back to the pre-existing bgImage-gated logic only when a card hasn't been mirrored yet. */generatedCardUrl:raw.composited_image_url||(raw.card_design_image_url?null:(raw.image_url||raw.player_image_url||null)),nationImage:raw.nation_image_url||null,leagueImage:raw.league_image_url||null,clubImage:raw.club_image_url||null,stats:{pace:raw.pace,shooting:raw.shooting,passing:raw.passing,dribbling:raw.dribbling,defending:raw.defending,physicality:raw.physicality}}}}
function normalise(item){if(!item)return null;if(item.player)return{...item,
  locked:item.locked===true,currentBin:item.currentBin??item.currentBinApprox??null,
  reasoning:item.reasoning||item.headlineReason||null,
  player:{...item.player,displayName:item.player.nickname||item.player.cardName||item.player.displayName||item.player.name}};return{...item,cardId:item.cardId??item.card_id,recommendation:item.recommendation??item.status,expectedRoi:toPct(item.likely_net_roi),netRoi:{likely:toPct(item.likely_net_roi)},currentBin:item.currentBin??item.current_bin,fairValue:item.fairValue??item.fair_value_24h,entryPrice:item.entryPrice??item.entry_price,breakEvenPrice:item.breakEvenPrice??item.break_even_sale_price,sales24h:item.sales24h??item.sales_24h,holdingPeriod:item.holdingPeriod??"Flexible",risk:item.risk??riskLabel(item.score_risk),confidence:item.confidence??item.score_confidence??null,updatedAt:item.updatedAt??item.computed_at,reasoning:item.reasoning||statusReason(item.status),player:{name:item.name,displayName:item.nickname||item.card_name||item.display_name||item.name,cardName:item.card_name,nickname:item.nickname,rating:item.rating,version:item.version,position:item.position,imageUrl:item.image_url,cardBgImage:item.card_bg_image,cardCutoutImage:item.card_cutout_image,cardCutoutType:item.card_cutout_type,generatedCardUrl:item.generated_card_url,nationImage:item.nation_image,leagueImage:item.league_image,clubImage:item.club_image,stats:{pace:item.pace,shooting:item.shooting,passing:item.passing,dribbling:item.dribbling,defending:item.defending,physicality:item.physicality}}}}
function nameOf(player={}){return player.nickname||player.cardName||player.displayName||player.name||"Unknown player"}
// null (not 0) when there's no real entry/ROI to compute from - a
// fabricated "0" reads as a confident "zero expected profit" answer
// rather than "no recommendation data for this card."
// Checks the raw value for null BEFORE calling Number() on it - Number(null)
// is 0 (not NaN), so relying on Number.isFinite alone after coercion let a
// genuinely-missing ROI silently become a "confirmed 0% ROI", which then
// made this function's own real-zero-vs-no-data distinction meaningless.
function profit(item){const entry=Number(item?.entryPrice??item?.currentBin);const returnPctRaw=item?.netRoi?.likely??item?.expectedRoi;if(returnPctRaw==null)return null;const returnPct=Number(returnPctRaw);return entry&&Number.isFinite(returnPct)?Math.round(entry*returnPct/100):null}
// FUT.GG items carry the backend's own expected_profit_after_tax, computed
// with correct EA price-increment rounding (see trading_math.py) - prefer
// that over profit()'s generic entry*roi/100 recompute, which doesn't know
// about EA's real listing increments and can suggest an unlistable price.
function expectedProfit(item){return item?.source==="futgg"&&item.expectedProfitAfterTax!=null?Math.round(Number(item.expectedProfitAfterTax)):profit(item)}function count(value){const number=Number(value);return Number.isFinite(number)?new Intl.NumberFormat("en-GB",{notation:number>999?"compact":"standard"}).format(number):"—"}function percent(value){const number=Number(value);return Number.isFinite(number)?`${number>0?"+":""}${number.toFixed(1)}%`:"—"}function roi(item){return percent(item?.netRoi?.likely??item?.expectedRoi)}function toPct(value){if(value==null)return null;const number=Number(value);return!Number.isFinite(number)?null:Math.abs(number)<=1?number*100:number}function riskLabel(value){const number=Number(value);return!Number.isFinite(number)?"Unknown":number>=70?"High":number>=40?"Medium":"Low"}function statusReason(status){return status==="BUY"?"Price and sales data support an entry.":status==="WAIT"?"Useful card, but the current entry is weak.":"Risk currently outweighs the likely return."}function reason(item){return item?.trendDescription||item?.reasoning?buildWhyText(item):statusReason(item?.recommendation)}// The market's CONDITION comes from the backend's own regime label - it
// is the only thing that has actually looked at trend, discount and
// liquidity. `score` is confidence in that verdict, a different axis
// entirely, and deriving the headline from it (score>=80 -> "Strong
// market") is how the dashboard came to announce "STRONG MARKET 95/100"
// directly above "BUY SIGNALS 0": the old confidence was a candidate
// count, so a large scan of cards nothing could be said about read as a
// strong market. A confident read of a FALLING market would have been
// mislabelled the same way.
// The regime LABEL only has four possible values (see STATE_LABEL-style
// mapping upstream) and stays on whichever one is true for days at a
// time, while `score` - a completely different axis, confidence in that
// label - moves every refresh. Reading "text never changes, just the
// number" was the natural result of only ever qualifying the label
// below one single cutoff: a score crawling from 41 to 79 showed the
// exact same string throughout. Graduated bands make the qualifier
// track the number the way the number visibly moves, without inventing
// a market condition the backend never actually said.
function confidenceBand(score){
  if(score<25)return"very low confidence";
  if(score<MIN_CONFIDENT_SCORE)return"low confidence";
  if(score<70)return"building confidence";
  if(score<85)return"good confidence";
  return"high confidence";
}
function mood(label,score){
  const known=String(label||"").trim();
  if(!known||known==="Unknown")return"Checking the market";
  return`${known} (${confidenceBand(score)})`;
}
const MIN_CONFIDENT_SCORE=40;
