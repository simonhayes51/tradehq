import { useEffect, useMemo, useState } from "react";
import { Check, Target, X } from "lucide-react";
import { openTrade } from "../api/trades";

const fmt = (value) => new Intl.NumberFormat("en-GB").format(Number(value) || 0);

export default function LogTradeModal({ item, onClose, onSaved }) {
  const suggestedBuy = Number(item?.entryPrice ?? item?.currentBin ?? 0) || 0;
  const suggestedTarget = useMemo(() => {
    const roi = Number(item?.netRoi?.likely ?? item?.expectedRoi ?? 0) || 0;
    return suggestedBuy ? Math.ceil((suggestedBuy * (1 + roi / 100)) / 0.95 / 250) * 250 : 0;
  }, [item, suggestedBuy]);
  const player = item?.player || {};
  const name = player.nickname || player.cardName || player.displayName || player.name || item?.playerName || "Unknown player";

  const [form, setForm] = useState({
    buy: suggestedBuy,
    quantity: 1,
    platform: "ps",
    target_sell: suggestedTarget,
    notes: "",
  });
  const [state, setState] = useState("idle");
  const [error, setError] = useState("");

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  async function submit(event) {
    event.preventDefault();
    setState("saving");
    setError("");
    try {
      const payload = {
        card_id: item?.cardId ? Number(item.cardId) : null,
        player: name,
        version: player.version || "Standard",
        buy: Number(form.buy),
        quantity: Number(form.quantity),
        platform: form.platform,
        target_sell: Number(form.target_sell) || null,
        notes: form.notes,
        recommendation: {
          status: item?.recommendation || null,
          strategy: item?.strategy || item?.strategyName || null,
          confidence: Number(item?.confidence) || null,
          expected_roi: Number(item?.netRoi?.likely ?? item?.expectedRoi) || null,
          buy_below: suggestedBuy || null,
          sell_around: suggestedTarget || null,
          fair_value: Number(item?.fairValue) || null,
          reasoning: item?.reasoning || null,
        },
      };
      const result = await openTrade(payload);
      setState("saved");
      onSaved?.(result);
    } catch (err) {
      setError(err.message || "Could not log this purchase.");
      setState("idle");
    }
  }

  if (!item) return null;

  return (
    <div className="club-modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="club-modal" role="dialog" aria-modal="true" aria-label={`Log purchase for ${name}`}>
        <button className="club-modal-x" onClick={onClose} aria-label="Close"><X size={20} /></button>
        {state === "saved" ? (
          <div className="club-success">
            <span><Check size={28} /></span>
            <h2>Purchase logged</h2>
            <p>{name} is now being tracked in your Club.</p>
            <button onClick={onClose}>Done</button>
          </div>
        ) : (
          <form onSubmit={submit}>
            <div className="club-modal-title"><Target size={18} /><div><span>LOG PURCHASE</span><h2>{name}</h2><p>{player.rating} {player.position} · {player.version || "Card"}</p></div></div>
            <div className="club-form-grid">
              <label><span>Buy price</span><input type="number" min="1" required value={form.buy} onChange={(e) => setForm({ ...form, buy: e.target.value })} /></label>
              <label><span>Quantity</span><input type="number" min="1" max="100" required value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></label>
              <label><span>Target sale</span><input type="number" min="1" value={form.target_sell} onChange={(e) => setForm({ ...form, target_sell: e.target.value })} /></label>
              <label><span>Platform</span><select value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })}><option value="ps">PlayStation</option><option value="xbox">Xbox</option><option value="pc">PC</option></select></label>
              <label className="wide"><span>Notes <em>optional</em></span><textarea rows="3" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} placeholder="Why you bought, planned hold, anything useful…" /></label>
            </div>
            <div className="club-preview"><div><span>Total invested</span><strong>{fmt(Number(form.buy) * Number(form.quantity))}</strong></div><div><span>Suggested target</span><strong>{fmt(form.target_sell)}</strong></div></div>
            {error ? <p className="club-error">{error}</p> : null}
            <div className="club-modal-actions"><button type="button" className="secondary" onClick={onClose}>Cancel</button><button type="submit" disabled={state === "saving"}>{state === "saving" ? "Saving…" : "Save purchase"}</button></div>
          </form>
        )}
      </section>
    </div>
  );
}
