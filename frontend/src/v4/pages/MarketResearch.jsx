import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { useFutggOpportunities, useFutggTradeFinder } from "../../v2/hooks/useFutggMarket";
import { ResearchResults } from "../components/ResearchResults";

function getItems(data){return Array.isArray(data?.items)?data.items:Array.isArray(data?.results)?data.results:Array.isArray(data)?data:[];}
function Header({type}){
 const shortlist=type==="opportunities";
 return <header className="v4-research-heading"><div><div className="v4-eyebrow">{shortlist?"LIVE MARKET / CURATED":"RESEARCH / CUSTOM SCAN"}</div><h1>{shortlist?"THE SHORTLIST.":"FIND YOUR EDGE."}</h1><p>{shortlist?"Live buy candidates screened by the backend. Review the numbers and check in-game prices before buying.":"Explore the wider market with filters tailored to your trading strategy."}</p></div><Link to={shortlist?"/trade-finder":"/opportunities"} className="v4-button v4-button--secondary">{shortlist?"Open Trade Finder":"Today's shortlist"} <ArrowRight size={16}/></Link></header>;
}
function ResearchScreen({type,query,risk,setRisk,confidence,setConfidence,maxPrice,setMaxPrice}){
 const items=getItems(query.data);
 return <div className="v4-research-page"><Header type={type}/><section className="v4-research-controls" aria-label="Filter market">
  <label>Risk<select value={risk} onChange={e=>setRisk(e.target.value)}><option value="">All risks</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
  <label>Minimum confidence<select value={confidence} onChange={e=>setConfidence(e.target.value)}><option value="">Any</option><option value="60">60%</option><option value="70">70%</option><option value="80">80%</option><option value="90">90%</option></select></label>
  {type==="finder"?<label>Maximum price<input type="number" min="0" step="1000" placeholder="Any budget" value={maxPrice} onChange={e=>setMaxPrice(e.target.value)}/></label>:null}
  <span className="v4-research-count">{query.isLoading?"SCANNING":`${items.length} RESULTS`}</span>
 </section><ResearchResults query={query} items={items}/><p className="v4-research-disclaimer">*Estimated profit after EA tax at maximum buy price. Results are not guaranteed. Verify all prices in-game before buying.</p></div>;
}

function OpportunitiesScreen(){
 const [risk,setRisk]=useState("");
 const [confidence,setConfidence]=useState("");
 const query=useFutggOpportunities({risk:risk||undefined,min_confidence:confidence?Number(confidence):undefined,max_price_age_minutes:3,page_size:40});
 return <ResearchScreen type="opportunities" query={query} risk={risk} setRisk={setRisk} confidence={confidence} setConfidence={setConfidence} maxPrice="" setMaxPrice={()=>{}}/>;
}
function FinderScreen(){
 const [risk,setRisk]=useState("");
 const [confidence,setConfidence]=useState("");
 const [maxPrice,setMaxPrice]=useState("");
 const query=useFutggTradeFinder({risk:risk||undefined,min_confidence:confidence?Number(confidence):undefined,max_price_age:3,page_size:40,budget:maxPrice?Number(maxPrice):undefined});
 return <ResearchScreen type="finder" query={query} risk={risk} setRisk={setRisk} confidence={confidence} setConfidence={setConfidence} maxPrice={maxPrice} setMaxPrice={setMaxPrice}/>;
}
export default function MarketResearch({type="opportunities"}){return type==="finder"?<FinderScreen/>:<OpportunitiesScreen/>;}
