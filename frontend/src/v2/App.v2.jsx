import { Navigate, Routes, Route, Link } from "react-router-dom";
import "./styles/tokens.css";
import "./styles/mobile-polish.css";
import Nav from "./components/Nav";
import HomeDashboard from "./pages/HomeDashboard/HomeDashboard";
import PlayerPage from "./pages/PlayerPage/PlayerPage";
import AdminSbcImports from "./pages/AdminSbcImports/AdminSbcImports";
import Admin from "./pages/Admin/Admin";
import ClubRoute from "./pages/Club/ClubRoute";
import Players from "./pages/Players/Players";
import Watchlist from "./pages/Watchlist/Watchlist";
import TradeFinder from "./pages/TradeFinder/TradeFinder";
import Opportunities from "./pages/Opportunities/Opportunities";
import Account from "./pages/Account/Account";
import Chat from "./pages/Chat/Chat";
import TrackRecord from "./pages/TrackRecord/TrackRecord";
import Billing from "./pages/Billing/Billing";
import DiscordFeed from "./pages/DiscordFeed/DiscordFeed";

export default function V2App() {
  return (
    <Routes>
      <Route index element={<ShellLayout><HomeDashboard /></ShellLayout>} />
      {/* FUT.GG migration: was a redirect to /v2/trade-finder (no
          dedicated opportunities page existed); now a real page wired to
          GET /api/v2/opportunities. */}
      <Route path="opportunities" element={<ShellLayout><Opportunities /></ShellLayout>} />
      {/* Investments is retired from the product surface.
          The Investment Engine calculates in SHADOW MODE in every
          deployment: INVESTMENT_ENGINE_PUBLISH_SIGNALS defaults false and
          FC27_LAUNCH.md's own recommended production configuration keeps
          it false, and GET /api/v2/investments/signals returns only
          non-shadow rows to non-admins. So this page could never show a
          signal to a customer - a permanently empty item in the primary
          navigation, promising multi-day investment calls that the
          deployment is configured never to publish.
          The backend engine, its migrations (058-062) and its ~10 test
          modules are deliberately left intact and still running: it is
          real, tested work that is genuinely mid-validation. What is
          removed is the claim to users that it is a shipped feature.
          Old links land on Opportunities rather than a dead route. */}
      <Route path="investments" element={<Navigate to="/v2/opportunities" replace />} />
      {/* The full performance record. Was the largest section of the
          homepage; it is reference material, so it is a page. */}
      <Route path="track-record" element={<ShellLayout><TrackRecord /></ShellLayout>} />
      <Route path="trade-finder" element={<ShellLayout><TradeFinder /></ShellLayout>} />
      <Route path="players" element={<ShellLayout><Players /></ShellLayout>} />
      <Route path="players/:cardId" element={<ShellLayout><PlayerPage /></ShellLayout>} />
      <Route path="market" element={<Navigate to="/v2/trade-finder" replace />} />
      <Route path="chat" element={<ShellLayout><Chat /></ShellLayout>} />
      <Route path="watchlist" element={<ShellLayout><Watchlist /></ShellLayout>} />
      <Route path="portfolio" element={<ShellLayout><ClubRoute /></ShellLayout>} />
      <Route path="account" element={<ShellLayout><Account /></ShellLayout>} />
      {/* The one Billing users are ever sent to now - see Billing.jsx's
          header comment. The old v1 /billing (purple styling, "Smart Buy
          AI"/"Sharpe ratio" copy that doesn't describe v2) is left in
          place at its own URL for now (nothing deletes it), but every
          in-app link (PremiumGate, LockedPreviewCard, this public site)
          points here instead. */}
      <Route path="billing" element={<ShellLayout><Billing /></ShellLayout>} />
      {/* Self-service web settings page for a Discord server's paid
          Server Feed - a convenience alongside the /feed Discord command
          group, not a replacement for it (see discord_feed_web.py). */}
      <Route path="discord-feed" element={<ShellLayout><DiscordFeed /></ShellLayout>} />
      <Route path="club" element={<ShellLayout><ClubRoute /></ShellLayout>} />
      <Route path="admin" element={<ShellLayout><Admin /></ShellLayout>} />
      <Route path="admin/sbc-imports" element={<ShellLayout><AdminSbcImports /></ShellLayout>} />
      {/* No catch-all previously existed - an unmatched /v2/* path (a
          stale bookmark, a typo, a removed route) rendered nothing at
          all inside these <Routes>, i.e. a blank page with the sidebar
          still up. */}
      <Route path="*" element={<ShellLayout><NotFound /></ShellLayout>} />
    </Routes>
  );
}

function NotFound() {
  return (
    <main className="v2-destination">
      <div className="v2-page-state">
        <p>We couldn't find that page.</p>
        <Link to="/v2" className="v2-icon-action">Back to Home</Link>
      </div>
    </main>
  );
}

function ShellLayout({ children }) {
  return (
    <div className="v2-root">
      <Nav />
      <div className="v2-shell-content">{children}</div>
    </div>
  );
}
