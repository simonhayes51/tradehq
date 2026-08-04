// src/components/squad/PlayerTile.jsx
import React from "react";
import { isValidForSlot } from "../../utils/positions";
import "../../styles/squad.css";

export default function PlayerTile({
  player,
  slotPosition,
  chem = 0,
  draggable = false,
  onDragStart,
}) {
  if (!player) return null;
  const outOfPosition = !isValidForSlot(slotPosition, player.positions);

  return (
    <div
      className="squad-card relative rounded-xl border border-transparent"
      draggable={draggable}
      onDragStart={onDragStart}
      style={{ width: 96, height: 128 }}
      title={`${player.name || ""}${outOfPosition ? " • Out of position" : ""}`}
    >
      {/* Card art */}
      {player.image_url && (
        <img
          className="squad-card__img"
          src={player.image_url}
          alt={player.name}
          referrerPolicy="no-referrer"
        />
      )}

      {/* Subtle frame */}
      <div className="squad-card__frame" />

      {/* Chemistry dot (bigger) */}
      <div
        className={`absolute top-1.5 right-1.5 chem-dot ${
          outOfPosition ? "oop-indicator" : chem >= 3 ? "chem-3" : chem === 2 ? "chem-2" : chem === 1 ? "chem-1" : "chem-0"
        }`}
        style={{ width: 14, height: 14 }}
      />

      {/* Price pill — centered bottom */}
      {typeof player.price === "number" && (
        <div
          className="absolute left-1/2 -translate-x-1/2 bottom-1 price"
          style={{ minWidth: 56, textAlign: "center" }}
        >
          <span className="coin" />
          {player.price.toLocaleString()}
        </div>
      )}
    </div>
  );
}