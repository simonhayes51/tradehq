import { Navigate, Routes, Route, Link } from "react-router-dom";
import "./styles/tokens.css";
import "./styles/mobile-polish.css";
import Nav from "./components/Nav";
import LimitReachedPrompt from "./components/LimitReachedPrompt";
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
import Onboarding from "./pages/Onboarding/Onboarding";
import Billing from "./pages/Billing/Billing";
import DiscordFeed from "./pages/DiscordFeed/DiscordFeed";
// Keep the screenshot-pass overrides last. Several destination modules
// import older shared CSS of their own; these narrowly-scoped mobile rules
// intentionally win after those legacy imports have been collected.
import "./styles/mobile-screenshot-fixes.css";

export default function V2App() {
  return (
    <Routes>
      <Route index element={<ShellLayout><HomeDashboard /></ShellLayout>} />
      <Route path="opportunities" element={<ShellLayout><Opportunities /></ShellLayout>} />
      <Route path="investments" element={<Navigate to="/opportunities" replace />} />
      <Route path="welcome" element={<Onboarding />} />
      <Route path="track-record" element={<ShellLayout><TrackRecord /></ShellLayout>} />
      <Route path="trade-finder" element={<ShellLayout><TradeFinder /></ShellLayout>} />
      <Route path="players" element={<ShellLayout><Players /></ShellLayout>} />
      <Route path="players/:cardId" element={<ShellLayout><PlayerPage /></ShellLayout>} />
      <Route path="market" element={<Navigate to="/trade-finder" replace />} />
      <Route path="chat" element={<ShellLayout><Chat /></ShellLayout>} />
      <Route path="watchlist" element={<ShellLayout><Watchlist /></ShellLayout>} />
      <Route path="portfolio" element={<ShellLayout><ClubRoute /></ShellLayout>} />
      <Route path="account" element={<ShellLayout><Account /></ShellLayout>} />
      <Route path="billing" element={<ShellLayout><Billing /></ShellLayout>} />
      <Route path="discord-feed" element={<ShellLayout><DiscordFeed /></ShellLayout>} />
      <Route path="club" element={<ShellLayout><ClubRoute /></ShellLayout>} />
      <Route path="admin" element={<ShellLayout><Admin /></ShellLayout>} />
      <Route path="admin/sbc-imports" element={<ShellLayout><AdminSbcImports /></ShellLayout>} />
      <Route path="*" element={<ShellLayout><NotFound /></ShellLayout>} />
    </Routes>
  );
}

function NotFound() {
  return (
    <main className="v2-destination">
      <div className="v2-page-state">
        <p>We couldn't find that page.</p>
        <Link to="/" className="v2-icon-action">Back to Home</Link>
      </div>
    </main>
  );
}

function ShellLayout({ children }) {
  return (
    <div className="v2-root">
      <Nav />
      <div className="v2-shell-content">{children}</div>
      <LimitReachedPrompt />
    </div>
  );
}
