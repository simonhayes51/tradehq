// src/pages/PlayerCompare.jsx
import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import PriceTrendChart from "../components/PriceTrendChart.jsx";
import PlayerCardImage from "../components/PlayerCardImage.jsx";

const API_BASE = import.meta.env.VITE_API_URL || "";
const PLACEHOLDER = "/img/card-placeholder.png";
const buildProxy = (url) => `${API_BASE}/img?url=${encodeURIComponent(url)}`;

// ---- shared helpers (same endpoints used in PlayerSearch) ----
async function searchPlayers(query) {
  if (!query.trim()) return [];
  try {
    const r = await fetch(`${API_BASE}/api/search-players?q=${encodeURIComponent(query)}`, {
      credentials: "include",
    });
    if (!r.ok) return [];
    const data = await r.json();
    return data.players || [];
  } catch {
    return [];
  }
}

async function fetchPlayerDefinition(cardId) {
  try {
    const r = await fetch(`${API_BASE}/api/fut-player-definition/${cardId}`, {
      credentials: "include",
    });
    if (!r.ok) return null;
    const data = await r.json();
    return data?.data || null;
  } catch {
    return null;
  }
}

async function fetchPlayerPrice(cardId) {
  try {
    const r = await fetch(`${API_BASE}/api/fut-player-price/${cardId}`, {
      credentials: "include",
    });
    if (!r.ok) return null;
    const data = await r.json();
    return {
      current: data?.data?.currentPrice?.price ?? null,
      isExtinct: data?.data?.currentPrice?.isExtinct ?? false,
      updatedAt: data?.data?.currentPrice?.priceUpdatedAt ?? null,
      auctions: data?.data?.completedAuctions ?? [],
    };
  } catch {
    return null;
  }
}

async function addToWatchlist({ player_name, card_id, version, platform, notes }) {
  const r = await fetch(`${API_BASE}/api/watchlist`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ player_name, card_id, version, platform, notes }),
  });
  const payload = await r.json().catch(() => null);
  if (!r.ok) throw new Error(payload?.detail || "Failed to add to watchlist");
  return payload;
}

function number(v) {
  if (v == null) return "—";
  return Number(v).toLocaleString();
}

function priceRangeFromAuctions(auctions) {
  const values = (auctions || []).map((a) => a?.soldPrice).filter(Boolean);
  if (!values.length) return null;
  return { min: Math.min(...values), max: Math.max(...values) };
}

