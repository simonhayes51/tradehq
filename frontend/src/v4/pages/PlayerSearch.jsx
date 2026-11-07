import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Search, ArrowRight } from "lucide-react";
import { useFutggPlayers } from "../../v2/hooks/useFutggMarket";
import "../components/research.css";
import "./players.css";

const EXAMPLES=["Mbappé","Bellingham","Haaland","Putellas"];
const coins=n=>n==null?"—":Math.round(Number(n)).toLocaleString("en-GB");
export default function PlayerSearch(){
 const [params,setParams]=useSearchParams();
 const [search,setSearch]=useState(params.get("q")||"");
 const [term,setTerm]=useState(search.trim());
 useEffect(()=>{const t=setTimeout(()=>setTerm(search.trim()),300);return()=>clearTimeout(t)},[search]);
 useEffect(()=>{setParams(term?{q:term}:{},{replace:true})},[term,setParams]);
 const q=useFutggPlayers({search:term,page_size:40},{enabled:term.length>=2});
 const items=Array.isArray(q.data?.items)?q.data.items:Array.isArray(q.data?.results)?q.data.results:[];
 const accountRequired=q.data?.requires_account===true;
 return <div className="v4-players">
  <header className="v4-research-heading"><div><div className="v4-eyebrow">PLAYER DATABASE / LIVE RESEARCH</div><h1>KNOW YOUR CARDS.</h1><p>Search every version, check prices and inspect the evidence before you buy.</p></div></header>
  <label className="v4-players-search"><Search size={20}/><span className="v4-sr-only">Player name</span><input autoFocus placeholder="Search a player…" value={search} onChange={e=>setSearch(e.target.value)}/></label>
  {term.length<2?<section className="v4-players-start"><div><div className="v4-eyebrow">START HERE</div><h2>FIND THE RIGHT CARD.</h2><p>Enter at least two letters. Each card version is shown separately.</p></div><div className="v4-players-examples">{EXAMPLES.map(name=><button type="button" onClick={()=>setSearch(name)} key={name}>{name}<ArrowRight size={15}/></button>)}</div></section>:q.isLoading?<div className="v4-research-state" role="status">Searching cards…</div>:q.isError?<div className="v4-research-state" role="alert">Player search is unavailable. <button onClick={()=>q.refetch()} type="button">Retry</button></div>:items.length?<div className="v4-players-results">{items.map(player=>{const id=player.card_id??player.source_card_id;return <Link key={id} to={`/players/${id}`} state={{from:"players",player}} className="v4-players-result"><div className="v4-players-art">{player.composited_image_url||player.image_url?<img src={player.composited_image_url||player.image_url} alt=""/>:null}</div><div className="v4-players-copy"><span className="v4-eyebrow">{player.rating??"—"} OVR · {player.primary_position||player.position||"—"}</span><strong>{player.name}</strong><small>{player.rarity||"Player card"}</small></div><div className="v4-players-price"><small>{accountRequired?"APPROX PRICE":"CURRENT BIN"}</small><strong>{coins(accountRequired?player.current_bin_approx:player.current_bin)}</strong></div><ArrowRight size={17}/></Link>})}</div>:<div className="v4-research-state">No cards found for “{term}”.</div>}
  {accountRequired&&items.length>0?<p className="v4-research-disclaimer">Sign in to see exact live prices and detailed card analysis.</p>:null}
 </div>;
}
