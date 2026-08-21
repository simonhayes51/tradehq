import { Link, useLocation, useNavigate } from "react-router-dom";
import { Briefcase, Home, LineChart, SlidersHorizontal, Sparkles, Star, TrendingUp, User, Users } from "lucide-react";
import { useEntitlements } from "../../context/EntitlementsContext";
import { useAuth } from "../../context/AuthContext";

const ITEMS = [
  { to: "/v2", label: "Home", short: "Home", icon: Home, match: (p) => p === "/v2" },
  // FUT.GG migration: Opportunities now has its own page/route (GET
  // /api/v2/opportunities) instead of redirecting into Trade Finder.
  { to: "/v2/opportunities", label: "Opportunities", short: "Opps", icon: TrendingUp, match: (p) => p.startsWith("/v2/opportunities") },
  { to: "/v2/trade-finder", label: "Trade Finder", short: "Finder", icon: SlidersHorizontal, match: (p) => p.startsWith("/v2/trade-finder") },
  // Investment Engine: market-cycle/cohort plays (1-3 day holds), a
  // different question from Opportunities/Trade Finder's single-listing
  // flips - kept as its own nav item so the distinction is visible in
  // the UI, not just internally. See pages/Investments/Investments.jsx.
  { to: "/v2/investments", label: "Investments", short: "Invest", icon: LineChart, match: (p) => p.startsWith("/v2/investments") },
  { to: "/v2/players", label: "Players", short: "Players", icon: Users, match: (p) => p.startsWith("/v2/players") },
  // Everything below needs a signed-in account - a signed-out visitor
  // sees only the genuinely public part of the app (Home/Opportunities/
  // Trade Finder/Players preview), not the full sidebar of a page that
  // will just bounce them straight to signup anyway.
  { to: "/v2/chat", label: "AI Chat", short: "Chat", icon: Sparkles, match: (p) => p.startsWith("/v2/chat"), authOnly: true },
  { to: "/v2/watchlist", label: "Watchlist", short: "Watch", icon: Star, match: (p) => p.startsWith("/v2/watchlist"), authOnly: true },
  { to: "/v2/portfolio", label: "Portfolio", short: "Portfolio", icon: Briefcase, match: (p) => p.startsWith("/v2/portfolio") || p.startsWith("/v2/club"), authOnly: true },
  // Desktop reaches Account via the tier chip pinned to the bottom of the
  // rail below - that chip is hidden on mobile (no room), so without this
  // there'd be no way to reach account/billing/logout from a phone at all.
  { to: "/v2/account", label: "Account", short: "Account", icon: User, mobileOnly: true, authOnly: true, match: (p) => p.startsWith("/v2/account") },
];

export default function Sidebar() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { isPremium, isAdmin, features } = useEntitlements();
  const { isAuthenticated } = useAuth();
  const tier = isAdmin || features.includes("opportunity_feed") ? "ELITE" : isPremium ? "PREMIUM" : "FREE";
  const items = isAuthenticated ? ITEMS : ITEMS.filter((item) => !item.authOnly);

  return <aside className="fut-side v2-global-nav">
    <Link className="fut-brand" to="/v2"><span className="brand-mark">EL</span><strong>EAFC <b>Live</b></strong></Link>
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
      ? <button className={`account-chip ${pathname.startsWith("/v2/account") ? "active" : ""}`} onClick={() => navigate("/v2/account")}><User size={16}/><b>{tier}</b><span>Account</span></button>
      : <div className="account-chip auth-chip-pair">
          <button onClick={() => navigate("/login")}>Log in</button>
          <button className="primary" onClick={() => navigate("/signup")}>Start free</button>
        </div>}
  </aside>;
}
