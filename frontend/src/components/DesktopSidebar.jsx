// src/components/DesktopSidebar.jsx
import React, { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useEntitlements } from "../context/EntitlementsContext";
import {
  ChevronDown,
  ChevronUp,
  Crown,
  Lock,
  Home,
  Plus,
  BarChart3,
  Search,
  Users,
  Eye,
  Zap,
  Target,
  User,
  Settings,
  ArrowLeftRight,
  Sparkles,
  Calculator,
  Bot,
  Activity,
  Trophy,
  Gift,
  TrendingUp,
  MessageSquare,
  LogOut,
} from "lucide-react";

const DesktopSidebar = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const { isPremium } = useEntitlements();

  const [collapsed, setCollapsed] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("sidebarCollapsed") || "false");
    } catch {
      return false;
    }
  });
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userCardRef = useRef(null);

  useEffect(() => {
    const width = collapsed ? "4rem" : "16rem";
    document.documentElement.style.setProperty("--sidebar-width", width);
    localStorage.setItem("sidebarCollapsed", JSON.stringify(collapsed));
  }, [collapsed]);

  useEffect(() => {
    const onDocClick = (e) => {
      if (!userCardRef.current) return;
      if (!userCardRef.current.contains(e.target)) setUserMenuOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);
  useEffect(() => setUserMenuOpen(false), [location.pathname]);

  const navItems = [
    { path: "/dashboard", label: "Dashboard", icon: Home },
    { path: "/add-trade", label: "Add Trade", icon: Plus },
    { path: "/trades", label: "Recent Trades", icon: BarChart3 },
    { path: "/profit-calculator", label: "Profit Calc", icon: Calculator },
    { path: "/player-search", label: "Player Search", icon: Search },
    { path: "/player-compare", label: "Compare", icon: ArrowLeftRight },
    { path: "/watchlist", label: "Watchlist", icon: Eye },
    { path: "/undervalued", label: "Undervalued 🔥", icon: TrendingUp },
    { path: "/trending", label: "Trending", icon: Target },
    { path: "/squad", label: "Squad Builder", icon: Users },
    { path: "/leaderboard", label: "Leaderboard", icon: Trophy },
    { path: "/referrals", label: "Referrals", icon: Gift },
  ];

  const premiumItems = [
    { path: "/smart-buy", label: "Smart Buy", icon: Zap, premium: true },
    { path: "/smart-buyer-ai", label: "Smart Buyer AI", icon: Bot, premium: true },
    { path: "/best-buys", label: "Best Buys", icon: Sparkles, premium: true },
    { path: "/trade-finder", label: "Trade Finder", icon: Target, premium: true },
    { path: "/advanced-analytics", label: "Advanced Analytics", icon: BarChart3, premium: true },
    { path: "/portfolio-optimizer", label: "Portfolio Optimizer", icon: Activity, premium: true },
    { path: "/trade-copilot", label: "Trade Copilot", icon: MessageSquare, premium: true },
    { path: "/market-sentiment", label: "Market Sentiment", icon: TrendingUp, premium: true },
    { path: "/market-maker", label: "Market Maker", icon: Target, premium: true },
  ];

  const isActive = (p) => location.pathname === p;
  const onPremiumClick = (e, item) => {
    if (item.premium && !isPremium) {
      e.preventDefault();
      navigate("/billing");
    }
  };

  return (
    <aside
      className={`fixed left-0 top-0 h-screen bg-gray-900/95 backdrop-blur-sm border-r border-gray-700/50
                  flex flex-col transition-all duration-200
                  ${collapsed ? "w-16 px-2" : "w-64 p-4"}`}
    >
      {/* Logo / header */}
      <div className="relative mb-6 shrink-0 flex flex-col items-center">
        <div
          className={`rounded-full p-[4px] bg-gradient-to-r from-green-400/80 to-blue-500/80 
                      overflow-hidden aspect-square mt-2
                      ${collapsed ? "w-10" : "w-16"}`}
        >
          <img
            src="/server-logo.png"
            alt="Server Logo"
            className="w-full h-full rounded-full object-cover block"
          />
        </div>
        {!collapsed && (
          <h1 className="mt-2 text-lg font-bold text-white text-center">
            FUT Traders Hub
          </h1>
        )}
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="absolute top-1/2 -right-3 -translate-y-1/2 
                     p-1.5 rounded-md bg-gray-900 border border-gray-700
                     hover:bg-gray-800 text-gray-300 shadow-lg"
        >
          {collapsed ? "›" : "‹"}
        </button>
      </div>

      {/* User card */}
      <div
        ref={userCardRef}
        className={`relative bg-gray-800/40 rounded-xl ${collapsed ? "p-2 mb-3" : "p-3 mb-4"} shrink-0`}
      >
        <button
          type="button"
          onClick={() => setUserMenuOpen((o) => !o)}
          className={`w-full flex items-center ${collapsed ? "justify-center" : "gap-3"} text-left`}
        >
          <img
            src={user?.avatar_url}
            alt={user?.global_name}
            className={`${collapsed ? "w-8 h-8" : "w-10 h-10"} rounded-full border-2 border-purple-500`}
          />
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-white truncate flex items-center gap-1">
                {user?.global_name || user?.username}
                {userMenuOpen ? (
                  <ChevronUp className="w-4 h-4 text-gray-400" />
                ) : (
                  <ChevronDown className="w-4 h-4 text-gray-400" />
                )}
              </p>
              <p className="text-xs text-gray-400">Trader</p>
            </div>
          )}
        </button>

        {userMenuOpen && (
          <div className="absolute left-0 top-full mt-2 z-20 w-52 bg-gray-900 border border-gray-700/70 rounded-xl shadow-xl overflow-hidden py-1">
            <Link
              to="/profile"
              className="flex items-center gap-2 px-3 py-2 text-sm text-gray-200 hover:bg-gray-800"
            >
              <User className="w-4 h-4" /> Profile
            </Link>
            <Link
              to="/settings"
              className="flex items-center gap-2 px-3 py-2 text-sm text-gray-200 hover:bg-gray-800"
            >
              <Settings className="w-4 h-4" /> Settings
            </Link>
            <button
              type="button"
              onClick={async () => {
                setUserMenuOpen(false);
                await logout();
                navigate("/login");
              }}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-300 hover:bg-gray-800 text-left"
            >
              <LogOut className="w-4 h-4" /> Log out
            </button>
          </div>
        )}
      </div>

      {/* Nav items */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        <nav className={`${collapsed ? "px-1" : "px-1.5"} space-y-1`}>
          {navItems.map((item) => {
            const locked = item.premium && !isPremium;
            const isHighlighted = item.highlight;
            return (
              <Link
                key={item.path}
                to={locked ? "/billing" : item.path}
                onClick={(e) => onPremiumClick(e, item)}
                className={`flex items-center rounded-lg transition-colors h-9
                           ${collapsed ? "justify-center" : "gap-2 px-2.5"}
                           ${
                             isActive(item.path)
                               ? isHighlighted
                                 ? "bg-gradient-to-r from-purple-600/30 to-indigo-600/30 text-purple-200 border-r-2 border-purple-400"
                                 : "bg-purple-600/20 text-purple-300 border-r-2 border-purple-500"
                               : isHighlighted
                               ? "bg-gradient-to-r from-purple-900/20 to-indigo-900/20 text-purple-300 hover:from-purple-800/30 hover:to-indigo-800/30"
                               : "text-gray-300 hover:text-white hover:bg-gray-800/60"
                           } ${locked ? "opacity-80" : ""}`}
              >
                <item.icon className={`w-5 h-5 shrink-0 ${isHighlighted && !isActive(item.path) ? "text-purple-400" : ""}`} />
                {!collapsed && <span className="text-sm">{item.label}</span>}
                {locked && !collapsed && (
                  <span className="ml-auto flex items-center gap-1 text-xs text-yellow-300">
                    <Lock className="w-3.5 h-3.5" />
                    <Crown className="w-3.5 h-3.5" />
                  </span>
                )}
              </Link>
            );
          })}

          {!collapsed && (
            <p className="px-2.5 pt-4 pb-1 text-[11px] uppercase tracking-wide text-gray-500">
              Premium Tools
            </p>
          )}
          {collapsed && <div className="my-2 border-t border-gray-700/50" />}

          {premiumItems.map((item) => {
            const locked = item.premium && !isPremium;
            return (
              <Link
                key={item.path}
                to={locked ? "/billing" : item.path}
                onClick={(e) => onPremiumClick(e, item)}
                className={`flex items-center rounded-lg transition-colors h-9
                           ${collapsed ? "justify-center" : "gap-2 px-2.5"}
                           ${
                             isActive(item.path)
                               ? "bg-purple-600/20 text-purple-300 border-r-2 border-purple-500"
                               : "text-gray-300 hover:text-white hover:bg-gray-800/60"
                           } ${locked ? "opacity-80" : ""}`}
              >
                <item.icon className="w-5 h-5 shrink-0" />
                {!collapsed && <span className="text-sm">{item.label}</span>}
                {locked && !collapsed && (
                  <span className="ml-auto flex items-center gap-1 text-xs text-yellow-300">
                    <Lock className="w-3.5 h-3.5" />
                    <Crown className="w-3.5 h-3.5" />
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
      </div>

      {/* Footer Premium button */}
      <div className={`${collapsed ? "px-2 py-2" : "px-2 py-3"} border-t border-gray-700/50`}>
        <Link
          to="/billing"
          className={`flex items-center rounded-lg h-8 text-[13px]
                      ${collapsed ? "justify-center" : "gap-2 px-2.5"}
                      text-gray-200 hover:text-white hover:bg-gray-800/60`}
        >
          <Crown className="w-4 h-4 text-yellow-400" />
          {!collapsed && (
            <span>{isPremium ? "Premium Active" : "Upgrade to Premium"}</span>
          )}
        </Link>
      </div>
    </aside>
  );
};

export default DesktopSidebar;
