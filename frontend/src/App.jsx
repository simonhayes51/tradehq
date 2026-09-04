// src/App.jsx

import { lazy, Suspense, useEffect } from "react";
import { HashRouter as Router, Routes, Route, Navigate, useLocation } from "react-router-dom";
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
import { legacyV2Target } from "./lib/routes";

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
const Login         = lazy(() => import("./pages/Login"));
const AccessDenied  = lazy(() => import("./pages/AccessDenied"));
const PlayerCompare = lazy(() => import("./pages/PlayerCompare"));

// NEW pages
const AdminUsers = lazy(() => import("./pages/AdminUsers"));
const Demo = lazy(() => import("./pages/Demo"));

// EAFC.Live Companion userscript launcher page - public on purpose, same
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
// mounted with a root wildcard rather than nested child routes here.
const V2App = lazy(() => import("./v2/App.v2"));

// ENHANCED features
const PortfolioOptimizer = lazy(() => import("./pages/PortfolioOptimizer"));
const ProfitCalculator = lazy(() => import("./pages/ProfitCalculator"));
const Leaderboard = lazy(() => import("./pages/Leaderboard"));
const ReferralProgram = lazy(() => import("./pages/ReferralProgram"));
const MarketMaker = lazy(() => import("./pages/MarketMaker"));

