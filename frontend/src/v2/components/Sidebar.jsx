import { Link, useLocation, useNavigate } from "react-router-dom";
import { Briefcase, Home, SlidersHorizontal, Sparkles, Star, TrendingUp, User, Users } from "lucide-react";
import { useEntitlements } from "../../context/EntitlementsContext";

const ITEMS = [
  { to: "/v2", label: "Home", short: "Home", icon: Home, match: (p) => p === "/v2" },
  // FUT.GG migration: Opportunities now has its own page/route (GET
  // /api/v2/opportunities) instead of redirecting into Trade Finder.
  { to: "/v2/opportunities", label: "Opportunities", short: "Opps", icon: TrendingUp, match: (p) => p.startsWith("/v2/opportunities") },
  { to: "/v2/trade-finder", label: "Trade Finder", short: "Finder", icon: SlidersHorizontal, match: (p) => p.startsWith("/v2/trade-finder") },
  { to: "/v2/players", label: "Players", short: "Players", icon: Users, match: (p) => p.startsWith("/v2/players") },
  { to: "/v2/chat", label: "AI Chat", short: "Chat", icon: Sparkles, match: (p) => p.startsWith("/v2/chat") },
  { to: "/v2/watchlist", label: "Watchlist", short: "Watch", icon: Star, match: (p) => p.startsWith("/v2/watchlist") },
  { to: "/v2/portfolio", label: "Portfolio", short: "Portfolio", icon: Briefcase, match: (p) => p.startsWith("/v2/portfolio") || p.startsWith("/v2/club") },
  // Desktop reaches Account via the tier chip pinned to the bottom of the
  // rail below - that chip is hidden on mobile (no room), so without this
  // there'd be no way to reach account/billing/logout from a phone at all.
  { to: "/v2/account", label: "Account", short: "Account", icon: User, mobileOnly: true, match: (p) => p.startsWith("/v2/account") },
];

export default function Sidebar() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { isPremium, isAdmin, features } = useEntitlements();
  const tier = isAdmin || features.includes("opportunity_feed") ? "ELITE" : isPremium ? "PRO" : "FREE";

  return <aside className="fut-side v2-global-nav">
    <Link className="fut-brand" to="/v2"><span className="brand-mark">FT</span><strong>FUT <b>Hub</b></strong></Link>
    <nav aria-label="Main">
      {ITEMS.map((item) => {
        const active = item.match(pathname);
        const Icon = item.icon;
        return <Link key={item.to} to={item.to} aria-current={active ? "page" : undefined} className={`${active ? "active" : ""}${item.desktopOnly ? " desktop-only-nav" : ""}${item.mobileOnly ? " mobile-only-nav" : ""}`}>
          <Icon/><span className="nav-full-label">{item.label}</span><span className="nav-short-label">{item.short}</span>
        </Link>;
      })}
    </nav>
    <button className={`account-chip ${pathname.startsWith("/v2/account") ? "active" : ""}`} onClick={() => navigate("/v2/account")}><User size={16}/><b>{tier}</b><span>Account</span></button>
  </aside>;
}
