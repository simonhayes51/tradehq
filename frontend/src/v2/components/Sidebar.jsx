import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowUpRight, Briefcase, Home, Lock, MoreHorizontal, Server, SlidersHorizontal, Sparkles, Star, TrendingUp, User, Users, X } from "lucide-react";
import { useEntitlements } from "../../context/EntitlementsContext";
import { useAuth } from "../../context/AuthContext";
import { useAiChatAvailability } from "../hooks/useAiChatAvailability";
import { useDiscordFeedGuilds } from "../hooks/useDiscordFeedGuilds";
import "../styles/mobile-nav.css";

const ITEMS = [
  { to: "/", label: "Home", short: "Home", icon: Home, match: (p) => p === "/" },
  { to: "/opportunities", label: "Opportunities", short: "Opps", icon: TrendingUp, match: (p) => p.startsWith("/opportunities") },
  // Free sees Finder with a lock rather than having the product hidden.
  { to: "/trade-finder", label: "Trade Finder", short: "Finder", icon: SlidersHorizontal, match: (p) => p.startsWith("/trade-finder"), eliteFeature: "trade_finder", authOnly: true },
  { to: "/players", label: "Players", short: "Players", icon: Users, match: (p) => p.startsWith("/players") },
  // Mobile has room for four primary destinations plus one More button.
  // These remain first-class desktop links and move into the mobile More
  // sheet instead of being squeezed into an eight-across bottom bar.
  { to: "/chat", label: "AI Chat", short: "Chat", icon: Sparkles, match: (p) => p.startsWith("/chat"), authOnly: true, requiresAiChat: true, eliteFeature: "ai_chat", mobileOverflow: true },
  { to: "/watchlist", label: "Watchlist", short: "Watch", icon: Star, match: (p) => p.startsWith("/watchlist"), authOnly: true, mobileOverflow: true },
  { to: "/portfolio", label: "Portfolio", short: "Portfolio", icon: Briefcase, match: (p) => p.startsWith("/portfolio") || p.startsWith("/club"), authOnly: true, mobileOverflow: true },
  // Server Feed is guild-scoped and separately billed. It is deliberately
  // NOT tied to isElite: a Free personal account can legitimately manage
  // a paid Discord feed. Only show it once the live guild endpoint says
  // this member actually has a configured feed to manage.
  { to: "/discord-feed", label: "Server Feed", short: "Feed", icon: Server, match: (p) => p.startsWith("/discord-feed"), authOnly: true, requiresServerFeed: true, mobileOverflow: true },
  // Desktop reaches Account through the tier chip. Mobile reaches it from
  // More, so there is still one obvious route to billing/logout without
  // consuming another bottom-tab slot.
  { to: "/account", label: "Account", short: "Account", icon: User, desktopHidden: true, authOnly: true, mobileOverflow: true, match: (p) => p.startsWith("/account") || p.startsWith("/billing") },
];

export default function Sidebar() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { isAdmin, features, tier: plan } = useEntitlements();
  const { isAuthenticated } = useAuth();
  const [moreOpen, setMoreOpen] = useState(false);
  const isElite = plan === "elite" || isAdmin;
  const tierLabel = isElite ? "ELITE" : "FREE";
  const aiChat = useAiChatAvailability();
  const serverFeeds = useDiscordFeedGuilds(isAuthenticated);
  const hasServerFeed = Array.isArray(serverFeeds.data) && serverFeeds.data.length > 0;
  const items = ITEMS.filter(
    (item) =>
      (isAuthenticated || !item.authOnly) &&
      (!item.requiresAiChat || aiChat.data?.configured === true) &&
      (!item.requiresServerFeed || hasServerFeed),
  );
  const mobileOverflowItems = items.filter((item) => item.mobileOverflow);
  const moreActive = mobileOverflowItems.some((item) => item.match(pathname));
  const isLocked = (item) => Boolean(item.eliteFeature) && !features.includes(item.eliteFeature);

  const renderItem = (item, { inMore = false } = {}) => {
    const active = item.match(pathname);
    const Icon = item.icon;
    const locked = isLocked(item);
    return <Link
      key={`${inMore ? "more-" : ""}${item.to}`}
      to={item.to}
      aria-current={active ? "page" : undefined}
      onClick={() => inMore && setMoreOpen(false)}
      className={`${active ? "active" : ""}${item.desktopHidden ? " desktop-hidden-nav" : ""}${item.mobileOverflow ? " mobile-overflow-nav" : ""}${locked ? " nav-locked" : ""}${inMore ? " mobile-more-link" : ""}`}
      title={locked ? `${item.label} is part of Elite - see what it includes` : undefined}
    >
      <Icon/>
      <span className="nav-full-label">{item.label}</span>
      <span className="nav-short-label">{item.short}</span>
      {locked ? <Lock size={12} className="nav-lock" aria-hidden="true"/> : null}
      {locked ? <span className="sr-only">Elite feature</span> : null}
    </Link>;
  };

  return <aside className="fut-side v2-global-nav">
    <Link className="fut-brand" to="/" aria-label="EAFC.Live home"><img className="brand-logo" src="/icons/icon-192x192.png" alt="" /><strong>EAFC<b>.Live</b></strong></Link>
    <nav aria-label="Main">
      {items.map((item) => renderItem(item))}
      {mobileOverflowItems.length ? (
        <button
          type="button"
          className={`mobile-more-trigger${moreActive ? " active" : ""}`}
          aria-expanded={moreOpen}
          aria-controls="mobile-more-menu"
          onClick={() => setMoreOpen((open) => !open)}
        >
          {moreOpen ? <X/> : <MoreHorizontal/>}
          <span>More</span>
        </button>
      ) : null}
    </nav>

    {moreOpen && mobileOverflowItems.length ? (
      <div id="mobile-more-menu" className="mobile-more-menu" role="menu" aria-label="More navigation">
        <div className="mobile-more-head"><strong>More</strong><span>{tierLabel}</span></div>
        <div className="mobile-more-grid">
          {mobileOverflowItems.map((item) => renderItem(item, { inMore: true }))}
        </div>
        {!isElite ? <Link className="mobile-more-upgrade" to="/billing?unlock=elite" onClick={() => setMoreOpen(false)}><Sparkles size={16}/> Upgrade to Elite <ArrowUpRight size={14}/></Link> : null}
      </div>
    ) : null}

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
