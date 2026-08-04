// src/components/PlayerSearch.jsx
import React, { useState, useEffect, useLayoutEffect, useRef } from "react";
import { Search, TrendingUp, TrendingDown, Minus, Loader2, Target } from "lucide-react";
import PriceTrendChart from "./PriceTrendChart.jsx";
import FairValueBadge from "./FairValueBadge.jsx";
import PlayerCardImage from "./PlayerCardImage.jsx";
import BacktestPanel from "./BacktestPanel.jsx";
import SalesLineChart from "./SalesLineChart.jsx";

const API_BASE = import.meta.env.VITE_API_URL || "";
const buildProxy = (url) => `${API_BASE}/img?url=${encodeURIComponent(url)}`;
const PLACEHOLDER = "/img/card-placeholder.png";

const FETCH_TIMEOUT_MS = 10000;

// None of this file's fetch helpers ever had a timeout - a single hung
// request (most likely candidate: fetchPlayerDefinition, which proxies an
// external fut.gg endpoint outside our control) could block forever with
// no way to fail. Every helper's existing try/catch already treats a
// thrown error as "no data available" and returns null, so an abort just
// routes through that same path - this only adds a ceiling on the wait.
const fetchWithTimeout = (url, options = {}, timeoutMs = FETCH_TIMEOUT_MS) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...options, signal: controller.signal }).finally(() => clearTimeout(timer));
};

// ------- text helpers (accent/diacritic-insensitive) -------
// ADDITION: normalizeForSearch — "Mbappé" -> "mbappe"
const normalizeForSearch = (s = "") =>
  s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();

// ------- backend helpers -------
const searchPlayers = async (query) => {
  if (!query.trim()) return [];
  // ADDITION: compute normalized query and send alongside the raw query
  const qNorm = normalizeForSearch(query);

  try {
    const r = await fetch(
      `${API_BASE}/api/search-players?q=${encodeURIComponent(query)}&q_norm=${encodeURIComponent(qNorm)}`,
      {
        credentials: "include",
      }
    );
    if (!r.ok) return [];
    const data = await r.json();
    return data.players || [];
  } catch (e) {
    console.error("Search failed:", e);
    return [];
  }
};

const addToWatchlist = async ({ player_name, card_id, version, platform, notes }) => {
  const r = await fetch(`${API_BASE}/api/watchlist`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ player_name, card_id, version, platform, notes }),
  });
  const payload = await r.json().catch(() => null);
  if (!r.ok) throw new Error(payload?.detail || "Failed to add to watchlist");
  return payload;
};

const fetchPlayerDefinition = async (cardId) => {
  try {
    const response = await fetchWithTimeout(`${API_BASE}/api/fut-player-definition/${cardId}`, {
      credentials: "include",
    });
    if (response.ok) {
      const data = await response.json();
      return data.data;
    }
  } catch (error) {
    console.error("Failed to fetch player definition:", error);
  }
  return null;
};

// Games played / avg goals / top chemistry style - scraped from the
// player's futbin bio page alongside BIN price, so only populated once
// bin_sales_history_sync.py has covered this card at least once.
const fetchBioStats = async (cardId) => {
  try {
    const response = await fetchWithTimeout(`${API_BASE}/api/players/${cardId}`, {
      credentials: "include",
    });
    if (response.ok) return await response.json();
  } catch (error) {
    console.error("Failed to fetch bio stats:", error);
  }
  return null;
};

// Lazy Buyer Odds (LBO) + Confidence Score - how often this card's sales
// clear above its own BIN vs the tracked pool average, ranked against
// every other card with enough sales history. Only meaningful once a
// card has enough recent sales, so callers should check `.available`.
const fetchLazyBuyerScore = async (cardId) => {
  try {
    const response = await fetchWithTimeout(`${API_BASE}/api/players/${cardId}/lazy-buyer-score`, {
      credentials: "include",
    });
    if (response.ok) return await response.json();
  } catch (error) {
    console.error("Failed to fetch lazy buyer score:", error);
  }
  return null;
};

// Real price/liquidity/volatility/snipe/margin data from bin_history and
// sales_history - what actually happened in the market, not just BIN.
const fetchMarketMetrics = async (cardId) => {
  try {
    const response = await fetchWithTimeout(`${API_BASE}/api/players/${cardId}/market-metrics`, {
      credentials: "include",
    });
    if (response.ok) return await response.json();
  } catch (error) {
    console.error("Failed to fetch market metrics:", error);
  }
  return null;
};

const fetchPlayerPrice = async (cardId) => {
  try {
    const response = await fetchWithTimeout(`${API_BASE}/api/fut-player-price/${cardId}`, {
      credentials: "include",
    });
    if (response.ok) {
      const data = await response.json();
      return {
        current: data.data?.currentPrice?.price || null,
        isExtinct: data.data?.currentPrice?.isExtinct || false,
        updatedAt: data.data?.currentPrice?.priceUpdatedAt || null,
        auctions: data.data?.completedAuctions || [],
      };
    }
  } catch (error) {
    console.error("Failed to fetch price:", error);
  }
  return null;
};

