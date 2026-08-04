import { useEffect, useMemo, useState } from "react";
import { Coins, Minus, Plus, WalletCards } from "lucide-react";
import CoinValue from "../../components/CoinValue";

const STORAGE_KEY = "futhub_coin_balance";

export default function PersonalisedMove({ item, onOpen }) {
  const entry = Number(item?.entryPrice ?? item?.currentBin) || 0;
  const perCardProfit = projectedProfit(item);
  const [balance, setBalance] = useState(() => readBalance());
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(balance));

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, String(balance));
  }, [balance]);

  const maxQuantity = entry > 0 ? Math.max(1, Math.floor(balance / entry)) : 1;
  const [quantity, setQuantity] = useState(1);

  useEffect(() => {
    setQuantity((current) => Math.min(Math.max(1, current), maxQuantity));
  }, [maxQuantity, item?.cardId]);

  const move = useMemo(() => ({
    capital: entry * quantity,
    profit: perCardProfit * quantity,
  }), [entry, perCardProfit, quantity]);

  function saveBalance() {
    const next = Math.max(0, Math.round(Number(String(draft).replace(/[, ]/g, "")) || 0));
    setBalance(next);
    setDraft(String(next));
    setEditing(false);
  }

  if (!item) return null;

  return (
    <section className="personal-move" aria-label="Personalised best move">
      <div className="personal-move-top">
        <div>
          <span><WalletCards size={15}/> YOUR BEST MOVE</span>
          <h2>Buy {quantity}× {displayName(item.player)}</h2>
        </div>
        <div className="coin-balance">
          <span><Coins size={14}/> Your coins</span>
          {editing ? (
            <form onSubmit={(event) => { event.preventDefault(); saveBalance(); }}>
              <input autoFocus inputMode="numeric" value={draft} onChange={(event) => setDraft(event.target.value)} aria-label="Coin balance"/>
              <button type="submit">Save</button>
            </form>
          ) : (
            <button onClick={() => setEditing(true)}><CoinValue value={balance}/> <small>Edit</small></button>
          )}
        </div>
      </div>

      <div className="personal-move-body">
        <div className="quantity-control" aria-label="Quantity">
          <span>Quantity</span>
          <div>
            <button onClick={() => setQuantity((value) => Math.max(1, value - 1))} disabled={quantity <= 1} aria-label="Decrease quantity"><Minus size={17}/></button>
            <strong>{quantity}</strong>
            <button onClick={() => setQuantity((value) => Math.min(maxQuantity, value + 1))} disabled={quantity >= maxQuantity} aria-label="Increase quantity"><Plus size={17}/></button>
          </div>
        </div>
        <MoveNumber label="Buy below" value={<CoinValue value={entry}/>} sub="each"/>
        <MoveNumber label="Capital needed" value={<CoinValue value={move.capital}/>} sub={<><CoinValue value={Math.max(0, balance - move.capital)}/> left</>}/>
        <MoveNumber label="Expected profit" value={<CoinValue value={move.profit} signed/>} sub="after EA tax" positive/>
        <button className="personal-move-action" onClick={() => onOpen({ ...item, suggestedQuantity: quantity, coinBalance: balance })}>View move</button>
      </div>
      {balance < entry && <p className="personal-move-warning">Your saved balance is below this card's entry price. Update your coins or choose a cheaper opportunity.</p>}
    </section>
  );
}

function MoveNumber({ label, value, sub, positive }) {
  return <div className={`personal-move-number ${positive ? "positive" : ""}`}><span>{label}</span><strong>{value}</strong><small>{sub}</small></div>;
}

function readBalance() {
  try {
    const stored = Number(localStorage.getItem(STORAGE_KEY));
    return Number.isFinite(stored) && stored > 0 ? Math.round(stored) : 500000;
  } catch {
    return 500000;
  }
}

function projectedProfit(item) {
  const entry = Number(item?.entryPrice ?? item?.currentBin);
  const roi = Number(item?.netRoi?.likely ?? item?.expectedRoi);
  return entry > 0 && Number.isFinite(roi) ? Math.round(entry * roi / 100) : 0;
}

function displayName(player = {}) {
  return player.displayName || player.cardName || player.nickname || player.name || "Unknown player";
}
