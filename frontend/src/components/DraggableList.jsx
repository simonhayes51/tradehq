import React, { useState, useRef } from "react";
import { GripVertical } from "lucide-react";

/**
 * DraggableList
 * props:
 * - items: string[] (keys)
 * - renderItem: (key) => ReactNode
 * - onChange: (newOrder: string[]) => void
 * - disabled?: boolean
 */
export default function DraggableList({ items, renderItem, onChange, disabled }) {
  const [dragIndex, setDragIndex] = useState(null);
  const [overIndex, setOverIndex] = useState(null);
  const listRef = useRef(null);

  const handleDragStart = (idx) => (e) => {
    if (disabled) return;
    setDragIndex(idx);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(idx));
  };

  const handleDragOver = (idx) => (e) => {
    if (disabled) return;
    e.preventDefault(); // allow drop
    setOverIndex(idx);
  };

  const handleDrop = (idx) => (e) => {
    if (disabled) return;
    e.preventDefault();
    const from = dragIndex ?? Number(e.dataTransfer.getData("text/plain"));
    if (Number.isInteger(from) && from !== idx) {
      const next = [...items];
      const [moved] = next.splice(from, 1);
      next.splice(idx, 0, moved);
      onChange(next);
    }
    setDragIndex(null);
    setOverIndex(null);
  };

  const handleDragEnd = () => {
    setDragIndex(null);
    setOverIndex(null);
  };

  return (
    <ul ref={listRef} className="space-y-2">
      {items.map((key, idx) => (
        <li
          key={key}
          draggable={!disabled}
          onDragStart={handleDragStart(idx)}
          onDragOver={handleDragOver(idx)}
          onDrop={handleDrop(idx)}
          onDragEnd={handleDragEnd}
          className={`flex items-center gap-3 p-3 rounded-xl border transition-colors
            ${overIndex === idx ? "border-gray-600 bg-gray-800/60" : "border-gray-800 bg-gray-900/70"}
            ${disabled ? "opacity-60" : "hover:border-gray-700"}
          `}
        >
          <span className="shrink-0 text-gray-400">
            <GripVertical size={16} />
          </span>
          <div className="flex-1">{renderItem(key)}</div>
        </li>
      ))}
    </ul>
  );
}
