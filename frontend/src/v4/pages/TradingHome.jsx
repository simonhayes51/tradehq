import { Link } from "react-router-dom";
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
   <div className="v4-opportunity-art">{item.composited_image_url||item.image_url?<img src={item.composited_image_url||item.image_url} alt="" loading="lazy"/>:<span>EAFC</span>}</div>
   <div className="v4-opportunity-copy"><div className="v4-eyebrow">{item.rating??"—"} OVR · {item.position??"—"}</div><h3>{item.name||"Unknown player"}</h3><div className="v4-opportunity-numbers"><div><span>BUY MAX</span><strong>{isLocked?"LOCKED":fmt(entry)}</strong></div><div><span>MIN PROFIT*</span><strong className="v4-positive">{isLocked?"LOCKED":profit==null?"—":`+${fmt(profit)}`}</strong></div></div><Link className="v4-opportunity-action" to={isLocked?"/billing":`/players/${id}`}>{isLocked?"View Elite":"View analysis"} <ArrowRight size={16}/></Link></div>
 </article>;
}
export default function TradingHome(){
 const query=useFutggOpportunities({page_size:8,max_price_age_minutes:3});
 const items=Array.isArray(query.data?.items)?query.data.items:[];
 const unlocked=items.filter(x=>!x.locked);
 const lead=unlocked[0]??null;
 return <div className="v4-home">
  <div className="v4-home-heading"><div><div className="v4-eyebrow">EAFC.LIVE / TRADING DESK</div><h1>YOUR MARKET.<br/><em>YOUR MOVE.</em></h1><p>Live opportunities, clear entry targets and the tools to act on them.</p></div><Link to="/opportunities" className="v4-button v4-button--primary">See all trades <ArrowRight size={16}/></Link></div>
  <div className="v4-home-shortcuts">
   <Link to="/opportunities"><BarChart2 size={18}/><span>Opportunities</span><ArrowRight size={16}/></Link>
   <Link to="/trade-finder"><Search size={18}/><span>Trade Finder</span><ArrowRight size={16}/></Link>
   <Link to="/watchlist"><Star size={18}/><span>Watchlist</span><ArrowRight size={16}/></Link>
   <Link to="/portfolio"><Wallet size={18}/><span>Portfolio</span><ArrowRight size={16}/></Link>
  </div>
  <section className="v4-home-feed" aria-labelledby="v4-feed-heading"><div className="v4-home-feed-head"><div><div className="v4-eyebrow">LIVE OPPORTUNITIES</div><h2 id="v4-feed-heading">THE SHORTLIST</h2></div><span className="v4-status">{query.isLoading?"Checking market":"Market feed"}</span></div>
   {query.isLoading?<div className="v4-home-state" role="status">Checking live market opportunities…</div>:query.isError?<div className="v4-home-state" role="alert">Market data is unavailable right now. <button type="button" onClick={()=>query.refetch()}>Try again</button></div>:lead?<><Opportunity item={lead}/>{unlocked.slice(1,3).map(item=><Opportunity key={item.card_id??item.source_card_id} item={item}/>)}</>:<div className="v4-home-state">No unlocked trade currently meets the live-market checks. Explore the full feed or check again later.</div>}
   <div className="v4-home-feed-foot">*Estimated minimum profit after EA tax at the advertised maximum buy price. Verify live prices in-game; returns are not guaranteed.</div>
  </section>
 </div>;
}
