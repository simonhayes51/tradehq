import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BarChart2, RefreshCw, Sparkles, Target } from "lucide-react";
import Link from "../components/V4Link";
import { useFutggOpportunities } from "../../v2/hooks/useFutggMarket";
import { api } from "../../v2/lib/api";
import { getTradeHistory } from "../../v2/api/trades";
import "./market-insights.css";

const num = value => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
const coins = value => num(value) == null ? "—" : Math.round(Number(value)).toLocaleString("en-GB");
const rows = data => Array.isArray(data?.items) ? data.items : Array.isArray(data?.results) ? data.results : Array.isArray(data) ? data : [];

function PicksCheck() {
  const query = useQuery({queryKey:["v4","insights","my-picks"],queryFn:()=>getTradeHistory(500),retry:false,staleTime:60000});
  if(query.isLoading)return <p className="edge-empty">Checking your completed trades…</p>;
  if(query.isError)return <p className="edge-empty">Sign in to see how your saved trades went. <Link to="/account">My account <ArrowRight size={14}/></Link></p>;
  const trades = Array.isArray(query.data?.trades) ? query.data.trades : [];
  const tracked = trades.filter(t => t.recommendation_status || t.recommendation_snapshot);
  const won = tracked.filter(t => num(t.profit) != null && num(t.profit) > 0);
  const decided = tracked.filter(t => num(t.profit) != null);
  return <div>
    <div className="edge-stat-grid">
      <div><span>Trades using a saved pick</span><strong>{tracked.length}</strong></div>
      <div><span>Finished with a profit</span><strong>{won.length}</strong></div>
      <div><span>Your winning trades</span><strong>{decided.length ? won.length + " of " + decided.length : "—"}</strong></div>
    </div>
    {tracked.length ? <><p className="edge-help">Only your completed trades linked to a saved pick are counted. This does not measure how often The Edge recommendations are correct.</p><div className="edge-mini-list">{tracked.slice(0,5).map((t,i)=><div key={t.trade_id ?? i}><span>{t.player || "Player"} <small>{num(t.profit)>0?"Made coins":"No profit"}</small></span><strong className={num(t.profit)>0?"edge-gain":""}>{num(t.profit)>0?"+":""}{coins(t.profit)}</strong></div>)}</div></>:<p className="edge-empty">No completed trades with saved picks yet. When you log and finish a trade from a recommendation, you'll see its result here.</p>}
    {query.data?.history_truncated?<p className="edge-help">Your membership only shows a recent part of your trading history.</p>:null}
    <Link className="edge-text-link" to="/portfolio">See all my trades <ArrowRight size={15}/></Link>
  </div>;
}


function RatingWatch({query}) {
 const [selected,setSelected]=useState("all");
 const items=rows(query.data);
 const active=items.filter(g=>selected==="all"||String(g.rating)===selected);
 return <div>
  <p className="edge-help">Prices from tradeable players in the current game, checked within 30 minutes. These are typical prices, not guaranteed buying prices.</p>
  <div className="edge-filter"><label htmlFor="edge-rating">Show rating</label><select id="edge-rating" value={selected} onChange={e=>setSelected(e.target.value)}><option value="all">All ratings</option>{[83,84,85,86,87,88,89].map(r=><option key={r} value={r}>{r} rated</option>)}</select></div>
  {query.isLoading?<p className="edge-empty">Checking prices…</p>:query.isError?<p className="edge-empty">Couldn't load rating prices. <button type="button" onClick={()=>query.refetch()}><RefreshCw size={14}/> Try again</button></p>:active.length?<div className="edge-rating-grid">{active.map(g=><div key={g.rating}><strong>{g.rating}</strong><span>Typical price</span><b>{coins(g.typical_price)} <small>coins</small></b><small>{coins(g.card_count)} cards checked</small></div>)}</div>:<p className="edge-empty">No recent prices for this rating. Try another rating or check back later.</p>}
 </div>;
}
function PriceSurprises({query}) {
 const items=rows(query.data);
 const finds=items.filter(i=>!i.locked && (i.card_id??i.source_card_id)!=null && num(i.recommended_buy_max)>0 && num(i.minimum_profit_at_max_buy??i.expected_profit_after_tax)>0).sort((a,b)=>Number(b.minimum_profit_at_max_buy??b.expected_profit_after_tax)-Number(a.minimum_profit_at_max_buy??a.expected_profit_after_tax)).slice(0,6);
 return <div><p className="edge-help">Today's available picks, showing the most you should pay and estimated coins you could make after the 5% selling fee.</p>
  {query.isLoading?<p className="edge-empty">Checking today's picks…</p>:query.isError?<p className="edge-empty">Couldn't load picks. <button type="button" onClick={()=>query.refetch()}><RefreshCw size={14}/> Try again</button></p>:finds.length?<div className="edge-mini-list">{finds.map((i,index)=><Link key={i.card_id??i.source_card_id??index} to={"/players/"+(i.card_id??i.source_card_id)}><span><b>{i.display_name||i.name||"Player"}</b><small>Buy for {coins(i.recommended_buy_max)} or less</small></span><strong className="edge-gain">+{coins(i.minimum_profit_at_max_buy??i.expected_profit_after_tax)} <ArrowRight size={14}/></strong></Link>)}</div>:<p className="edge-empty">No unlocked picks with a positive estimated profit right now. <Link to="/opportunities">See today's picks <ArrowRight size={14}/></Link></p>}
  <p className="edge-help">These are estimates, not coins already earned. Check the live in-game price before buying.</p>
 </div>;
}
export default function MarketInsights(){
  const feed=useFutggOpportunities({page_size:100,max_price_age_minutes:3});
  const ratingQuery=useQuery({queryKey:["v4","insights","rating-prices"],queryFn:async()=> (await api.get("/api/v2/market/rating-prices",{__skipAuthRedirect:true,__noRetry:true,timeout:12000})).data,retry:false,staleTime:120000});
  return <div className="edge-insights">
    <header className="edge-insights-hero"><div className="v4-eyebrow">THE EDGE / NEW TOOLS</div><h1>KNOW MORE.<br/><em>MAKE SMARTER MOVES.</em></h1><p>Three simple ways to learn from your trades and spot interesting prices.</p></header>
    <div className="edge-insights-grid">
      <section className="edge-insights-card"><div className="edge-card-heading"><span className="edge-card-icon"><Target size={22}/></span><div><span className="edge-kicker">YOUR RESULTS</span><h2>Did my picks work?</h2></div></div><p>See how your completed trades from saved picks turned out.</p><PicksCheck/></section>
      <section className="edge-insights-card"><div className="edge-card-heading"><span className="edge-card-icon"><BarChart2 size={22}/></span><div><span className="edge-kicker">PLAYER RATINGS</span><h2>Which ratings cost more?</h2></div></div><p>Compare typical prices for 83–89 rated cards across the market.</p><RatingWatch query={ratingQuery}/></section>
      <section className="edge-insights-card edge-insights-wide"><div className="edge-card-heading"><span className="edge-card-icon"><Sparkles size={22}/></span><div><span className="edge-kicker">POSSIBLE BARGAINS</span><h2>Today's picks worth checking</h2></div></div><p>See suggested buying prices and estimated coins you could make.</p><PriceSurprises query={feed}/></section>
    </div><p className="edge-footer-note">These tools use available market information and your own saved trades. They don't see every sale or guarantee a profit.</p>
  </div>;
}
