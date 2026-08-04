import { Link, useNavigate } from "react-router-dom";
import { Briefcase, LogOut, Star, User } from "lucide-react";
import { useAuth } from "../../../context/AuthContext";
import { useEntitlements } from "../../../context/EntitlementsContext";
import { PageHead } from "../Players/Players";
import "./account.css";

export default function Account() {
  const { user, isAuthenticated, logout } = useAuth();
  const { isPremium, isAdmin, features, limits } = useEntitlements();
  const navigate = useNavigate();
  const tier = isAdmin || features.includes("opportunity_feed") ? "ELITE" : isPremium ? "PRO" : "FREE";
  const name = user?.global_name || user?.username || "FutHub member";

  async function signOut() {
    await logout();
    navigate("/v2", { replace: true });
  }

  return <main className="v2-destination v2-account-page">
    <PageHead eyebrow="YOUR ACCOUNT" title={name} copy="Your v2 membership, saved cards and trading record in one place." />
    <section className="v2-account-grid">
      <article className="v2-account-hero">
        <div className="v2-account-avatar">{user?.avatar_url ? <img src={user.avatar_url} alt=""/> : <User/>}</div>
        <div><span>CURRENT PLAN</span><h2>{tier}</h2><p>{isPremium || isAdmin ? "Full access to FutHub’s calculated trading tools." : "Core tools with a smaller watchlist allowance."}</p></div>
      </article>
      <article className="v2-account-panel">
        <span>ACCOUNT DETAILS</span>
        <dl><div><dt>Username</dt><dd>{name}</dd></div><div><dt>Watchlist allowance</dt><dd>{limits?.watchlist_max ?? 3} cards</dd></div><div><dt>Status</dt><dd>{isAuthenticated ? "Signed in" : "Guest"}</dd></div></dl>
      </article>
    </section>
    <section className="v2-account-links">
      <Link to="/v2/watchlist"><Star/><div><strong>Watchlist</strong><span>Review cards you’re tracking</span></div></Link>
      <Link to="/v2/portfolio"><Briefcase/><div><strong>Portfolio</strong><span>See open and completed trades</span></div></Link>
    </section>
    {isAuthenticated ? <button className="v2-sign-out" onClick={signOut}><LogOut size={17}/> Sign out</button> : null}
  </main>;
}
