import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Activity, ArrowRight, Bell, ChevronRight, Search, Star, Target, X, Zap } from "lucide-react";
import { useDashboard } from "../../hooks/useDashboard";
import { useFutggMarketFreshness, useFutggOpportunities, useFutggTradeFinder } from "../../hooks/useFutggMarket";
import { useGeneratedCardImages } from "../../hooks/useRecommendationFeeds";
import PlayerCardArt from "../../../components/PlayerCardArt";
import { addWatch } from "../../../api/watchlist";
import ModalPriceChart from "./ModalPriceChart";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import MarketFreshness from "../../components/MarketFreshness";
import TrackRecordPanel from "../../components/TrackRecordPanel";
import "../../styles/terminal.css";
import "../../styles/dashboard-v2.css";
import "../../styles/dashboard-analysis-modal.css";
import "../../styles/mobile-polish.css";

const EMPTY={marketRegime:{label:"Unknown",confidence:0,summary:"",metrics:{}},todaysOpportunities:[],highConfidenceInvestments:[],cardsToAvoid:[],biggestMovers:[],watchlistAlerts:[],latestMarketEvents:[],latestSbcImpact:[],locked:{opportunityFeed:false,previewCount:0}};
// Strategy Finder used to hit the legacy FUTBIN engine's
// /api/v2/recommendations/strategy/{name} - that pipeline's fair_value_mv
// is broken (see the comment above baseItems below), so every tab always
// came back empty. Rebuilt on the same /api/v2/trade-finder endpoint the
// rest of this page already uses for live FUT.GG data. Tabs are limited to
// what trade-finder can actually answer honestly - it has no holding-
// period or SBC-relevance signal, so "2-3 days"/"Long hold"/"SBC plays"
// were dropped rather than faked onto an unrelated sort.
const STRATEGIES=[["best_opportunity","For you"],["liquidity","Quick flips"],["low_risk","Low risk"],["roi","Best ROI"],["confidence","Most confident"]];
const STRATEGY_PARAMS={
  best_opportunity:{sort_by:"best_opportunity"},
  liquidity:{sort_by:"liquidity"},
  low_risk:{risk_tolerance:"low",sort_by:"confidence"},
  roi:{sort_by:"roi"},
  confidence:{sort_by:"confidence"},
};

