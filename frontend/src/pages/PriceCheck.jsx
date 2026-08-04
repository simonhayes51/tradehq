import React, { useState } from "react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_URL || "";

// There is no `/api/pricecheck` endpoint on the backend - this page used to
// call it and 404 on every search. The real, working price-lookup flow
// (see src/components/PlayerSearch.jsx) is two calls: resolve the typed
// name to a card_id via `/api/search-players`, then fetch that card's live
// price via `/api/fut-player-price/{card_id}`. We reuse that same pair of
// real endpoints here, just kept lightweight for this single-purpose tool.

// Accepts input like "Lamine Yamal 97" and splits off a trailing rating so
// we can disambiguate when a name matches multiple cards.
const parsePlayerQuery = (input) => {
  const trimmed = input.trim();
  const match = trimmed.match(/^(.*?)\s+(\d{2,3})$/);
  if (match) {
    return { name: match[1].trim(), rating: parseInt(match[2], 10) };
  }
  return { name: trimmed, rating: null };
};

const PriceCheck = () => {
  const [player, setPlayer] = useState("");
  const [platform, setPlatform] = useState("console");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [priceData, setPriceData] = useState(null);

  const fetchPrice = async () => {
    if (!player) {
      setError("Please enter a player name & rating, e.g. 'Lamine Yamal 97'");
      return;
    }
    setLoading(true);
    setError("");
    setPriceData(null);

    try {
      const { name, rating } = parsePlayerQuery(player);
      if (!name) {
        setError("Please enter a player name & rating, e.g. 'Lamine Yamal 97'");
        return;
      }

      const searchResponse = await axios.get(`${API_BASE}/api/search-players`, {
        params: { q: name },
        withCredentials: true,
      });
      const players = searchResponse.data?.players || [];
      if (players.length === 0) {
        setError(`No player found matching "${name}".`);
        return;
      }
      const match = (rating != null ? players.find((p) => p.rating === rating) : null) || players[0];

      const priceResponse = await axios.get(`${API_BASE}/api/fut-player-price/${match.card_id}`, {
        params: { platform },
        withCredentials: true,
      });
      const currentPrice = priceResponse.data?.data?.currentPrice;

      setPriceData({
        player: match.name,
        rating: match.rating,
        platform: platform === "console" ? "Console" : "PC",
        price: currentPrice && !currentPrice.isExtinct && currentPrice.price != null ? currentPrice.price : "N/A",
        source: "Futbin",
      });
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to fetch player data. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto bg-gray-900 text-white p-6 rounded-2xl shadow-lg border border-gray-700">
      <h2 className="text-2xl font-bold mb-4">🔍 FUT Price Check</h2>

      <div className="flex flex-col sm:flex-row gap-3 mb-4">
        <input
          type="text"
          placeholder="e.g. Lamine Yamal 97"
          value={player}
          onChange={(e) => setPlayer(e.target.value)}
          className="flex-1 px-4 py-2 rounded-lg bg-gray-800 border border-gray-600 text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-lime-400"
        />
        <select
          value={platform}
          onChange={(e) => setPlatform(e.target.value)}
          className="px-4 py-2 rounded-lg bg-gray-800 border border-gray-600 text-white focus:outline-none focus:ring-2 focus:ring-lime-400"
        >
          <option value="console">🎮 Console</option>
          <option value="pc">💻 PC</option>
        </select>
        <button
          onClick={fetchPrice}
          disabled={loading}
          className="px-6 py-2 bg-lime-500 hover:bg-lime-600 text-black font-semibold rounded-lg transition-colors disabled:opacity-50"
        >
          {loading ? "Checking..." : "Check Price"}
        </button>
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}

      {priceData && (
        <div className="bg-gray-800 rounded-lg p-5 mt-4 shadow-md border border-gray-700">
          <h3 className="text-xl font-semibold text-lime-400 mb-2">
            {priceData.player} ({priceData.rating})
          </h3>

          <p className="text-lg">
            <span className="font-semibold">Platform:</span>{" "}
            {priceData.platform === "Console" ? "🎮 Console" : "💻 PC"}
          </p>

          <p className="text-lg">
            <span className="font-semibold">Price:</span>{" "}
            {priceData.price !== "N/A" ? `${priceData.price} 🪙` : "N/A"}
          </p>

          <div className="text-gray-400 text-sm mt-3">
            Data from {priceData.source}
          </div>
        </div>
      )}
    </div>
  );
};

export default PriceCheck;
