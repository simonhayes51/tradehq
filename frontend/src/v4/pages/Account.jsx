import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, CreditCard, LogOut, MessageCircle, ShieldCheck, User, Wallet } from "lucide-react";
import api from "../../axios";
import { useAuth } from "../../context/AuthContext";
import { useEntitlements } from "../../context/EntitlementsContext";
import Link from "../components/V4Link";
import "./account.css";

const TOOLS=[
 {to:"/portfolio",label:"Portfolio",description:"Your trades and trading history",icon:Wallet},
 {to:"/billing",label:"Membership and billing",description:"Plans, subscription and payment management",icon:CreditCard},
 {to:"/discord-feed",label:"Discord server feeds",description:"Manage connected server feeds",icon:MessageCircle},
];
export default function Account(){
 const {user,isAuthenticated,logout}=useAuth();
 const {tier,isAdmin}=useEntitlements();
 const navigate=useNavigate();
 const subscription=useQuery({queryKey:["v4","account","subscription"],queryFn:async()=>{const {data}=await api.get("/api/billing/subscription",{__skipAuthRedirect:true});return data?.subscription||null},enabled:isAuthenticated,retry:false,staleTime:60000});
 const displayName=user?.global_name||user?.username||"EAFC.Live member";
 const elite=isAdmin||tier==="elite";
 return <div className="v4-account">
  <header className="v4-research-heading"><div><div className="v4-eyebrow">EAFC.LIVE / MEMBER ACCOUNT</div><h1>YOUR ACCOUNT.</h1><p>Membership, account access and the tools linked to your profile.</p></div></header>
  {!isAuthenticated?<section className="v4-account-panel"><User size={26}/><h2>WELCOME BACK.</h2><p>Sign in to see your membership and connected tools.</p><Link to="/login" className="v4-button v4-button--primary">Sign in <ArrowRight size={16}/></Link></section>:<>
  <section className="v4-account-identity"><div className="v4-account-avatar"><User size={32}/></div><div><span className="v4-eyebrow">SIGNED IN AS</span><h2>{displayName}</h2><p>{user?.email||"EAFC.Live member"}</p></div><span className="v4-account-tier"><ShieldCheck size={15}/>{isAdmin?"ADMIN":elite?"ELITE":"FREE"}</span></section>
  <section className="v4-account-panel"><div className="v4-account-panel-head"><div><span className="v4-eyebrow">SUBSCRIPTION</span><h2>MEMBERSHIP.</h2></div><CreditCard size={22}/></div><p>{subscription.isLoading?"Checking your subscription…":subscription.isError?"Subscription details are currently unavailable.":subscription.data?.plan_name?subscription.data.plan_name:elite?"Elite access active":"Free membership"}</p>{subscription.data?.next_billing_date?<p className="v4-account-meta">Next billing: {subscription.data.next_billing_date}</p>:null}<Link to="/billing" className="v4-button v4-button--primary">Manage membership <ArrowRight size={16}/></Link></section>
  <div className="v4-account-links">{TOOLS.map(({to,label,description,icon:Icon})=><Link key={to} to={to} className="v4-account-link"><Icon size={21}/><span><strong>{label}</strong><small>{description}</small></span><ArrowRight size={17}/></Link>)}</div>
  <button className="v4-account-logout" type="button" onClick={async()=>{await logout();navigate("/login")}}><LogOut size={17}/> Sign out</button>
  </>}
 </div>;
}