// our DB-backed definition endpoint returns full, ready-to-use image URLs
// (from futbin) for club/nation/league - no CDN prefix needed, but keep
// this cheap check in case a relative path ever shows up.
function resolveImage(imagePath) {
  if (!imagePath) return null;
  if (/^https?:\/\//i.test(imagePath)) return imagePath;
  return `https://game-assets.fut.gg/cdn-cgi/image/quality=100,format=auto,width=40/${imagePath}`;
}

// ---- tiny autocomplete (re-using your search endpoint) ----
function PlayerSearchBox({ label, onPick }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const t = setTimeout(async () => {
      if (!query.trim()) {
        setResults([]);
        setOpen(false);
        return;
      }
      setLoading(true);
      const players = await searchPlayers(query);
      setResults(players);
      setLoading(false);
      setOpen(true);
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  return (
    <div className="relative">
      <div className="text-xs text-white/70 mb-1">{label}</div>
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => results.length && setOpen(true)}
        placeholder="Type player name…"
        className="w-full px-3 py-2 rounded-md bg-black/50 border border-white/15 text-white text-sm"
      />
      {open && results.length > 0 && (
        <div className="absolute z-10 w-full mt-2 bg-[#0b1220]/90 backdrop-blur-md border border-white/20 rounded-lg shadow-lg max-h-96 overflow-y-auto">
          {results.map((p) => (
            <button
              key={`${p.card_id}-${p.rating}`}
              className="w-full px-3 py-2 text-left hover:bg-white/10 border-b border-white/10 last:border-b-0 focus:outline-none"
              onClick={() => {
                onPick(p);
                setQuery("");
                setOpen(false);
              }}
            >
              <div className="flex items-center gap-3">
                <img
                  src={p.image_url || PLACEHOLDER}
                  alt={`${p.name} (${p.rating})`}
                  loading="lazy"
                  className="w-10 h-14 object-contain"
                  onError={(e) => {
                    const img = e.currentTarget;
                    if (!img.dataset.triedProxy && p.image_url) {
                      img.dataset.triedProxy = "1";
                      img.src = buildProxy(p.image_url);
                    } else {
                      img.src = PLACEHOLDER;
                    }
                  }}
                  referrerPolicy="no-referrer"
                />
                <div className="font-medium text-white truncate">
                  {p.name} ({p.rating})
                  {p.version ? <span className="text-white/60"> · {p.version}</span> : null}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}
      {open && !loading && query && results.length === 0 && (
        <div className="absolute z-10 w-full mt-2 bg-[#0b1220]/90 border border-white/20 rounded-lg shadow-lg p-3 text-center text-white/80">
          No players found for “{query}”
        </div>
      )}
    </div>
  );
}

// ---- card block ----
function CardBlock({ cardId, platform }) {
  const [loading, setLoading] = useState(true);
  const [def, setDef] = useState(null);
  const [price, setPrice] = useState(null);
  const [notes, setNotes] = useState("");
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    let live = true;
    (async () => {
      setLoading(true);
      const [d, p] = await Promise.all([fetchPlayerDefinition(cardId), fetchPlayerPrice(cardId)]);
      if (!live) return;
      setDef(d);
      setPrice(p);
      setLoading(false);
    })();
    return () => {
      live = false;
    };
  }, [cardId]);

  const meta = useMemo(() => {
    const fullName =
      def?.commonName ||
      (def?.firstName && def?.lastName ? `${def.firstName} ${def.lastName}` : `Card ${cardId}`);
    // futbin's own short display name (e.g. "Vini Jr", not the full legal
    // name "Vinícius José de Oliveira Júnior") - prefer it over commonName,
    // which is often the full legal name as scraped from the listing page.
    const displayName = def?.cardName || fullName;
    const club = def?.club?.name || "—";
    const nation = def?.nation?.name || "—";
    const league = def?.league?.name || "—";
    const rating = def?.overall ?? "—";
    const version = def?.rarity?.name || "Base";
    const position =
      def?.positionName ||
      def?.shortPosition ||
      def?.mainPosition ||
      def?.preferredPosition1Name ||
      "—";
    const cardImage = def?.futggCardImagePath
      ? `https://game-assets.fut.gg/cdn-cgi/image/quality=90,format=auto,width=500/${def.futggCardImagePath}`
      : def?.cutoutImageUrl || PLACEHOLDER;

    return {
      fullName,
      displayName,
      club,
      nation,
      league,
      rating,
      version,
      position,
      cardImage,
      bgImage: def?.bgImageUrl || null,
      cutoutImage: def?.cutoutImageUrl || null,
      cutoutType: def?.cutoutType || null,
      nationImage: resolveImage(def?.nation?.imagePath),
      leagueImage: resolveImage(def?.league?.imagePath),
      clubImage: resolveImage(def?.club?.imagePath),
      stats: {
        pace: def?.facePace || 0,
        shooting: def?.faceShooting || 0,
        passing: def?.facePassing || 0,
        dribbling: def?.faceDribbling || 0,
        defending: def?.faceDefending || 0,
        physicality: def?.facePhysicality || 0,
      },
    };
  }, [def, cardId]);

  const range = useMemo(() => priceRangeFromAuctions(price?.auctions), [price]);
  const recentSales = (price?.auctions || []).slice(0, 8);

  return (
    <div className="bg-black/40 border border-white/10 rounded-2xl p-4">
      <div className="flex items-start gap-4">
        <PlayerCardImage
          enablePolling
          player={{
            card_id: cardId,
            card_bg_image: meta.bgImage,
            card_cutout_image: meta.cutoutImage,
            card_cutout_type: meta.cutoutType,
            image_url: meta.cardImage,
            rating: meta.rating,
            position: meta.position,
            card_name: meta.displayName,
            stats: meta.stats,
            nation_image: meta.nationImage,
            league_image: meta.leagueImage,
            club_image: meta.clubImage,
          }}
          versionLabel={meta.version}
          altText={meta.fullName}
          widthClass="w-36"
        />

        <div className="flex-1 min-w-0">
          <div className="text-xl font-semibold mb-1 truncate">
            {meta.displayName} <span className="text-white/60">{meta.rating}</span>
          </div>
          <div className="text-sm text-white/70 mb-3">
            {meta.position} · {meta.club} · {meta.league} · {meta.nation}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="bg-white/5 rounded-md p-3 border border-white/10">
              <div className="text-xs text-white/70">Current Price ({platform.toUpperCase()})</div>
              <div className="text-lg font-bold text-yellow-300 mt-1">
                {loading ? "…" : price?.isExtinct ? "Extinct" : number(price?.current)}
              </div>
            </div>
            <div className="bg-white/5 rounded-md p-3 border border-white/10">
              <div className="text-xs text-white/70">Range (recent sales)</div>
              <div className="text-sm font-medium mt-1">
                {range ? `${number(range.min)} – ${number(range.max)}` : "—"}
              </div>
            </div>
          </div>

          {/* Watchlist */}
          <div className="mt-3 flex items-center gap-2">
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Notes (optional)"
              className="w-56 px-2 py-1 rounded-md bg-black/50 border border-white/15 text-white text-sm"
            />
            <button
              disabled={adding}
              onClick={async () => {
                try {
                  setAdding(true);
                  await addToWatchlist({
                    player_name: meta.fullName,
                    card_id: Number(cardId),
                    version: meta.version,
                    platform,
                    notes: notes?.trim() || null,
                  });
                  alert("Added to watchlist ✓");
                  setNotes("");
                } catch (e) {
                  alert(e?.message || "Failed");
                } finally {
                  setAdding(false);
                }
              }}
              className="px-3 py-2 rounded-md bg-lime-400/90 hover:bg-lime-400 text-black font-semibold text-sm"
            >
              {adding ? "Adding…" : "+ Add to Watchlist"}
            </button>
          </div>
        </div>
      </div>

      {/* Recent sales */}
      <div className="mt-4">
        <div className="text-sm text-white/70 mb-2">Recent Sales</div>
        {recentSales.length ? (
          <div className="grid grid-cols-2 gap-2">
            {recentSales.map((a, i) => (
              <div
                key={i}
                className="flex items-center justify-between text-sm bg-white/10 border border-white/10 rounded px-2 py-1"
              >
                <span className="text-white/80">
                  {a?.soldDate ? new Date(a.soldDate).toLocaleString() : "—"}
                </span>
                <span className="font-medium text-yellow-300">
                  {a?.soldPrice ? number(a.soldPrice) : "—"}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-xs text-white/60">No recent sales.</div>
        )}
      </div>

      {/* Chart (today) */}
      <div className="mt-4">
        <PriceTrendChart
          playerId={Number(cardId)}
          platform={platform}
          initialTimeframe="today"
          height={220}
        />
      </div>
    </div>
  );
}

// ---- page ----
export default function PlayerCompare() {
  const [sp, setSp] = useSearchParams();
  const navigate = useNavigate();
  const [platform, setPlatform] = useState("ps");

  // read up to two ids from query string
  const idsParam = sp.get("ids") || "";
  const ids = idsParam.split(",").map((s) => s.trim()).filter(Boolean).slice(0, 2);

  // local UI state (ids + chosen players)
  const [idA, setIdA] = useState(ids[0] || "");
  const [idB, setIdB] = useState(ids[1] || "");
  const [chosenA, setChosenA] = useState(null);
  const [chosenB, setChosenB] = useState(null);

  // keep URL in sync when we press Compare
  const applyIds = () => {
    const next = [String(idA || "").trim(), String(idB || "").trim()]
      .filter(Boolean)
      .slice(0, 2)
      .join(",");
    if (next) {
      sp.set("ids", next);
    } else {
      sp.delete("ids");
    }
    navigate({ pathname: "/player-compare", search: sp.toString() });
  };

  // hydrate local state if URL changes externally
  useEffect(() => {
    const list = (sp.get("ids") || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, 2);
    setIdA(list[0] || "");
    setIdB(list[1] || "");
  }, [sp]);

  return (
    <div className="min-h-screen relative text-white">
      <div className="absolute inset-0 bg-gradient-to-br from-slate-900 via-slate-900 to-black" />
      <div className="relative z-10 p-6">
        <div className="container mx-auto max-w-7xl">
          <div className="mb-6 flex flex-wrap items-end gap-3">
            <h1 className="text-3xl font-bold">Player Compare</h1>
            <div className="ml-auto flex items-center gap-2">
              <label className="text-sm text-white/70">Platform</label>
              <select
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
                className="px-2 py-1 rounded-md bg-black/50 border border-white/15 text-white text-sm"
              >
                <option value="ps">PS</option>
                <option value="xbox">Xbox</option>
              </select>
            </div>
          </div>

          {/* Search by name + hidden ID inputs */}
          <div className="bg-white/5 border border-white/10 rounded-xl p-4 mb-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              <div>
                <PlayerSearchBox
                  label="Search Player A"
                  onPick={(p) => {
                    setChosenA(p);
                    setIdA(String(p.card_id));
                  }}
                />
                <div className="mt-2 text-xs text-white/60">
                  {chosenA ? `Selected: ${chosenA.name} (${chosenA.rating})` : "—"}
                </div>
              </div>
              <div>
                <PlayerSearchBox
                  label="Search Player B"
                  onPick={(p) => {
                    setChosenB(p);
                    setIdB(String(p.card_id));
                  }}
                />
                <div className="mt-2 text-xs text-white/60">
                  {chosenB ? `Selected: ${chosenB.name} (${chosenB.rating})` : "—"}
                </div>
              </div>
            </div>

            {/* Keep inputs for logic/URL sync, but hide them */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <input
                type="hidden"
                value={idA}
                onChange={(e) => setIdA(e.target.value)}
                placeholder="First Card ID"
                className="hidden"
              />
              <input
                type="hidden"
                value={idB}
                onChange={(e) => setIdB(e.target.value)}
                placeholder="Second Card ID"
                className="hidden"
              />
              <button
                onClick={applyIds}
                className="px-3 py-2 rounded-md bg-blue-500 hover:bg-blue-400 text-black font-semibold text-sm"
              >
                Compare
              </button>
            </div>

            <div className="text-xs text-white/60 mt-2">
              Tip: deep-link like <code>/player-compare?ids=12345,67890</code>
            </div>
          </div>

          {/* Results */}
          {!idA && !idB ? (
            <div className="text-white/80">Pick 1–2 players above to compare.</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {idA ? (
                <CardBlock cardId={idA} platform={platform} />
              ) : (
                <div className="border border-dashed border-white/15 rounded-2xl p-4 text-white/60 flex items-center justify-center">
                  Select Player A
                </div>
              )}
              {idB ? (
                <CardBlock cardId={idB} platform={platform} />
              ) : (
                <div className="border border-dashed border-white/15 rounded-2xl p-4 text-white/60 flex items-center justify-center">
                  Select Player B
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
