import { useState } from "react";
import Link from "../components/V4Link";
import { ArrowRight, BarChart2, Search, Star, Wallet } from "lucide-react";
import { useFutggOpportunities } from "../../v2/hooks/useFutggMarket";
import "./home.css";

const fmt=value=>value==null||!Number.isFinite(Number(value))?"—":Math.round(Number(value)).toLocaleString("en-GB");
function Opportunity({item}){
 const id=item.card_id??item.source_card_id;
 const isLocked=Boolean(item.locked);
 const entry=item.recommended_buy_max;
 const profit=item.minimum_profit_at_max_buy??item.expected_profit_after_tax;
 return <article className="v4-opportunity">
   <div className="v4-opportunity-art">{item.composited_image_url||item.player_image_url||item.image_url?<img src={item.composited_image_url||item.player_image_url||item.image_url} alt="" loading="lazy"/>:<span>EAFC</span>}</div>
   <div className="v4-opportunity-copy"><div className="v4-eyebrow">{item.rating??"—"} OVR · {item.position??"—"}</div><h3>{item.display_name||item.name||"Unknown player"}</h3><div className="v4-opportunity-numbers"><div><span>BUY FOR UP TO</span><strong>{isLocked?"LOCKED":fmt(entry)}</strong></div><div><span>POSSIBLE PROFIT*</span><strong className="v4-positive">{isLocked?"LOCKED":profit==null?"—":`+${fmt(profit)}`}</strong></div></div><Link className="v4-opportunity-action" to={isLocked?"/billing":`/players/${id}`}>{isLocked?"View Elite":"See player details"} <ArrowRight size={16}/></Link></div>
 </article>;
}
export default function TradingHome(){
 const [platform,setPlatform]=useState("console");
 const query=useFutggOpportunities({platform,page_size:8,max_price_age_minutes:3});
 const items=Array.isArray(query.data?.items)?query.data.items:[];
 const unlocked=items.filter(x=>!x.locked);
 const lead=unlocked[0]??null;
 return <div className="v4-home">
  <div className="v4-home-heading"><div><div className="v4-eyebrow">THE EDGE / YOUR HOME</div><h1>YOUR MARKET.<br/><em>YOUR MOVE.</em></h1><p>Find players worth checking, see what to pay and keep track of your coins.</p></div><Link to="/opportunities" className="v4-button v4-button--primary">See all trades <ArrowRight size={16}/></Link></div>
  <div className="v4-home-shortcuts">
   <Link to="/insights"><Star size={18}/><span>Easy Insights</span><ArrowRight size={16}/></Link>
   <Link to="/opportunities"><BarChart2 size={18}/><span>Opportunities</span><ArrowRight size={16}/></Link>
   <Link to="/trade-finder"><Search size={18}/><span>Trade Finder</span><ArrowRight size={16}/></Link>
   <Link to="/watchlist"><Star size={18}/><span>Watchlist</span><ArrowRight size={16}/></Link>
   <Link to="/portfolio"><Wallet size={18}/><span>Portfolio</span><ArrowRight size={16}/></Link>
  </div>
  <div style={{display:"flex",gap:8,alignItems:"center",margin:"12px 0"}}><label htmlFor="home-market">Trading market</label><select id="home-market" value={platform} onChange={e=>setPlatform(e.target.value)} style={{padding:"9px 12px",border:"1px solid #ddd",borderRadius:6,background:"#fff"}}><option value="console">Console (PS / Xbox)</option><option value="pc">PC</option></select></div>
  <section className="v4-home-feed" aria-labelledby="v4-feed-heading"><div className="v4-home-feed-head"><div><div className="v4-eyebrow">TODAY'S PICKS</div><h2 id="v4-feed-heading">THE SHORTLIST</h2></div><span className="v4-status">{query.isLoading?"Checking market":"Market feed"}</span></div>
   {query.isLoading?<div className="v4-home-state" role="status">Looking for today's player picks…</div>:query.isError?<div className="v4-home-state" role="alert">Market data is unavailable right now. <button type="button" onClick={()=>query.refetch()}>Try again</button></div>:lead?<><Opportunity item={lead}/>{unlocked.slice(1,3).map(item=><Opportunity key={item.card_id??item.source_card_id} item={item}/>)}</>:<div className="v4-home-state">No picks are ready right now. See all trades or check back later.</div>}
   <div className="v4-home-feed-foot">*Possible coins after the 5% in-game selling fee, if you buy at or below the shown price. Check the game before buying — profit isn't guaranteed.</div>
  </section>
 </div>;
}
