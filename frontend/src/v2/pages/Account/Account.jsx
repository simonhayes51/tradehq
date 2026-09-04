import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowUpRight, Briefcase, Calculator, ExternalLink, Gift, Layers, LogOut, MessageCircle, RefreshCw, Star, Trash2, Trophy, User, Users, Wrench } from "lucide-react";
import api from "../../../axios";
import { DISCORD_INVITE_URL } from "../../../lib/domains";
import { useAuth } from "../../../context/AuthContext";
import { useEntitlements } from "../../../context/EntitlementsContext";
import { deleteAccount } from "../../../api/account";
import { PageHead } from "../Players/Players";
import { FREE_LIMITS, PAID_PLAN_NAME } from "../../lib/limits";
import CompanionConnect from "../../components/CompanionConnect";
import "./account.css";

// The v1 tools that still work on live data and aren't duplicated by a
// v2 screen. Deliberately explicit rather than "and more": every entry
// here was opened and traced to a working endpoint.
const TOOLS = [
  { to: "/add-trade", label: "Log a trade", copy: "Record a buy or sell by hand", icon: Wrench },
  { to: "/profit-calculator", label: "Profit calculator", copy: "Work out your return after EA's 5% tax", icon: Calculator },
  { to: "/market-maker", label: "Bulk trade logging", copy: "Enter or paste many trades at once", icon: Layers },
  { to: "/squad", label: "Squad builder", copy: "Cost out a squad before you buy it", icon: Users },
  { to: "/leaderboard", label: "Leaderboard", copy: "See how your logged profit compares", icon: Trophy },
  { to: "/referrals", label: "Referrals", copy: "Invite a friend and earn credit", icon: Gift },
];