export default function HomeDashboard(){
  const dashboardQuery=useDashboard();
  const dashboard=dashboardQuery.data??EMPTY;
  const navigate=useNavigate();
  const {pathname}=useLocation();
  const [strategy,setStrategy]=useState("best_opportunity");
  const [watchStates,setWatchStates]=useState({});
  const [selected,setSelected]=useState(null);
  const strategyQuery=useFutggTradeFinder({...STRATEGY_PARAMS[strategy],page_size:12});
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
  const ids=[...baseItems,...strategyRaw].map(x=>x.cardId);
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
  const opportunities=futggItems.filter(x=>x.recommendation==="BUY");
  const lead=opportunities[0]??null;
  const queue=opportunities.filter(x=>String(x.cardId)!==String(lead?.cardId)).slice(0,6);
  const shown=new Set([lead,...queue].filter(Boolean).map(x=>String(x.cardId)));
  const strategyItems=strategyRaw.map(hydrate).filter(x=>!shown.has(String(x.cardId))).slice(0,8);
  const buyCount=futggItems.filter(x=>x.recommendation==="BUY").length;
  const score=Math.round(Number(dashboard.marketRegime?.confidence||0));
  // Prefer the backend's own expected_profit_after_tax (correct EA price-
  // increment rounding) over the generic profit() recompute, same reason
  // AnalysisModal's `gain`/`target` do below.
  const potential=futggItems.filter(x=>x.recommendation==="BUY").reduce((sum,item)=>sum+Math.max(0,expectedProfit(item)||0),0);

  useEffect(()=>{if(pathname!=="/v2/opportunities")return;const timer=setTimeout(()=>document.getElementById("queue")?.scrollIntoView({behavior:"smooth",block:"start"}),50);return()=>clearTimeout(timer)},[pathname]);

  async function watchItem(item){const id=String(item?.cardId||"");if(!item||watchStates[id]==="saving"||watchStates[id]==="saved")return;setWatchStates(current=>({...current,[id]:"saving"}));try{await addWatch({player_name:nameOf(item.player),card_id:id,version:item.player?.version??null,platform:"ps",source:item.source==="futgg"?"futgg":"futbin"});setWatchStates(current=>({...current,[id]:"saved"}))}catch(error){if(error?.response?.status===401)navigate("/login");else if(error?.response?.status===409)setWatchStates(current=>({...current,[id]:"saved"}));else setWatchStates(current=>({...current,[id]:"error"}))}}

  return <div className="fut-dashboard-shell">
    <main className="fut-main home-in-shell">
      <header className="fut-topbar"><button className="fut-search" onClick={()=>navigate("/v2/players")}><Search size={19}/><span>Search player, card or price</span><kbd>/</kbd></button><div className="feed-live"><i/> LIVE</div><button className="bell-btn" onClick={()=>navigate("/v2/watchlist")}><Bell size={18}/></button></header>
      <Ticker items={futggItems} events={dashboard.latestMarketEvents}/>
      <section className="decision-stage">
        <div className="lead-trade"><div className="lead-label"><Zap size={15}/> TOP CARD</div>{lead?<div className="lead-layout"><button className="lead-card player-open-button" onClick={()=>setSelected(lead)} aria-label={`Open ${nameOf(lead.player)} analysis`}><CardImage player={lead.player} featured/></button><div className="lead-copy"><div className="lead-head"><div><PlayerMeta player={lead.player} source={lead.source}/><h1>{nameOf(lead.player)}</h1></div><div className="lead-status"><b className={`call ${String(lead.recommendation).toLowerCase()}`}>{lead.recommendation}</b><ConfidenceRing value={lead.confidence}/></div></div>{lead.source==="futgg"?<MarketFreshness priceAgeSeconds={lead.priceAgeSeconds} capturedAt={lead.updatedAt}/>:null}<p>{reason(lead)}</p><div className="lead-numbers"><Metric label="Buy below" value={<CoinValue value={lead.entryPrice??lead.currentBin}/>} sub="Maximum entry" tone="entry"/><Metric label="Expected profit" value={<CoinValue value={expectedProfit(lead)} signed/>} sub="After EA tax" tone="positive"/><Metric label="Risk level" value={lead.risk||"Unknown"} sub={hold(lead.holdingPeriod)}/></div><div className="lead-actions"><button onClick={()=>setSelected(lead)}>View move <ArrowRight size={17}/></button><button className="secondary" onClick={()=>watchItem(lead)} aria-label="Add top card to watchlist"><Star size={16} fill={watchStates[String(lead.cardId)]==="saved"?"currentColor":"none"}/>{watchStates[String(lead.cardId)]==="saved"?"Watching":"Watch"}</button></div></div></div>:<EmptyState compact text="No clean trade has passed the filters yet."/>}</div>
        <aside className="market-panel"><div className="panel-label"><Activity size={15}/> MARKET PULSE</div><div className="pulse-head"><div><h2>{mood(score)}</h2><p>{dashboard.marketRegime?.summary||"Prices and completed sales are being checked now."}</p></div><strong>{score}<small>/100</small></strong></div><div className="pulse-track"><span style={{width:`${Math.max(5,score)}%`}}/></div><div className="pulse-stats"><Metric label="Buy signals" value={buyCount}/><Metric label="Coin potential" value={<CoinValue value={potential} signed/>}/><Metric label="Tracked" value={count(Number(dashboard.marketRegime?.metrics?.liquidCards||0)+Number(freshnessQuery.data?.cards_priced||0))}/></div><div className="pulse-events">{(dashboard.latestMarketEvents||[]).slice(0,3).map((event,index)=><div key={event.id||index}><i/><span>{event.title}</span></div>)}</div></aside>
      </section>
      <TrackRecordPanel/>
      <section className="queue-section" id="queue"><div className="section-head"><div><span>LIVE OPPORTUNITIES</span><h2>Next best moves</h2></div><Link to="/v2/players">Find a player <ArrowRight size={15}/></Link></div><div className="queue-grid">{queue.length?queue.map((item,index)=><QueueCard key={item.cardId} item={item} rank={index+2} onOpen={setSelected} onWatch={watchItem} watchState={watchStates[String(item.cardId)]}/>):<EmptyState compact text="No additional opportunities right now."/>}{dashboard.locked?.opportunityFeed&&dashboard.locked?.previewCount>0?<LockedPreviewCard count={dashboard.locked.previewCount} navigate={navigate}/>:null}</div></section>
      <section className="lower-grid"><div className="market-map"><div className="section-head compact"><div><span>MARKET MAP</span><h2>Where coins are moving</h2></div></div><div className="map-bars">{futggItems.length?futggItems.slice(0,7).map(item=><button key={item.cardId} onClick={()=>setSelected(item)}><span>{nameOf(item.player)}</span><i><b style={{width:`${Math.min(100,Math.max(8,Math.abs(Number(item.expectedRoi||0))*4))}%`}}/></i><strong>{roi(item)}</strong></button>):<EmptyState compact text="Still building live market data - check back as more FUT.GG cards get priced."/>}</div></div><div className="activity-feed"><div className="section-head compact"><div><span>YOUR FEED</span><h2>Alerts & content</h2></div><Link to="/v2/watchlist">Open</Link></div>{[...(dashboard.watchlistAlerts||[]).slice(0,3),...(dashboard.latestSbcImpact||[]).slice(0,3)].slice(0,5).map((x,index)=><div className="feed-row" key={x.id||x.eventId||index}><i/><div><strong>{x.title||x.message||"Market update"}</strong><span>{x.message||x.estimatedMarketImpact?`${percent(x.estimatedMarketImpact)} estimated impact`:"New market activity"}</span></div></div>)}{!dashboard.watchlistAlerts?.length&&!dashboard.latestSbcImpact?.length?<EmptyState compact text="No alerts yet. Add players to your watchlist."/>:null}</div></section>
      <section className="strategy-section"><div className="section-head"><div><span>STRATEGY FINDER</span><h2>Find a different angle</h2></div></div><div className="strategy-tabs">{STRATEGIES.map(([key,label])=><button key={key} className={strategy===key?"active":""} onClick={()=>setStrategy(key)}>{label}</button>)}</div><div className="strategy-grid">{strategyQuery.isLoading?<EmptyState compact text="Scanning this strategy…"/>:strategyItems.length?strategyItems.map(item=><StrategyCard key={item.cardId} item={item} onOpen={setSelected} onWatch={watchItem} watchState={watchStates[String(item.cardId)]}/>):<EmptyState compact text="No different cards qualify for this strategy right now."/>}</div></section>
      <p className="fut-disclaimer">Recent sales and live listings are used to estimate opportunities. Profit is never guaranteed and EA charges 5% on sales.</p>
    </main>
    {selected&&<AnalysisModal item={selected} onClose={()=>setSelected(null)} navigate={navigate} onWatch={watchItem} watchState={watchStates[String(selected.cardId)]}/>}
  </div>;
}

