// src/components/squad/chemistry.js
import { normalizePositions, isValidForSlot } from "../../utils/positions";

/** Extract a normalized slot position from various possible keys. */
function getSlotPos(slot) {
  const raw =
    (slot && (slot.pos ?? slot.position ?? slot.label ?? slot.code)) || null;
  if (!raw) return null;
  const [norm] = normalizePositions([raw]);
  return norm || null;
}

/** Robustly extract a player's usable positions from player object + DB fallbacks */
function getPlayerPositions(p) {
  if (Array.isArray(p?.positions) && p.positions.length) {
    return normalizePositions(p.positions);
  }
  const raw = [];
  if (p?.position) raw.push(p.position);
  if (p?.altposition) raw.push(...String(p.altposition).split(/[,\s;/|]+/));
  return normalizePositions(raw);
}

/** Prefer stable IDs when available; fall back to lowercased names */
function keyOf(id, name) {
  if (id != null) return `id:${id}`;
  if (name) return `nm:${String(name).toLowerCase()}`;
  return null;
}

// EA FC25 thresholds
const chemFromClub   = (n) => (n >= 7 ? 3 : n >= 4 ? 2 : n >= 2 ? 1 : 0);
const chemFromNation = (n) => (n >= 8 ? 3 : n >= 5 ? 2 : n >= 2 ? 1 : 0);
const chemFromLeague = (n) => (n >= 8 ? 3 : n >= 5 ? 2 : n >= 3 ? 1 : 0);

export function computeChemistry(placed, formation) {
  const perPlayerChem = {};

  const clubMap   = new Map();
  const nationMap = new Map();
  const leagueMap = new Map();

  const icons  = [];
  const heroes = [];
  const inPosContributors = [];

  // ---------- First pass: tally IN-POSITION players ----------
  for (const slot of formation) {
    const slotPos = getSlotPos(slot);
    if (!slotPos) continue;

    const p = placed[slot.key];
    if (!p) continue;

    const positions = getPlayerPositions(p);
    const inPos = isValidForSlot(slotPos, positions);
    if (!inPos) continue;

    const ck = keyOf(p.clubId, p.club);
    const nk = keyOf(p.nationId, p.nation);
    const lk = keyOf(p.leagueId, p.league);

    if (ck) clubMap.set(ck, (clubMap.get(ck) || 0) + 1);
    if (nk) nationMap.set(nk, (nationMap.get(nk) || 0) + 1);
    if (lk) leagueMap.set(lk, (leagueMap.get(lk) || 0) + 1);

    const clubU = String(p.club || "").toUpperCase();
    const leagueL = String(p.league || "").toLowerCase();
    const isIcon = !!p.isIcon || clubU === "ICON" || leagueL === "icons";
    const isHero = !!p.isHero || clubU === "HERO";

    if (isIcon) {
      icons.push(p.id);
      if (nk) nationMap.set(nk, (nationMap.get(nk) || 0) + 2);  // +2 nation
      const ALL = "league:__all__";
      leagueMap.set(ALL, (leagueMap.get(ALL) || 0) + 1);        // +1 all leagues
      if (lk) leagueMap.set(lk, (leagueMap.get(lk) || 0) + 1);  // +1 own league too
    }

    if (isHero) {
      heroes.push(p.id);
      if (nk) nationMap.set(nk, (nationMap.get(nk) || 0) + 1);  // +1 nation
      if (lk) leagueMap.set(lk, (leagueMap.get(lk) || 0) + 2);  // +2 own league
    }

    inPosContributors.push({
      id: p.id,
      name: p.name,
      club: p.club,
      nation: p.nation,
      league: p.league,
      slot: slotPos,
    });
  }

  // ---------- Second pass: per-player chem ----------
  for (const slot of formation) {
    const slotPos = getSlotPos(slot);
    if (!slotPos) continue;

    const p = placed[slot.key];
    if (!p) continue;

    const positions = getPlayerPositions(p);
    const inPos = isValidForSlot(slotPos, positions);
    if (!inPos) {
      perPlayerChem[p.id] = 0;
      continue;
    }

    const ck = keyOf(p.clubId, p.club);
    const nk = keyOf(p.nationId, p.nation);
    const lk = keyOf(p.leagueId, p.league);

    const clubC   = ck ? chemFromClub(clubMap.get(ck) || 0) : 0;
    const nationC = nk ? chemFromNation(nationMap.get(nk) || 0) : 0;

    const ALL = "league:__all__";
    const leagueCount = (lk ? (leagueMap.get(lk) || 0) : 0) + (leagueMap.get(ALL) || 0);
    const leagueC = chemFromLeague(leagueCount);

    let chem = clubC + nationC + leagueC;

    // Icons/Heroes: 3 in position
    const clubU = String(p.club || "").toUpperCase();
    const leagueL = String(p.league || "").toLowerCase();
    const isIcon = !!p.isIcon || clubU === "ICON" || leagueL === "icons";
    const isHero = !!p.isHero || clubU === "HERO";
    if (isIcon || isHero) chem = 3;

    perPlayerChem[p.id] = Math.max(0, Math.min(3, chem));
  }

  const teamChem = Math.min(
    33,
    Object.values(perPlayerChem).reduce((a, b) => a + (b || 0), 0)
  );

  // ---------- Debug ----------
  if (typeof window !== "undefined" && window?.localStorage?.getItem("chem_debug") === "1") {
    const rows = [];
    for (const slot of formation) {
      const slotPos = getSlotPos(slot);
      const p = placed[slot.key];
      if (!p) continue;
      const positions = getPlayerPositions(p);
      rows.push({
        slot: slotPos,
        id: p.id,
        name: p.name,
        inPos: isValidForSlot(slotPos, positions),
        club: p.club,
        nation: p.nation,
        league: p.league,
        sz: positions.join(","),
        chem: perPlayerChem[p.id] ?? 0
      });
    }
    // eslint-disable-next-line no-console
    console.log("[CHEM] tallies:", {
      clubs: Object.fromEntries(clubMap.entries()),
      nations: Object.fromEntries(nationMap.entries()),
      leagues: Object.fromEntries(leagueMap.entries()),
      icons,
      heroes,
      inPosContributors,
      rows,
    }, " teamChem:", teamChem);
  }

  return { perPlayerChem, teamChem };
}