// Permanent redirect from every historical /v2/* URL to its current,
// unversioned equivalent. Deep links carrying the old prefix exist in
// Discord messages this product has already posted and in Stripe return
// URLs, so this stays indefinitely - it is a URL contract, not a
// migration step to clean up later.
function LegacyV2Redirect() {
  const { pathname, search, hash } = useLocation();
  return <Navigate to={`${legacyV2Target(pathname)}${search}${hash}`} replace />;
}

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
    <>
    {/* index.css's base theme is LIGHT (`body { background-color: rgb(255
        255 255) }`, and its `.dark :root` override is a selector that can
        never match, since :root IS the html element). The app shell below
        paints itself black, but a background on <body> propagates to the
        document canvas, so every pixel the shell's own box does not cover
        stayed white: overscroll/rubber-banding at the top and bottom of a
        long page, and the gutters beside a max-width container. On the
        Player Page - the longest scroll in the app - that read as white
        leaking outside the dark app container. Set once, at the document
        level, rather than patching each page's min-height. */}
    <DarkDocumentCanvas />
    <AuthProvider>
      <EntitlementsProvider>
        <Router>
          <RoutedErrorBoundary>
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
                  {/* EAFC.Live Companion launcher: "install the userscript
                      once, then launch the Web App normally" - see
                      pages/Companion.jsx. Public, no app shell. */}
                  <Route path="/companion" element={<Companion />} />

                  {/* Internal render-only route for card PNG generation -
                      see PlayerCardExport.jsx's header comment. */}
                  <Route path="/internal/render/player-card/:cardId" element={<PlayerCardExport />} />
                  <Route path="/internal/render/trade-share/:tradeId" element={<TradeShareExport />} />
                  <Route path="/internal/render/trade-summary/:period" element={<TradeSummaryExport />} />

                  {/* "" was internal versioning leaking into every URL
                      a customer sees, shares or bookmarks - #/players/123
                      tells a trader nothing except that there is presumably
                      a v1 somewhere. The app it named IS the product now,
                      so the version is gone from the address: the current
                      app is mounted at the root (see the catch-all at the
                      bottom of this list, ranked below every explicit
                      route so it only takes what nothing else claims),
                      and every /v2/* URL permanently redirects to its
                      clean equivalent.

                      That redirect is not optional cleanup: /#/... deep
                      links are already out in the world, in Discord
                      messages this backend posts (discord_trading_feed,
                      discord_daily_proof) and in Stripe return URLs. They
                      have to keep working indefinitely.

                      The current app still handles its own per-panel
                      gating via useEntitlements/PremiumGate rather than a
                      full-page PrivateRoute redirect, so it sits outside
                      the protected shell below like /demo does - the
                      all-or-nothing login redirect must never fire for a
                      first-time visitor landing on "/". The v1 tools that
                      still run on live data and aren't duplicated (trade
                      logging, Profit Calculator, bulk trade entry, Squad
                      Builder, Leaderboard, Referrals, Portfolio
                      Optimizer) keep their existing URLs below and are
                      linked individually from the Account page. The ones
                      backed by dead or divergent data are retired - see
                      the RETIRED v1 TOOLS block further down. */}
                  <Route path="/v2/*" element={<LegacyV2Redirect />} />

                  {/* v1 names for pages the current app replaced. Kept as
                      redirects for old bookmarks. "/watchlist" and
                      "/trade-finder" no longer need one - those ARE the
                      current URLs now. */}
                  <Route path="/player-search" element={<Navigate to="/players" replace />} />
                  {/* Signup sends new members here. It lives inside the
                      app's own route table (see App.v2.jsx) - this entry
                      exists only so the path is documented alongside the
                      other top-level destinations. */}

                  {/* Protected shell.
                      Deliberately PATHLESS (no `path` prop). It used to be
                      `path="/"`, which meant the layout route itself
                      matched the bare "/" and rendered the old app shell
                      around an empty <Outlet/> - previously masked by an
                      explicit "/" redirect defined above it, which no
                      longer exists now that "/" is the app's own home.
                      A pathless layout route contributes no path segment,
                      so its children keep their absolute URLs and nothing
                      claims "/" but the app itself. */}
                  <Route
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
                    <Route path="overview" element={<Navigate to="/" replace />} />
                    <Route path="dashboard" element={<Navigate to="/" replace />} />

                    {/* Free tier pages */}
                    <Route path="add-trade" element={<AddTrade />} />
                    <Route path="trades" element={<Trades />} />
                    <Route path="player-compare" element={<PlayerCompare />} />
                    <Route path="profile" element={<Profile />} />
                    <Route path="settings" element={<Settings />} />
                    <Route path="analytics" element={<ProfitGraph />} />
                    <Route path="pricecheck" element={<PriceCheck />} />
                    <Route path="squad" element={<SquadBuilder />} />
                    {/* "billing" is deliberately NOT listed here any more.
                        /billing is now the current Billing page itself
                        (served by the catch-all below); a v1 child route
                        claiming the same path would out-rank it and
                        redirect the page to itself. Only the old alias
                        still needs a redirect. */}
                    <Route path="pricing" element={<Navigate to="/billing" replace />} />
                    <Route path="profit-calculator" element={<ProfitCalculator />} />
                    <Route path="leaderboard" element={<Leaderboard />} />
                    <Route path="referrals" element={<ReferralProgram />} />

                    {/* Admin (server enforces require_admin; page hides itself
                        for non-admins as UX) */}
                    <Route path="admin/users" element={<AdminUsers />} />

                    {/* RETIRED v1 TOOLS.
                        smart-buyer-ai and best-buys were already
                        redirected here for one reason: they price off
                        live FUTBIN scrapes on every request
                        (app/services/price_history.py -> app.futbin_client),
                        a separate and less reliable source than the
                        FUT.GG pipeline every current screen uses, so the
                        same paying user could see two different prices
                        for one card.

                        That reasoning applies unchanged to the rest of
                        this group, which the audit traced to the same
                        dead or divergent sources, so they now redirect
                        too rather than sitting in the paid product
                        showing numbers nothing refreshes:
                          smart-buy       -> /api/smart-buy, FUTBIN scrape pricing
                          trending        -> FUTBIN + fut_players
                          undervalued     -> fair_value_mv, which does not
                                             exist in a FUT.GG-only
                                             database at all (migration 011
                                             requires FUTBIN-only columns)
                          market-sentiment-> sales_history, the frozen
                                             legacy FUTBIN table, and a
                                             second market read competing
                                             with Home's Market Pulse
                          trade-copilot   -> the v1 keyword matcher,
                                             superseded by /chat  

                        Each goes to the v2 screen that answers the same
                        question, so an old bookmark lands somewhere
                        useful instead of on a broken page or a 404. The
                        page components themselves are deleted (git
                        history keeps them) - leaving unrouted JSX in the
                        tree is how a codebase accumulates screens nobody
                        can reach and nobody dares remove. */}
                    <Route path="smart-buy" element={<Navigate to="/opportunities" replace />} />
                    <Route path="smart-buyer-ai" element={<Navigate to="/opportunities" replace />} />
                    <Route path="best-buys" element={<Navigate to="/opportunities" replace />} />
                    <Route path="trending" element={<Navigate to="/players" replace />} />
                    <Route path="undervalued" element={<Navigate to="/opportunities" replace />} />
                    <Route path="market-sentiment" element={<Navigate to="/" replace />} />
                    <Route path="trade-copilot" element={<Navigate to="/chat" replace />} />

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
                      path="market-maker"
                      element={
                        <PremiumRoute feature="market_maker" featureName="Market Maker Mode">
                          <MarketMaker />
                        </PremiumRoute>
                      }
                    />
                  </Route>

                  {/* The current app, mounted last and at the lowest
                      possible route rank so every explicit route above -
                      /login, /demo, the v1 tools, the internal render
                      routes - still wins. It supplies its own not-found
                      page for anything none of them claim, inside the app
                      shell, which is a better landing than the bare v1
                      404 page this used to be (now deleted). */}
                  <Route path="*" element={<V2App />} />
                </Routes>
              </Suspense>
            </div>
          </RoutedErrorBoundary>
        </Router>
      </EntitlementsProvider>
    </AuthProvider>
    </>
  );
}

// Paints the document canvas (html + body) dark for the app host, and
// puts it back on unmount so nothing else that renders in this build
// inherits it. See the comment at the call site.
function DarkDocumentCanvas() {
  useEffect(() => {
    const targets = [document.documentElement, document.body];
    const previous = targets.map((el) => el.style.backgroundColor);
    targets.forEach((el) => { el.style.backgroundColor = "#000000"; });
    return () => targets.forEach((el, i) => { el.style.backgroundColor = previous[i]; });
  }, []);
  return null;
}

// ErrorBoundary sits INSIDE the Router so it can see navigation. It used
// to sit outside: once any page threw, `hasError` stayed true for the
// life of the mount, so every subsequent route - including the back
// button - rendered "Something went wrong" until the user manually
// reloaded. One broken page took down the whole session.
//
// main.jsx keeps its own outer boundary for failures during the very
// first render (a provider throwing before any router exists); this one
// handles everything after that.
function RoutedErrorBoundary({ children }) {
  const location = useLocation();
  return <ErrorBoundary resetKey={location.pathname}>{children}</ErrorBoundary>;
}

export default App;
