import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Briefcase, ExternalLink, LogOut, MessageCircle, RefreshCw, Star, Trash2, User, Wrench } from "lucide-react";
import api from "../../../axios";
import { DISCORD_INVITE_URL } from "../../../lib/domains";
import { useAuth } from "../../../context/AuthContext";
import { useEntitlements } from "../../../context/EntitlementsContext";
import { deleteAccount } from "../../../api/account";
import { PageHead } from "../Players/Players";
import "./account.css";

export default function Account() {
  const { user, isAuthenticated, logout } = useAuth();
  const { isPremium, isAdmin, features, limits } = useEntitlements();
  const navigate = useNavigate();
  const tier = isAdmin || features.includes("opportunity_feed") ? "ELITE" : isPremium ? "PREMIUM" : "FREE";
  const name = user?.global_name || user?.username || "EAFC Live member";
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const [discord, setDiscord] = useState(null);
  const [discordBusy, setDiscordBusy] = useState(false);

  async function loadDiscordStatus() {
    if (!isAuthenticated) return;
    try {
      const { data } = await api.get("/api/discord/status", { __skipAuthRedirect: true });
      setDiscord(data);
    } catch {
      setDiscord({ guild_membership: "unavailable", membership_access: isPremium ? "EAFC.Live Member" : "Free" });
    }
  }

  useEffect(() => { loadDiscordStatus(); }, [isAuthenticated]);

  async function refreshDiscordAccess() {
    setDiscordBusy(true);
    try {
      await api.post("/api/discord/reconcile", null, { __skipAuthRedirect: true });
      await loadDiscordStatus();
    } finally {
      setDiscordBusy(false);
    }
  }

  async function signOut() {
    await logout();
    navigate("/v2", { replace: true });
  }

  async function confirmDeleteAccount() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteAccount();
      // The backend already cleared the session server-side - navigate
      // to a fresh, fully signed-out load rather than a client-side
      // route change, so nothing stale from AuthContext/EntitlementsContext
      // lingers in memory after an account that no longer exists.
      window.location.href = "/v2";
    } catch (e) {
      setDeleting(false);
      setDeleteError(e?.response?.data?.detail || "Couldn't delete your account - try again, or contact support if this keeps happening.");
    }
  }

  return <main className="v2-destination v2-account-page">
    <PageHead eyebrow="YOUR ACCOUNT" title={name} copy="Your membership, saved cards and trading record in one place." />
    <section className="v2-account-grid">
      <article className="v2-account-hero">
        <div className="v2-account-avatar">{user?.avatar_url ? <img src={user.avatar_url} alt=""/> : <User/>}</div>
        <div><span>CURRENT PLAN</span><h2>{tier}</h2><p>{isPremium || isAdmin ? "Full access to EAFC Live’s calculated trading tools." : "Core tools with a smaller watchlist allowance."}</p></div>
      </article>
      <article className="v2-account-panel">
        <span>ACCOUNT DETAILS</span>
        <dl><div><dt>Username</dt><dd>{name}</dd></div><div><dt>Watchlist allowance</dt><dd>{limits?.watchlist_max ?? 3} cards</dd></div><div><dt>Status</dt><dd>{isAuthenticated ? "Signed in" : "Guest"}</dd></div></dl>
      </article>
    </section>
    <section className="v2-account-links">
      <Link to="/v2/watchlist"><Star/><div><strong>Watchlist</strong><span>Review cards you’re tracking</span></div></Link>
      <Link to="/v2/portfolio"><Briefcase/><div><strong>Portfolio</strong><span>See open and completed trades</span></div></Link>
      {/* Smart Buy AI, Portfolio Optimizer, Trade Copilot, Market
          Sentiment, Market Maker, Leaderboard, Referrals and Squad
          Builder haven't been rebuilt here yet - this is the only way
          into that sidebar now that "/" and "/dashboard" both redirect
          straight into this app instead of the old one. */}
      <Link to="/add-trade"><Wrench/><div><strong>More tools</strong><span>Smart Buy, Portfolio Optimizer, Trade Copilot and more</span></div></Link>
    </section>
    {isAuthenticated ? (
      <section className="v2-discord-panel">
        <div className="v2-discord-heading"><MessageCircle size={18}/><span>DISCORD</span></div>
        <dl>
          <div><dt>Connected as</dt><dd>{discord?.display_name || name}</dd></div>
          <div><dt>EAFC Live server</dt><dd>{discord?.guild_membership === "joined" ? "Joined" : discord?.guild_membership === "not_joined" ? "Not joined" : "Status unavailable"}</dd></div>
          <div><dt>Membership access</dt><dd>{discord?.membership_access || (isPremium ? "EAFC.Live Member" : "Free")}</dd></div>
        </dl>
        <div className="v2-discord-actions">
          <a href={discord?.invite_url || DISCORD_INVITE_URL} target="_blank" rel="noreferrer"><ExternalLink size={15}/> Join EAFC Live Discord</a>
          <button type="button" onClick={refreshDiscordAccess} disabled={discordBusy}><RefreshCw size={15}/>{discordBusy ? "Refreshing..." : "Refresh Discord access"}</button>
        </div>
      </section>
    ) : null}
    {isAuthenticated ? <button className="v2-sign-out" onClick={signOut}><LogOut size={17}/> Sign out</button> : null}
    {isAuthenticated ? (
      <section className="v2-account-danger">
        <span>DANGER ZONE</span>
        {!confirmingDelete ? (
          <button className="v2-delete-account" onClick={() => setConfirmingDelete(true)}>
            <Trash2 size={16}/> Delete EAFC Live account
          </button>
        ) : (
          <div className="v2-delete-confirm">
            <p>This removes your watchlist, alerts, trade history and profile. It can't be undone. Are you sure?</p>
            {deleteError ? <p className="v2-delete-error">{deleteError}</p> : null}
            <div className="v2-delete-actions">
              <button className="v2-delete-account confirm" onClick={confirmDeleteAccount} disabled={deleting}>
                {deleting ? "Deleting…" : "Yes, delete my account"}
              </button>
              <button className="v2-delete-cancel" onClick={() => { setConfirmingDelete(false); setDeleteError(null); }} disabled={deleting}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </section>
    ) : null}
  </main>;
}
