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
import PublicSite from "./PublicSite";
import { isPublicHost } from "./lib/domains";

// Eager pages
import SquadBuilder from "./pages/SquadBuilder";

// Lazy pages
const Signup         = lazy(() => import("./pages/Signup"));
const Privacy        = lazy(() => import("./pages/Privacy"));
const Terms          = lazy(() => import("./pages/Terms"));
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

// NEW pages
const UndervaluedBoard = lazy(() => import("./pages/UndervaluedBoard"));
const AdminUsers = lazy(() => import("./pages/AdminUsers"));
const Demo = lazy(() => import("./pages/Demo"));

// EAFC Live Companion userscript launcher page - public on purpose, same
// reasoning as /demo: a visitor reaching this link to install the
// userscript is never expected to already have a TradeHQ session.
const Companion = lazy(() => import("./pages/Companion"));

// Server-render-only route: the backend's headless Chromium renderer
// navigates here (see app/services/player_card_render.py), not a real
// visitor - sits outside PrivateRoute like /demo, since Chromium carries
// no session cookie. Gated instead by a signed token query param the
// backend mints and verifies per request.
const PlayerCardExport = lazy(() => import("./pages/internal/PlayerCardExport"));
// Same pattern, for the Portfolio "Share" button's trade-result card -
// see app/services/trade_share_render.py.
const TradeShareExport = lazy(() => import("./pages/internal/TradeShareExport"));
// Same pattern again, for the period-summary (Today/This Week/All-Time)
// recap card - see app/services/trade_summary_render.py.
const TradeSummaryExport = lazy(() => import("./pages/internal/TradeSummaryExport"));

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
  // eafc.live (the public marketing site) and app.eafc.live (the trading
  // app) are the SAME build/deploy - see lib/domains.js - branching here,
  // before AuthProvider/Router even mount, keeps PublicSite fully
  // independent of the app's auth/routing machinery rather than trying
  // to thread a "public mode" flag through it.
  if (isPublicHost()) {
    return <PublicSite />;
  }

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
                  <Route path="/signup" element={<Signup />} />
                  {/* Also reachable directly on app.eafc.live (not just
                      eafc.live/PublicSite) so Billing's links don't need
                      a cross-domain hop for something this simple. */}
                  <Route path="/privacy" element={<Privacy />} />
                  <Route path="/terms" element={<Terms />} />
                  <Route path="/access-denied" element={<AccessDenied />} />
                  {/* Investor/buyer-facing read-only data pipeline demo -
                      public on purpose, must not sit behind PrivateRoute */}
                  <Route path="/demo" element={<Demo />} />
                  {/* EAFC Live Companion launcher: "install the userscript
                      once, then launch the Web App normally" - see
                      pages/Companion.jsx. Public, no app shell. */}
                  <Route path="/companion" element={<Companion />} />

                  {/* Internal render-only route for card PNG generation -
                      see PlayerCardExport.jsx's header comment. */}
                  <Route path="/internal/render/player-card/:cardId" element={<PlayerCardExport />} />
                  <Route path="/internal/render/trade-share/:tradeId" element={<TradeShareExport />} />
                  <Route path="/internal/render/trade-summary/:period" element={<TradeSummaryExport />} />

                  {/* v2 - handles its own per-panel gating via
                      useEntitlements/PremiumGate rather than a full-page
                      PrivateRoute redirect, so it sits outside the
                      protected shell below like /demo does. */}
                  <Route path="/v2/*" element={<V2App />} />

                  {/* v2 is now the public entry point: the root domain and
                      every v1 page it has replaced (dashboard home,
                      watchlist, player search, trade finder) redirect
                      straight into it, ungated - v2 handles its own
                      per-panel entitlement gating rather than the
                      all-or-nothing PrivateRoute login redirect below,
                      which must never fire for a first-time visitor
                      landing on "/". Everything v2 doesn't have a
                      replacement for yet (Smart Buy AI, Portfolio
                      Optimizer, Trade Copilot, Market Sentiment, Market
                      Maker, Leaderboard, Referrals, Squad Builder, trade
                      logging) stays reachable at its existing v1 URL
                      below so paying users don't lose access to anything
                      at launch. */}
                  <Route path="/" element={<Navigate to="/v2" replace />} />
                  <Route path="/watchlist" element={<Navigate to="/v2/watchlist" replace />} />
                  <Route path="/player-search" element={<Navigate to="/v2/players" replace />} />
                  <Route path="/trade-finder" element={<Navigate to="/v2/trade-finder" replace />} />

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
                    {/* "/" itself is claimed by the public v2 redirect
                        above - no index route here. "overview"/"dashboard"
                        keep working as v2 redirects too, for old bookmarks. */}
                    <Route path="overview" element={<Navigate to="/v2" replace />} />
                    <Route path="dashboard" element={<Navigate to="/v2" replace />} />

                    {/* Free tier pages */}
                    <Route path="add-trade" element={<AddTrade />} />
                    <Route path="trades" element={<Trades />} />
                    <Route path="player-compare" element={<PlayerCompare />} />
                    <Route path="profile" element={<Profile />} />
                    <Route path="settings" element={<Settings />} />
                    <Route path="analytics" element={<ProfitGraph />} />
                    <Route path="pricecheck" element={<PriceCheck />} />
                    <Route path="squad" element={<SquadBuilder />} />
                    {/* v1 Billing (purple gradients, "Smart Buy AI"/"Sharpe
                        ratio" copy that doesn't describe what v2 actually
                        ships) is no longer part of the launch funnel - both
                        this and /pricing now redirect to the real v2
                        Billing page. The old <Billing/> import/component
                        file is left in place (nothing deletes it) in case
                        anything outside this app still deep-links here,
                        but no in-app path leads to it any more. */}
                    <Route path="billing" element={<Navigate to="/v2/billing" replace />} />
                    <Route path="pricing" element={<Navigate to="/v2/billing" replace />} />
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

                    {/* smart-buyer-ai and best-buys priced off live futbin
                        scrapes on every request (app/services/price_history.py
                        -> app.futbin_client), a separate and less reliable
                        pricing source than the FUT.GG pipeline the rest of
                        the app (incl. /v2/opportunities) now uses - showing
                        both to the same paying user risks two different
                        prices for the same card. Point both at the v2
                        equivalent until they're rebuilt on FUT.GG data. */}
                    <Route path="smart-buyer-ai" element={<Navigate to="/v2/opportunities" replace />} />
                    <Route path="best-buys" element={<Navigate to="/v2/opportunities" replace />} />

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
