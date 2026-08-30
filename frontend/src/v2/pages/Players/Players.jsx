import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, Lock, Search, Sparkles } from "lucide-react";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import MarketFreshness from "../../components/MarketFreshness";
import { useFutggPlayers } from "../../hooks/useFutggMarket";
import { versionLabel } from "../../lib/format";
import { EVENTS, trackOnce } from "../../../lib/analytics";
import "../../styles/v2-destinations.css";

const EXAMPLES = ["Mbappé", "Bellingham", "Haaland", "Putellas"];

export default function Players() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get("q") || "");

  // Single source now: GET /api/v2/players (FUT.GG-backed, see
  // app/routers/v2/futgg_market.py). This page used to also fetch
  // /api/search-players in parallel and render both result sets - that
  // legacy endpoint queried the FUTBIN-only fut_players table, which a
  // FUT.GG-only deployment never populates, so it always came back
  // empty/wrong until it was migrated onto the same FUT.GG data source
  // this hook already uses. Once both endpoints read the same data,
  // running both just rendered every matching card twice (once per
  // endpoint), with nothing but an unlabelled "FUT.GG" pill on one copy
  // to tell them apart.
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const trimmed = query.trim();
    const timer = setTimeout(() => setDebouncedSearch(trimmed), 250);
    return () => clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    setParams(debouncedSearch ? { q: debouncedSearch } : {}, { replace: true });
  }, [debouncedSearch, setParams]);

  const futggQuery = useFutggPlayers({ search: debouncedSearch, page_size: 20 }, { enabled: debouncedSearch.length >= 2 });
  const players = useMemo(() => {
    return Array.isArray(futggQuery.data?.items) ? futggQuery.data.items
      : Array.isArray(futggQuery.data?.results) ? futggQuery.data.results
      : Array.isArray(futggQuery.data) ? futggQuery.data
      : [];
  }, [futggQuery.data]);

  const loading = futggQuery.isLoading;
  const error = futggQuery.isError ? "Player search is unavailable right now." : "";
  // The server's own answer. Search stays public - it is discovery, and a
  // shared link has to be worth opening - but a signed-out visitor gets
  // identity and an approximate price rather than the live market data.
  const needsAccount = futggQuery.data?.requires_account === true;

  return (
    <main className="v2-destination">
      <PageHead eyebrow="PLAYER DATABASE" title="Find any card" copy="Search any player by name, then open the card for live prices and what it's actually worth." />
      <label className="v2-search-box">
        <Search size={20} />
        <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search a player…" />
      </label>
      {loading ? <EmptyState text="Searching cards…" compact /> : error ? <EmptyState text={error} error compact /> : query.trim().length < 2 ? (
        <section className="v2-player-start">
          <div className="v2-player-start-copy"><span><Sparkles size={15}/> QUICK START</span><h2>Search the full card database</h2><p>Enter two or more letters. Results are grouped as cards, so you can compare different versions of the same player.</p></div>
          <div className="v2-search-examples"><small>POPULAR SEARCHES</small>{EXAMPLES.map((name) => <button key={name} onClick={() => setQuery(name)}>{name}<ArrowRight size={14}/></button>)}</div>
        </section>
      ) : players.length ? (
        <section className="v2-player-grid">
          {players.map((player) => {
            const cardId = player.card_id ?? player.source_card_id;
            return (
              <Link className="v2-player-result" key={cardId} to={`/players/${cardId}`} state={{ from: "players", player }}>
                <img src={player.composited_image_url || player.image_url || "/img/card-placeholder.png"} alt="" />
                <div className="v2-player-result-copy"><strong>{player.name}</strong><div className="v2-meta-pills"><span>{player.rating ?? "—"} OVR</span><span>{player.position || "—"}</span><span>{versionLabel(player.rarity)}</span></div><MarketFreshness priceAgeSeconds={player.price_age_seconds} capturedAt={player.current_bin_captured_at} compact /></div>
                {needsAccount
                  ? <div className="v2-result-price approx"><small>AROUND</small><CoinValue value={player.current_bin_approx} /></div>
                  : <div className="v2-result-price"><small>LIVE PRICE</small><CoinValue value={player.current_bin} /></div>}
              </Link>
            );
          })}
        </section>
      ) : <EmptyState text={`No cards found for “${query.trim()}”.`} />}
      {needsAccount && players.length ? <SearchAccountNote /> : null}
    </main>
  );
}

// Sits under the results, where someone has just seen that the card
// exists and roughly what it costs, and now wants the number. That is the
// moment the account is worth explaining - not before the search.
function SearchAccountNote() {
  useEffect(() => {
    trackOnce(EVENTS.UPGRADE_PROMPT_VIEWED, { feature: "fair_value", surface: "players" }, "players");
  }, []);
  return (
    <section className="v2-search-account-note">
      <p>
        <Lock size={14} /> Prices shown are approximate. A free account shows the exact live
        price, what each card is actually worth, and whether it&apos;s worth buying.
      </p>
      <Link to="/signup?from=%2Fplayers">Create free account <ArrowRight size={15} /></Link>
      <small>Free permanently. No card required.</small>
    </section>
  );
}

export function PageHead({ eyebrow, title, copy, action }) {
  return <header className="v2-page-head"><div><span>{eyebrow}</span><h1>{title}</h1><p>{copy}</p></div>{action}</header>;
}
