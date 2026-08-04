// src/pages/SquadBuilder.jsx 
import React, { useEffect, useMemo, useRef, useState } from "react";
import { Search, X, Star, Trophy, DollarSign, FlaskConical, Users, Plus } from "lucide-react";
import Pitch from "../components/squad/Pitch";
import { FORMATIONS } from "../components/squad/formations";
import { VERTICAL_COORDS } from "../components/squad/formations_vertical";
import { computeChemistry } from "../components/squad/chemistry";
import { searchPlayers, enrichPlayer } from "../api/squadApi";
import { isValidForSlot } from "../utils/positions";
import "../styles/squad.css";

const cls = (...xs) => xs.filter(Boolean).join(" ");
const coins = (n) => (typeof n === "number" ? `${n.toLocaleString()}c` : "—");

// --- Helpers to rotate a horizontal formation to vertical if needed
function rotateSlot(slot) {
  return { ...slot, x: slot.y, y: 100 - slot.x };
}
function rotateFormationSlots(slots) {
  return (slots || []).map(rotateSlot);
}
function getVerticalSlots(formationKey) {
  return VERTICAL_COORDS[formationKey] || rotateFormationSlots(FORMATIONS[formationKey] || []);
}

/* =========================
   CARD + EMPTY SLOT UI
========================= */

// Minimal empty-slot (dashed) only when no player is placed
function EnhancedEmptySlot({ position, onClick, isSelected }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cls(
        "w-24 h-32 border-2 border-dashed rounded-xl grid place-items-center cursor-pointer transition-all",
        isSelected
          ? "border-green-400 bg-green-400/10 shadow-lg shadow-green-400/20"
          : "border-gray-600 hover:border-gray-500 hover:bg-gray-800/30"
      )}
      title={`Add ${position}`}
    >
      <div className={cls("text-sm font-bold", isSelected ? "text-green-400" : "text-gray-400")}>
        {position}
      </div>
      <Plus size={16} className={cls("mt-1", isSelected ? "text-green-400" : "text-gray-500")} />
    </button>
  );
}

// Clean, image-focused player card (no outer box), bigger chem dot, centered price pill
function EnhancedPlayerCard({
  player,
  slotPosition,
  onRemove,
  chem = 0,
  size = "md",
  draggable = false,
  onDragStart,
}) {
  if (!player) return null;
  const outOfPosition = !isValidForSlot(slotPosition, player.positions);
  const sizeClasses = size === "sm" ? "w-16 h-20" : "w-24 h-32";

  return (
    <div className="relative">
      <div
        className={cls(
          sizeClasses,
          "relative rounded-xl overflow-hidden shadow-lg hover:shadow-xl transition-transform duration-150 hover:-translate-y-[2px]"
        )}
        draggable={draggable}
        onDragStart={onDragStart}
      >
        {player.image_url && (
          <img
            className="absolute inset-0 w-full h-full object-cover"
            src={player.image_url}
            alt={player.name}
            referrerPolicy="no-referrer"
          />
        )}

        {/* subtle highlight */}
        <div className="absolute inset-0 bg-gradient-to-b from-white/10 to-transparent pointer-events-none" />

        {/* Chemistry dot (bigger) */}
        <div
          className={cls(
            "absolute top-2 right-2 w-3.5 h-3.5 rounded-full shadow-sm ring-1 ring-black/30",
            outOfPosition
              ? "bg-red-500"
              : chem >= 3
              ? "bg-lime-400"
              : chem === 2
              ? "bg-yellow-400"
              : chem === 1
              ? "bg-orange-400"
              : "bg-gray-500"
          )}
          title={`Chemistry: ${outOfPosition ? 0 : chem}/3`}
        />

        {/* Centered price pill at bottom */}
        {typeof player.price === "number" && (
          <div className="absolute -bottom-1 left-1/2 -translate-x-1/2">
            <div className="px-2.5 py-0.5 rounded-full bg-black/85 text-gray-100 text-xs font-semibold border border-white/10">
              {player.price.toLocaleString()}c
            </div>
          </div>
        )}
      </div>

      {/* Remove (kept tiny & subtle) */}
      {onRemove && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          className="absolute -top-1 -right-1 w-5 h-5 bg-red-500 text-white rounded-full opacity-0 hover:opacity-100 focus:opacity-100 transition-opacity duration-150 grid place-items-center shadow-lg"
          title="Remove"
        >
          <X size={10} />
        </button>
      )}

      {/* Out-of-position micro tag */}
      {outOfPosition && (
        <div className="absolute -bottom-5 left-1/2 -translate-x-1/2">
          <span className="text-[10px] text-red-300 bg-red-900/70 px-2 py-[2px] rounded-full border border-red-500/40">
            OOP
          </span>
        </div>
      )}
    </div>
  );
}

