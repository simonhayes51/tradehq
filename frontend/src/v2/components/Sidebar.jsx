import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowUpRight, Briefcase, Home, Lock, SlidersHorizontal, Sparkles, Star, TrendingUp, User, Users } from "lucide-react";
import { useEntitlements } from "../../context/EntitlementsContext";
import { useAuth } from "../../context/AuthContext";
import { useAiChatAvailability } from "../hooks/useAiChatAvailability";

const ITEMS = [
  { to: "/", label: "Home", short: "Home", icon: Home, match: (p) => p === "/" },
  // FUT.GG migration: Opportunities now has its own page/route (GET
  // /api/v2/opportunities) instead of redirecting into Trade Finder.
  { to: "/opportunities", label: "Opportunities", short: "Opps", icon: TrendingUp, match: (p) => p.startsWith("/opportunities") },
  // Shown to Free members with a lock rather than hidden. Hiding it means
  // a Free member never learns Trade Finder exists, which is the thing
  // they would be upgrading FOR; the lock is the advert. It is hidden
  // from signed-out visitors, who should be deciding whether to make an
  // account, not comparing plans.
  { to: "/trade-finder", label: "Trade Finder", short: "Finder", icon: SlidersHorizontal, match: (p) => p.startsWith("/trade-finder"), eliteFeature: "trade_finder", authOnly: true },
  { to: "/players", label: "Players", short: "Players", icon: Users, match: (p) => p.startsWith("/players") },
  // Everything below needs a signed-in account - a signed-out visitor
  // sees only the genuinely public part of the app (Home/Opportunities/
  // Trade Finder/Players preview), not the full sidebar of a page that
  // will just bounce them straight to signup anyway.
  // Also requires the assistant to actually be configured on this
  // deployment (GET /api/v2/ai/status) - a nav entry whose only possible
  // reply is "this isn't set up" is worse than no nav entry.
  { to: "/chat", label: "AI Chat", short: "Chat", icon: Sparkles, match: (p) => p.startsWith("/chat"), authOnly: true, requiresAiChat: true, eliteFeature: "ai_chat" },
  { to: "/watchlist", label: "Watchlist", short: "Watch", icon: Star, match: (p) => p.startsWith("/watchlist"), authOnly: true },
  { to: "/portfolio", label: "Portfolio", short: "Portfolio", icon: Briefcase, match: (p) => p.startsWith("/portfolio") || p.startsWith("/club"), authOnly: true },
  // Desktop reaches Account via the tier chip pinned to the bottom of the
  // rail below - that chip is hidden on mobile (no room), so without this
  // there'd be no way to reach account/billing/logout from a phone at all.
  { to: "/account", label: "Account", short: "Account", icon: User, mobileOnly: true, authOnly: true, match: (p) => p.startsWith("/account") },
];

export default function Sidebar() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { isAdmin, features, tier: plan } = useEntitlements();
  const { isAuthenticated } = useAuth();
  // Two states, one name for each. This used to render "PREMIUM" for a
  // paying customer and "ELITE" for an admin, which is the same
  // vocabulary split the billing page had.
  const isElite = plan === "elite" || isAdmin;
  const tierLabel = isElite ? "ELITE" : "FREE";
  const aiChat = useAiChatAvailability();
  const items = ITEMS.filter(
    (item) =>
      (isAuthenticated || !item.authOnly) &&
      (!item.requiresAiChat || aiChat.data?.configured === true),
  );
  // Whether to draw a lock beside an item. Derived from the entitlement
  // features, which come from the server - the lock is a label, and the
  // API is what actually refuses.
  const isLocked = (item) => Boolean(item.eliteFeature) && !features.includes(item.eliteFeature);

  return <aside className="fut-side v2-global-nav">
    <Link className="fut-brand" to="/"><span className="brand-mark">EL</span><strong>EAFC <b>Live</b></strong></Link>
    <nav aria-label="Main">
      {items.map((item) => {
        const active = item.match(pathname);
        const Icon = item.icon;
        const locked = isLocked(item);
        return <Link
          key={item.to}
          to={item.to}
          aria-current={active ? "page" : undefined}
          className={`${active ? "active" : ""}${item.desktopOnly ? " desktop-only-nav" : ""}${item.mobileOnly ? " mobile-only-nav" : ""}${locked ? " nav-locked" : ""}`}
          // A locked item is a working link, not an inert label: it goes
          // to the feature's own page, which renders the UpgradePrompt
          // written for THAT feature (what it does, why it's paid, what
          // you get, what it costs). Sending every lock to a generic
          // /billing page instead would lose the thing that makes the
          // prompt worth reading.
          title={locked ? `${item.label} is part of Elite - see what it includes` : undefined}
        >
          <Icon/>
          <span className="nav-full-label">{item.label}</span>
          <span className="nav-short-label">{item.short}</span>
          {/* aria-hidden on the icon, with the state in the link's own
              accessible name, so a screen reader hears "Trade Finder,
              Elite feature" once rather than an unlabelled icon. */}
          {locked ? <Lock size={12} className="nav-lock" aria-hidden="true"/> : null}
          {locked ? <span className="sr-only">Elite feature</span> : null}
        </Link>;
      })}
    </nav>
    {/* THE persistent upgrade entry point for a Free account.
        Before this there was none: /billing existed as a route but
        nothing in the signed-in app linked to it. A Free member could
        only reach it by hitting a lock somewhere and clicking the
        contextual prompt - so anyone who never happened to open Trade
        Finder or run out of watchlist slots had no way to pay us.
        Deliberately ONE entry, pinned above the account chip, rather
        than a banner on every page: the contextual prompts already
        cover the moments where the limit is actually felt. Hidden for
        Elite and for admins, who have nothing to upgrade to. */}
    {isAuthenticated && !isElite
      ? <Link className={`nav-upgrade ${pathname.startsWith("/billing") ? "active" : ""}`} to="/billing?unlock=elite">
          <Sparkles size={16}/>
          <span className="nav-full-label">Upgrade to Elite</span>
          <span className="nav-short-label">Elite</span>
          <ArrowUpRight size={14} className="nav-upgrade-arrow" aria-hidden="true"/>
        </Link>
      : null}
    {isAuthenticated
      ? <button className={`account-chip ${pathname.startsWith("/account") ? "active" : ""}`} onClick={() => navigate("/account")}><User size={16}/><b>{tierLabel}</b><span>Account</span></button>
      : <div className="account-chip auth-chip-pair">
          <button onClick={() => navigate("/login")}>Log in</button>
          <button className="primary" onClick={() => navigate("/signup")}>Start free</button>
        </div>}
  </aside>;
}
