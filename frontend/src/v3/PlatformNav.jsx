import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Activity, ArrowUpRight, Briefcase, TrendingUp, UserCircle, Compass, Crown, House, Menu, Search, SlidersHorizontal, Star, Users, X } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useEntitlements } from "../../context/EntitlementsContext";
import "./platform.css";

const PRIMARY = [
  {to:"/",label:"The Desk",short:"Desk",icon:House},
  {to:"/opportunities",label:"Opportunities",short:"Trades",icon:TrendingUp},
  {to:"/trade-finder",label:"Trade Finder",short:"Finder",icon:SlidersHorizontal},
  {to:"/players",label:"Player Research",short:"Players",icon:Search},
];
const PERSONAL = [
  {to:"/watchlist",label:"My Watchlist",short:"Watch",icon:Star,auth:true},
  {to:"/portfolio",label:"My Portfolio",short:"Portfolio",icon:Briefcase,auth:true},
  {to:"/track-record",label:"Track Record",short:"Results",icon:Activity},
  {to:"/discord-feed",label:"Discord Feed",short:"Feed",icon:Users,auth:true},
  {to:"/billing",label:"Membership",short:"Elite",icon:Crown},
  {to:"/account",label:"My Account",short:"Account",icon:UserCircle,auth:true},
];
const MORE = [{to:"/sbc",label:"SBC Market"},{to:"/gallery",label:"FUT Gallery"},{to:"/release-price-rises",label:"Launch Movers"},{to:"/chat",label:"AI Chat"}];
function active(path,to){return to==="/"?path==="/":path===to||path.startsWith(to+"/");}
function NavItem({item,path,onSelect}){
  const Icon=item.icon||Compass;
  return <Link to={item.to} onClick={onSelect} aria-current={active(path,item.to)?"page":undefined} className={"v3-nav-link"+(active(path,item.to)?" is-active":"")}><Icon size={19} strokeWidth={1.8}/><span>{item.label}</span>{active(path,item.to)?<span className="v3-nav-active-marker" aria-hidden="true"/>:null}</Link>;
}
export default function PlatformNav(){
  const {pathname}=useLocation();
  const {isAuthenticated}=useAuth();
  const {tier}=useEntitlements();
  const [more,setMore]=useState(false);
  const personal=PERSONAL.filter(x=>!x.auth||isAuthenticated);
  const mobile=[PRIMARY[0],PRIMARY[1],PRIMARY[2],{to:"/watchlist",label:"Watchlist",short:"Watch",icon:Star,auth:true}].filter(x=>!x.auth||isAuthenticated);
  return <>
    <aside className="v3-rail" aria-label="Trading platform navigation">
      <Link className="v3-rail-brand" to="/" aria-label="EAFC.Live Trading Desk"><span className="v3-slash">/</span><span>EAFC<span className="v3-orange">.LIVE</span><small>THE TRADING DESK</small></span></Link>
      <div className="v3-rail-group-label">DISCOVER</div>
      <nav className="v3-rail-links" aria-label="Discover">{PRIMARY.map(item=><NavItem key={item.to} item={item} path={pathname}/>)}</nav>
      <div className="v3-rail-group-label">YOUR WORKSPACE</div>
      <nav className="v3-rail-links" aria-label="Your workspace">{personal.map(item=><NavItem key={item.to} item={item} path={pathname}/>)}</nav>
      <div className="v3-rail-group-label">MORE TOOLS</div>
      <nav className="v3-rail-links" aria-label="More tools">{MORE.map(item=><NavItem key={item.to} item={item} path={pathname}/>)}</nav>
      <Link className="v3-rail-upgrade" to="/billing"><Crown size={19}/><span><strong>{tier==="elite"?"ELITE MEMBER":"UNLOCK ELITE"}</strong><small>{tier==="elite"?"Manage membership":"See the full market"}</small></span><ArrowUpRight size={16}/></Link>
      <div className="v3-rail-footer">EAFC.LIVE <span>MARKET INTELLIGENCE</span></div>
    </aside>
    <nav className="v3-mobile-dock" aria-label="Mobile navigation">
      {mobile.map(item=>{const Icon=item.icon;return <Link key={item.to} to={item.to} aria-current={active(pathname,item.to)?"page":undefined} className={"v3-dock-item"+(active(pathname,item.to)?" is-active":"")}><Icon size={21}/><span>{item.short}</span></Link>;})}
      <button type="button" className={"v3-dock-item"+(more?" is-active":"")} onClick={()=>setMore(x=>!x)} aria-expanded={more} aria-controls="v3-mobile-more">{more?<X size={21}/>:<Menu size={21}/>}<span>More</span></button>
    </nav>
    {more?<div className="v3-mobile-overlay"><button className="v3-mobile-scrim" type="button" aria-label="Close navigation" onClick={()=>setMore(false)}/><div className="v3-mobile-sheet" id="v3-mobile-more"><div className="v3-mobile-sheet-head"><strong>YOUR WORKSPACE</strong><button type="button" onClick={()=>setMore(false)} aria-label="Close"><X size={22}/></button></div>{[...personal,...MORE].map(item=><NavItem key={item.to} item={item} path={pathname} onSelect={()=>setMore(false)}/>)}</div></div>:null}
  </>;
}
