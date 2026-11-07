import { Routes, Route } from "react-router-dom";
import Workspace from "./layout/Workspace";
import TradingHome from "./pages/TradingHome";
import MarketResearch from "./pages/MarketResearch";
import PlayerSearch from "./pages/PlayerSearch";
import Watchlist from "./pages/Watchlist";
import PlayerDetail from "./pages/PlayerDetail";
import Account from "./pages/Account";
import Portfolio from "./pages/Portfolio";
import MarketInsights from "./pages/MarketInsights";

// Isolated preview: production routes are still served by V2/V3.
// Full V4 cutover is forbidden until every route and test is complete.
export default function V4Preview(){return <Workspace><Routes>
  <Route index element={<TradingHome/>}/>
  <Route path="opportunities" element={<MarketResearch type="opportunities"/>}/>
  <Route path="trade-finder" element={<MarketResearch type="finder"/>}/>
  <Route path="players" element={<PlayerSearch/>}/>
  <Route path="players/:cardId" element={<PlayerDetail/>}/>
  <Route path="watchlist" element={<Watchlist/>}/>
  <Route path="account" element={<Account/>}/>
  <Route path="insights" element={<MarketInsights/>}/>
  <Route path="portfolio" element={<Portfolio/>}/>
</Routes></Workspace>;}
