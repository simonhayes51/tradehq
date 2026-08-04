import { useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import LogTradeModal from "../../components/LogTradeModal";
import Club from "./Club";

export default function ClubRoute() {
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const initial = useMemo(() => {
    const player = params.get("player");
    const buy = Number(params.get("buy") || 0);
    if (!player || !buy) return null;
    return {
      cardId: params.get("card_id") || null,
      entryPrice: buy,
      currentBin: buy,
      player: { name: player, displayName: player, version: params.get("version") || "Card" },
      recommendation: params.get("status") || "BUY",
      confidence: Number(params.get("confidence") || 0),
      expectedRoi: Number(params.get("roi") || 0),
    };
  }, [params]);
  const [logItem, setLogItem] = useState(initial);
  const close = () => { setLogItem(null); navigate(location.pathname, { replace: true }); };
  return <><Club />{logItem ? <LogTradeModal item={logItem} onClose={close} onSaved={() => setTimeout(() => window.location.reload(), 500)} /> : null}</>;
}
