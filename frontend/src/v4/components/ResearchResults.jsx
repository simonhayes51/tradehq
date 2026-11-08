import Link from "./V4Link";
import { ArrowRight } from "lucide-react";
import "./research.css";
const number=value=>value==null||!Number.isFinite(Number(value))?"—":Math.round(Number(value)).toLocaleString("en-GB");
export function ResearchCard({item}){
 const id=item.card_id??item.source_card_id;
 const locked=Boolean(item.locked);
 const profit=item.minimum_profit_at_max_buy??item.expected_profit_after_tax;
 return <article className="v4-research-card">
   <div className="v4-research-card-art">{item.composited_image_url||item.player_image_url||item.image_url?<img src={item.composited_image_url||item.player_image_url||item.image_url} alt="" loading="lazy"/>:null}</div>
   <div className="v4-research-card-body"><span className="v4-eyebrow">{item.rating??"—"} OVR · {item.position??"—"} · {item.rarity??"CARD"}</span><h3>{item.display_name||item.name||"Unknown player"}</h3><div className="v4-research-values"><div><small>BUY MAX</small><strong>{locked?"LOCKED":number(item.recommended_buy_max)}</strong></div><div><small>MIN PROFIT*</small><strong className="v4-positive">{locked?"LOCKED":profit==null?"—":`+${number(profit)}`}</strong></div></div><div className="v4-research-meta"><span>{item.risk_level?String(item.risk_level).toUpperCase()+" RISK":"RISK UNAVAILABLE"}</span><span>{item.price_age_seconds==null?"PRICE AGE UNKNOWN":`${Math.ceil(Number(item.price_age_seconds)/60)} MIN PRICE AGE`}</span></div><Link to={locked?"/billing":`/players/${id}?platform=${item.platform === "pc" ? "pc" : "console"}`} className="v4-research-action">{locked?"Unlock Elite":"Inspect trade"} <ArrowRight size={16}/></Link></div>
 </article>;
}
export function ResearchResults({query,items}){
 if(query.isLoading)return <div className="v4-research-state" role="status">Checking live market prices…</div>;
 if(query.isError)return <div className="v4-research-state" role="alert">The live market feed is unavailable. <button onClick={()=>query.refetch()} type="button">Retry</button></div>;
 if(!items.length)return <div className="v4-research-state">No cards meet these filters right now. Try broadening your search.</div>;
 return <div className="v4-research-grid">{items.map(item=><ResearchCard key={item.card_id??item.source_card_id} item={item}/>)}</div>;
}
