import React, { useState, useEffect } from "react";
import playersData from "../assets/players_temp.json";

export default function PlayerSearch({ onSelect }) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState([]);

  useEffect(() => {
    if (!query.trim()) {
      setSuggestions([]);
      return;
    }

    const matches = playersData
      .filter((p) =>
        `${p.name} ${p.rating}`.toLowerCase().includes(query.toLowerCase())
      )
      .slice(0, 10);

    setSuggestions(matches);
  }, [query]);

  return (
    <div className="relative w-full max-w-xl">
      <input
        type="text"
        placeholder="Search player name..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        className="w-full p-3 rounded-lg bg-gray-900 border border-gray-700 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-lime-400"
      />

      {suggestions.length > 0 && (
        <div className="absolute w-full bg-gray-800 mt-2 rounded-lg shadow-lg z-10">
          {suggestions.map((player, idx) => (
            <button
              key={idx}
              onClick={() => {
                onSelect(player);
                setQuery(`${player.name} ${player.rating}`);
                setSuggestions([]);
              }}
              className="flex items-center gap-3 p-3 w-full hover:bg-gray-700 transition"
            >
              <img
                src={`https://cdn.futbin.com/content/fifa25/img/players/${player.id}.png`}
                alt={player.name}
                className="w-10 h-10 rounded"
                onError={(e) => {
                  e.target.src =
                    "https://cdn.futbin.com/content/fifa25/img/players/placeholder.png";
                }}
              />
              <div className="text-left">
                <p className="text-sm font-semibold text-white">
                  {player.name} <span className="text-lime-400">({player.rating})</span>
                </p>
                <p className="text-xs text-gray-400">{player.club}</p>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
