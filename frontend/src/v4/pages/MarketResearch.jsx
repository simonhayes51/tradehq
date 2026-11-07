import { useState } from "react";
import Link from "../components/V4Link";
import { ArrowRight } from "lucide-react";
import { useFutggOpportunities, useFutggTradeFinder } from "../../v2/hooks/useFutggMarket";
import { ResearchResults } from "../components/ResearchResults";

function getItems(data){return Array.isArray(data?.items)?data.items:Array.isArray(data?.results)?data.results:Array.isArray(data)?data:[];}
function Header({type}){
 const shortlist=type==="opportunities";
 return <header className="v4-research-heading"><div><div className="v4-eyebrow">{shortlist?"THE EDGE / TODAY'S PICKS":"THE EDGE / FIND PLAYERS"}</div><h1>{shortlist?"THE SHORTLIST.":"FIND YOUR EDGE."}</h1><p>{shortlist?"Players worth checking today. See what to pay and how many coins you might make.":"Set your budget and find players that match what you're looking for."}</p></div><Link to={shortlist?"/trade-finder":"/opportunities"} className="v4-button v4-button--secondary">{shortlist?"Find more players":"Today's shortlist"} <ArrowRight size={16}/></Link></header>;
}
function ResearchScreen({type,query,risk,setRisk,confidence,setConfidence,maxPrice,setMaxPrice,platform,setPlatform}){
 const items=getItems(query.data);
 return <div className="v4-research-page"><Header type={type}/><section className="v4-research-controls" aria-label="Filter market"><label>Market<select aria-label="Trading platform" value={platform} onChange={e=>setPlatform(e.target.value)}><option value="console">Console (PS / Xbox)</option><option value="pc">PC</option></select></label>
  <label>Risk<select value={risk} onChange={e=>setRisk(e.target.value)}><option value="">All risks</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
  <label>How sure are we?<select value={confidence} onChange={e=>setConfidence(e.target.value)}><option value="">Any</option><option value="60">60%</option><option value="70">70%</option><option value="80">80%</option><option value="90">90%</option></select></label>
  {type==="finder"?<label>My coin budget<input type="number" min="0" step="1000" placeholder="Any amount" value={maxPrice} onChange={e=>setMaxPrice(e.target.value)}/></label>:null}
  <span className="v4-research-count">{query.isLoading?"CHECKING":`${items.length} PLAYERS`}</span>
 </section><ResearchResults query={query} items={items}/><p className="v4-research-disclaimer">*Possible coins after the 5% selling fee. Always check the in-game price before buying.</p></div>;
}

function OpportunitiesScreen(){
 const [platform,setPlatform]=useState("console");
 const [risk,setRisk]=useState("");
 const [confidence,setConfidence]=useState("");
 const query=useFutggOpportunities({platform,risk:risk||undefined,min_confidence:confidence?Number(confidence)/100:undefined,max_price_age_minutes:3,page_size:40});
 return <ResearchScreen type="opportunities" platform={platform} setPlatform={setPlatform} query={query} risk={risk} setRisk={setRisk} confidence={confidence} setConfidence={setConfidence} maxPrice="" setMaxPrice={()=>{}}/>;
}
function FinderScreen(){
 const [risk,setRisk]=useState("");
 const [confidence,setConfidence]=useState("");
 const [maxPrice,setMaxPrice]=useState("");
 const query=useFutggTradeFinder({platform,risk:risk||undefined,min_confidence:confidence?Number(confidence)/100:undefined,max_price_age:3,page_size:40,budget:maxPrice?Number(maxPrice):undefined});
 return <ResearchScreen type="finder" platform={platform} setPlatform={setPlatform} query={query} risk={risk} setRisk={setRisk} confidence={confidence} setConfidence={setConfidence} maxPrice={maxPrice} setMaxPrice={setMaxPrice}/>;
}
export default function MarketResearch({type="opportunities"}){return type==="finder"?<FinderScreen/>:<OpportunitiesScreen/>;}
