// src/api/squadApi.js
import { normalizePositions } from "../utils/positions";

const API_BASE = import.meta.env.VITE_API_URL || "";

// simple TTL caches
const defCache = new Map();
const priceCache = new Map();
const searchCache = new Map();
const CACHE_TTL = 5 * 60 * 1000;
const valid = (t) => t && (Date.now() - t) < CACHE_TTL;

function buildApiUrl(path) {
  const base = API_BASE || window.location.origin;
  return new URL(path, base).toString();
}

/** Search by q and/or pos. `pos` is the squad slot position (e.g. "ST"). */
export async function searchPlayers(query, pos) {
  const q = (query || "").trim();
  const p = (pos || "").trim().toUpperCase();
  const cacheKey = `${q}::${p}`;

  const cached = searchCache.get(cacheKey);
  if (cached && valid(cached.t)) return cached.data;

  const url = new URL(buildApiUrl("/api/search-players"));
  if (q) url.searchParams.set("q", q);
  if (p) url.searchParams.set("pos", p);

  const r = await fetch(url.toString(), { credentials: "include" });
  if (!r.ok) {
    console.warn("search-players failed", r.status, r.statusText, url.toString());
    return [];
  }
  const { players = [] } = await r.json();

  const out = players.map((p) => {
    const alts = typeof p.altposition === "string"
      ? p.altposition.split(/[,;/|\s]+/).filter(Boolean)
      : [];
    const positions = normalizePositions([p.position, ...alts]);

    return {
      id: Number(p.card_id),
      card_id: Number(p.card_id),
      name: p.name || "Unknown",
      rating: Number(p.rating) || 0,
      version: p.version || null,
      image_url: p.image_url || null,
      club: p.club || null,
      league: p.league || null,
      nation: p.nation || null,
      positions,
      price: typeof p.price === "number" ? p.price : null,
      // will be filled by FUT.GG def if needed
      clubId: null, leagueId: null, nationId: null,
      isIcon: false, isHero: false,
    };
  });

  searchCache.set(cacheKey, { t: Date.now(), data: out });
  return out;
}

// ---- FUT.GG enrichment (optional, keeps your proxies) ----
function cardImageFromDef(def) {
  if (def?.futggCardImagePath) {
    return `https://game-assets.fut.gg/cdn-cgi/image/quality=90,format=auto,width=500/${def.futggCardImagePath}`;
  }
  if (def?.futggCardImage?.path) {
    return `https://game-assets.fut.gg/cdn-cgi/image/quality=90,format=auto,width=500/${def.futggCardImage.path}`;
  }
  return null;
}

async function fetchDefinition(cardId) {
  const id = Number(cardId);
  if (!id) return null;
  const c = defCache.get(id);
  if (c && valid(c.t)) return c.data;

  const r = await fetch(buildApiUrl(`/api/fut-player-definition/${id}`), { credentials: "include" });
  if (!r.ok) return null;
  const json = await r.json();
  const def = json?.data || null;

  defCache.set(id, { t: Date.now(), data: def });
  return def;
}

async function fetchLivePrice(cardId) {
  const id = Number(cardId);
  if (!id) return { current: null, isExtinct: false, updatedAt: null };
  const c = priceCache.get(id);
  if (c && valid(c.t)) return c.data;

  const r = await fetch(buildApiUrl(`/api/fut-player-price/${id}`), { credentials: "include" });
  if (!r.ok) return { current: null, isExtinct: false, updatedAt: null };
  const json = await r.json();
  const cur = json?.data?.currentPrice || {};
  const data = {
    current: typeof cur.price === "number" ? cur.price : null,
    isExtinct: !!cur.isExtinct,
    updatedAt: cur.priceUpdatedAt || null,
  };
  priceCache.set(id, { t: Date.now(), data });
  return data;
}

function extractPositionsFromDef(def) {
  const raw = [
    def?.preferredPosition1Name,
    def?.preferredPosition2Name,
    def?.preferredPosition3Name,
    def?.positionShort,
    def?.positionName,
    ...(Array.isArray(def?.preferredPositions) ? def.preferredPositions : []),
    ...(Array.isArray(def?.playablePositions) ? def.playablePositions : []),
  ].flat().filter(Boolean);
  return normalizePositions(raw);
}

export async function enrichPlayer(base) {
  const id = Number(base.card_id || base.id);
  if (!id) return base;

  const [def, live] = await Promise.all([fetchDefinition(id), fetchLivePrice(id)]);
  const positions = extractPositionsFromDef(def);
  const image = cardImageFromDef(def) || base.image_url;

  return {
    ...base,
    positions: positions.length ? positions : base.positions,
    image_url: image,
    price: typeof live?.current === "number" ? live.current : base.price ?? null,
  };
}