export function AnalysisModal({item,onClose,navigate,onWatch,watchState,backLabel,extra}){useEffect(()=>{const previous=document.body.style.overflow;document.body.style.overflow="hidden";const close=(event)=>event.key==="Escape"&&onClose();window.addEventListener("keydown",close);return()=>{document.body.style.overflow=previous;window.removeEventListener("keydown",close)}},[onClose]);const entry=Number(item.entryPrice??item.currentBin)||0;const isFutgg=item.source==="futgg";const gain=expectedProfit(item);const negativeCall=isNegativeCall(item);const target=isFutgg&&item.recommendedSellTarget!=null?Number(item.recommendedSellTarget):modalTarget(item,entry,gain);const confidence=item.confidence==null?null:Math.max(0,Math.min(100,Math.round(Number(item.confidence))));const fair=Number(item.fairValue)||0;const reasons=[fair>entry?`${percent((fair-entry)/entry*100)} below recent value`:reason(item),gain>0?<><CoinValue value={gain} signed/> estimated after EA tax</>:`Current price does not leave enough margin`,`${item.risk||"Unknown"} risk · ${hold(item.holdingPeriod)}`];return <div className="dash-analysis-backdrop" onMouseDown={(event)=>event.target===event.currentTarget&&onClose()}><section className={`dash-analysis-modal${extra?" has-extra":""}`} role="dialog" aria-modal="true" aria-label={`${nameOf(item.player)} quick analysis`}>{backLabel?<button className="dash-modal-back" onClick={onClose}><ArrowRight size={16}/>{backLabel}</button>:null}<button className="dash-modal-close" onClick={onClose} aria-label="Close analysis"><X size={20}/></button><div className="dash-modal-card"><CardImage player={item.player} featured/></div><div className="dash-modal-copy"><div className="dash-modal-heading"><div><PlayerMeta player={item.player} source={item.source}/><h2>{nameOf(item.player)}</h2></div><b className={`call ${String(item.recommendation||"WATCH").toLowerCase()}`}>{item.recommendation||"WATCH"}</b></div>{isFutgg?<MarketFreshness priceAgeSeconds={item.priceAgeSeconds} capturedAt={item.updatedAt} className="dash-modal-freshness"/>:null}<ModalVerdict item={item} entry={entry} target={target}/><div className="dash-modal-numbers"><Metric label={negativeCall?"Current price":"Buy below"} value={<CoinValue value={entry}/>} sub={negativeCall?"Current market entry":"Maximum entry"} tone="entry"/><Metric label={negativeCall?"Return after tax":"Expected profit"} value={<CoinValue value={gain} signed/>} sub="After EA tax" tone={gain==null?"":gain>=0?"positive":"negative"}/><Metric label={negativeCall?"Break-even sale":"Sell target"} value={<CoinValue value={target}/>} sub={negativeCall?"Minimum sale to cover tax":"List around this price"} tone="target"/></div><div className="dash-modal-lower"><ModalPriceChart cardId={item.cardId} entryPrice={entry} targetPrice={target} fairValue={fair} source={item.source}/><div className="dash-modal-summary"><div className={`dash-confidence${confidence==null?" no-data":""}`}><div><span>Confidence</span><strong>{confidence==null?"—":`${confidence}%`}</strong></div><i><b style={{width:`${confidence??0}%`}}/></i></div>{item.popularity!=null?<div className="dash-confidence"><div><span>In-game popularity</span><strong>{item.popularity}%</strong></div><i><b style={{width:`${item.popularity}%`}}/></i></div>:null}{item.psPcBinGapPct!=null?<p className="ps-pc-gap">{item.psPcBinGapPct>0?`${Math.abs(item.psPcBinGapPct).toFixed(1)}% cheaper listed on PS`:`${Math.abs(item.psPcBinGapPct).toFixed(1)}% cheaper listed on PC`}</p>:null}<ul>{reasons.map((text,index)=><li key={index}><span>✓</span><span className="reason-copy">{text}</span></li>)}</ul><div className="dash-modal-actions"><button onClick={()=>negativeCall?navigate("/v2/trade-finder"):navigate(`/v2/portfolio?card_id=${item.cardId}&buy=${entry}&sell=${target}&quantity=1&player=${encodeURIComponent(nameOf(item.player))}`)}><Target size={17}/>{negativeCall?"Find a better trade":"Log trade"}</button><button className="secondary watch-action" onClick={()=>onWatch(item)}><Star size={17} fill={watchState==="saved"?"currentColor":"none"}/>{watchState==="saved"?"Watching":"Add to watchlist"}</button></div></div></div>{extra?<div className="dash-modal-extra">{extra}</div>:null}</div></section></div>}
function ModalVerdict({item,entry,target}){if(isNegativeCall(item))return <p className="dash-modal-verdict">Avoid at <CoinValue value={entry}/>. It needs to sell above <CoinValue value={target}/> just to cover EA tax.</p>;if(String(item.recommendation||"").toUpperCase()==="WAIT")return <p className="dash-modal-verdict">Wait for a better entry below <CoinValue value={entry}/> before considering this card.</p>;return <p className="dash-modal-verdict">Buy below <CoinValue value={entry}/>. Aim to sell around <CoinValue value={target}/>.</p>}
function isNegativeCall(item){return ["AVOID","SELL"].includes(String(item?.recommendation||"").toUpperCase())}
// null (not 0) when there's no real entry/gain to compute a target from -
// a fabricated "0" here looks like a real "sell for nothing" instruction.
function modalTarget(item,entry,gain){if(!entry)return null;if(isNegativeCall(item))return Math.ceil(entry/.95/250)*250;return gain!=null?Math.ceil((entry+gain)/.95/250)*250:null}
function Metric({label,value,sub,tone}){return <div className={`metric ${tone||""}`}><span>{label}</span><strong>{value}</strong>{sub?<small>{sub}</small>:null}</div>}
function ConfidenceRing({value,compact=false}){const score=value==null?null:Math.max(0,Math.min(100,Math.round(Number(value))));return <div className={`confidence-ring ${compact?"compact":""}${score==null?" no-data":""}`} style={{"--confidence":`${(score??0)*3.6}deg`}}><span>{score==null?"—":score}{score==null?null:<small>%</small>}</span></div>}
function LockedPreviewCard({count,navigate}){return <button className="queue-card locked-preview" onClick={()=>navigate("/billing")}><div className="locked-preview-copy"><strong>{count} more {count===1?"opportunity":"opportunities"} today</strong><span>Upgrade to see the full live feed, updated as the market moves.</span></div><ChevronRight className="queue-arrow" size={18}/></button>}
function QueueCard({item,rank,onOpen,onWatch,watchState}){const open=()=>onOpen(item);return <div className="queue-card" role="button" tabIndex={0} onClick={open} onKeyDown={event=>(event.key==="Enter"||event.key===" ")&&open()}><span className="rank">#{rank}</span><div className="queue-art"><CardImage player={item.player} compact/></div><div className="queue-name"><strong>{nameOf(item.player)}</strong><PlayerMeta player={item.player} source={item.source}/><small>{reason(item)}</small>{item.source==="futgg"?<MarketFreshness priceAgeSeconds={item.priceAgeSeconds} capturedAt={item.updatedAt} compact/>:null}</div><button className={`queue-watch ${watchState==="saved"?"saved":""}`} onClick={event=>{event.stopPropagation();onWatch(item)}} aria-label={watchState==="saved"?`${nameOf(item.player)} is on your watchlist`:`Add ${nameOf(item.player)} to watchlist`}><Star size={17} fill={watchState==="saved"?"currentColor":"none"}/></button><div className="queue-reason"><b>WHY THIS MOVE</b><span>{reason(item)}</span></div><div className="queue-signals"><TradeSignal label="BUY BELOW" value={item.entryPrice??item.currentBin} tone="entry"/><TradeSignal label="EXPECTED PROFIT" value={expectedProfit(item)} tone="profit" signed sub={`${roi(item)} after tax`}/></div><ConfidenceRing value={item.confidence} compact/><ChevronRight className="queue-arrow" size={18}/></div>}
function StrategyCard({item,onOpen,onWatch,watchState}){const open=()=>onOpen(item);return <div className="strategy-card-new" role="button" tabIndex={0} onClick={open} onKeyDown={event=>(event.key==="Enter"||event.key===" ")&&open()}><div className="strategy-card-art"><CardImage player={item.player} compact/></div><div className="strategy-card-copy"><div className="strategy-card-title"><strong>{nameOf(item.player)}</strong><button className={`queue-watch ${watchState==="saved"?"saved":""}`} onClick={event=>{event.stopPropagation();onWatch(item)}} aria-label={`Add ${nameOf(item.player)} to watchlist`}><Star size={16} fill={watchState==="saved"?"currentColor":"none"}/></button></div><PlayerMeta player={item.player} source={item.source}/><p>{item.risk||"Unknown"} risk · {hold(item.holdingPeriod)}</p>{item.source==="futgg"?<MarketFreshness priceAgeSeconds={item.priceAgeSeconds} capturedAt={item.updatedAt} compact/>:null}</div><div className="strategy-trade"><TradeSignal label="BUY BELOW" value={item.entryPrice??item.currentBin} tone="entry"/><TradeSignal label="EXPECTED PROFIT" value={expectedProfit(item)} tone="profit" signed sub={`${roi(item)} after tax`}/></div><ChevronRight className="strategy-arrow" size={18}/></div>}
function TradeSignal({label,value,tone,signed=false,sub}){return <div className={`trade-signal ${tone}`}><span>{label}</span><strong><CoinValue value={value} signed={signed}/></strong>{sub?<small>{sub}</small>:null}</div>}
function PlayerMeta({player={},source}){return <div className="player-meta-pills"><span>{player.rating||"—"} OVR</span><span>{player.position||"—"}</span><span>{player.version||"Card"}</span>{source==="futgg"?<span>FUT.GG</span>:null}</div>}
function Ticker({items,events}){const rows=[...items.slice(0,7).map(x=>({label:nameOf(x.player),value:roi(x),tone:Number(x.expectedRoi)>=0?"up":"down"})),...(events||[]).slice(0,2).map(x=>({label:x.title,value:"EVENT",tone:"event"}))];return <div className="fut-ticker"><div>{[...rows,...rows].map((x,index)=><span key={`${x.label}-${index}`} className={x.tone}>{x.label} — <b>{x.value}</b></span>)}</div></div>}
function CardImage({player={},featured,compact}){if(player.generatedCardUrl)return <img className={`ft-card-img ${featured?"featured":compact?"compact":""}`} src={player.generatedCardUrl} alt={nameOf(player)}/>;return <PlayerCardArt compact={compact} bgImage={player.cardBgImage} cutoutImage={player.cardCutoutImage} cutoutType={player.cardCutoutType||"special"} fallbackImage={player.imageUrl} rating={player.rating} position={player.position} name={nameOf(player)} altText={nameOf(player)} stats={player.stats} nationImage={player.nationImage} leagueImage={player.leagueImage} clubImage={player.clubImage} showStats={Boolean(featured)} widthClass={featured?"w-52":"w-16"}/>}
// GET /api/v2/opportunities items are already server-side filtered to
// buy/strong_buy signals only, so recommendation is always "BUY" here -
// unlike normalise() this never has to interpret a status/score. expected_roi
// and confidence_score arrive as 0-1 fractions from the API; this dashboard's
// profit()/ConfidenceRing both expect 0-100 scale, same convention normalise()
// already uses via toPct().
function normaliseFutgg(raw){if(!raw)return null;const cardId=raw.card_id??raw.source_card_id;if(cardId==null)return null;const expectedRoi=raw.expected_roi==null?null:Number(raw.expected_roi)*100;const risk=raw.risk_level?raw.risk_level.charAt(0).toUpperCase()+raw.risk_level.slice(1):"Unknown";const reasons=Array.isArray(raw.signal_reasons)?raw.signal_reasons:[];return{cardId,source:"futgg",recommendation:"BUY",expectedRoi,netRoi:{likely:expectedRoi},currentBin:raw.current_bin,fairValue:raw.fair_value,entryPrice:raw.recommended_buy_max??raw.current_bin,recommendedSellTarget:raw.recommended_sell_target,expectedProfitAfterTax:raw.expected_profit_after_tax,breakEvenPrice:null,sales24h:raw.recent_sale_count,holdingPeriod:"Flexible",risk,confidence:raw.confidence_score==null?null:Number(raw.confidence_score)*100,updatedAt:raw.current_bin_captured_at,priceAgeSeconds:raw.price_age_seconds,reasoning:reasons[0]||"FUT.GG market signal",player:{name:raw.name,displayName:raw.name,cardName:raw.name,nickname:null,rating:raw.rating,version:raw.rarity||"Card",position:raw.position,imageUrl:raw.image_url,cardBgImage:null,cardCutoutImage:null,generatedCardUrl:null,nationImage:null,leagueImage:null,clubImage:null,stats:{}}}}
function normalise(item){if(!item)return null;if(item.player)return{...item,player:{...item.player,displayName:item.player.nickname||item.player.cardName||item.player.displayName||item.player.name}};return{...item,cardId:item.cardId??item.card_id,recommendation:item.recommendation??item.status,expectedRoi:toPct(item.likely_net_roi),netRoi:{likely:toPct(item.likely_net_roi)},currentBin:item.currentBin??item.current_bin,fairValue:item.fairValue??item.fair_value_24h,entryPrice:item.entryPrice??item.entry_price,breakEvenPrice:item.breakEvenPrice??item.break_even_sale_price,sales24h:item.sales24h??item.sales_24h,holdingPeriod:item.holdingPeriod??"Flexible",risk:item.risk??riskLabel(item.score_risk),confidence:item.confidence??item.score_confidence??null,updatedAt:item.updatedAt??item.computed_at,reasoning:item.reasoning||statusReason(item.status),player:{name:item.name,displayName:item.nickname||item.card_name||item.display_name||item.name,cardName:item.card_name,nickname:item.nickname,rating:item.rating,version:item.version,position:item.position,imageUrl:item.image_url,cardBgImage:item.card_bg_image,cardCutoutImage:item.card_cutout_image,cardCutoutType:item.card_cutout_type,generatedCardUrl:item.generated_card_url,nationImage:item.nation_image,leagueImage:item.league_image,clubImage:item.club_image,stats:{pace:item.pace,shooting:item.shooting,passing:item.passing,dribbling:item.dribbling,defending:item.defending,physicality:item.physicality}}}}
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
function expectedProfit(item){return item?.source==="futgg"&&item.expectedProfitAfterTax!=null?Math.round(Number(item.expectedProfitAfterTax)):profit(item)}function count(value){const number=Number(value);return Number.isFinite(number)?new Intl.NumberFormat("en-GB",{notation:number>999?"compact":"standard"}).format(number):"—"}function percent(value){const number=Number(value);return Number.isFinite(number)?`${number>0?"+":""}${number.toFixed(1)}%`:"—"}function roi(item){return percent(item?.netRoi?.likely??item?.expectedRoi)}function toPct(value){if(value==null)return null;const number=Number(value);return!Number.isFinite(number)?null:Math.abs(number)<=1?number*100:number}function riskLabel(value){const number=Number(value);return!Number.isFinite(number)?"Unknown":number>=70?"High":number>=40?"Medium":"Low"}function statusReason(status){return status==="BUY"?"Price and sales data support an entry.":status==="WAIT"?"Useful card, but the current entry is weak.":"Risk currently outweighs the likely return."}function reason(item){return item?.reasoning||statusReason(item?.recommendation)}function hold(value){return String(value||"Flexible").replace("~24h","Up to 1 day").replace("~48h","1–2 days").replace("~7d","Up to a week")}function mood(score){return score>=80?"Strong market":score>=60?"Good conditions":score>=40?"Selective market":"Quiet market"}