/* =========================
   MAIN PAGE
========================= */

export default function SquadBuilder() {
  // formation
  const [formationKey, setFormationKey] = useState("4-3-3");
  const slots = useMemo(() => getVerticalSlots(formationKey), [formationKey]);

  // placed players by slot.key
  const [placed, setPlaced] = useState(() =>
    Object.fromEntries(slots.map((s) => [s.key, null]))
  );

  // when formation changes, preserve matching keys
  useEffect(() => {
    setPlaced((prev) => {
      const next = {};
      for (const s of slots) next[s.key] = prev[s.key] || null;
      return next;
    });
  }, [formationKey, slots]);

  // chemistry
  const { perPlayerChem, teamChem } = useMemo(
    () => computeChemistry(placed, slots),
    [placed, slots]
  );

  // quick stats
  const avgRating = useMemo(() => {
    const ps = Object.values(placed).filter(Boolean);
    if (!ps.length) return 0;
    return Math.round(ps.reduce((a, p) => a + (p.rating || 0), 0) / ps.length);
  }, [placed]);

  const squadPrice = useMemo(
    () =>
      Object.values(placed)
        .filter(Boolean)
        .reduce((a, p) => a + (p.price || 0), 0),
    [placed]
  );

  const playerCount = useMemo(
    () => Object.values(placed).filter(Boolean).length,
    [placed]
  );

  // search
  const [search, setSearch] = useState("");
  const [results, setResults] = useState([]);
  const [searchOpen, setSearchOpen] = useState(null); // slot key being filled
  const debounceRef = useRef();

  // only load players matching the currently selected slot (server supports ?slot=)
  useEffect(() => {
    clearTimeout(debounceRef.current);
    if (!search.trim()) {
      setResults([]);
      return;
    }
    const slotPos =
      searchOpen && slots.find((s) => s.key === searchOpen)?.pos;

    debounceRef.current = setTimeout(async () => {
      const base = await searchPlayers(search, slotPos);
      setResults(base);
    }, 300);

    return () => clearTimeout(debounceRef.current);
  }, [search, searchOpen, slots]);

  async function addPlayerToSlot(basePlayer, slotKey) {
    const full = await enrichPlayer(basePlayer);
    setPlaced((prev) => ({ ...prev, [slotKey]: full }));
    setSearchOpen(null);
    setSearch("");
  }

  // DnD
  function handleDragStart(e, playerId) {
    e.dataTransfer.setData("text/plain", String(playerId));
  }
  function handleDrop(e, slotKey) {
    e.preventDefault();
    const id = Number(e.dataTransfer.getData("text/plain"));
    const base = results.find((x) => x.id === id) || null;
    if (!base) return;
    addPlayerToSlot(base, slotKey);
  }

  function clearSlot(slotKey) {
    setPlaced((prev) => ({ ...prev, [slotKey]: null }));
  }
  function clearAll() {
    setPlaced(Object.fromEntries(slots.map((s) => [s.key, null])));
    setSearchOpen(null);
  }

  // share link
  function shareUrl() {
    const pruned = Object.fromEntries(
      Object.entries(placed).map(([k, v]) => [
        k,
        v
          ? {
              id: v.id,
              name: v.name,
              rating: v.rating,
              club: v.club,
              club_id: v.club_id,
              nation: v.nation,
              nation_id: v.nation_id,
              league: v.league,
              league_id: v.league_id,
              positions: v.positions,
              image_url: v.image_url,
              price: v.price,
              isIcon: v.isIcon,
              isHero: v.isHero,
            }
          : null,
      ])
    );
    const state = { formationKey, placed: pruned };
    const encoded = encodeURIComponent(btoa(JSON.stringify(state)));
    const url = new URL(window.location.href);
    url.searchParams.set("squad", encoded);
    navigator.clipboard.writeText(url.toString());
  }

  // import from ?squad=
  useEffect(() => {
    try {
      const url = new URL(window.location.href);
      const encoded = url.searchParams.get("squad");
      if (!encoded) return;
      const state = JSON.parse(atob(decodeURIComponent(encoded)));
      if (state?.formationKey && (VERTICAL_COORDS[state.formationKey] || FORMATIONS[state.formationKey])) {
        setFormationKey(state.formationKey);
      }
      if (state?.placed && typeof state.placed === "object") {
        setPlaced(state.placed);
      }
    } catch {
      // ignore bad imports
    }
  }, []);

  return (
    <div className="min-h-screen bg-black text-white">
      {/* Header */}
      <header className="sticky top-0 z-20 border-b border-gray-800 bg-gray-950/90 backdrop-blur-sm">
        <div className="mx-auto max-w-[1400px] px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <div className="text-2xl font-black tracking-tight">
              <span className="text-green-400">FUT</span> Squad Builder
            </div>

            <select
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-400"
              value={formationKey}
              onChange={(e) => setFormationKey(e.target.value)}
            >
              {Object.keys({ ...VERTICAL_COORDS, ...FORMATIONS }).map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-3 text-sm">
            <div className="flex items-center gap-2 bg-gray-800/80 border border-gray-700 px-3 py-2 rounded-lg">
              <Star size={16} className="text-yellow-400" />
              <span className="font-semibold">{avgRating}</span>
            </div>
            <div className="flex items-center gap-2 bg-gray-800/80 border border-gray-700 px-3 py-2 rounded-lg">
              <FlaskConical size={16} className="text-blue-400" />
              <span className="font-semibold">{teamChem}/33</span>
            </div>
            <div className="flex items-center gap-2 bg-gray-800/80 border border-gray-700 px-3 py-2 rounded-lg">
              <DollarSign size={16} className="text-green-400" />
              <span className="font-semibold">{coins(squadPrice)}</span>
            </div>
            <button
              className="bg-green-500 hover:bg-green-600 px-4 py-2 rounded-lg font-semibold transition-colors"
              onClick={shareUrl}
              title="Copy shareable link"
            >
              Share Link
            </button>
            <button
              className="bg-gray-700 hover:bg-gray-600 px-4 py-2 rounded-lg font-semibold transition-colors"
              onClick={clearAll}
            >
              Clear
            </button>
          </div>
        </div>
      </header>

      {/* Body */}
      <main className="mx-auto max-w-[1400px] px-6 py-6 grid grid-cols-12 gap-6">
        {/* Pitch */}
        <div className="col-span-8">
          <Pitch height="600px">
            {slots.map((slot) => {
              const pl = placed[slot.key];
              const chem = pl ? perPlayerChem[pl.id] ?? 0 : 0;

              return (
                <div
                  key={slot.key}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => handleDrop(e, slot.key)}
                  className="absolute -translate-x-1/2 -translate-y-1/2"
                  style={{ left: `${slot.x}%`, top: `${slot.y}%` }}
                >
                  {pl ? (
                    <div className="relative">
                      <EnhancedPlayerCard
                        player={pl}
                        slotPosition={slot.pos}
                        chem={chem}
                        draggable
                        onDragStart={(e) => handleDragStart(e, pl.id)}
                        onRemove={() => clearSlot(slot.key)}
                      />
                      {/* action buttons (hidden by default to keep it clean) */}
                      <div className="absolute -bottom-8 left-0 right-0 opacity-0 hover:opacity-100 transition-opacity duration-200">
                        <div className="flex justify-center gap-2 text-xs">
                          <button
                            className="bg-gray-800/90 hover:bg-gray-700 text-white px-2 py-1 rounded border border-gray-600"
                            onClick={() => setSearchOpen(slot.key)}
                          >
                            Swap
                          </button>
                          <button
                            className="bg-red-800/90 hover:bg-red-700 text-white px-2 py-1 rounded border border-red-600"
                            onClick={() => clearSlot(slot.key)}
                          >
                            Remove
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <EnhancedEmptySlot
                      position={slot.pos}
                      isSelected={searchOpen === slot.key}
                      onClick={() => setSearchOpen(slot.key)}
                    />
                  )}
                </div>
              );
            })}
          </Pitch>

          <div className="mt-6 flex items-center gap-6 text-xs text-gray-400">
            <span>💡 Tip: Click an empty slot to search eligible players. Drag cards to rearrange.</span>
            <span>🔴 Out of position → 0 chem</span>
            <span>🟢 Full chem → 3/3</span>
          </div>
        </div>

        {/* Search & Side info */}
        <aside className="col-span-4 space-y-4">
          {/* Search */}
          <div className="bg-gray-900/90 border border-gray-800 rounded-2xl overflow-hidden">
            <div className="p-4 border-b border-gray-800">
              <div className="relative">
                <Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-10 pr-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-400"
                  placeholder={
                    searchOpen ? `Search for ${slots.find(s => s.key === searchOpen)?.pos}…` : "Search name, club, league, nation, position"
                  }
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>

              {searchOpen && (
                <div className="mt-3 flex items-center justify-between bg-gray-800/50 rounded-lg px-3 py-2">
                  <span className="text-sm text-gray-300">
                    Adding to: <span className="font-semibold text-green-400">{searchOpen}</span>
                  </span>
                  <button
                    onClick={() => {
                      setSearchOpen(null);
                      setSearch("");
                    }}
                    className="text-gray-400 hover:text-white transition-colors"
                    title="Close search"
                  >
                    <X size={16} />
                  </button>
                </div>
              )}
            </div>

            <div className="max-h-96 overflow-y-auto custom-scrollbar">
              {results.length > 0 ? (
                <div className="p-3 space-y-2">
                  {results.map((p) => {
                    const slotPos = searchOpen ? slots.find((s) => s.key === searchOpen)?.pos : null;
                    const validForSlot = slotPos ? isValidForSlot(slotPos, p.positions) : true;

                    return (
                      <div
                        key={p.id}
                        className={cls(
                          "bg-gray-800 border border-gray-700 rounded-xl p-3 transition-all",
                          searchOpen && validForSlot
                            ? "hover:bg-gray-700 cursor-pointer hover:border-gray-600"
                            : searchOpen && !validForSlot
                            ? "opacity-50 cursor-not-allowed"
                            : "cursor-default"
                        )}
                        onClick={() => searchOpen && validForSlot && addPlayerToSlot(p, searchOpen)}
                      >
                        <div className="flex items-center gap-3">
                          <EnhancedPlayerCard
                            player={p}
                            size="sm"
                            draggable
                            onDragStart={(e) => handleDragStart(e, p.id)}
                            slotPosition={slotPos || p.positions?.[0]}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="font-semibold text-sm truncate">{p.name}</span>
                              {p.isIcon && (
                                <span className="text-xs bg-orange-500/80 text-white px-1.5 py-0.5 rounded font-bold">
                                  ICON
                                </span>
                              )}
                              {p.isHero && (
                                <span className="text-xs bg-purple-500/80 text-white px-1.5 py-0.5 rounded font-bold">
                                  HERO
                                </span>
                              )}
                            </div>
                            <div className="text-xs text-gray-400 truncate">
                              {(p.club || "—") + " • " + (p.nation || "—")}
                            </div>
                            <div className="text-xs flex items-center gap-2">
                              <span className="text-green-400 font-medium">{coins(p.price)}</span>
                            </div>
                            {!validForSlot && searchOpen && (
                              <div className="text-xs text-red-400 mt-1">❌ Cannot play {slotPos}</div>
                            )}
                          </div>

                          {searchOpen && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                if (validForSlot) addPlayerToSlot(p, searchOpen);
                              }}
                              disabled={!validForSlot}
                              className={cls(
                                "px-3 py-1.5 rounded-lg text-xs font-bold transition-all",
                                validForSlot
                                  ? "bg-green-500 hover:bg-green-600 text-white shadow-sm"
                                  : "bg-gray-600 text-gray-300 cursor-not-allowed"
                              )}
                            >
                              Add
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : search ? (
                <div className="p-8 text-center text-gray-400">
                  <Search size={32} className="mx-auto mb-3 opacity-50" />
                  <div className="text-sm">No players found for “{search}”.</div>
                  <div className="text-xs text-gray-500 mt-1">Try a different search term.</div>
                </div>
              ) : (
                <div className="p-8 text-center text-gray-500">
                  <Users size={32} className="mx-auto mb-3 opacity-50" />
                  <div className="text-sm">Start typing to search players…</div>
                  {searchOpen && (
                    <div className="text-xs mt-2 text-green-400">
                      Filtering for {slots.find((s) => s.key === searchOpen)?.pos}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Squad Overview */}
          <div className="bg-gray-900/90 border border-gray-800 rounded-2xl p-4">
            <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
              <Users size={16} className="text-blue-400" />
              Squad Overview
            </h3>
            <div className="space-y-3">
              <div className="bg-gray-800 rounded-lg p-3">
                <div className="flex justify-between items-center mb-2">
                  <span className="text-gray-400 text-xs">Squad Completion</span>
                  <span className="font-bold text-sm">{playerCount}/11</span>
                </div>
                <div className="w-full bg-gray-700 rounded-full h-2">
                  <div
                    className="bg-gradient-to-r from-green-500 to-green-400 h-2 rounded-full transition-all"
                    style={{ width: `${(playerCount / 11) * 100}%` }}
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-gray-800 rounded-lg p-3 text-center">
                  <div className="text-yellow-400 font-bold text-xl">{avgRating}</div>
                  <div className="text-gray-400 text-xs">Avg Rating</div>
                </div>
                <div className="bg-gray-800 rounded-lg p-3 text-center">
                  <div className="text-green-400 font-bold text-xl">{teamChem}</div>
                  <div className="text-gray-400 text-xs">Team Chemistry</div>
                </div>
              </div>
              <div className="bg-gray-800 rounded-lg p-3 text-center">
                <div className="text-blue-400 font-bold text-lg">{squadPrice.toLocaleString()}</div>
                <div className="text-gray-400 text-xs">Total Squad Value (coins)</div>
              </div>
            </div>
          </div>

          {/* Chemistry rules quick reference */}
          <div className="bg-gray-900/90 border border-gray-800 rounded-2xl p-4">
            <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
              <Trophy size={16} className="text-green-400" />
              Chemistry Rules (FC25-style)
            </h3>
            <div className="grid grid-cols-3 gap-2">
              <div className="bg-gray-800 rounded-lg p-2 text-center">
                <div className="text-blue-400 font-semibold text-xs">Club</div>
                <div className="text-gray-400 text-xs mt-1">2/4/7 → +1/+2/+3</div>
              </div>
              <div className="bg-gray-800 rounded-lg p-2 text-center">
                <div className="text-green-400 font-semibold text-xs">Nation</div>
                <div className="text-gray-400 text-xs mt-1">2/5/8 → +1/+2/+3</div>
              </div>
              <div className="bg-gray-800 rounded-lg p-2 text-center">
                <div className="text-purple-400 font-semibold text-xs">League</div>
                <div className="text-gray-400 text-xs mt-1">3/5/8 → +1/+2/+3</div>
              </div>
            </div>
            <div className="text-gray-400 text-xs space-y-1 border-t border-gray-800 pt-3">
              <div>ICON: special nation boosts; 3 chem in position.</div>
              <div>HERO: special league boosts; 3 chem in position.</div>
              <div>OOP: Out of position → 0 chem, no contributions.</div>
            </div>
          </div>
        </aside>
      </main>
    </div>
  );
}
