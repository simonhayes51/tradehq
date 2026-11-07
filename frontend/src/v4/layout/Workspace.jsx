import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { BarChart2, Home, Menu, Search, Sliders, Star, X, Zap } from "lucide-react";
import "../design/foundation.css";
import "./workspace.css";

const MAIN = [
 {path:"/",label:"Home",icon:Home},
 {path:"/opportunities",label:"Trades",icon:BarChart2},
 {path:"/trade-finder",label:"Finder",icon:Sliders},
 {path:"/watchlist",label:"Watchlist",icon:Star},
];
const EXTRA = [
 {path:"/insights",label:"Easy Insights"},
 {path:"/players",label:"Find a player"},
 {path:"/portfolio",label:"Portfolio"},
 {path:"/track-record",label:"How our picks did"},
 {path:"/billing",label:"Membership"},
 {path:"/account",label:"Account"},
 {path:"/discord-feed",label:"Discord feeds"},
 {path:"/sbc",label:"SBC market"},
 {path:"/gallery",label:"Gallery"},
 {path:"/chat",label:"Ask The Edge"},
];
function isCurrent(path,target){return target==="/"?path==="/":path===target||path.startsWith(target+"/");}
function Brand({prefix=""}){return <Link to={prefix||"/"} className="v4-brand" aria-label="The Edge home"><span className="v4-brand-mark">/</span><span>edge<small>ULTIMATE TEAM TOOLS</small></span></Link>;}
function NavLink({item,path,onClick,prefix=""}){
 const Icon=item.icon;
 return <Link to={prefix && ["/","/opportunities","/trade-finder","/watchlist","/players","/portfolio","/account","/insights"].includes(item.path)?prefix+item.path:item.path} onClick={onClick} className={"v4-nav-link"+(isCurrent(path,item.path)?" v4-nav-link--current":"")} aria-current={isCurrent(path,item.path)?"page":undefined}>{Icon?<Icon size={18} aria-hidden="true"/>:null}<span>{item.label}</span></Link>;
}
export default function Workspace({children}){
 const {pathname}=useLocation();
 const prefix=pathname.startsWith("/preview-v4")?"/preview-v4":"";
 const activePath=prefix?(pathname.slice(prefix.length)||"/"):pathname;
 const [menuOpen,setMenuOpen]=useState(false);
 return <div className="v4-app v4-workspace">
   <aside className="v4-sidebar" aria-label="Desktop navigation">
     <Brand prefix={prefix}/>
     <div className="v4-sidebar-caption">YOUR TOOLS</div>
     <nav aria-label="Primary">{MAIN.map(item=><NavLink key={item.path} item={item} path={activePath} prefix={prefix}/>)}</nav>
     <div className="v4-sidebar-caption">TOOLS & ACCOUNT</div>
     <nav aria-label="Tools">{EXTRA.map(item=><NavLink key={item.path} item={item} path={activePath} prefix={prefix}/>)}</nav>
     <Link className="v4-sidebar-elite" to="/billing"><Zap size={18}/> EXPLORE ELITE</Link>
   </aside>
   <div className="v4-workspace-main">
     <header className="v4-topbar"><div className="v4-mobile-brand"><Brand prefix={prefix}/></div><Link to={prefix+"/players"} className="v4-search-link"><Search size={17}/><span>Search players and prices</span><span className="v4-search-shortcut">/</span></Link><Link to={prefix+"/account"} className="v4-topbar-account">ACCOUNT</Link></header>
     <main id="v4-main" className="v4-content">{children}</main>
   </div>
   <nav className="v4-mobile-nav" aria-label="Mobile primary navigation">{MAIN.map(item=><NavLink key={item.path} item={item} path={activePath} prefix={prefix}/>)}<button type="button" onClick={()=>setMenuOpen(true)} aria-label="More navigation"><Menu size={21}/><span>More</span></button></nav>
   {menuOpen?<div className="v4-menu-layer"><button className="v4-menu-backdrop" type="button" aria-label="Close menu" onClick={()=>setMenuOpen(false)}/><div className="v4-menu-sheet" role="dialog" aria-modal="true" aria-label="More navigation"><div className="v4-menu-title">MORE <button type="button" onClick={()=>setMenuOpen(false)} aria-label="Close menu"><X size={21}/></button></div><nav>{EXTRA.map(item=><NavLink key={item.path} item={item} path={activePath} prefix={prefix} onClick={()=>setMenuOpen(false)}/>)}</nav></div></div>:null}
 </div>;
}
