import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ArrowDownRight, ArrowUpRight, RefreshCw } from "lucide-react";
import CoinValue from "../../components/CoinValue";
import { PageHead } from "../Players/Players";
import "../../styles/v2-destinations.css";

const API_BASE = import.meta.env.VITE_API_URL || "";

export default function Market() {
  const [type, setType] = useState("risers");
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      setLoading(true);
      setError("");
      try {
        const response = await fetch(`${API_BASE}/api/trending?type=${type}&tf=24`, { credentials: "include", signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        setItems(payload.items || []);
      } catch (requestError) {
        if (requestError.name !== "AbortError") setError("Live market movers could not be loaded.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [type, refreshKey]);

  return (
    <main className="v2-destination">
      <PageHead eyebrow="LIVE MARKET" title="Market movers" copy="Real cards ranked by their completed-sales movement over the last 24 hours." action={<button className="v2-icon-action" onClick={() => setRefreshKey((value) => value + 1)}><RefreshCw size={17} /> Refresh</button>} />
      <div className="v2-segmented"><button className={type === "risers" ? "active" : ""} onClick={() => setType("risers")}>Risers</button><button className={type === "fallers" ? "active" : ""} onClick={() => setType("fallers")}>Fallers</button></div>
      {loading ? <MarketState text="Loading live market…" /> : error ? <MarketState text={error} /> : items.length ? (
        <>
        {items.some((item) => Math.abs(Number(item.percent ?? item.percent_24h ?? 0)) > 250) ? <div className="v2-data-note"><AlertTriangle size={16}/><span>Extreme moves are marked for review instead of being presented as reliable trends.</span></div> : null}
        <section className="v2-market-list">
          <div className="v2-market-head"><span>#</span><span>Card</span><span>Player</span><span>Current price</span><span>24h move</span></div>
          {items.slice(0, 20).map((raw, index) => {
            const cardId = raw.card_id || raw.pid || raw.id;
            const change = Number(raw.percent ?? raw.percent_24h ?? 0);
            const up = change >= 0;
            const verified = Number.isFinite(change) && Math.abs(change) <= 250;
            return (
              <Link to={`/v2/players/${cardId}`} className={`v2-market-row ${verified ? "" : "unverified"}`} key={cardId || index}>
                <span className="v2-market-rank">{String(index + 1).padStart(2, "0")}</span>
                <img src={raw.generated_card_url || raw.image_url || raw.image || "/img/card-placeholder.png"} alt="" />
                <div><strong>{raw.nickname || raw.card_name || raw.name || "Unknown player"}</strong><small>{raw.rating || "—"} {raw.position || ""} · {raw.version || "Card"}</small></div>
                <CoinValue value={raw.price_console ?? raw.price_ps ?? raw.price} />
                {verified ? <b className={up ? "positive" : "negative"}>{up ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />}{up ? "+" : ""}{change.toFixed(1)}%</b> : <b className="needs-review"><AlertTriangle size={14}/> Review</b>}
              </Link>
            );
          })}
        </section>
        </>
      ) : <MarketState text="No market movers are available yet." />}
    </main>
  );
}
function MarketState({ text }) { return <div className="v2-page-state"><p>{text}</p></div>; }
