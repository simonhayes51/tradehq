// src/components/squad/Pitch.jsx
import React from "react";

export default function Pitch({ children, height = "600px" }) {
  return (
    <div
      className="relative rounded-3xl border border-emerald-900 bg-emerald-900/80 overflow-hidden"
      style={{ height }}
    >
      {/* Grass gradient */}
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_50%,rgba(255,255,255,0.06),transparent_55%)]" />

      {/* Touchlines */}
      <div className="absolute inset-4 rounded-2xl border border-emerald-300/30" />

      {/* Halfway & centre */}
      <div className="absolute left-1/2 top-4 bottom-4 w-px bg-emerald-300/30 -translate-x-1/2" />
      <div className="absolute left-1/2 top-1/2 w-24 h-24 rounded-full border border-emerald-300/30 -translate-x-1/2 -translate-y-1/2" />
      <div className="absolute left-1/2 top-1/2 w-2 h-2 rounded-full bg-emerald-300/40 -translate-x-1/2 -translate-y-1/2" />

      {/* Penalty boxes */}
      {/* Top */}
      <div className="absolute left-[18%] right-[18%] top-4 h-28 border border-emerald-300/30 rounded-b-2xl" />
      <div className="absolute left-[32%] right-[32%] top-4 h-14 border border-emerald-300/30 rounded-b-xl" />
      {/* Bottom */}
      <div className="absolute left-[18%] right-[18%] bottom-4 h-28 border border-emerald-300/30 rounded-t-2xl" />
      <div className="absolute left-[32%] right-[32%] bottom-4 h-14 border border-emerald-300/30 rounded-t-xl" />

      {/* Children (absolute at slot coords) */}
      <div className="absolute inset-0">{children}</div>
    </div>
  );
}