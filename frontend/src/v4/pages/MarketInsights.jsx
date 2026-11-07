import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, ArrowRight, BarChart2, RefreshCw, Sparkles, Target } from "lucide-react";
import Link from "../components/V4Link";
import { useFutggOpportunities } from "../../v2/hooks/useFutggMarket";
import { getTradeHistory } from "../../v2/api/trades";
import "./market-insights.css";

const num = value => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
const coins = value => num(value) == null ? "—" : Math.round(Number(value)).toLocaleString("en-GB");
const rows = data => Array.isArray(data?.items) ? data.items : Array.isArray(data?.results) ? data.results : Array.isArray(data) ? data : [];
const readPrice = item => num(item.current_bin ?? item.price ?? item.current_price);
const readRating = item => num(item.rating ?? item.overall);
const percent = (a,b) => b > 0 ? Math.round(a / b * 100) : 0;

function PicksCheck() {
  const query = useQuery({queryKey:["v4","insights","my-picks"],queryFn:()=>getTradeHistory(500),retry:false,staleTime:60000});
  if(query.isLoading)return <p className="edge-empty">Checking your completed trades…</p>;
  if(query.isError)return <p className="edge-empty">Sign in to see how your saved trades went. <Link to="/account">My account <ArrowRight size={14}/></Link></p>;
  const trades = Array.isArray(query.data?.trades) ? query.data.trades : [];
  const tracked = trades.filter(t => t.recommendation_status || t.recommendation_snapshot);
  const won = tracked.filter(t => num(t.profit) != null && num(t.profit) > 0);
  const lost = tracked.filter(t => num(t.profit) != null && num(t.profit) <= 0);
  return <div>
    <div className="edge-stat-grid">
      <div><span>Trades using a saved pick</span><strong>{tracked.length}</strong></div>
      <div><span>Finished with a profit</span><strong>{won.length}</strong></div>
      <div><span>Profit rate</span><strong>{tracked.length ? percent(won.length, tracked.length) + "%" : "—"}</strong></div>
    </div>
    {tracked.length ? <><p className="edge-help">Based only on your completed trades that saved a recommendation when you bought. This is not the success rate of every pick on The Edge.</p><div className="edge-mini-list">{tracked.slice(0,5).map((t,i)=><div key={t.trade_id ?? i}><span>{t.player || "Player"} <small>{num(t.profit)>0?"Made coins":"No profit"}</small></span><strong className={num(t.profit)>0?"edge-gain":""}>{num(t.profit)>0?"+":""}{coins(t.profit)}</strong></div>)}</div></>:<p className="edge-empty">No completed trades with saved picks yet. When you log and finish a trade from a recommendation, you'll see its result here.</p>}
    {query.data?.history_truncated?<p className="edge-help">Your membership only shows a recent part of your trading history.</p>:null}
    <Link className="edge-text-link" to="/portfolio">See all my trades <ArrowRight size={15}/></Link>
  </div>;
}

function RatingWatch({items}) {
  const [selected,setSelected]=useState("all");
  const usable=items.filter(i=>!i.locked && readRating(i)!=null && readPrice(i)!=null);
  const ratings=[83,84,85,86,87,88,89];
  const groups=ratings.map(r=>({rating:r,prices:usable.filter(i=>readRating(i)===r).map(readPrice)}));
  const active=groups.filter(g=>g.prices.length && (selected==="all" || String(g.rating)===selected));
  return <div>
    <p className="edge-help">A quick look at the prices of rated players in today's available picks. This is a small sample, not the price of every card or a market-wide trend.</p>
    <div className="edge-filter"><label htmlFor="edge-rating">Show rating</label><select id="edge-rating" value={selected} onChange={e=>setSelected(e.target.value)}><option value="all">All ratings</option>{ratings.map(r=><option key={r} value={r}>{r} rated</option>)}</select></div>
    {active.length?<div className="edge-rating-grid">{active.map(g=>{const sorted=[...g.prices].sort((a,b)=>a-b);const middle=sorted.length/2;const median=sorted.length%2?sorted[Math.floor(middle)]:(sorted[middle-1]+sorted[middle])/2;return <div key={g.rating}><strong>{g.rating}</strong><span>Typical price in sample</span><b>{coins(median)} <small>coins</small></b><small>{g.prices.length} {g.prices.length===1?"card":"cards"} checked</small></div>})}</div>:<p className="edge-empty">No unlocked cards with matching ratings and prices in the current picks. Try again later.</p>}
  </div>;
}

function PriceSurprises({items}) {
  const finds=items.filter(i=>!i.locked).map(i=>{const price=readPrice(i),value=num(i.fair_value ?? i.estimated_fair_value);return {...i,_price:price,_value:value,_gap:price!=null && value>0?(value-price)/value:null};}).filter(i=>i._gap!=null && i._gap>=0.05 && i._price>0).sort((a,b)=>b._gap-a._gap).slice(0,6);
  return <div><p className="edge-help">Players priced below The Edge's estimated value. These are possible bargains, not confirmed sudden drops or guaranteed profits.</p>
    {finds.length?<div className="edge-mini-list">{finds.map((i,index)=><Link key={i.card_id??i.source_card_id??index} to={`/players/${i.card_id??i.source_card_id}`}><span><b>{i.display_name||i.name||"Player"}</b><small>Now {coins(i._price)} · Estimate {coins(i._value)}</small></span><strong>{Math.round(i._gap*100)}% below <ArrowRight size={14}/></strong></Link>)}</div>:<p className="edge-empty">No clear price gaps in the available picks right now. That's fine — we won't invent bargains.</p>}
    <p className="edge-help">Prices can move quickly. Always check the in-game market before buying.</p>
  </div>;
}

export default function MarketInsights(){
  const feed=useFutggOpportunities({page_size:100,max_price_age_minutes:3});
  const items=rows(feed.data);
  return <div className="edge-insights">
    <header className="edge-insights-hero"><div className="v4-eyebrow">THE EDGE / NEW TOOLS</div><h1>KNOW MORE.<br/><em>MAKE SMARTER MOVES.</em></h1><p>Three simple ways to learn from your trades and spot interesting prices.</p></header>
    <div className="edge-insights-grid">
      <section className="edge-insights-card"><div className="edge-card-heading"><span className="edge-card-icon"><Target size={22}/></span><div><span className="edge-kicker">YOUR RESULTS</span><h2>Did my picks work?</h2></div></div><p>See how your completed trades from saved picks turned out.</p><PicksCheck/></section>
      <section className="edge-insights-card"><div className="edge-card-heading"><span className="edge-card-icon"><BarChart2 size={22}/></span><div><span className="edge-kicker">PLAYER RATINGS</span><h2>Which ratings cost more?</h2></div></div><p>Compare typical prices for 83–89 rated cards in today's picks.</p>{feed.isLoading?<p className="edge-empty">Loading player prices…</p>:feed.isError?<p className="edge-empty">Player prices aren't available. <button onClick={()=>feed.refetch()} type="button"><RefreshCw size={14}/> Try again</button></p>:<RatingWatch items={items}/>}</section>
      <section className="edge-insights-card edge-insights-wide"><div className="edge-card-heading"><span className="edge-card-icon"><Sparkles size={22}/></span><div><span className="edge-kicker">POSSIBLE BARGAINS</span><h2>Prices worth a second look</h2></div></div><p>Cards priced below their estimated value — with the numbers explained.</p>{feed.isLoading?<p className="edge-empty">Looking for price gaps…</p>:feed.isError?<p className="edge-empty"><AlertCircle size={16}/> The market feed is unavailable right now.</p>:<PriceSurprises items={items}/>}</section>
    </div><p className="edge-footer-note">These tools use available market information and your own saved trades. They don't see every sale or guarantee a profit.</p>
  </div>;
}