export default function Account() {
  const { user, isAuthenticated, logout } = useAuth();
  const { isAdmin, tier: serverTier, limits } = useEntitlements();
  const navigate = useNavigate();
  // The server's own answer, not a third opinion assembled here. This
  // line used to be able to produce "PREMIUM" - a plan name the product
  // does not have and does not sell - for anyone whose is_premium flag
  // was set but whose feature list hadn't been read yet.
  const isElite = serverTier === "elite" || isAdmin;
  const tier = isElite ? PAID_PLAN_NAME.toUpperCase() : "FREE";
  const name = user?.global_name || user?.username || "EAFC Live member";
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const [discord, setDiscord] = useState(null);
  const [discordBusy, setDiscordBusy] = useState(false);
  // Falls back to the account's own entitlement rather than the literal
  // string "Free" when /api/discord/status is unreachable - guessing
  // "Free" for a paying customer is the worse of the two failure modes.
  const discordRole = discord?.membership_access
    || (discord?.connected === false ? "Not linked to Discord" : (isElite ? "EAFC.Live Member" : "Free"));

  // useCallback + a correct dependency array: this was declared inline
  // and then omitted from the effect's deps, so the effect closed over
  // whichever render's copy happened to be first. Harmless today (the
  // function only reads isAuthenticated, which IS in the array) but it is
  // exactly the shape that starts serving stale state the moment the
  // function grows a second dependency.
  const loadDiscordStatus = useCallback(async () => {
    if (!isAuthenticated) return;
    try {
      const { data } = await api.get("/api/discord/status", { __skipAuthRedirect: true });
      setDiscord(data);
    } catch {
      // Leave membership_access unset on failure - `discordRole` above
      // derives it from the account's real entitlement instead of
      // asserting "Free" because a request failed.
      setDiscord({ guild_membership: "unavailable" });
    }
  }, [isAuthenticated]);

  useEffect(() => { loadDiscordStatus(); }, [loadDiscordStatus]);

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
    navigate("/", { replace: true });
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
      window.location.href = "/";
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
        {/* The plan line says what the plan actually is, in numbers.
            "Core tools with a smaller watchlist allowance" told a free
            member neither what they have nor what they are missing. */}
        <div><span>CURRENT PLAN</span><h2>{tier}</h2><p>{isElite
          ? "The full opportunities feed, Trade Finder, the trading assistant and the complete track record."
          : `${FREE_LIMITS.dailyOpportunities} complete picks a day, ${FREE_LIMITS.watchlist} watchlist cards, ${FREE_LIMITS.alerts} price alerts. Free permanently.`}</p>
          {/* The upgrade route from a Free account. /billing existed but
              nothing on this page linked to it - so the page that
              literally shows "CURRENT PLAN: FREE" had no way to change
              it. This is also the mobile path to billing, since the
              sidebar's upgrade entry has no room in the bottom tab bar
              (see tokens.css). */}
          {!isElite ? (
            <Link className="v2-account-upgrade" to="/billing?unlock=elite">
              Upgrade to {PAID_PLAN_NAME} <ArrowUpRight size={15} />
            </Link>
          ) : (
            <Link className="v2-account-manage" to="/billing">Manage subscription</Link>
          )}
        </div>
      </article>
      <article className="v2-account-panel">
        <span>ACCOUNT DETAILS</span>
        <dl><div><dt>Username</dt><dd>{name}</dd></div><div><dt>Watchlist allowance</dt><dd>{limits?.watchlist_max ?? FREE_LIMITS.watchlist} cards</dd></div><div><dt>Active price alerts</dt><dd>{limits?.alerts_max ?? FREE_LIMITS.alerts}</dd></div><div><dt>Status</dt><dd>{isAuthenticated ? "Signed in" : "Guest"}</dd></div></dl>
      </article>
    </section>
    <section className="v2-account-links">
      <Link to="/watchlist"><Star/><div><strong>Watchlist</strong><span>Review cards you’re tracking</span></div></Link>
      <Link to="/portfolio"><Briefcase/><div><strong>Portfolio</strong><span>See open and completed trades</span></div></Link>
      {/* This was a single "More tools - Smart Buy, Portfolio Optimizer,
          Trade Copilot and more" link pointing at /add-trade, which drops
          the user into the OLD app shell and its sidebar. Three of the
          four tools it advertised are retired (they priced off live
          FUTBIN scrapes or the frozen fair_value_mv, i.e. a different and
          less reliable source than the FUT.GG data every current screen
          uses), and their routes now redirect here - so the link sold
          features that no longer exist and, for the ones that do, sent
          people into a different version of the product to find them.
          Each surviving tool is now linked directly, by what it does. */}
      {TOOLS.map((tool) => (
        <Link key={tool.to} to={tool.to}>
          <tool.icon/><div><strong>{tool.label}</strong><span>{tool.copy}</span></div>
        </Link>
      ))}
    </section>
    {isAuthenticated ? <CompanionConnect /> : null}
    {isAuthenticated ? (
      <section className="v2-discord-panel">
        <div className="v2-discord-heading"><MessageCircle size={18}/><span>DISCORD</span></div>
        {/* "Membership access" describes the ROLE this account should
            carry inside the Discord server. It is not a second opinion on
            the plan shown above, and it must never read as one: the page
            could previously show "CURRENT PLAN: ELITE" and "Membership
            access: Free" together, because the Discord side computed paid
            /free with its own rules (no admin grants, no profile premium,
            and an automatic "Free" for anyone with no linked Discord
            account). Both now come from one resolver server-side, and the
            row is labelled as what it is. */}
        <dl>
          <div><dt>Connected as</dt><dd>{discord?.connected === false ? "Not linked" : (discord?.display_name || name)}</dd></div>
          <div><dt>EAFC Live server</dt><dd>{discord?.guild_membership === "joined" ? "Joined" : discord?.guild_membership === "not_joined" ? "Not joined" : "Status unavailable"}</dd></div>
          <div><dt>Discord role</dt><dd>{discordRole}</dd></div>
        </dl>
        {discord?.connected === false && isElite ? (
          <p className="v2-discord-note">
            Your {tier.toLowerCase()} plan is active on the website. Link Discord to get the member
            role in the server as well.
          </p>
        ) : null}
        <div className="v2-discord-actions">
          <a href={discord?.invite_url || DISCORD_INVITE_URL} target="_blank" rel="noreferrer"><ExternalLink size={15}/> Join EAFC Live Discord</a>
          <button type="button" onClick={refreshDiscordAccess} disabled={discordBusy}><RefreshCw size={15}/>{discordBusy ? "Refreshing..." : "Refresh Discord access"}</button>
        </div>
        {/* Only relevant to someone with Manage Server somewhere with a
            Server Feed set up - the page itself resolves that live
            against Discord and shows "no servers" otherwise, so this
            link is safe to always show rather than trying to predict
            eligibility client-side. */}
        <Link to="/discord-feed" className="v2-discord-feed-link">Manage a server's trading feed &rarr;</Link>
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
