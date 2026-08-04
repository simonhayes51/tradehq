import React, { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useEntitlements } from "../context/EntitlementsContext";
import { useAuth } from "../context/AuthContext";
import {
  X,
  User,
  Settings as SettingsIcon,
  LogOut,
  Target,
  Users,
  Trophy,
  ArrowLeftRight,
  Gift,
  Zap,
  Bot,
  Sparkles,
  BarChart3,
  Activity,
  MessageSquare,
  TrendingUp,
} from "lucide-react";

const NAV_H = 72;
const LIME = "#91db32";

const MORE_LINKS = [
  { to: "/undervalued", label: "Undervalued 🔥", icon: TrendingUp },
  { to: "/trending", label: "Trending", icon: Target },
  { to: "/squad", label: "Squad Builder", icon: Users },
  { to: "/player-compare", label: "Compare Players", icon: ArrowLeftRight },
  { to: "/leaderboard", label: "Leaderboard", icon: Trophy },
  { to: "/referrals", label: "Referrals", icon: Gift },
];

const MORE_PREMIUM_LINKS = [
  { to: "/smart-buyer-ai", label: "Smart Buyer AI", icon: Bot },
  { to: "/best-buys", label: "Best Buys", icon: Sparkles },
  { to: "/trade-finder", label: "Trade Finder", icon: Target },
  { to: "/advanced-analytics", label: "Advanced Analytics", icon: BarChart3 },
  { to: "/portfolio-optimizer", label: "Portfolio Optimizer", icon: Activity },
  { to: "/trade-copilot", label: "Trade Copilot", icon: MessageSquare },
  { to: "/market-sentiment", label: "Market Sentiment", icon: TrendingUp },
  { to: "/market-maker", label: "Market Maker", icon: Zap },
];

function LockBadge() {
  return (
    <span
      className="absolute -right-1 -top-1 grid place-items-center rounded-full w-4 h-4 text-[10px]"
      style={{ background: "rgba(0,0,0,0.6)", border: "1px solid rgba(255,255,255,0.2)" }}
      title="Premium feature"
    >
      {/* tiny lock */}
      <svg viewBox="0 0 24 24" width="10" height="10" stroke="gold" fill="none">
        <path d="M7 11h10v8H7z" strokeWidth="2" />
        <path d="M9 11V8a3 3 0 0 1 6 0v3" strokeWidth="2" />
      </svg>
    </span>
  );
}

const Item = ({ to, label, icon, active, locked, onClick }) => (
  <Link
    to={to}
    onClick={onClick}
    aria-current={active ? "page" : undefined}
    className="relative flex-1 h-full grid place-items-center text-xs active:scale-[0.98] transition"
    title={locked ? "Premium feature" : label}
  >
    <div
      className={`flex items-center gap-2 px-3 py-2 rounded-xl ${
        active ? "bg-white/10 ring-1 ring-white/10" : "bg-transparent"
      }`}
      style={{ color: active ? LIME : "rgba(255,255,255,0.7)" }}
    >
      <span className="relative w-5 h-5">
        {icon}
        {locked && <LockBadge />}
      </span>
      <span className="leading-none">{label}</span>
    </div>
  </Link>
);

