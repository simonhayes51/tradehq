
import React, { useEffect, useState } from "react";
const API_BASE = import.meta.env.VITE_API_URL || "";

function fmtUK(iso) {
  try {
    return new Date(iso).toLocaleString("en-GB", { timeZone: "Europe/London", hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" });
  } catch { return iso; }
}

export default function NextPromoWidget() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let alive = true;
    fetch(`${API_BASE}/api/events/next`).then(r => r.json()).then(j => { if (alive) setData(j); }).catch(e => setErr(String(e)));
    return () => { alive = false; };
  }, []);
  if (err) return <div className="p-3 rounded-xl bg-zinc-900 text-red-400">{err}</div>;
  return (
    <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 shadow-lg flex items-center justify-between">
      <div>
        <div className="text-sm uppercase tracking-widest text-zinc-400">Next Promo</div>
        <div className="text-xl font-semibold text-white mt-1">{data ? data.name : "Loading…"}</div>
        <div className="text-zinc-400 text-sm mt-1">
          {data ? `${fmtUK(data.start_at)} UK` : ""} {data?.confidence ? <span className="ml-2 px-2 py-0.5 rounded bg-zinc-800 text-xs uppercase">{data.confidence}</span> : null}
        </div>
      </div>
      <div className="text-right text-zinc-400 text-xs">{data?.kind ? data.kind.toUpperCase() : ""}</div>
    </div>
  );
}
