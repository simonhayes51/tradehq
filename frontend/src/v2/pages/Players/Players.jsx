import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, Search, Sparkles, Users } from "lucide-react";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import MarketFreshness from "../../components/MarketFreshness";
import { useFutggPlayers } from "../../hooks/useFutggMarket";
import "../../styles/v2-destinations.css";

const API_BASE = import.meta.env.VITE_API_URL || "";
const SEARCH_CACHE = new Map();
const EXAMPLES = ["Mbappé", "Bellingham", "Haaland", "Putellas"];

export default function Players() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get("q") || "");
  const [players, setPlayers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // FUT.GG migration: alongside the legacy /api/search-players lookup
  // below, also search the new FUT.GG-backed GET /api/v2/players
  // contract (backend built in a sibling repo, in parallel), so results
  // include live FUT.GG cards without replacing the existing search.
  // Debounced separately (same 250ms) rather than sharing the legacy
  // effect's timer, to keep this additive and not risk the existing
  // fetch's cache/abort/error-handling logic.
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const trimmed = query.trim();
    const timer = setTimeout(() => setDebouncedSearch(trimmed), 250);
    return () => clearTimeout(timer);
  }, [query]);
  const futggQuery = useFutggPlayers({ search: debouncedSearch, page_size: 20 }, { enabled: debouncedSearch.length >= 2 });
  const futggPlayers = useMemo(() => {
    const list = Array.isArray(futggQuery.data?.items) ? futggQuery.data.items
      : Array.isArray(futggQuery.data?.results) ? futggQuery.data.results
      : Array.isArray(futggQuery.data) ? futggQuery.data
      : [];
    return list.map((p) => ({ ...p, __source: "futgg" }));
  }, [futggQuery.data]);

  useEffect(() => {
    const search = query.trim();
    if (search.length < 2) {
      setPlayers([]);
      setError("");
      return undefined;
    }
    const controller = new AbortController();
    let timedOut = false;
    let requestTimeout;
    const timer = setTimeout(async () => {
      setLoading(true);
      setError("");
      try {
        if (SEARCH_CACHE.has(search.toLowerCase())) {
          setPlayers(SEARCH_CACHE.get(search.toLowerCase()));
          setParams({ q: search }, { replace: true });
          setLoading(false);
          return;
        }
        const normalised = search.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
        requestTimeout = window.setTimeout(() => {
          timedOut = true;
          controller.abort();
        }, 8000);
        const response = await fetch(`${API_BASE}/api/search-players?q=${encodeURIComponent(search)}&q_norm=${encodeURIComponent(normalised)}`, {
          credentials: "include",
          signal: controller.signal,
        });
        window.clearTimeout(requestTimeout);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        const results = payload.players || [];
        SEARCH_CACHE.set(search.toLowerCase(), results);
        setPlayers(results);
        setParams(search ? { q: search } : {}, { replace: true });
      } catch (requestError) {
        if (timedOut) setError("Search took too long. Please try again.");
        else if (requestError.name !== "AbortError") setError("Player search is unavailable right now.");
      } finally {
        window.clearTimeout(requestTimeout);
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 250);
    return () => {
      clearTimeout(timer);
      window.clearTimeout(requestTimeout);
      controller.abort();
    };
  }, [query, setParams]);

  return (
    <main className="v2-destination">
      <PageHead eyebrow="PLAYER DATABASE" title="Find any card" copy="Search by player name, then open the live v2 market analysis." />
      <label className="v2-search-box">
        <Search size={20} />
        <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search a player…" />
      </label>
      {loading ? <EmptyState text="Searching cards…" compact /> : error ? <EmptyState text={error} error compact /> : query.trim().length < 2 ? (
        <section className="v2-player-start">
          <div className="v2-player-start-copy"><span><Sparkles size={15}/> QUICK START</span><h2>Search the full card database</h2><p>Enter two or more letters. Results are grouped as cards, so you can compare different versions of the same player.</p></div>
          <div className="v2-search-examples"><small>POPULAR SEARCHES</small>{EXAMPLES.map((name) => <button key={name} onClick={() => setQuery(name)}>{name}<ArrowRight size={14}/></button>)}</div>
        </section>
      ) : players.length || futggPlayers.length ? (
        <section className="v2-player-grid">
          {players.map((player) => {
            const cardId = player.card_id || player.id;
            const price = player.price_num ?? player.price ?? player.current_price;
            return (
              <Link className="v2-player-result" key={`legacy-${cardId}`} to={`/v2/players/${cardId}`} state={{ from: "players", player }}>
                <img src={player.generated_card_url || player.image_url || "/img/card-placeholder.png"} alt="" />
                <div className="v2-player-result-copy"><strong>{player.nickname || player.card_name || player.name}</strong><div className="v2-meta-pills"><span>{player.rating || "—"} OVR</span><span>{player.position || "—"}</span><span>{player.version || "Card"}</span></div></div>
                <div className="v2-result-price"><small>LIVE PRICE</small><CoinValue value={price} /></div>
              </Link>
            );
          })}
          {futggPlayers.map((player) => {
            const cardId = player.card_id ?? player.source_card_id;
            return (
              <Link className="v2-player-result" key={`futgg-${cardId}`} to={`/v2/players/${cardId}`} state={{ from: "players", player }}>
                <img src={player.image_url || "/img/card-placeholder.png"} alt="" />
                <div className="v2-player-result-copy"><strong>{player.name}</strong><div className="v2-meta-pills"><span>{player.rating ?? "—"} OVR</span><span>{player.position || "—"}</span><span>{player.rarity || "Card"}</span><span>FUT.GG</span></div><MarketFreshness priceAgeSeconds={player.price_age_seconds} capturedAt={player.current_bin_captured_at} compact /></div>
                <div className="v2-result-price"><small>LIVE PRICE</small><CoinValue value={player.current_bin} /></div>
              </Link>
            );
          })}
        </section>
      ) : <EmptyState text={`No cards found for “${query.trim()}”.`} />}
    </main>
  );
}

export function PageHead({ eyebrow, title, copy, action }) {
  return <header className="v2-page-head"><div><span>{eyebrow}</span><h1>{title}</h1><p>{copy}</p></div>{action}</header>;
}
