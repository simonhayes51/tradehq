import { useEffect, useMemo, useState } from "react";
import { Award, BarChart3, CheckCircle2, Clock3, Coins, Plus, Target, TrendingUp, Trophy, X } from "lucide-react";
import { closeTrade, getOpenTrades, getProfitTimeline, getTradeHistory, getTradingPerformance } from "../../api/trades";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import RecommendationBadge from "../../components/RecommendationBadge";
import "./club.css";

const pct = (value) => `${Number(value || 0).toFixed(1)}%`;
const date = (value) => value ? new Date(value).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "—";

export default function Club() {
  const [tab, setTab] = useState("open");
  const [open, setOpen] = useState([]);
  const [history, setHistory] = useState([]);
  const [performance, setPerformance] = useState({});
  const [timeline, setTimeline] = useState([]);
  const [closing, setClosing] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [openData, historyData, performanceData, timelineData] = await Promise.all([
        getOpenTrades(), getTradeHistory(), getTradingPerformance(), getProfitTimeline(30),
      ]);
      setOpen(openData.trades || []);
      setHistory(historyData.trades || []);
      setPerformance(performanceData || {});
      setTimeline(timelineData.items || []);
    } catch (err) {
      setError(err.message || "Could not load your Club.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  const invested = useMemo(() => open.reduce((sum, trade) => sum + Number(trade.buy || 0) * Number(trade.quantity || 1), 0), [open]);
  const achievements = useMemo(() => buildAchievements(performance), [performance]);

  if (loading) return <div className="club-page"><div className="club-loading">Loading your Club…</div></div>;

  return (
    <div className="club-page">
      <header className="club-hero">
        <div><span>MY CLUB</span><h1>Your trades. Your results.</h1><p>Track every purchase, close sales properly and learn which FutHub calls actually make you coins.</p></div>
        <a href="#/v2" className="club-add"><Plus size={18} /> Find a trade</a>
      </header>

      {error ? <div className="club-alert">{error}<button onClick={load}>Try again</button></div> : null}

      <section className="club-kpis">
        <Kpi icon={<Coins />} label="Profit today" value={<CoinValue value={performance.profit_today} signed />} />
        <Kpi icon={<TrendingUp />} label="This week" value={<CoinValue value={performance.profit_week} signed />} />
        <Kpi icon={<Trophy />} label="Win rate" value={pct(performance.win_rate)} />
        <Kpi icon={<BarChart3 />} label="Average ROI" value={pct(performance.average_roi)} />
      </section>

      <section className="club-summary-grid">
        <div className="club-panel chart-panel"><PanelTitle eyebrow="30 DAY FORM" title="30-day profit" /><ProfitChart items={timeline} /></div>
        <div className="club-panel"><PanelTitle eyebrow="AT A GLANCE" title="Trading account" /><div className="account-stats"><Stat label="Open positions" value={performance.open_positions || 0} /><Stat label="Coins invested" value={<CoinValue value={invested} />} /><Stat label="Total profit" value={<CoinValue value={performance.total_profit} signed />} /><Stat label="EA tax paid" value={<CoinValue value={performance.total_ea_tax} />} /><Stat label="Average hold" value={hold(performance.average_hold_hours)} /><Stat label="Closed trades" value={performance.closed_trades || 0} /></div></div>
      </section>

      <div className="club-tabs"><button className={tab === "open" ? "active" : ""} onClick={() => setTab("open")}>Open trades <span>{open.length}</span></button><button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>Sold players <span>{history.length}</span></button><button className={tab === "performance" ? "active" : ""} onClick={() => setTab("performance")}>Performance</button><button className={tab === "achievements" ? "active" : ""} onClick={() => setTab("achievements")}>Achievements</button></div>

      {tab === "open" ? <OpenTrades trades={open} onClose={setClosing} /> : null}
      {tab === "history" ? <History trades={history} /> : null}
      {tab === "performance" ? <Performance data={performance} /> : null}
      {tab === "achievements" ? <Achievements items={achievements} /> : null}

      {closing ? <CloseModal trade={closing} onCancel={() => setClosing(null)} onClosed={async () => { setClosing(null); await load(); }} /> : null}
    </div>
  );
}

function OpenTrades({ trades, onClose }) {
  if (!trades.length) return <EmptyState icon={<Target />} title="No open trades yet" text="Open a recommendation on the dashboard and press Log purchase. It will appear here instantly." action={<a href="#/v2">Browse recommendations</a>} />;
  return <section className="trade-grid">{trades.map((trade) => { const qty = Number(trade.quantity || 1); const target = Number(trade.target_sell || 0); const projected = target ? (Math.floor(target * .95) - Number(trade.buy)) * qty : 0; const liveStatus = trade.current_recommendation_status ? trade.current_recommendation_status.toLowerCase() : null; return <article className="trade-card" key={trade.trade_id}><div className="trade-top"><div><span>{trade.version || "Card"}</span><h3>{trade.player}</h3></div><b>OPEN</b></div><div className="trade-numbers"><Stat label="Bought" value={<CoinValue value={trade.buy} />} /><Stat label="Quantity" value={qty} /><Stat label="Target" value={<CoinValue value={target || null} />} /><Stat label="Projected" value={<CoinValue value={projected} signed />} /></div>{liveStatus ? <div className="trade-verdict"><RecommendationBadge recommendation={liveStatus} /><p>{trade.current_recommendation_reasoning}</p></div> : null}<div className="trade-meta"><span><Clock3 size={14} /> {date(trade.bought_at || trade.timestamp)}</span><span>{String(trade.platform || "ps").toUpperCase()}</span></div><button className="close-trade" onClick={() => onClose(trade)}>Close trade</button></article>; })}</section>;
}

function History({ trades }) {
  if (!trades.length) return <EmptyState icon={<CheckCircle2 />} title="No completed sales" text="When you close a trade, the result and EA tax will be stored here." />;
  return <div className="history-table"><div className="history-head"><span>Player</span><span>Bought</span><span>Sold</span><span>Profit</span><span>Date</span></div>{trades.map((trade) => <div className="history-row" key={trade.trade_id}><div><strong>{trade.player}</strong><small>{trade.version || "Card"} · x{trade.quantity || 1}</small></div><CoinValue value={trade.buy} /><CoinValue value={trade.sell} /><strong className={Number(trade.profit) >= 0 ? "positive" : "negative"}><CoinValue value={trade.profit} signed /></strong><span>{date(trade.sold_at)}</span></div>)}</div>;
}

function Performance({ data }) {
  const strategies = data.strategies || [];
  const confidence = data.confidence_accuracy || [];
  return <section className="performance-grid"><div className="club-panel"><PanelTitle eyebrow="WHAT WORKS" title="Strategy performance" />{strategies.length ? strategies.map((row) => <div className="performance-row" key={row.strategy}><div><strong>{label(row.strategy)}</strong><span>{row.wins}/{row.trades} wins</span></div><b><CoinValue value={row.profit} signed /></b><em>{pct(row.roi)}</em></div>) : <p className="muted">Close a few recommendation-backed trades to compare strategies.</p>}</div><div className="club-panel"><PanelTitle eyebrow="MODEL CHECK" title="Confidence accuracy" />{confidence.length ? confidence.map((row) => <div className="performance-row" key={row.band}><div><strong>{row.band}% confidence</strong><span>{row.wins}/{row.trades} wins</span></div><b><CoinValue value={row.average_profit} signed /> avg</b></div>) : <p className="muted">Confidence tracking begins when recommendation trades are completed.</p>}</div></section>;
}

function Achievements({ items }) { return <section className="achievement-grid">{items.map((item) => <article className={item.unlocked ? "achievement unlocked" : "achievement"} key={item.title}><span>{item.icon}</span><div><h3>{item.title}</h3><p>{item.text}</p></div>{item.unlocked ? <CheckCircle2 size={18} /> : <Award size={18} />}</article>)}</section>; }

function CloseModal({ trade, onCancel, onClosed }) {
  const [sell, setSell] = useState(trade.target_sell || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const qty = Number(trade.quantity || 1);
  const tax = Math.ceil(Number(sell || 0) * .05) * qty;
  const profit = (Math.floor(Number(sell || 0) * .95) - Number(trade.buy || 0)) * qty;
  async function submit(event) { event.preventDefault(); setSaving(true); setError(""); try { await closeTrade(trade.trade_id, Number(sell)); await onClosed(); } catch (err) { setError(err.message || "Could not close trade."); setSaving(false); } }
  return <div className="club-modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onCancel()}><section className="club-modal close-modal"><button className="club-modal-x" onClick={onCancel}><X size={20} /></button><form onSubmit={submit}><span className="eyebrow">COMPLETE SALE</span><h2>{trade.player}</h2><label><span>Sale price</span><input autoFocus type="number" min="1" required value={sell} onChange={(e) => setSell(e.target.value)} /></label><div className="close-breakdown"><Stat label="EA tax" value={<CoinValue value={tax} />} /><Stat label="Net profit" value={<CoinValue value={profit} signed />} /></div>{error ? <p className="club-error">{error}</p> : null}<div className="club-modal-actions"><button type="button" className="secondary" onClick={onCancel}>Cancel</button><button disabled={saving}>{saving ? "Saving…" : "Complete sale"}</button></div></form></section></div>;
}

function ProfitChart({ items }) { const values = items.map((x) => Number(x.cumulative_profit || 0)); const min = Math.min(0, ...values), max = Math.max(1, ...values), range = max - min || 1; const points = values.map((value, index) => `${items.length <= 1 ? 0 : index / (items.length - 1) * 100},${90 - ((value - min) / range) * 75}`).join(" "); return <div className="profit-chart"><svg viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points={points || "0,90 100,90"} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" /></svg><div><span>{items[0]?.day ? date(items[0].day) : "30 days ago"}</span><strong><CoinValue value={values.at(-1) || 0} signed /></strong><span>Today</span></div></div>; }
function Kpi({ icon, label, value }) { return <article className="club-kpi"><span>{icon}</span><div><small>{label}</small><strong>{value}</strong></div></article>; }
function Stat({ label, value }) { return <div className="club-stat"><span>{label}</span><strong>{value}</strong></div>; }
function PanelTitle({ eyebrow, title }) { return <div className="panel-title"><span>{eyebrow}</span><h2>{title}</h2></div>; }
function hold(hours) { const n = Number(hours || 0); if (n > 0 && n < 1) return "<1h"; return n < 24 ? `${Math.round(n)}h` : `${(n / 24).toFixed(1)}d`; }
function label(value) { return String(value || "Unlabelled").replaceAll("_", " ").replace(/\b\w/g, (x) => x.toUpperCase()); }
function buildAchievements(p) { const closed = Number(p.closed_trades || 0), wins = Number(p.wins || 0), profit = Number(p.total_profit || 0), winRate = Number(p.win_rate || 0); return [{ icon: "🌱", title: "First Trade", text: "Complete your first profitable sale.", unlocked: wins >= 1 }, { icon: "📚", title: "Getting Serious", text: "Complete 10 trades.", unlocked: closed >= 10 }, { icon: "💰", title: "Coin Maker", text: "Earn 100,000 coins profit.", unlocked: profit >= 100000 }, { icon: "🏆", title: "Millionaire", text: "Earn 1,000,000 coins profit.", unlocked: profit >= 1000000 }, { icon: "🎯", title: "Sharp Shooter", text: "Maintain an 80% win rate across 20 trades.", unlocked: closed >= 20 && winRate >= 80 }, { icon: "🧠", title: "Market Master", text: "Complete 50 trades.", unlocked: closed >= 50 }]; }
