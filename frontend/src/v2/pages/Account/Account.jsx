import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Briefcase, ExternalLink, LogOut, MessageCircle, RefreshCw, Sparkles, Star, Trash2, User, Wrench } from "lucide-react";
import api from "../../../axios";
import { DISCORD_INVITE_URL } from "../../../lib/domains";
import { useAuth } from "../../../context/AuthContext";
import { useEntitlements } from "../../../context/EntitlementsContext";
import { deleteAccount } from "../../../api/account";
import { getAlerts } from "../../../api/watchlist";
import { getOpenTrades } from "../../api/trades";
import { PageHead } from "../Players/Players";
import "./account.css";

export default function Account() {
  const { user, isAuthenticated, logout } = useAuth();
  const { loading: entitlementsLoading, isPremium, isAdmin, features, limits, capabilities } = useEntitlements();
  const navigate = useNavigate();
  const tier = isAdmin || features.includes("opportunity_feed") ? "ELITE" : isPremium ? "PREMIUM" : "FREE";
  const name = user?.global_name || user?.username || "EAFC Live member";
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const [discord, setDiscord] = useState(null);
  const [discordBusy, setDiscordBusy] = useState(false);

  // Free-tier usage counters - built entirely from data already fetchable
  // off existing endpoints (never a new backend route): watchlist's own
  // GET /api/watchlist/usage, the alerts list (count of items vs
  // capabilities.active_alert_limit), and open trades (count vs
  // capabilities.open_trade_limit). Daily opportunities/finder-search
  // counters are a documented gap - see this component's own note below
  // and the task report for why.
  const [usage, setUsage] = useState({ loading: true, watchlist: null, alertsUsed: null, openTradesUsed: null });

  useEffect(() => {
    if (entitlementsLoading || isPremium || !isAuthenticated) return;
    let cancelled = false;
    (async () => {
      const [watchlistRes, alertsRes, tradesRes] = await Promise.allSettled([
        api.get("/api/watchlist/usage", { __skipAuthRedirect: true }),
        getAlerts({ __skipAuthRedirect: true }),
        getOpenTrades(),
      ]);
      if (cancelled) return;
      setUsage({
        loading: false,
        watchlist: watchlistRes.status === "fulfilled" ? watchlistRes.value.data : null,
        alertsUsed: alertsRes.status === "fulfilled" ? (alertsRes.value?.items?.length ?? null) : null,
        openTradesUsed: tradesRes.status === "fulfilled" ? (tradesRes.value?.trades?.length ?? null) : null,
      });
    })();
    return () => { cancelled = true; };
  }, [entitlementsLoading, isPremium, isAuthenticated]);

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

  // Entitlements resolve async on first load - render a neutral skeleton
  // until then rather than the Free OR Premium UI, so a Premium member's
  // dashboard never flashes Free-tier copy/counters (or vice versa)
  // before the real /api/entitlements response lands.
  if (entitlementsLoading) {
    return <main className="v2-destination v2-account-page">
      <PageHead eyebrow="YOUR ACCOUNT" title={name} copy="Your membership, saved cards and trading record in one place." />
      <section className="v2-account-grid">
        <article className="v2-account-hero v2-account-skeleton"><div className="v2-account-avatar">{user?.avatar_url ? <img src={user.avatar_url} alt=""/> : <User/>}</div><div><span>CURRENT PLAN</span><h2 className="v2-skeleton-line" style={{width:"90px"}}>&nbsp;</h2></div></article>
        <article className="v2-account-panel v2-account-skeleton"><span>ACCOUNT DETAILS</span></article>
      </section>
    </main>;
  }

  const isFree = !isPremium && !isAdmin;

  return <main className="v2-destination v2-account-page">
    <PageHead eyebrow="YOUR ACCOUNT" title={name} copy="Your membership, saved cards and trading record in one place." />
    <section className="v2-account-grid">
      <article className="v2-account-hero">
        <div className="v2-account-avatar">{user?.avatar_url ? <img src={user.avatar_url} alt=""/> : <User/>}</div>
        <div><span>CURRENT PLAN</span><h2>{tier}</h2><p>{isPremium || isAdmin ? "Full access to EAFC Live’s calculated trading tools." : "Explore the market and unlock 2 selected trading opportunities each day."}</p></div>
      </article>
      <article className="v2-account-panel">
        <span>ACCOUNT DETAILS</span>
        <dl><div><dt>Username</dt><dd>{name}</dd></div><div><dt>Watchlist allowance</dt><dd>{limits?.watchlist_max ?? 3} cards</dd></div><div><dt>Status</dt><dd>{isAuthenticated ? "Signed in" : "Guest"}</dd></div></dl>
      </article>
    </section>

    {isFree ? (
      <section className="v2-account-usage">
        <span>YOUR USAGE TODAY</span>
        <dl>
          <div><dt>Watchlist</dt><dd>{usage.loading ? "…" : usage.watchlist ? `${usage.watchlist.used} of ${usage.watchlist.max} cards` : "—"}</dd></div>
          <div><dt>Alerts</dt><dd>{usage.loading ? "…" : usage.alertsUsed != null ? `${usage.alertsUsed} of ${capabilities?.active_alert_limit ?? 1} active` : "—"}</dd></div>
          <div><dt>Portfolio</dt><dd>{usage.loading ? "…" : usage.openTradesUsed != null ? `${usage.openTradesUsed} of ${capabilities?.open_trade_limit ?? 3} open trades` : "—"}</dd></div>
        </dl>
        {/* Daily opportunities/Finder-search counters are a known gap:
            there's no read-only usage endpoint for either today (unlike
            watchlist's own /usage) - fetching the count would mean
            either running the whole dashboard aggregation query just for
            this page, or spending one of the user's own daily allowance
            by calling /trade-finder to find out how much of it is left.
            Flagged in the task report rather than guessed or faked. */}
        <p className="v2-account-usage-note">Daily opportunity and Trade Finder search counts are shown on those pages themselves.</p>
      </section>
    ) : (
      <section className="v2-account-usage v2-account-usage-premium">
        <span>YOUR ACCESS</span>
        <p>You have full access — every live opportunity, unlimited Trade Finder, unlimited tracking, alerts and personal trading tools.</p>
      </section>
    )}

    {isFree ? (
      <section className="v2-premium-upsell">
        <div className="v2-premium-upsell-head"><Sparkles size={18}/><h2>UNLOCK PREMIUM — £3/MONTH</h2></div>
        <p>Every live opportunity, full Trade Finder, unlimited tracking, alerts and personal trading tools.</p>
        <Link className="v2-premium-upsell-cta" to="/v2/billing?unlock=Premium">Upgrade to Premium</Link>
      </section>
    ) : null}

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
        {/* Only relevant to someone with Manage Server somewhere with a
            Server Feed set up - the page itself resolves that live
            against Discord and shows "no servers" otherwise, so this
            link is safe to always show rather than trying to predict
            eligibility client-side. */}
        <Link to="/v2/discord-feed" className="v2-discord-feed-link">Manage a server's trading feed &rarr;</Link>
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