// ------- position helpers -------
const POS_CODE_TO_NAME = {
  0: "GK", 1: "GK", 2: "GK",
  3: "RB", 4: "RB",
  5: "CB", 6: "CB",
  7: "LB", 8: "LB", 9: "LB",
  10: "CDM", 11: "CDM",
  12: "RM", 13: "RM",
  14: "CM", 15: "CM",
  16: "LM", 17: "LM",
  18: "CAM", 19: "CAM", 20: "CAM", 21: "CAM", 22: "CAM",
  23: "RW", 24: "RW",
  25: "ST", 26: "ST",
  27: "LW",
};
const getPositionName = (id) => POS_CODE_TO_NAME[id] || "Unknown";

const _posNorm = (x) => {
  if (x == null) return null;
  if (typeof x === "number") return getPositionName(x);
  if (typeof x === "string") {
    const s = x.trim().toUpperCase();
    if (!s) return null;
    return s.split(/[,\|/]+/)[0]?.trim() || null;
  }
  if (Array.isArray(x)) {
    for (const v of x) {
      const t = _posNorm(v);
      if (t) return t;
    }
    return null;
  }
  if (typeof x === "object") {
    return (
      _posNorm(x.name) ||
      _posNorm(x.shortName) ||
      _posNorm(x.short) ||
      _posNorm(x.code) ||
      _posNorm(x.position) ||
      _posNorm(x.id)
    );
  }
  return null;
};

const resolvePosition = (pd, fb) => {
  const cands = [
    pd?.position, pd?.preferredPosition1, pd?.preferredPosition2, pd?.preferredPosition3,
    pd?.mainPosition, pd?.primaryPosition, pd?.bestPosition, pd?.basePosition, pd?.defaultPosition,
    pd?.positionId, pd?.positionName, pd?.preferredPosition1Name, pd?.shortPosition, pd?.positionShort,
    pd?.positionFull, pd?.positionLong, pd?.positions, pd?.preferredPositions, pd?.alternativePositions,
    pd?.altPositions, pd?.playablePositions, pd?.positionsList, pd?.positionList, pd?.displayPositions,
    fb?.position, fb?.position_name, fb?.position_short, fb?.pos, fb?.positions,
  ];
  for (const c of cands) {
    const t = _posNorm(c);
    if (t) return t;
  }
  return "Unknown";
};

// fut.gg's definition API returned a relative imagePath that needed its own
// CDN prefix; our own DB-backed definition endpoint returns a full,
// ready-to-use image URL (from futbin) instead - support both.
const resolveImage = (imagePath, cdnPrefix) => {
  if (!imagePath) return "";
  if (/^https?:\/\//i.test(imagePath)) return imagePath;
  return `${cdnPrefix}${imagePath}`;
};

const getAttributeColor = (v) =>
  v >= 90 ? "text-green-400" :
  v >= 80 ? "text-green-300" :
  v >= 70 ? "text-yellow-300" :
  v >= 60 ? "text-orange-300" : "text-red-400";

// ================== AutoFitNumber ==================
function AutoFitNumber({ value, className = "" }) {
  const spanRef = useRef(null);
  const [scale, setScale] = useState(1);

  useLayoutEffect(() => {
    const el = spanRef.current;
    if (!el) return;
    const parent = el.parentElement;
    if (!parent) return;

    const recalc = () => {
      const cs = getComputedStyle(parent);
      const pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
      const icon = parent.querySelector("img");
      const iconW = icon ? icon.getBoundingClientRect().width : 20;
      const gap =  (parseFloat(cs.columnGap) || 0) || 8;
      const safety = 6;

      const available = Math.max(0, parent.clientWidth - pad - iconW - gap - safety);
      el.style.transform = "scale(1)";
      const needed = el.scrollWidth || 1;

      const next = Math.min(1, Math.max(0.4, available / needed));
      setScale(next);
    };

    recalc();
    const ro = new ResizeObserver(recalc);
    ro.observe(parent);
    window.addEventListener("resize", recalc);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", recalc);
    };
  }, [value]);

  return (
    <span
      ref={spanRef}
      className={className}
      style={{
        display: "inline-block",
        transform: `scale(${scale})`,
        transformOrigin: "left center",
        whiteSpace: "nowrap",
        lineHeight: 1,
      }}
    >
      {value != null ? Number(value).toLocaleString() : "N/A"}
    </span>
  );
}

