import React from "react";

export default function PlayerCard({ player, graphUrl }) {
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 shadow-lg">
      <div className="flex items-center gap-6">
        <img
          src={player.image}
          alt={player.name}
          className="w-20 h-20 rounded-lg border border-lime-400"
        />

        <div>
          <h2 className="text-2xl font-bold text-white">
            {player.name} <span className="text-lime-400">({player.rating})</span>
          </h2>
          <p className="text-gray-400 text-sm">{player.club} • {player.position}</p>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-2 gap-4">
        <div className="bg-gray-800 p-4 rounded-lg">
          <p className="text-xs text-gray-400">Price</p>
          <p className="text-xl font-bold text-lime-400">{player.price} 🪙</p>
        </div>
        <div className="bg-gray-800 p-4 rounded-lg">
          <p className="text-xs text-gray-400">Price Range</p>
          <p className="text-lg font-medium text-white">{player.range}</p>
        </div>
        <div className="bg-gray-800 p-4 rounded-lg">
          <p className="text-xs text-gray-400">Trend</p>
          <p className="text-lg font-medium">{player.trend}</p>
        </div>
        <div className="bg-gray-800 p-4 rounded-lg">
          <p className="text-xs text-gray-400">Updated</p>
          <p className="text-sm text-white">{player.updated}</p>
        </div>
      </div>

      {graphUrl && (
        <div className="mt-6">
          <img
            src={graphUrl}
            alt="Price Graph"
            className="rounded-lg border border-gray-700"
          />
        </div>
      )}
    </div>
  );
}
