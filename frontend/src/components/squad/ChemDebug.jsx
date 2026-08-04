import React from "react";

export default function ChemDebug({ debug, perPlayerChem, placed }) {
  if (!debug) return null;

  return (
    <div className="fixed bottom-4 right-4 z-40 bg-black/90 border border-green-500/40 rounded-xl p-3 max-w-[520px] max-h-[60vh] overflow-auto text-xs">
      <div className="font-bold text-green-400 mb-2">Chem Debug</div>

      <div className="grid grid-cols-3 gap-2 mb-2">
        <div>
          <div className="text-gray-400 mb-1">Clubs</div>
          {Object.entries(debug.clubs).map(([k, v]) => (
            <div key={`c-${k}`} className="flex justify-between">
              <span>{k}</span><span>{v}</span>
            </div>
          ))}
        </div>
        <div>
          <div className="text-gray-400 mb-1">Nations</div>
          {Object.entries(debug.nations).map(([k, v]) => (
            <div key={`n-${k}`} className="flex justify-between">
              <span>{k}</span><span>{v}</span>
            </div>
          ))}
        </div>
        <div>
          <div className="text-gray-400 mb-1">Leagues</div>
          {Object.entries(debug.leagues).map(([k, v]) => (
            <div key={`l-${k}`} className="flex justify-between">
              <span>{k}</span><span>{v}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="mb-2 text-gray-300">
        <div>In-position contributors: {debug.inPosContributors.join(", ") || "—"}</div>
        <div>Icons (3/3): {debug.icons.join(", ") || "—"}</div>
        <div>Heroes (3/3): {debug.heroes.join(", ") || "—"}</div>
      </div>

      <div className="mt-2">
        <div className="text-gray-400 mb-1">Players</div>
        <table className="w-full text-left border border-gray-700">
          <thead>
            <tr className="[&>th]:px-2 [&>th]:py-1 text-gray-400">
              <th>Name</th>
              <th>Slot</th>
              <th>InPos</th>
              <th>Pos</th>
              <th>Club</th>
              <th>Nation</th>
              <th>League</th>
              <th>Chem</th>
            </tr>
          </thead>
          <tbody>
            {debug.rows.map((r) => (
              <tr key={r.id} className="[&>td]:px-2 [&>td]:py-1 border-t border-gray-800">
                <td>{r.name}</td>
                <td>{r.slot}</td>
                <td className={r.inPos ? "text-green-400" : "text-red-400"}>{String(r.inPos)}</td>
                <td>{r.positions}</td>
                <td>{r.club || "—"}</td>
                <td>{r.nation || "—"}</td>
                <td>{r.league || "—"}</td>
                <td>{perPlayerChem[r.id] ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}