import { Routes, Route } from "react-router-dom";
import Workspace from "./layout/Workspace";
import TradingHome from "./pages/TradingHome";
import MarketResearch from "./pages/MarketResearch";

// Isolated preview: production routes are still served by V2/V3.
// Full V4 cutover is forbidden until every route and test is complete.
export default function V4Preview(){return <Workspace><Routes>
  <Route index element={<TradingHome/>}/>
  <Route path="opportunities" element={<MarketResearch type="opportunities"/>}/>
  <Route path="trade-finder" element={<MarketResearch type="finder"/>}/>
</Routes></Workspace>;}
