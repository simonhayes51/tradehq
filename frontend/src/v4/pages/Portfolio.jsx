import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Check, Plus, RefreshCw, Search } from "lucide-react";
import { closeTrade, getOpenTrades, getTradeHistory, getTradingPerformance } from "../../v2/api/trades";
import Link from "../components/V4Link";
import "./portfolio.css";
const coins=n=>n==null?"—":Math.round(Number(n)).toLocaleString("en-GB");
const signed=n=>n==null?"—":`${Number(n)>0?"+":""}${coins(n)}`;
const PAGE=20;
export default function Portfolio(){
 const [open,setOpen]=useState([]),[history,setHistory]=useState([]),[stats,setStats]=useState(null);
 const [status,setStatus]=useState("loading"),[error,setError]=useState("");
 const [tab,setTab]=useState("open"),[search,setSearch]=useState(""),[shown,setShown]=useState(PAGE);
 const [closing,setClosing]=useState(null),[sell,setSell]=useState(""),[busy,setBusy]=useState(false);
 async function load(){setStatus("loading");setError("");try{const [a,b,c]=await Promise.all([getOpenTrades(),getTradeHistory(500),getTradingPerformance()]);setOpen(a?.trades||[]);setHistory(b?.trades||[]);setStats(c||{});setStatus("ready")}catch(e){setError(e?.message||"Portfolio unavailable");setStatus("error")}}
 useEffect(()=>{load()},[]);
 const rows=useMemo(()=>(tab==="open"?open:history).filter(t=>String(t.player||"").toLowerCase().includes(search.toLowerCase())).slice(0,shown),[tab,open,history,search,shown]);
 async function submit(e){e.preventDefault();const amount=Number(sell);if(!closing||!Number.isFinite(amount)||amount<0)return;setBusy(true);setError("");try{await closeTrade(closing.trade_id,amount);setClosing(null);setSell("");await load()}catch(e){setError(e?.response?.data?.detail||e?.message||"Could not close trade")}finally{setBusy(false)}}
 return <div className="v4-portfolio">
 <header className="v4-research-heading"><div><div className="v4-eyebrow">YOUR TRADING JOURNAL</div><h1>OWN YOUR RESULTS.</h1><p>Monitor positions, record sales and measure profit after EA tax.</p></div><Link className="v4-button v4-button--primary" to="/add-trade"><Plus size={16}/> Log a trade</Link></header>
 {status==="loading"?<div className="v4-research-state" role="status">Loading trading history…</div>:status==="error"?<div className="v4-research-state" role="alert">{error} <button type="button" onClick={load}>Retry</button></div>:<>
 <section className="v4-portfolio-kpis"><div><span>PROFIT TODAY</span><strong>{signed(stats?.profit_today)}</strong></div><div><span>THIS WEEK</span><strong>{signed(stats?.profit_week)}</strong></div><div><span>TOTAL PROFIT</span><strong>{signed(stats?.total_profit)}</strong></div><div><span>OPEN POSITIONS</span><strong>{stats?.open_positions??open.length}</strong></div></section>
 <div className="v4-portfolio-tabs" role="tablist" aria-label="Trading history"><button type="button" role="tab" aria-selected={tab==="open"} onClick={()=>{setTab("open");setShown(PAGE)}}>Open trades ({open.length})</button><button type="button" role="tab" aria-selected={tab==="history"} onClick={()=>{setTab("history");setShown(PAGE)}}>Completed sales ({history.length})</button><button type="button" onClick={load} aria-label="Refresh portfolio"><RefreshCw size={16}/></button></div>
 <label className="v4-portfolio-search"><Search size={16}/><input type="search" value={search} onChange={e=>{setSearch(e.target.value);setShown(PAGE)}} placeholder="Find a player…" aria-label="Search trades"/></label>
 {rows.length?<div className="v4-portfolio-grid">{rows.map(t=><article className="v4-portfolio-card" key={t.trade_id}><div className="v4-portfolio-card-head"><span>{tab==="open"?"OPEN POSITION":"SOLD"}</span><span>×{t.quantity||1}</span></div><h2>{t.player||"Unknown player"}</h2><small>{t.version||"Card"}</small><div className="v4-portfolio-values"><div><span>BUY</span><strong>{coins(t.buy)}</strong></div><div><span>{tab==="open"?"QUANTITY":"SELL"}</span><strong>{tab==="open"?t.quantity||1:coins(t.sell)}</strong></div><div><span>{tab==="open"?"INVESTED":"NET PROFIT"}</span><strong className={tab==="history"&&Number(t.profit)>=0?"v4-positive":""}>{tab==="open"?coins(Number(t.buy||0)*Number(t.quantity||1)):signed(t.profit)}</strong></div></div>{tab==="open"?<button type="button" className="v4-button v4-button--primary" onClick={()=>{setClosing(t);setSell("")}}>Record sale <ArrowRight size={15}/></button>:null}</article>)}</div>:<div className="v4-research-state">{search?"No matching trades.":tab==="open"?"No open trades yet.":"No completed sales yet."}</div>}
 {rows.length===shown?<button className="v4-portfolio-more" type="button" onClick={()=>setShown(n=>n+PAGE)}>Show more</button>:null}
 </>}
 {closing?<div className="v4-watch-modal-layer"><button className="v4-watch-modal-shade" type="button" aria-label="Cancel sale" onClick={()=>setClosing(null)}/><form className="v4-watch-modal" onSubmit={submit} role="dialog" aria-modal="true" aria-label="Record sale"><h2>RECORD SALE.</h2><p>{closing.player} · {closing.quantity||1} card(s)</p><label>Sell price per card<input type="number" min="0" step="1" required value={sell} onChange={e=>setSell(e.target.value)}/></label>{error?<p role="alert">{error}</p>:null}<div><button type="button" className="v4-button v4-button--secondary" onClick={()=>setClosing(null)}>Cancel</button><button type="submit" disabled={busy||sell===""} className="v4-button v4-button--primary"><Check size={15}/> {busy?"Saving…":"Confirm sale"}</button></div></form></div>:null}
 </div>;
}