// ================== SearchBox ==================
const SearchBox = ({ onPlayerSelect }) => {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [showResults, setShowResults] = useState(false);

  useEffect(() => {
    const t = setTimeout(async () => {
      if (!query.trim()) {
        setResults([]);
        setShowResults(false);
        return;
      }
      setLoading(true);
      const players = await searchPlayers(query);
      setResults(players);
      setLoading(false);
      setShowResults(true);
    }, 300);
    return () => clearTimeout(t);
  }, [query]);

  return (
    <div className="relative w-full max-w-md mx-auto">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-200 w-5 h-5" />
        {loading && (
          <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-200 w-5 h-5 animate-spin" />
        )}
        <input
          type="text"
          placeholder="Search players (e.g. Messi, Mbappé)..."
          className="w-full pl-10 pr-4 py-3 bg-[#0b1220]/70 border border-white/20 rounded-lg focus:border-blue-400 focus:outline-none text-lg text-white placeholder-white/70 backdrop-blur-sm"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => setShowResults(true)}
        />
      </div>

      {showResults && results.length > 0 && (
        <div className="absolute z-10 w-full mt-2 bg-[#0b1220]/90 backdrop-blur-md border border-white/20 rounded-lg shadow-lg max-h-96 overflow-y-auto">
          {results.map((player) => (
            <button
              key={`${player.card_id}-${player.rating}`}
              className="w-full px-4 py-3 text-left hover:bg-white/10 border-b border-white/10 last:border-b-0 focus:outline-none"
              onClick={() => {
                onPlayerSelect(player);
                setShowResults(false);
                setQuery("");
              }}
            >
              <div className="flex items-center gap-3">
                <img
                  src={player.image_url || PLACEHOLDER}
                  alt={`${player.name} (${player.rating})`}
                  loading="lazy"
                  className="w-12 h-16 object-contain"
                  onError={(e) => {
                    const img = e.currentTarget;
                    if (!img.dataset.triedProxy && player.image_url) {
                      img.dataset.triedProxy = "1";
                      img.src = buildProxy(player.image_url);
                    } else {
                      img.src = PLACEHOLDER;
                    }
                  }}
                  referrerPolicy="no-referrer"
                />
                <div className="font-semibold text-white truncate">
                  {player.name} ({player.rating})
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      {showResults && !loading && query && results.length === 0 && (
        <div className="absolute z-10 w-full mt-2 bg-[#0b1220]/90 border border-white/20 rounded-lg shadow-lg p-4 text-center text-white/80">
          No players found for "{query}"
        </div>
      )}
    </div>
  );
};

// ================== PriceTrend ==================
const PriceTrend = ({ auctions }) => {
  if (!auctions || auctions.length < 2) return null;
  const [a, b] = auctions.slice(0, 2);
  const cur = a?.soldPrice;
  const prev = b?.soldPrice;
  if (!cur || !prev) return null;

  const change = cur - prev;
  const pct = ((change / prev) * 100).toFixed(2);

  return (
    <div className="flex items-center gap-2 text-sm">
      {change > 0 ? (
        <TrendingUp className="w-4 h-4 text-green-400" />
      ) : change < 0 ? (
        <TrendingDown className="w-4 h-4 text-red-400" />
      ) : (
        <Minus className="w-4 h-4 text-gray-300" />
      )}
      <span className={`font-medium ${change > 0 ? "text-green-300" : change < 0 ? "text-red-300" : "text-gray-300"}`}>
        {pct}% {change > 0 ? `(+${change.toLocaleString()})` : `(${change.toLocaleString()})`}
      </span>
    </div>
  );
};

// ================== PlayerDetail ==================
const PlayerDetail = ({ player, onBack }) => {
  const [priceData, setPriceData] = useState(null);
  const [playerData, setPlayerData] = useState(null);
  const [marketMetrics, setMarketMetrics] = useState(null);
  const [bioStats, setBioStats] = useState(null);
  const [lazyBuyerScore, setLazyBuyerScore] = useState(null);
  // Price used to share a single `loading` flag with four unrelated
  // fetches (definition/market-metrics/bio-stats/lazy-buyer-score) via one
  // Promise.all - so the slowest of the five (often fetchPlayerDefinition,
  // an external fut.gg-proxied call outside our control) kept the Price
  // spinner up no matter how fast the price itself actually resolved.
  // priceLoading now tracks fetchPlayerPrice alone.
  const [priceLoading, setPriceLoading] = useState(true);

  const [adding, setAdding] = useState(false);
  const [platform, setPlatform] = useState("ps");
  const [notes, setNotes] = useState("");

  const cardId = player.card_id || player.id;

  useEffect(() => {
    setPriceLoading(true);
    fetchPlayerPrice(cardId).then((priceInfo) => {
      setPriceData(priceInfo);
      setPriceLoading(false);
    });
    (async () => {
      const [defInfo, marketInfo, bioInfo, lazyBuyerInfo] = await Promise.all([
        fetchPlayerDefinition(cardId),
        fetchMarketMetrics(cardId),
        fetchBioStats(cardId),
        fetchLazyBuyerScore(cardId),
      ]);
      setPlayerData(defInfo);
      setMarketMetrics(marketInfo);
      setBioStats(bioInfo);
      setLazyBuyerScore(lazyBuyerInfo);
    })();
  }, [cardId]);

  const formatPrice = (p) => (p ? p.toLocaleString() : "N/A");

  const priceRange =
    priceData?.auctions?.length
      ? (() => {
          const prices = priceData.auctions.map((a) => a.soldPrice).filter(Boolean);
          if (!prices.length) return null;
          return { min: Math.min(...prices), max: Math.max(...prices) };
        })()
      : null;

  const d = {
    fullName:
      playerData?.commonName ||
      (playerData?.firstName && playerData?.lastName
        ? `${playerData.firstName} ${playerData.lastName}`
        : `${player.name} (${player.rating})`),
    position: resolvePosition(playerData, player),
    club: playerData?.club?.name || player.club || "Unknown",
    clubImage: resolveImage(
      playerData?.club?.imagePath,
      "https://game-assets.fut.gg/cdn-cgi/image/quality=100,format=auto,width=40/"
    ),
    nation: playerData?.nation?.name || player.nation || "Unknown",
    nationImage: resolveImage(
      playerData?.nation?.imagePath,
      "https://game-assets.fut.gg/cdn-cgi/image/quality=100,format=auto,width=40/"
    ),
    league: playerData?.league?.name || "Unknown League",
    leagueImage: resolveImage(
      playerData?.league?.imagePath,
      "https://game-assets.fut.gg/cdn-cgi/image/quality=100,format=auto,width=40/"
    ),
    // futbin has no single flat card image - it composites a card-template
    // background PNG with a separate player cutout PNG client-side. When
    // both layers are available we stack them ourselves; otherwise fall
    // back to whatever single image we do have.
    cardBgImage: playerData?.bgImageUrl || null,
    cardCutoutImage: playerData?.cutoutImageUrl || player.image_url || null,
    // Special-card cutouts read as pre-scaled to fill the card frame; base
    // gold/silver/bronze cutouts are a closer, more generic headshot crop
    // that renders far too large stretched to fill the same frame the same
    // way - size each differently instead of treating every cutout as a
    // full-bleed layer like the background.
    cutoutType: playerData?.cutoutType || "special",
    // futbin's own short display name (e.g. "Cristiano Ronaldo", not the
    // full legal name) - no word-position guess on the full name is
    // reliable, so prefer what futbin itself renders when we have it.
    cardName: playerData?.cardName || null,
    cardImage: playerData?.futggCardImagePath
      ? resolveImage(
          playerData.futggCardImagePath,
          "https://game-assets.fut.gg/cdn-cgi/image/quality=90,format=auto,width=500/"
        )
      : playerData?.cutoutImageUrl || player.image_url,
    rating: playerData?.overall ?? player.rating,
    version: playerData?.rarity?.name || player.version || "Base",
    skillMoves: playerData?.skillMoves ?? 3,
    weakFoot: playerData?.weakFoot ?? 3,
    age: playerData?.dateOfBirth
      ? new Date().getFullYear() - new Date(playerData.dateOfBirth).getFullYear()
      : null,
    foot: playerData?.foot === 2 || playerData?.foot === "Left" ? "Left" : "Right",
    accelerateType: playerData?.accelerateType || "Controlled",
    stats: {
      pace: playerData?.facePace || 0,
      shooting: playerData?.faceShooting || 0,
      passing: playerData?.facePassing || 0,
      dribbling: playerData?.faceDribbling || 0,
      defending: playerData?.faceDefending || 0,
      physicality: playerData?.facePhysicality || 0,
    },
    attributes: {
      acceleration: playerData?.attributeAcceleration || 0,
      sprintSpeed: playerData?.attributeSprintSpeed || 0,
      agility: playerData?.attributeAgility || 0,
      balance: playerData?.attributeBalance || 0,
      jumping: playerData?.attributeJumping || 0,
      stamina: playerData?.attributeStamina || 0,
      strength: playerData?.attributeStrength || 0,
      reactions: playerData?.attributeReactions || 0,
      aggression: playerData?.attributeAggression || 0,
      composure: playerData?.attributeComposure || 0,
      interceptions: playerData?.attributeInterceptions || 0,
      positioning: playerData?.attributePositioning || 0,
      vision: playerData?.attributeVision || 0,
      ballControl: playerData?.attributeBallControl || 0,
      crossing: playerData?.attributeCrossing || 0,
      dribbling: playerData?.attributeDribbling || 0,
      finishing: playerData?.attributeFinishing || 0,
      freeKickAccuracy: playerData?.attributeFkAccuracy || 0,
      headingAccuracy: playerData?.attributeHeadingAccuracy || 0,
      longPassing: playerData?.attributeLongPassing || 0,
      shortPassing: playerData?.attributeShortPassing || 0,
      shotPower: playerData?.attributeShotPower || 0,
      longShots: playerData?.attributeLongShots || 0,
      standingTackle: playerData?.attributeStandingTackle || 0,
      slidingTackle: playerData?.attributeSlidingTackle || 0,
      volleys: playerData?.attributeVolleys || 0,
      curve: playerData?.attributeCurve || 0,
      penalties: playerData?.attributePenalties || 0,
    },
  };

  const onAddClick = async () => {
    try {
      setAdding(true);
      await addToWatchlist({
        player_name: player.name,
        card_id: Number(cardId),
        version: player.version || playerData?.rarity?.name || null,
        platform,
        notes: notes?.trim() || null,
      });
      alert(`Added ${player.name} to your watchlist ✓`);
      setNotes("");
    } catch (e) {
      console.error(e);
      alert(e.message || "Failed to add to watchlist");
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="w-full max-w-6xl mx-auto">
      <button
        onClick={onBack}
        className="mb-6 px-4 py-2 text-blue-200 hover:text-white font-medium transition-colors bg-white/10 rounded-md"
      >
        ← Back to Search
      </button>

      <div className="bg-black/40 backdrop-blur-md border border-white/10 text-white rounded-xl p-6">
        <div className="flex flex-col lg:flex-row items-start gap-6 mb-6">
          <PlayerCardImage
            enablePolling
            player={{
              card_id: cardId,
              generated_card_url: player.generated_card_url,
              generated_card_status: player.generated_card_status,
              generated_card_flagged: player.generated_card_flagged,
              card_bg_image: d.cardBgImage,
              card_cutout_image: d.cardCutoutImage,
              card_cutout_type: d.cutoutType,
              image_url: d.cardImage,
              rating: d.rating,
              position: d.position,
              card_name: d.cardName || player.name || d.fullName,
              stats: d.stats,
              nation_image: d.nationImage,
              league_image: d.leagueImage,
              club_image: d.clubImage,
            }}
            versionLabel={d.version}
            altText={d.fullName}
            widthClass="w-48"
          />

          <div className="flex-1 min-w-0">
            <h1 className="mb-2 font-extrabold leading-tight tracking-tight break-words text-3xl sm:text-4xl md:text-5xl">
              {d.cardName || player.name || d.fullName}
            </h1>

            {/* Price / Range / Trend / AcceleRATE */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
              {/* Price */}
              <div className="bg-white/5 rounded-lg p-3 min-w-0 border border-white/10">
                <div className="text-white/80 text-xs md:text-sm mb-1 flex items-center gap-2">
                  Price <FairValueBadge cardId={cardId} />
                </div>
                <div className="font-bold text-yellow-300 leading-tight">
                  {priceLoading ? (
                    <Loader2 className="w-5 h-5 md:w-6 md:h-6 animate-spin inline align-[-2px]" />
                  ) : priceData?.isExtinct ? (
                    "Extinct"
                  ) : (
                    <span className="inline-flex items-center gap-2 min-w-0 overflow-hidden align-middle w-full">
                      <img
                        src="https://cdn2.futbin.com/https%3A%2F%2Fcdn.futbin.com%2Fdesign%2Fimg%2Fcoins_big.png?fm=png&ixlib=java-2.1.0&w=40&s=cad4ceb684da7f0b778fdeb1d4065fb1"
                        alt="Coins"
                        className="w-4 h-4 md:w-5 md:h-5 shrink-0"
                      />
                      <AutoFitNumber value={priceData?.current} />
                    </span>
                  )}
                </div>
              </div>

              {/* Range */}
              <div className="bg-white/5 rounded-lg p-3 min-w-0 border border-white/10">
                <div className="text-white/80 text-xs md:text-sm mb-1">Range</div>
                <div className="font-medium leading-snug text-sm md:text-base break-words">
                  {priceRange ? `${formatPrice(priceRange.min)} - ${formatPrice(priceRange.max)}` : "N/A"}
                </div>
              </div>

              {/* Trend */}
              <div className="bg-white/5 rounded-lg p-3 min-w-0 border border-white/10">
                <div className="text-white/80 text-xs md:text-sm mb-1">Trend</div>
                <PriceTrend auctions={priceData?.auctions} />
              </div>

              {/* AcceleRATE */}
              <div className="bg-white/5 rounded-lg p-3 min-w-0 border border-white/10">
                <div className="text-white/80 text-xs md:text-sm mb-1">AcceleRATE</div>
                <div className="font-semibold text-green-300 text-sm md:text-base leading-tight">
                  {d.accelerateType.replace(/_/g, " ")}
                </div>
              </div>
            </div>

            {/* Club / Nation / League / Position */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
              {/* Club */}
              <div className="text-center bg-white/5 border border-white/10 rounded-lg p-3">
                <div className="w-8 h-8 mx-auto mb-2 flex items-center justify-center">
                  {d.clubImage ? (
                    <img
                      src={d.clubImage}
                      alt={d.club}
                      className="w-8 h-8 object-contain"
                      referrerPolicy="no-referrer"
                      onError={(e) => {
                        if (!e.currentTarget.dataset.triedProxy) {
                          e.currentTarget.dataset.triedProxy = "1";
                          e.currentTarget.src = buildProxy(d.clubImage);
                          return;
                        }
                        e.currentTarget.style.display = "none";
                      }}
                    />
                  ) : (
                    <div className="w-8 h-8 bg-white/20 rounded flex items-center justify-center text-xs">CLUB</div>
                  )}
                </div>
                <div className="text-xs text-white/70 mb-1">Club</div>
                <div className="font-medium text-sm">{d.club}</div>
              </div>

              {/* Nation */}
              <div className="text-center bg-white/5 border border-white/10 rounded-lg p-3">
                <div className="w-8 h-6 mx-auto mb-2 flex items-center justify-center">
                  {d.nationImage ? (
                    <img
                      src={d.nationImage}
                      alt={d.nation}
                      className="w-8 h-6 object-contain"
                      referrerPolicy="no-referrer"
                      onError={(e) => {
                        if (!e.currentTarget.dataset.triedProxy) {
                          e.currentTarget.dataset.triedProxy = "1";
                          e.currentTarget.src = buildProxy(d.nationImage);
                          return;
                        }
                        e.currentTarget.style.display = "none";
                      }}
                    />
                  ) : (
                    <div className="w-8 h-6 bg-white/20 rounded flex items-center justify-center text-xs">NAT</div>
                  )}
                </div>
                <div className="text-xs text-white/70 mb-1">Nation</div>
                <div className="font-medium text-sm">{d.nation}</div>
              </div>

              {/* League */}
              <div className="text-center bg-white/5 border border-white/10 rounded-lg p-3">
                <div className="w-8 h-8 mx-auto mb-2 flex items-center justify-center">
                  {d.leagueImage ? (
                    <img
                      src={d.leagueImage}
                      alt={d.league}
                      className="w-8 h-8 object-contain"
                      referrerPolicy="no-referrer"
                      onError={(e) => {
                        if (!e.currentTarget.dataset.triedProxy) {
                          e.currentTarget.dataset.triedProxy = "1";
                          e.currentTarget.src = buildProxy(d.leagueImage);
                          return;
                        }
                        e.currentTarget.style.display = "none";
                      }}
                    />
                  ) : (
                    <div className="w-8 h-8 bg-white/20 rounded flex items-center justify-center text-xs">LEG</div>
                  )}
                </div>
                <div className="text-xs text-white/70 mb-1">League</div>
                <div className="font-medium text-sm">{d.league}</div>
              </div>

              {/* Position */}
              <div className="text-center bg-white/5 border border-white/10 rounded-lg p-3">
                <Target className="w-6 h-6 mx-auto mb-2 text-red-300" />
                <div className="text-xs text-white/70 mb-1">Position</div>
                <div className="font-medium">{d.position}</div>
              </div>
            </div>

            {/* Player Stats */}
            <div className="bg-white/5 border border-white/10 rounded-lg p-4 mb-4">
              <h3 className="font-semibold mb-3 text-lg">Player Stats</h3>
              <div className="grid grid-cols-3 md:grid-cols-6 gap-4">
                {Object.entries(d.stats).map(([stat, value]) => (
                  <div key={stat} className="text-center">
                    <div className={`text-2xl font-bold ${getAttributeColor(value)}`}>{value}</div>
                    <div className="text-xs text-white/70 capitalize">{stat}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Skill / Weak Foot / Age / Preferred Foot */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
              <div className="text-center bg-white/5 border border-white/10 rounded-lg p-3">
                <div className="text-lg font-semibold text-yellow-300">{"⭐".repeat(d.skillMoves)}</div>
                <div className="text-xs text-white/70">Skill Moves</div>
              </div>
              <div className="text-center bg-white/5 border border-white/10 rounded-lg p-3">
                <div className="text-lg font-semibold text-yellow-300">{"⭐".repeat(d.weakFoot)}</div>
                <div className="text-xs text-white/70">Weak Foot</div>
              </div>
              <div className="text-center bg-white/5 border border-white/10 rounded-lg p-3">
                <div className="text-sm font-semibold text-green-300">{d.age ? `${d.age} years` : "Unknown"}</div>
                <div className="text-xs text-white/70">Age</div>
              </div>
              <div className="text-center bg-white/5 border border-white/10 rounded-lg p-3">
                <div className="text-sm font-semibold text-blue-300">{d.foot}</div>
                <div className="text-xs text-white/70">Preferred Foot</div>
              </div>
            </div>

            {d.generatedCardUrl && (
              <a
                href={d.generatedCardUrl}
                download={`${(d.commonName || d.name || "card").replace(/\s+/g, "_")}.png`}
                className="inline-flex items-center gap-2 mb-4 px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-sm hover:bg-white/10"
              >
                Download card PNG
              </a>
            )}
          </div>
        </div>

        {/* Watchlist CTA */}
        <div className="mt-1 mb-6 p-4 bg-black/40 border border-white/10 rounded-xl flex flex-col sm:flex-row items-start sm:items-center gap-3">
          <div className="text-sm text-white/90">Track this player’s price on your Watchlist</div>
          <div className="flex items-center gap-2 sm:ml-auto">
            <select
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              className="px-2 py-1 rounded-md bg-black/50 border border-white/15 text-white text-sm"
              title="Platform"
            >
              <option value="ps">PS</option>
              <option value="xbox">Xbox</option>
            </select>
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Notes (optional)"
              className="hidden md:block w-56 px-2 py-1 rounded-md bg-black/50 border border-white/15 text-white text-sm"
            />
            <button
              onClick={onAddClick}
              disabled={adding}
              className="px-3 py-2 rounded-md bg-lime-400/90 hover:bg-lime-400 text-black font-semibold"
            >
              {adding ? "Adding…" : "+ Add to Watchlist"}
            </button>
          </div>
        </div>

        {/* Detailed Attributes */}
        <div className="bg-white/5 border border-white/10 rounded-lg p-4 mb-4">
          <h3 className="font-semibold mb-3 text-lg">Detailed Attributes</h3>
          <div className="grid grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3 text-sm">
            {Object.entries(d.attributes).map(
              ([attr, value]) =>
                value > 0 && (
                  <div key={attr} className="flex justify-between items-center bg-white/10 rounded px-2 py-1">
                    <span className="text-white/80 capitalize text-xs">
                      {attr.replace(/([A-Z])/g, " $1").trim()}
                    </span>
                    <span className={`font-semibold ${getAttributeColor(value)}`}>{value}</span>
                  </div>
                )
            )}
          </div>
        </div>

        {/* Real Market Data - what actually sold, not just what's asked */}
        {marketMetrics?.dataQualitySuspect && (
          <div className="bg-white/5 border border-white/10 rounded-lg p-4 mb-6">
            <h3 className="font-semibold mb-1 text-lg">Real Market Data</h3>
            <p className="text-xs text-white/60">
              We're still verifying this card's market data - the numbers we're seeing don't line up
              with the live price yet. Check back shortly.
            </p>
          </div>
        )}
        {!marketMetrics?.dataQualitySuspect && marketMetrics?.realPrice?.sampleSize24h > 0 && (
          <div className="bg-white/5 border border-white/10 rounded-lg p-4 mb-6">
            <h3 className="font-semibold mb-1 text-lg">Real Market Data</h3>
            <p className="text-xs text-white/60 mb-3">
              From {marketMetrics.realPrice.sampleSize24h} actual completed sales in the last 24h (PS market) - not just the asking price.
            </p>
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
              <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                <div className="text-white/80 text-xs mb-1">Median Sold (24h)</div>
                <div className="font-bold text-yellow-300">
                  {Math.round(marketMetrics.realPrice.medianSold24h).toLocaleString()}
                </div>
              </div>
              <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                <div className="text-white/80 text-xs mb-1">vs Current BIN</div>
                <div className={`font-bold ${marketMetrics.divergencePct24h < 0 ? "text-red-300" : "text-green-300"}`}>
                  {marketMetrics.divergencePct24h != null
                    ? `${marketMetrics.divergencePct24h > 0 ? "+" : ""}${marketMetrics.divergencePct24h}%`
                    : "N/A"}
                </div>
              </div>
              <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                <div className="text-white/80 text-xs mb-1">Liquidity</div>
                <div className="font-bold text-blue-300">
                  {marketMetrics.liquidity?.salesPerHour24h ?? "N/A"}/hr
                </div>
              </div>
              <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                <div className="text-white/80 text-xs mb-1">Volatility (CV)</div>
                <div className="font-bold text-purple-300">
                  {marketMetrics.volatility?.coefficientOfVariation24h ?? "N/A"}
                </div>
              </div>
              <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                <div className="text-white/80 text-xs mb-1">Snipe Index</div>
                <div className="font-bold text-lime-300">
                  {marketMetrics.snipeIndex24h != null ? `${Math.round(marketMetrics.snipeIndex24h * 100)}%` : "N/A"}
                </div>
              </div>
            </div>
            {marketMetrics.taxAwareMargin && (
              <div className="mt-3 text-sm text-white/80">
                Buy at BIN ({marketMetrics.taxAwareMargin.buyAt.toLocaleString()}), sell at median (
                {marketMetrics.taxAwareMargin.sellAt.toLocaleString()}) → net after EA tax{" "}
                <span className={marketMetrics.taxAwareMargin.estMarginCoins >= 0 ? "text-green-300" : "text-red-300"}>
                  {marketMetrics.taxAwareMargin.estMarginCoins >= 0 ? "+" : ""}
                  {marketMetrics.taxAwareMargin.estMarginCoins.toLocaleString()} ({marketMetrics.taxAwareMargin.estMarginPct}%)
                </span>
              </div>
            )}
          </div>
        )}

        {/* Lazy Buyer Score - LBO (how often this card's sales beat its
            own BIN price at the time of each sale vs the tracked pool
            average) and a cross-pool Confidence ranking, computed from
            fair_value_mv + sales_history + bin_history. Expected Profit
            and Recommended Chem Style reuse data already fetched above
            rather than recomputing. Copy is deliberately plain-language
            (no jargon like "percentile" or "z-score") since this is
            aimed at casual traders, not analysts. */}
        {lazyBuyerScore?.available && (
          <div className="bg-white/5 border border-white/10 rounded-lg p-4 mb-6">
            <h3 className="font-semibold mb-1 text-lg">Lazy Buyer Score</h3>
            <p className="text-xs text-white/60 mb-3">
              How often people paid MORE than the cheapest price for this card, based on{" "}
              {lazyBuyerScore.sampleSize7d} real sales in the last 7 days.
            </p>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                <div className="text-white/80 text-xs mb-1">Sold Above BIN</div>
                <div className="font-bold text-lime-300">{lazyBuyerScore.lboRate7d}%</div>
                <div className="text-white/50 text-xs mt-0.5">
                  of sales beat the cheapest price (average card: {lazyBuyerScore.poolAvgLboRate7d}%)
                </div>
              </div>
              <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                <div className="text-white/80 text-xs mb-1">Confidence</div>
                <div className="font-bold text-blue-300">{lazyBuyerScore.confidenceScore}%</div>
                <div className="text-white/50 text-xs mt-0.5">
                  ranks better than {lazyBuyerScore.confidenceScore}% of tracked cards
                </div>
              </div>
              {marketMetrics?.taxAwareMargin && (
                <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                  <div className="text-white/80 text-xs mb-1">Expected Profit</div>
                  <div
                    className={`font-bold ${
                      marketMetrics.taxAwareMargin.estMarginCoins >= 0 ? "text-green-300" : "text-red-300"
                    }`}
                  >
                    {marketMetrics.taxAwareMargin.estMarginCoins >= 0 ? "+" : ""}
                    {marketMetrics.taxAwareMargin.estMarginCoins.toLocaleString()} (
                    {marketMetrics.taxAwareMargin.estMarginPct}%)
                  </div>
                </div>
              )}
              {(bioStats?.top_chem_style_console || bioStats?.top_chem_style_pc) && (
                <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                  <div className="text-white/80 text-xs mb-1">Recommended Chem Style</div>
                  <div className="font-bold text-lime-300">
                    {bioStats.top_chem_style_console || bioStats.top_chem_style_pc}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Career Stats - scraped from the futbin bio page alongside BIN
            price, so only present once a sync has covered this card. */}
        {bioStats && (bioStats.games_played_console || bioStats.games_played_pc) && (
          <div className="bg-white/5 border border-white/10 rounded-lg p-4 mb-6">
            <h3 className="font-semibold mb-3 text-lg">Career Stats</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {bioStats.games_played_console != null && (
                <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                  <div className="text-white/80 text-xs mb-2">Console</div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-white/60">Games Played</span>
                    <span className="font-semibold">{bioStats.games_played_console.toLocaleString()}</span>
                  </div>
                  {bioStats.avg_goals_console != null && (
                    <div className="flex justify-between text-sm mb-1">
                      <span className="text-white/60">Avg Goals / Game</span>
                      <span className="font-semibold">{bioStats.avg_goals_console.toFixed(2)}</span>
                    </div>
                  )}
                  {bioStats.top_chem_style_console && (
                    <div className="flex justify-between text-sm">
                      <span className="text-white/60">Top Chem Style</span>
                      <span className="font-semibold text-lime-300">{bioStats.top_chem_style_console}</span>
                    </div>
                  )}
                </div>
              )}
              {bioStats.games_played_pc != null && (
                <div className="bg-white/5 rounded-lg p-3 border border-white/10">
                  <div className="text-white/80 text-xs mb-2">PC</div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-white/60">Games Played</span>
                    <span className="font-semibold">{bioStats.games_played_pc.toLocaleString()}</span>
                  </div>
                  {bioStats.avg_goals_pc != null && (
                    <div className="flex justify-between text-sm mb-1">
                      <span className="text-white/60">Avg Goals / Game</span>
                      <span className="font-semibold">{bioStats.avg_goals_pc.toFixed(2)}</span>
                    </div>
                  )}
                  {bioStats.top_chem_style_pc && (
                    <div className="flex justify-between text-sm">
                      <span className="text-white/60">Top Chem Style</span>
                      <span className="font-semibold text-lime-300">{bioStats.top_chem_style_pc}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Price History (BIN snapshots) */}
        <div className="bg-white/5 border border-white/10 rounded-lg p-4 mb-6">
          <h3 className="font-semibold mb-3 text-lg">Price History</h3>
          <PriceTrendChart playerId={cardId} platform={platform} initialTimeframe="today" height={300} />
        </div>

        {/* Real sold-price trend, Gold Rare cards only (bin_sales_history_sync.py coverage) */}
        <div className="bg-white/5 border border-white/10 rounded-lg p-4 mb-6">
          <SalesLineChart cardId={cardId} days={1} height={260} />
        </div>

        <BacktestPanel cardId={cardId} className="mb-6" />

        {priceData?.auctions?.length > 0 && (
          <div className="bg-white/5 border border-white/10 rounded-lg p-4">
            <h3 className="font-semibold mb-3 text-lg">Recent Sales</h3>
            <div className="space-y-2">
              {priceData.auctions.slice(0, 5).map((a, idx) => (
                <div key={idx} className="flex justify-between items-center text-sm bg-white/10 rounded px-3 py-2">
                  <span className="text-white/80">
                    {a.soldDate ? new Date(a.soldDate).toLocaleString() : "—"}
                  </span>
                  <span className="font-medium text-yellow-300">
                    {a.soldPrice ? a.soldPrice.toLocaleString() : "N/A"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {priceData?.updatedAt && (
          <div className="text-center text-white/70 text-xs mt-4">
            Price updated: {new Date(priceData.updatedAt).toLocaleString()}
          </div>
        )}
      </div>
    </div>
  );
};

// ================== Main ==================
export default function PlayerSearch() {
  const [selectedPlayer, setSelectedPlayer] = useState(null);

  return (
    <div className="min-h-screen relative text-white">
      {/* Stadium background */}
      <div
        className="absolute inset-0 bg-cover bg-center"
        style={{ backgroundImage: "url('/img/football-stadium-night.jpg')" }}
      />
      {/* Purple gradient overlay (match Add Trade) */}
      <div className="absolute inset-0 bg-gradient-to-br from-purple-700/70 via-purple-800/70 to-purple-900/80" />

      {/* Content */}
      <div className="relative z-10 p-6">
        <div className="container mx-auto">
          {!selectedPlayer ? (
            <div className="text-center py-20">
              <h1 className="text-4xl font-bold text-white mb-8">Player Search</h1>
              <SearchBox onPlayerSelect={setSelectedPlayer} />
              <p className="text-white/90 mt-4">
                Search by player name to view live market data and detailed stats
              </p>
            </div>
          ) : (
            <PlayerDetail player={selectedPlayer} onBack={() => setSelectedPlayer(null)} />
          )}
        </div>
      </div>
    </div>
  );
}
