// src/App.jsx

import { lazy, Suspense } from "react";
import { HashRouter as Router, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "react-hot-toast";
import { AuthProvider } from "./context/AuthContext";
import { DashboardProvider } from "./context/DashboardContext";
import { SettingsProvider } from "./context/SettingsContext";
import { EntitlementsProvider } from "./context/EntitlementsContext";

import ErrorBoundary from "./components/ErrorBoundary";
import Layout from "./components/Layout";
import Loading from "./components/Loading";
import PrivateRoute from "./components/PrivateRoute";
import PremiumRoute from "./components/PremiumRoute";

// Eager pages
import Landing from "./pages/Landing";
import Watchlist from "./pages/Watchlist";
import SquadBuilder from "./pages/SquadBuilder";
import PlayerSearch from "./pages/PlayerSearch";
import TradeFinder from "./pages/TradeFinder";

// Lazy pages
const Dashboard     = lazy(() => import("./pages/Dashboard"));
const AddTrade      = lazy(() => import("./pages/AddTrade"));
const Trades        = lazy(() => import("./pages/Trades"));
const Profile       = lazy(() => import("./pages/Profile"));
const Settings      = lazy(() => import("./pages/Settings"));
const ProfitGraph   = lazy(() => import("./pages/ProfitGraph"));
const PriceCheck    = lazy(() => import("./pages/PriceCheck"));
const Trending      = lazy(() => import("./pages/Trending"));
const SmartBuy      = lazy(() => import("./pages/SmartBuy"));
const Login         = lazy(() => import("./pages/Login"));
const AccessDenied  = lazy(() => import("./pages/AccessDenied"));
const NotFound      = lazy(() => import("./pages/NotFound"));
const PlayerCompare = lazy(() => import("./pages/PlayerCompare"));
const Billing       = lazy(() => import("./pages/Billing"));

// NEW pages
const SmartBuyerAI  = lazy(() => import("./pages/SmartBuyerAI"));
const BestBuys      = lazy(() => import("./pages/BestBuys"));
const UndervaluedBoard = lazy(() => import("./pages/UndervaluedBoard"));
const AdminUsers = lazy(() => import("./pages/AdminUsers"));
const Demo = lazy(() => import("./pages/Demo"));

// Server-render-only route: the backend's headless Chromium renderer
// navigates here (see app/services/player_card_render.py), not a real
// visitor - sits outside PrivateRoute like /demo, since Chromium carries
// no session cookie. Gated instead by a signed token query param the
// backend mints and verifies per request.
const PlayerCardExport = lazy(() => import("./pages/internal/PlayerCardExport"));

// v2 - AI market-intelligence redesign, self-contained under src/v2/ (see
// the v2 plan). Own internal routing via V2App's <Routes>, so it's
// mounted with a /v2/* wildcard rather than nested child routes here.
const V2App = lazy(() => import("./v2/App.v2"));

// ENHANCED features
const PortfolioOptimizer = lazy(() => import("./pages/PortfolioOptimizer"));
const ProfitCalculator = lazy(() => import("./pages/ProfitCalculator"));
const TradeCopilot = lazy(() => import("./pages/TradeCopilot"));
const Leaderboard = lazy(() => import("./pages/Leaderboard"));
const ReferralProgram = lazy(() => import("./pages/ReferralProgram"));
const MarketSentiment = lazy(() => import("./pages/MarketSentiment"));
const MarketMaker = lazy(() => import("./pages/MarketMaker"));

function App() {
  return (
    <ErrorBoundary>
      <AuthProvider>
        <EntitlementsProvider>
          <Router>
            <div id="app-shell-bg" className="bg-black min-h-screen text-white">
              {/* Mounted once here (not inside v1's Layout) so toasts -
                  e.g. EntitlementsContext's premium-expiry notice - render
                  regardless of whether a v1 or v2 route is active; v2
                  sits outside Layout entirely. */}
              <Toaster position="top-right" />
              <Suspense fallback={<Loading />}>
                <Routes>
                  {/* Public routes */}
                  <Route path="/login" element={<Login />} />
                  <Route path="/access-denied" element={<AccessDenied />} />
                  <Route path="/landing" element={<Landing />} />
                  {/* Investor/buyer-facing read-only data pipeline demo -
                      public on purpose, must not sit behind PrivateRoute */}
                  <Route path="/demo" element={<Demo />} />

                  {/* Internal render-only route for card PNG generation -
                      see PlayerCardExport.jsx's header comment. */}
                  <Route path="/internal/render/player-card/:cardId" element={<PlayerCardExport />} />

                  {/* v2 - handles its own per-panel gating via
                      useEntitlements/PremiumGate rather than a full-page
                      PrivateRoute redirect, so it sits outside the
                      protected shell below like /demo does. */}
                  <Route path="/v2/*" element={<V2App />} />

                  {/* Protected shell */}
                  <Route
                    path="/"
                    element={
                      <PrivateRoute>
                        <SettingsProvider>
                          <DashboardProvider>
                            <Layout />
                          </DashboardProvider>
                        </SettingsProvider>
                      </PrivateRoute>
                    }
                  >
                    {/* Dashboard (default) */}
                    <Route index element={<Dashboard />} />
                    {/* Optional aliases – both load Dashboard */}
                    <Route path="overview" element={<Dashboard />} />
                    <Route path="dashboard" element={<Dashboard />} />

                    {/* Free tier pages */}
                    <Route path="add-trade" element={<AddTrade />} />
                    <Route path="trades" element={<Trades />} />
                    <Route path="player-search" element={<PlayerSearch />} />
                    <Route path="player-compare" element={<PlayerCompare />} />
                    <Route path="profile" element={<Profile />} />
                    <Route path="settings" element={<Settings />} />
                    <Route path="analytics" element={<ProfitGraph />} />
                    <Route path="pricecheck" element={<PriceCheck />} />
                    <Route path="watchlist" element={<Watchlist />} />
                    <Route path="squad" element={<SquadBuilder />} />
                    <Route path="billing" element={<Billing />} />
                    {/* PricingTiers (3-tier plan) was removed - it described a pricing
                        model that never matched the app's real one (a single flat
                        Premium tier sold on Billing.jsx), and its checkout call sent a
                        payload the backend didn't understand. Old links redirect here. */}
                    <Route path="pricing" element={<Navigate to="/billing" replace />} />
                    <Route path="profit-calculator" element={<ProfitCalculator />} />
                    <Route path="leaderboard" element={<Leaderboard />} />
                    <Route path="referrals" element={<ReferralProgram />} />

                    {/* Basic trending (free tier gets limited access) */}
                    <Route path="trending" element={<Trending />} />

                    {/* Undervalued board - the page renders its own free
                        teaser vs Pro full-board split, so no PremiumRoute */}
                    <Route path="undervalued" element={<UndervaluedBoard />} />

                    {/* Admin (server enforces require_admin; page hides itself
                        for non-admins as UX) */}
                    <Route path="admin/users" element={<AdminUsers />} />

                    {/* Premium-only routes */}
                    <Route
                      path="smart-buy"
                      element={
                        <PremiumRoute feature="smart_buy" featureName="Smart Buy AI">
                          <SmartBuy />
                        </PremiumRoute>
                      }
                    />

                    <Route
                      path="trade-finder"
                      element={
                        <PremiumRoute feature="trade_finder" featureName="Advanced Trade Finder">
                          <TradeFinder />
                        </PremiumRoute>
                      }
                    />

                    {/* Smart Buyer (Simple) with name search + Trade Plan */}
                    <Route
                      path="smart-buyer-ai"
                      element={
                        <PremiumRoute feature="smart_buy" featureName="Smart Buyer AI">
                          <SmartBuyerAI />
                        </PremiumRoute>
                      }
                    />

                    {/* Best Buys board (NOT default) */}
                    <Route
                      path="best-buys"
                      element={
                        <PremiumRoute feature="smart_buy" featureName="Smart Buy AI">
                          <BestBuys />
                        </PremiumRoute>
                      }
                    />

                    {/* Optional premium analytics alias */}
                    <Route
                      path="advanced-analytics"
                      element={
                        <PremiumRoute feature="advanced_analytics" featureName="Advanced Analytics">
                          <ProfitGraph />
                        </PremiumRoute>
                      }
                    />

                    {/* Elite tier features */}
                    <Route
                      path="portfolio-optimizer"
                      element={
                        <PremiumRoute feature="portfolio_optimizer" featureName="Portfolio Optimizer">
                          <PortfolioOptimizer />
                        </PremiumRoute>
                      }
                    />

                    <Route
                      path="trade-copilot"
                      element={
                        <PremiumRoute feature="ai_copilot" featureName="AI Trade Copilot">
                          <TradeCopilot />
                        </PremiumRoute>
                      }
                    />

                    <Route
                      path="market-sentiment"
                      element={
                        <PremiumRoute feature="market_sentiment" featureName="Market Sentiment">
                          <MarketSentiment />
                        </PremiumRoute>
                      }
                    />

                    <Route
                      path="market-maker"
                      element={
                        <PremiumRoute feature="market_maker" featureName="Market Maker Mode">
                          <MarketMaker />
                        </PremiumRoute>
                      }
                    />
                  </Route>

                  {/* 404 fallback */}
                  <Route path="*" element={<NotFound />} />
                </Routes>
              </Suspense>
            </div>
          </Router>
        </EntitlementsProvider>
      </AuthProvider>
    </ErrorBoundary>
  );
}

export default App;
