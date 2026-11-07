import Workspace from "./layout/Workspace";
import TradingHome from "./pages/TradingHome";

// Isolated design preview. Does not replace production routes until
// every V4 page and regression gate is complete.
export default function V4Preview(){return <Workspace><TradingHome/></Workspace>;}
