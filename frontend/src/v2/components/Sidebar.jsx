import { Link, useLocation, useNavigate } from "react-router-dom";
import { Briefcase, Home, SlidersHorizontal, Sparkles, Star, TrendingUp, User, Users } from "lucide-react";
import { useEntitlements } from "../../context/EntitlementsContext";
import { useAuth } from "../../context/AuthContext";
import { useAiChatAvailability } from "../hooks/useAiChatAvailability";

const ITEMS = [
  { to: "/", label: "Home", short: "Home", icon: Home, match: (p) => p === "/" },
  // FUT.GG migration: Opportunities now has its own page/route (GET
  // /api/v2/opportunities) instead of redirecting into Trade Finder.
  { to: "/opportunities", label: "Opportunities", short: "Opps", icon: TrendingUp, match: (p) => p.startsWith("/opportunities") },
  { to: "/trade-finder", label: "Trade Finder", short: "Finder", icon: SlidersHorizontal, match: (p) => p.startsWith("/trade-finder") },
  { to: "/players", label: "Players", short: "Players", icon: Users, match: (p) => p.startsWith("/players") },
  // Everything below needs a signed-in account - a signed-out visitor
  // sees only the genuinely public part of the app (Home/Opportunities/
  // Trade Finder/Players preview), not the full sidebar of a page that
  // will just bounce them straight to signup anyway.
  // Also requires the assistant to actually be configured on this
  // deployment (GET /api/v2/ai/status) - a nav entry whose only possible
  // reply is "this isn't set up" is worse than no nav entry.
  { to: "/chat", label: "AI Chat", short: "Chat", icon: Sparkles, match: (p) => p.startsWith("/chat"), authOnly: true, requiresAiChat: true },
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
  const { isPremium, isAdmin, features } = useEntitlements();
  const { isAuthenticated } = useAuth();
  const tier = isAdmin || features.includes("opportunity_feed") ? "ELITE" : isPremium ? "PREMIUM" : "FREE";
  const aiChat = useAiChatAvailability();
  const items = ITEMS.filter(
    (item) =>
      (isAuthenticated || !item.authOnly) &&
      (!item.requiresAiChat || aiChat.data?.configured === true),
  );

  return <aside className="fut-side v2-global-nav">
    <Link className="fut-brand" to="/"><span className="brand-mark">EL</span><strong>EAFC <b>Live</b></strong></Link>
    <nav aria-label="Main">
      {items.map((item) => {
        const active = item.match(pathname);
        const Icon = item.icon;
        return <Link key={item.to} to={item.to} aria-current={active ? "page" : undefined} className={`${active ? "active" : ""}${item.desktopOnly ? " desktop-only-nav" : ""}${item.mobileOnly ? " mobile-only-nav" : ""}`}>
          <Icon/><span className="nav-full-label">{item.label}</span><span className="nav-short-label">{item.short}</span>
        </Link>;
      })}
    </nav>
    {isAuthenticated
      ? <button className={`account-chip ${pathname.startsWith("/account") ? "active" : ""}`} onClick={() => navigate("/account")}><User size={16}/><b>{tier}</b><span>Account</span></button>
      : <div className="account-chip auth-chip-pair">
          <button onClick={() => navigate("/login")}>Log in</button>
          <button className="primary" onClick={() => navigate("/signup")}>Start free</button>
        </div>}
  </aside>;
}
