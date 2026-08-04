import { Navigate, Routes, Route } from "react-router-dom";
import "./styles/tokens.css";
import "./styles/mobile-polish.css";
import Nav from "./components/Nav";
import HealthCheck from "./pages/HealthCheck";
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

export default function V2App() {
  return (
    <Routes>
      <Route index element={<ShellLayout><HomeDashboard /></ShellLayout>} />
      {/* FUT.GG migration: was a redirect to /v2/trade-finder (no
          dedicated opportunities page existed); now a real page wired to
          GET /api/v2/opportunities. */}
      <Route path="opportunities" element={<ShellLayout><Opportunities /></ShellLayout>} />
      <Route path="trade-finder" element={<ShellLayout><TradeFinder /></ShellLayout>} />
      <Route path="players" element={<ShellLayout><Players /></ShellLayout>} />
      <Route path="health" element={<ShellLayout><HealthCheck /></ShellLayout>} />
      <Route path="players/:cardId" element={<ShellLayout><PlayerPage /></ShellLayout>} />
      <Route path="market" element={<Navigate to="/v2/trade-finder" replace />} />
      <Route path="chat" element={<ShellLayout><Chat /></ShellLayout>} />
      <Route path="watchlist" element={<ShellLayout><Watchlist /></ShellLayout>} />
      <Route path="portfolio" element={<ShellLayout><ClubRoute /></ShellLayout>} />
      <Route path="account" element={<ShellLayout><Account /></ShellLayout>} />
      <Route path="club" element={<ShellLayout><ClubRoute /></ShellLayout>} />
      <Route path="admin" element={<ShellLayout><Admin /></ShellLayout>} />
      <Route path="admin/sbc-imports" element={<ShellLayout><AdminSbcImports /></ShellLayout>} />
    </Routes>
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
