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
  const [discordFeedback, setDiscordFeedback] = useState(null); // {ok, text}
  // "Manage a server's trading feed" was shown to every signed-in Elite
  // member regardless of whether they actually administer any Discord
  // server - GET /api/v2/discord-feed/guilds is the real, live-checked
  // list (MANAGE_GUILD/ADMINISTRATOR/owner - see discord_permissions.py)
  // that page itself already uses to decide what to show. Reusing it
  // here means the link only appears for someone it will actually do
  // something for.
  const [manageableGuilds, setManageableGuilds] = useState(null); // null = unknown yet
  // Real billing facts for an Elite member - the same
  // GET /api/billing/subscription Billing.jsx itself reads, not
  // invented here. null while loading/unknown; the fields actually used
  // below (plan_name, amount_display, next_billing_date) are exactly
  // what that endpoint returns, nothing added.
  const [subscription, setSubscription] = useState(null);
  // Falls back to the account's own entitlement rather than the literal
  // string "Free" when /api/discord/status is unreachable - guessing
  // "Free" for a paying customer is the worse of the two failure modes.
  // `membership_access` (from GET /api/discord/status) is the role this
  // account is ENTITLED to, not proof it's currently held in the server -
  // the backend computes it from the entitlement resolver, independent of
  // guild_membership/has_member_role (the live Discord read). Rendering
  // membership_access alone as "Discord role" is what produced "has an
  // EAFC.Live Member role" sitting next to "server: Not joined": entitled
  // and actually-in-the-server are two different facts, and only one of
  // them is real membership. Both are shown below, so this stays what it
  // is - the entitlement - and the actual state is separate.
  const discordEntitlement = discord?.membership_access
    || (discord?.connected === false ? "Not linked to Discord" : (isElite ? "EAFC.Live Member" : "Free"));
  const discordActuallyJoined = discord?.guild_membership === "joined";
  const roleNotYetSynced = discord?.connected !== false
    && discordEntitlement === "EAFC.Live Member"
    && !discord?.has_member_role;

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

  useEffect(() => {
    if (!isAuthenticated || !isElite) return;
    api.get("/api/v2/discord-feed/guilds", { __skipAuthRedirect: true })
      .then(({ data }) => setManageableGuilds(Array.isArray(data) ? data : Array.isArray(data?.guilds) ? data.guilds : []))
      .catch(() => setManageableGuilds([])); // fail closed: don't advertise a link that errors
  }, [isAuthenticated, isElite]);

  useEffect(() => {
    if (!isAuthenticated || !isElite) return;
    api.get("/api/billing/subscription", { __skipAuthRedirect: true })
      .then(({ data }) => setSubscription(data?.subscription || null))
      .catch(() => setSubscription(null));
  }, [isAuthenticated, isElite]);

  async function refreshDiscordAccess() {
    setDiscordBusy(true);
    setDiscordFeedback(null);
    try {
      await api.post("/api/discord/reconcile", null, { __skipAuthRedirect: true });
      await loadDiscordStatus();
      setDiscordFeedback({ ok: true, text: "Discord access refreshed." });
    } catch (e) {
      setDiscordFeedback({ ok: false, text: e?.response?.data?.detail || "Couldn't refresh Discord access. Try again." });
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
    {/* "Your account" / "Account", not the username - a page whose only
        job is to describe an account should be findable by what it IS,
        not by whichever name happens to be on it today. The username is
        still here, just as account information (identity strip below,
        and the Username row in Account details), not the heading. */}
    <PageHead eyebrow="YOUR ACCOUNT" title="Account" copy="Your membership, saved cards and trading record in one place." />

    {/* Who's signed in, on its own - separate from the plan card below.
        The avatar used to sit INSIDE the "Current plan" card, which read
        as if it were the plan's own branding rather than the member's
        identity. */}
    <div className="v2-account-identity">
      <div className="v2-account-avatar">{user?.avatar_url ? <img src={user.avatar_url} alt=""/> : <User/>}</div>
      <div><span>SIGNED IN AS</span><strong>{name}</strong></div>
    </div>

    <section className="v2-account-grid">
      <article className="v2-account-hero">
        {/* The plan line says what the plan actually is, in numbers.
            "Core tools with a smaller watchlist allowance" told a free
            member neither what they have nor what they are missing. */}
        <div><span>CURRENT PLAN</span><h2>{tier}</h2><p>{isElite
          ? "The full opportunities feed, Trade Finder, AI Chat and the complete track record."
          : `${FREE_LIMITS.dailyOpportunities} complete picks a day, ${FREE_LIMITS.watchlist} watchlist cards, ${FREE_LIMITS.alerts} price alerts. Free permanently.`}</p>
          {/* Real billing facts (GET /api/billing/subscription, same call
              Billing.jsx itself makes) - not fabricated here. Absent
              entirely rather than guessed at if the call hasn't
              resolved yet or the account has no Stripe subscription on
              file (e.g. an admin-granted Elite account). */}
          {isElite && subscription ? (
            <dl className="v2-account-billing-facts">
              <div><dt>Price</dt><dd>{subscription.amount_display || "—"}</dd></div>
              <div><dt>Renews</dt><dd>{subscription.next_billing_date ? new Date(subscription.next_billing_date).toLocaleDateString("en-GB") : "—"}</dd></div>
            </dl>
          ) : null}
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
            <div className="v2-account-billing-actions">
              <Link className="v2-account-manage" to="/billing">Update payment method</Link>
              <Link className="v2-account-manage ghost" to="/billing">Cancel subscription</Link>
            </div>
          )}
        </div>
      </article>
      <article className="v2-account-panel">
        <span>ACCOUNT DETAILS</span>
        <dl><div><dt>Username</dt><dd>{name}</dd></div><div><dt>Watchlist allowance</dt><dd>{limits?.watchlist_max ?? FREE_LIMITS.watchlist} cards</dd></div><div><dt>Active price alerts</dt><dd>{limits?.alerts_max ?? FREE_LIMITS.alerts}</dd></div><div><dt>Status</dt><dd>{isAuthenticated ? "Signed in" : "Guest"}</dd></div></dl>
      </article>
    </section>

    {/* Two distinct kinds of link, now labelled as such: things this
        account HAS (your own watchlist and portfolio) vs. TOOLS the
        product offers (calculators, builders, logging). Previously one
        undifferentiated grid left it unclear which of these were "your
        stuff" and which were general utilities. */}
    <h2 className="v2-account-section-label">Your account</h2>
    <section className="v2-account-links">
      <Link to="/watchlist"><Star/><div><strong>Watchlist</strong><span>Review cards you’re tracking</span></div></Link>
      <Link to="/portfolio"><Briefcase/><div><strong>Portfolio</strong><span>See open and completed trades</span></div></Link>
    </section>
    <h2 className="v2-account-section-label">Tools</h2>
    <section className="v2-account-links">
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
        {/* "Membership access" describes the ROLE this account is
            ENTITLED to, not proof it's currently held - see
            discordEntitlement's comment above. Both the entitlement and
            the live server state are shown, honestly labelled, instead
            of one field asserting "you have this role" while another
            says you're not in the server. */}
        <dl>
          <div><dt>Connected as</dt><dd>{discord?.connected === false ? "Not linked" : (discord?.display_name || name)}</dd></div>
          <div><dt>EAFC Live server</dt><dd>{discordActuallyJoined ? "Joined" : discord?.guild_membership === "not_joined" ? "Not joined" : "Status unavailable"}</dd></div>
          <div><dt>Discord role (entitled)</dt><dd>{discordEntitlement}</dd></div>
        </dl>
        {discord?.connected === false && isElite ? (
          <p className="v2-discord-note">
            Your {tier.toLowerCase()} plan is active on the website. Link Discord to get the member
            role in the server as well.
          </p>
        ) : roleNotYetSynced ? (
          <p className="v2-discord-note">
            You're entitled to the EAFC.Live Member role but it isn't showing as active in the
            server yet. Join the server if you haven't, then press Refresh Discord access below.
          </p>
        ) : null}
        <div className="v2-discord-actions">
          <a href={discord?.invite_url || DISCORD_INVITE_URL} target="_blank" rel="noreferrer"><ExternalLink size={15}/> Join EAFC Live Discord</a>
          <button type="button" onClick={refreshDiscordAccess} disabled={discordBusy}><RefreshCw size={15}/>{discordBusy ? "Refreshing..." : "Refresh Discord access"}</button>
        </div>
        {discordFeedback ? (
          <p className={`v2-discord-feedback ${discordFeedback.ok ? "ok" : "err"}`} role="status">{discordFeedback.text}</p>
        ) : null}
        {/* Only shown once we know, from the live GET /discord-feed/guilds
            call above, that this account actually administers at least
            one Discord server with MANAGE_GUILD/ADMINISTRATOR/ownership -
            not shown to every Elite member on the assumption they might. */}
        {manageableGuilds && manageableGuilds.length > 0 ? (
          <Link to="/discord-feed" className="v2-discord-feed-link">Manage a server's trading feed &rarr;</Link>
        ) : null}
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