export default function MobileNavigation() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { isPremium } = useEntitlements();
  const { user, logout } = useAuth();
  const [moreOpen, setMoreOpen] = useState(false);

  const tabs = [
    {
      to: "/",
      label: "Home",
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <path
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M4 6h16M4 12h10M4 18h12"
          />
        </svg>
      ),
    },
    {
      to: "/trades",
      label: "Trades",
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <path strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6" />
          <path
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M7 21h10a2 2 0 0 0 2-2V7l-6-4H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2z"
          />
        </svg>
      ),
    },
    {
      to: "/watchlist",
      label: "Watchlist",
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <path
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"
          />
          <circle cx="12" cy="12" r="3" strokeWidth="2" />
        </svg>
      ),
    },
    {
      to: "/player-search",
      label: "Search",
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <circle cx="11" cy="11" r="7" strokeWidth="2" />
          <path d="M20 20l-3.5-3.5" strokeWidth="2" strokeLinecap="round" />
        </svg>
      ),
    },
    // Premium tab (replaces Analytics)
    {
      to: "/smart-buy",
      label: "Smart Buy",
      premium: true,
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <path strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
        </svg>
      ),
    },
  ];

  const isActive = (to) => (to === "/" ? pathname === "/" : pathname.startsWith(to));

  return (
    <>
      {/* Spacer so content never hides behind nav + safe-area */}
      <div style={{ height: `calc(${NAV_H}px + env(safe-area-inset-bottom))` }} />
      <nav
        className="fixed bottom-0 left-0 right-0 z-30 bg-gray-950/80 backdrop-blur-xl border-t border-white/10"
        style={{
          height: `calc(${NAV_H}px + env(safe-area-inset-bottom))`,
          paddingBottom: "env(safe-area-inset-bottom)",
        }}
      >
        {/* Subtle top glow line */}
        <div
          aria-hidden
          className="pointer-events-none absolute -top-px left-0 right-0 h-px"
          style={{ background: `linear-gradient(90deg, transparent, ${LIME}, transparent)` }}
        />
        <div className="h-full flex items-center justify-around px-2 gap-1">
          {tabs.map((t) => {
            const locked = !!t.premium && !isPremium;
            return (
              <Item
                key={t.to}
                to={locked ? "/billing" : t.to}
                label={t.label}
                icon={t.icon}
                active={isActive(t.to)}
                locked={locked}
                onClick={(e) => {
                  if (locked) {
                    e.preventDefault();
                    navigate("/billing");
                  }
                }}
              />
            );
          })}
          <button
            type="button"
            onClick={() => setMoreOpen(true)}
            className="relative flex-1 h-full grid place-items-center text-xs active:scale-[0.98] transition"
            title="More"
          >
            <div
              className="flex items-center gap-2 px-3 py-2 rounded-xl"
              style={{ color: "rgba(255,255,255,0.7)" }}
            >
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor">
                <circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none" />
                <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
                <circle cx="19" cy="12" r="1.6" fill="currentColor" stroke="none" />
              </svg>
              <span className="leading-none">More</span>
            </div>
          </button>
        </div>
      </nav>

      {moreOpen && (
        <div className="fixed inset-0 z-40 flex items-end justify-center">
          <div
            className="absolute inset-0 bg-black/60"
            onClick={() => setMoreOpen(false)}
            aria-hidden
          />
          <div
            className="relative w-full max-w-md bg-gray-950 border-t border-white/10 rounded-t-2xl max-h-[80vh] overflow-y-auto"
            style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
          >
            <div className="flex items-center justify-between px-4 pt-4 pb-2 sticky top-0 bg-gray-950">
              <div className="flex items-center gap-2 min-w-0">
                {user?.avatar_url && (
                  <img src={user.avatar_url} alt="" className="w-8 h-8 rounded-full border border-purple-500" />
                )}
                <span className="text-sm text-white truncate">{user?.global_name || user?.username || "Menu"}</span>
              </div>
              <button
                type="button"
                onClick={() => setMoreOpen(false)}
                className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-white/10"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="px-2 pb-2 grid grid-cols-3 gap-1">
              {MORE_LINKS.map((l) => (
                <Link
                  key={l.to}
                  to={l.to}
                  onClick={() => setMoreOpen(false)}
                  className="flex flex-col items-center gap-1.5 py-3 rounded-xl text-gray-200 hover:bg-white/5"
                >
                  <l.icon className="w-5 h-5" style={{ color: LIME }} />
                  <span className="text-[11px] text-center leading-tight">{l.label}</span>
                </Link>
              ))}
            </div>

            <p className="px-4 pt-2 pb-1 text-[11px] uppercase tracking-wide text-gray-500">
              Premium Tools
            </p>
            <div className="px-2 pb-2 grid grid-cols-3 gap-1">
              {MORE_PREMIUM_LINKS.map((l) => {
                const locked = !isPremium;
                return (
                  <Link
                    key={l.to}
                    to={locked ? "/billing" : l.to}
                    onClick={() => setMoreOpen(false)}
                    className="flex flex-col items-center gap-1.5 py-3 rounded-xl text-gray-200 hover:bg-white/5"
                  >
                    <span className="relative w-5 h-5">
                      <l.icon className="w-5 h-5" style={{ color: locked ? "rgba(255,255,255,0.5)" : LIME }} />
                      {locked && <LockBadge />}
                    </span>
                    <span className="text-[11px] text-center leading-tight">{l.label}</span>
                  </Link>
                );
              })}
            </div>

            <div className="mt-1 border-t border-white/10 px-2 py-2">
              <Link
                to="/profile"
                onClick={() => setMoreOpen(false)}
                className="flex items-center gap-3 px-3 py-2.5 rounded-xl text-gray-200 hover:bg-white/5"
              >
                <User className="w-5 h-5" /> Profile
              </Link>
              <Link
                to="/settings"
                onClick={() => setMoreOpen(false)}
                className="flex items-center gap-3 px-3 py-2.5 rounded-xl text-gray-200 hover:bg-white/5"
              >
                <SettingsIcon className="w-5 h-5" /> Settings
              </Link>
              <button
                type="button"
                onClick={async () => {
                  setMoreOpen(false);
                  await logout();
                  navigate("/login");
                }}
                className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-red-300 hover:bg-white/5 text-left"
              >
                <LogOut className="w-5 h-5" /> Log out
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
