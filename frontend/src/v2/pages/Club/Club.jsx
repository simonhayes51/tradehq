import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Award, AlertTriangle, BarChart3, CheckCircle2, Clock3, Coins, Copy, Download, Loader2, Plus, Target, TrendingUp, Trophy, X } from "lucide-react";
import { closeTrade, getOpenTrades, getProfitTimeline, getTradeHistory, getTradingPerformance, shareSummaryCard, shareTradeCard } from "../../api/trades";
import { copyImageToClipboard, copyText, downloadDataUrlAsFile, slugFilename } from "../../lib/shareCard";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import RecommendationBadge from "../../components/RecommendationBadge";
import { versionLabel } from "../../lib/format";
import { eaTax, evaluateOpenTrade, netProfit, MIN_WIN_RATE_SAMPLE, winRateDisplay } from "../../lib/tradeMath";
import "./club.css";

const pct = (value) => `${Number(value || 0).toFixed(1)}%`;
const date = (value) => value ? new Date(value).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "—";

// How many rows of a list are rendered before "Show more". /history is
// capped at 500 rows server-side (app/routers/v2/trades.py's Query
// bound), and an Elite member with a year of trading will genuinely hit
// figures in the hundreds - which as one unpaginated list is a real cost
// on desktop and unusable on a phone.
const PAGE_SIZE = 20;

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
      setError(err.message || "Could not load your portfolio.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  const invested = useMemo(() => open.reduce((sum, trade) => sum + Number(trade.buy || 0) * Number(trade.quantity || 1), 0), [open]);
  const achievements = useMemo(() => buildAchievements(performance), [performance]);
  const winRate = useMemo(
    () => winRateDisplay(performance.wins, performance.closed_trades),
    [performance.wins, performance.closed_trades],
  );

  if (loading) return <div className="club-page"><div className="club-loading">Loading your portfolio…</div></div>;

  return (
    <div className="club-page">
      <header className="club-hero">
        <div><span>PORTFOLIO</span><h1>Your trades. Your results.</h1><p>Track every purchase, close sales properly and learn which EAFC Live calls actually make you coins.</p></div>
        <Link to="/opportunities" className="club-add"><Plus size={18} /> Find a trade</Link>
      </header>

      {error ? <div className="club-alert">{error}<button onClick={load}>Try again</button></div> : null}

      <section className="club-kpis">
        <Kpi icon={<Coins />} label="Profit today" value={<CoinValue value={performance.profit_today} signed />} />
        <Kpi icon={<TrendingUp />} label="This week" value={<CoinValue value={performance.profit_week} signed />} />
        {/* A "100% win rate" derived from a single closed trade is not a
            statistic, it is a coin flip with a percent sign on it - and
            it is the first number a new member sees about their own
            record. Below MIN_WIN_RATE_SAMPLE closed trades we show the
            raw count instead, and say why. */}
        <Kpi
          icon={<Trophy />}
          label={winRate.isRate ? "Win rate" : "Profitable trades"}
          value={winRate.display}
          note={winRate.isRate || winRate.sample === 0
            ? null
            : `A win rate needs ${MIN_WIN_RATE_SAMPLE} closed trades to mean anything`}
        />
        <Kpi icon={<BarChart3 />} label="Average ROI" value={pct(performance.average_roi)} />
      </section>

      <section className="club-summary-grid">
        <div className="club-panel chart-panel"><PanelTitle eyebrow="30 DAY FORM" title="30-day profit" /><ProfitChart items={timeline} /></div>
        <div className="club-panel"><PanelTitle eyebrow="AT A GLANCE" title="Trading account" /><div className="account-stats"><Stat label="Open positions" value={performance.open_positions || 0} /><Stat label="Coins invested" value={<CoinValue value={invested} />} /><Stat label="Total profit" value={<CoinValue value={performance.total_profit} signed />} /><Stat label="EA tax paid" value={<CoinValue value={performance.total_ea_tax} />} /><Stat label="Average hold" value={hold(performance.average_hold_hours)} /><Stat label="Closed trades" value={performance.closed_trades || 0} /></div></div>
      </section>

      <SummaryShare />

      <div className="club-tabs" role="tablist" aria-label="Portfolio sections"><button type="button" role="tab" aria-selected={tab === "open"} className={tab === "open" ? "active" : ""} onClick={() => setTab("open")}>Open trades <span>{open.length}</span></button><button type="button" role="tab" aria-selected={tab === "history"} className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>Sold players <span>{history.length}</span></button><button type="button" role="tab" aria-selected={tab === "performance"} className={tab === "performance" ? "active" : ""} onClick={() => setTab("performance")}>Performance</button><button type="button" role="tab" aria-selected={tab === "achievements"} className={tab === "achievements" ? "active" : ""} onClick={() => setTab("achievements")}>Achievements</button></div>

      {tab === "open" ? <OpenTrades trades={open} onClose={setClosing} /> : null}
      {tab === "history" ? <History trades={history} /> : null}
      {tab === "performance" ? <Performance data={performance} /> : null}
      {tab === "achievements" ? <Achievements items={achievements} /> : null}

      {closing ? <CloseModal trade={closing} onCancel={() => setClosing(null)} onClosed={async () => { setClosing(null); await load(); }} /> : null}
    </div>
  );
}

const SUMMARY_PERIODS = [
  { value: "today", label: "Today" },
  { value: "week", label: "This Week" },
  { value: "all", label: "All-Time" },
];

function SummaryShare() {
  const [period, setPeriod] = useState("week");
  const [format, setFormat] = useState("wide");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copyUnsupported, setCopyUnsupported] = useState(false);

  async function withResult(action) {
    setBusy(true);
    setError("");
    setCopyUnsupported(false);
    try {
      const result = await shareSummaryCard(period, format);
      await action(result);
    } catch (err) {
      if (String(err.message || "").includes("copying images")) setCopyUnsupported(true);
      else setError(err.message || "Couldn't create the share image.");
    } finally {
      setBusy(false);
    }
  }

  const download = () => withResult(async ({ image_data_url, caption }) => {
    await downloadDataUrlAsFile(image_data_url, `eafc-live-${period}-recap-${format}.png`);
    await copyText(caption);
  });
  const copy = () => withResult(async ({ image_data_url }) => copyImageToClipboard(image_data_url));

  return (
    <section className="club-panel summary-share">
      <div className="summary-share-row">
        <div>
          <PanelTitle eyebrow="SHARE" title="Share your recap" />
          <p className="summary-share-hint">A branded image of your record, win rate and best sale for the period below.</p>
        </div>
        <div className="summary-share-controls">
          <select value={period} onChange={(e) => setPeriod(e.target.value)} disabled={busy}>
            {SUMMARY_PERIODS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
          <FormatToggle value={format} onChange={setFormat} disabled={busy} />
          <button type="button" className="summary-share-btn" disabled={busy} onClick={download} title="Download image (and copy a caption)">
            {busy ? <Loader2 size={16} className="spin" /> : <Download size={16} />} Download
          </button>
          <button type="button" className="summary-share-btn" disabled={busy} onClick={copy} title="Copy image to clipboard - paste straight into Discord">
            {busy ? <Loader2 size={16} className="spin" /> : <Copy size={16} />} Copy
          </button>
        </div>
      </div>
      {error ? <p className="club-error">{error}</p> : null}
      {copyUnsupported ? <p className="club-error">Your browser can't copy images - use Download instead.</p> : null}
    </section>
  );
}

// Wide (900x470, Discord/X) vs Portrait (1080x1350, TikTok/IG) - a plain
// two-button toggle rather than a select, since there are only ever two
// options and this sits inline with other buttons.
function FormatToggle({ value, onChange, disabled }) {
  return (
    <div className="format-toggle" role="group" aria-label="Image format">
      <button type="button" className={value === "wide" ? "active" : ""} disabled={disabled} onClick={() => onChange("wide")}>Wide</button>
      <button type="button" className={value === "portrait" ? "active" : ""} disabled={disabled} onClick={() => onChange("portrait")}>Story</button>
    </div>
  );
}

// Copy for each break-even verdict. The point is that a member can tell
// a real return from a rounding artefact WITHOUT doing the tax
// arithmetic themselves - which is exactly what the Mbappé row
// (2,304,000 bought, 2,425,500 target, "projected profit" 225) asked
// them to do.
const TARGET_STATUS_NOTE = {
  loss: "This target sells at a LOSS once EA takes 5%. It needs to clear the break-even price below.",
  marginal: "This target is barely above break-even after EA's 5% cut - one price tick and it is a loss, not a trade.",
};

function OpenTradeCard({ trade, onClose }) {
  const qty = Number(trade.quantity || 1);
  // One shared calculation - the same function Trade Finder, the
  // analysis panel and the close modal use. See src/v2/lib/tradeMath.js.
  const { breakEven, netProfit: projected, netRoi, status } = evaluateOpenTrade({
    buy: trade.buy,
    target: trade.target_sell,
    quantity: qty,
  });
  const liveStatus = trade.current_recommendation_status ? trade.current_recommendation_status.toLowerCase() : null;
  const warn = status === "loss" || status === "marginal";

  return (
    <article className={`trade-card${warn ? " trade-card-warn" : ""}`} data-testid="open-trade" data-target-status={status}>
      <div className="trade-top">
        <div><span>{versionLabel(trade.version)}</span><h3>{trade.player}</h3></div>
        <b>OPEN</b>
      </div>
      <div className="trade-numbers">
        <Stat label="Bought" value={<CoinValue value={trade.buy} />} />
        <Stat label="Quantity" value={qty} />
        {/* Break-even is now a first-class figure rather than something
            the reader has to infer from a suspiciously small profit. */}
        <Stat label="Break-even sale" value={<CoinValue value={breakEven} />} sub="Lowest listable price that doesn't lose coins" />
        <Stat
          label="Target"
          value={<CoinValue value={Number(trade.target_sell) || null} />}
          // A target is only shown when one was actually recorded. We do
          // not synthesise one from the buy price - a fabricated target
          // is indistinguishable from a researched one on screen.
          sub={Number(trade.target_sell) ? null : "No target recorded"}
        />
        <Stat
          label="Projected net profit"
          value={projected == null ? "—" : <CoinValue value={projected} signed />}
          sub={projected == null
            ? "Needs a target price"
            : `After 5% EA tax${netRoi == null ? "" : ` · ${(netRoi * 100).toFixed(2)}% of stake`}`}
          tone={projected == null ? "" : projected > 0 ? (warn ? "warn" : "positive") : "negative"}
        />
      </div>
      {warn ? (
        <p className="trade-breakeven-warning" role="note">
          <AlertTriangle size={14} aria-hidden="true" /> {TARGET_STATUS_NOTE[status]}
        </p>
      ) : null}
      {liveStatus ? <div className="trade-verdict"><RecommendationBadge recommendation={liveStatus} /><p>{trade.current_recommendation_reasoning}</p></div> : null}
      <div className="trade-meta">
        <span><Clock3 size={14} /> {date(trade.bought_at || trade.timestamp)}</span>
        <span>{String(trade.platform || "ps").toUpperCase()}</span>
      </div>
      <button className="close-trade" onClick={() => onClose(trade)}>Close trade</button>
    </article>
  );
}

function OpenTrades({ trades, onClose }) {
  const [shown, setShown] = useState(PAGE_SIZE);
  if (!trades.length) return <EmptyState icon={<Target />} title="No open trades yet" text="Open a recommendation on the dashboard and press Log purchase. It will appear here instantly." action={<Link to="/opportunities">Browse recommendations</Link>} />;
  const visible = trades.slice(0, shown);
  return <>
    <section className="trade-grid">
      {visible.map((trade) => <OpenTradeCard key={trade.trade_id} trade={trade} onClose={onClose} />)}
    </section>
    <ShowMore total={trades.length} shown={visible.length} onMore={() => setShown((n) => n + PAGE_SIZE)} noun="open trades" />
  </>;
}

// Incremental rendering rather than a real paginator: the list is
// already in memory (one /history or /open response), so what costs
// something is rendering several hundred cards at once - on a phone,
// enough to drop frames on every scroll.
function ShowMore({ total, shown, onMore, noun }) {
  if (shown >= total) return total > PAGE_SIZE ? <p className="club-list-count">Showing all {total} {noun}.</p> : null;
  return (
    <div className="club-show-more">
      <button type="button" onClick={onMore}>Show more</button>
      <span>Showing {shown} of {total} {noun}</span>
    </div>
  );
}

function History({ trades }) {
  const [sharingId, setSharingId] = useState(null);
  const [shareError, setShareError] = useState("");
  const [format, setFormat] = useState("wide");
  const [shareCache, setShareCache] = useState({}); // `${trade_id}:${format}` -> { image_data_url, caption }
  const [shown, setShown] = useState(PAGE_SIZE);

  async function fetchShare(trade) {
    const key = `${trade.trade_id}:${format}`;
    if (shareCache[key]) return shareCache[key];
    const result = await shareTradeCard(trade.trade_id, format);
    setShareCache((prev) => ({ ...prev, [key]: result }));
    return result;
  }

  async function withResult(trade, action) {
    setSharingId(trade.trade_id);
    setShareError("");
    try {
      const result = await fetchShare(trade);
      await action(result);
    } catch (err) {
      setShareError(err.message || "Couldn't create the share image.");
    } finally {
      setSharingId(null);
    }
  }

  const download = (trade) => withResult(trade, async ({ image_data_url, caption }) => {
    await downloadDataUrlAsFile(image_data_url, `eafc-live-${slugFilename(trade.player)}-${format}.png`);
    await copyText(caption);
  });
  const copy = (trade) => withResult(trade, async ({ image_data_url }) => copyImageToClipboard(image_data_url));

  if (!trades.length) return <EmptyState icon={<CheckCircle2 />} title="No completed sales" text="When you close a trade, the result and EA tax will be stored here." />;
  const visible = trades.slice(0, shown);

  // ONE list, rendered as a table on wide screens and as real cards on
  // narrow ones - not a table shrunk until it fits. The previous mobile
  // treatment kept the six-column grid at a 720px min-width inside a
  // horizontal scroller, so a phone got a sideways-scrolling spreadsheet
  // with every column too narrow to read. What must survive on a card is
  // fixed by the same rule everywhere: player, status, buy, sold, net
  // profit, primary action.
  const rows = visible.map((trade) => {
    const busy = sharingId === trade.trade_id;
    const profit = Number(trade.profit);
    const tone = profit >= 0 ? "positive" : "negative";
    const actions = (
      <div className="history-share-actions">
        <button type="button" className="history-share" title="Download a shareable image (and copy a caption)" aria-label={`Download share image for ${trade.player}`} disabled={busy} onClick={() => download(trade)}>
          {busy ? <Loader2 size={15} className="spin" /> : <Download size={15} />}
        </button>
        <button type="button" className="history-share" title="Copy image to clipboard - paste straight into Discord" aria-label={`Copy share image for ${trade.player}`} disabled={busy} onClick={() => copy(trade)}>
          {busy ? <Loader2 size={15} className="spin" /> : <Copy size={15} />}
        </button>
      </div>
    );
    return (
      <div className="history-row" key={trade.trade_id}>
        <div className="history-cell history-player">
          <strong>{trade.player}</strong>
          <small>{versionLabel(trade.version)} · x{trade.quantity || 1}</small>
        </div>
        <div className="history-cell" data-label="Status"><span className="history-status">SOLD</span></div>
        <div className="history-cell" data-label="Bought"><CoinValue value={trade.buy} /></div>
        <div className="history-cell" data-label="Sold"><CoinValue value={trade.sell} /></div>
        <div className="history-cell" data-label="Net profit">
          <strong className={tone}><CoinValue value={trade.profit} signed /></strong>
        </div>
        <div className="history-cell" data-label="Date"><span>{date(trade.sold_at)}</span></div>
        <div className="history-cell history-actions-cell">{actions}</div>
      </div>
    );
  });

  return <>
    <div className="history-share-format"><span>Share image format:</span><FormatToggle value={format} onChange={setFormat} disabled={sharingId !== null} /></div>
    {shareError ? <p className="club-error">{shareError}</p> : null}
    <div className="history-table">
      <div className="history-head" aria-hidden="true">
        <span>Player</span><span>Status</span><span>Bought</span><span>Sold</span><span>Net profit</span><span>Date</span><span />
      </div>
      {rows}
    </div>
    <ShowMore total={trades.length} shown={visible.length} onMore={() => setShown((n) => n + PAGE_SIZE)} noun="completed sales" />
  </>;
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
  // Same module the open-trade card and the backend's
  // close_trade_economics agree on, so the preview here cannot differ
  // from the profit that gets persisted a second later.
  const tax = (eaTax(sell) ?? 0) * qty;
  const profit = netProfit(trade.buy, sell, qty);
  async function submit(event) { event.preventDefault(); setSaving(true); setError(""); try { await closeTrade(trade.trade_id, Number(sell)); await onClosed(); } catch (err) { setError(err.message || "Could not close trade."); setSaving(false); } }
  return <div className="club-modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onCancel()}><section className="club-modal close-modal"><button className="club-modal-x" onClick={onCancel}><X size={20} /></button><form onSubmit={submit}><span className="eyebrow">COMPLETE SALE</span><h2>{trade.player}</h2><label><span>Sale price</span><input autoFocus type="number" min="1" required value={sell} onChange={(e) => setSell(e.target.value)} /></label><div className="close-breakdown"><Stat label="EA tax" value={<CoinValue value={tax} />} /><Stat label="Net profit" value={profit == null ? "—" : <CoinValue value={profit} signed />} tone={profit == null ? "" : profit >= 0 ? "positive" : "negative"} /></div>{error ? <p className="club-error">{error}</p> : null}<div className="club-modal-actions"><button type="button" className="secondary" onClick={onCancel}>Cancel</button><button disabled={saving}>{saving ? "Saving…" : "Complete sale"}</button></div></form></section></div>;
}

function ProfitChart({ items }) { const values = items.map((x) => Number(x.cumulative_profit || 0)); const min = Math.min(0, ...values), max = Math.max(1, ...values), range = max - min || 1; const points = values.map((value, index) => `${items.length <= 1 ? 0 : index / (items.length - 1) * 100},${90 - ((value - min) / range) * 75}`).join(" "); return <div className="profit-chart"><svg viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points={points || "0,90 100,90"} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" /></svg><div><span>{items[0]?.day ? date(items[0].day) : "30 days ago"}</span><strong><CoinValue value={values.at(-1) || 0} signed /></strong><span>Today</span></div></div>; }
function Kpi({ icon, label, value, note }) { return <article className="club-kpi"><span>{icon}</span><div><small>{label}</small><strong>{value}</strong>{note ? <em className="club-kpi-note">{note}</em> : null}</div></article>; }
function Stat({ label, value, sub, tone }) { return <div className={`club-stat${tone ? ` tone-${tone}` : ""}`}><span>{label}</span><strong>{value}</strong>{sub ? <small>{sub}</small> : null}</div>; }
function PanelTitle({ eyebrow, title }) { return <div className="panel-title"><span>{eyebrow}</span><h2>{title}</h2></div>; }
function hold(hours) { const n = Number(hours || 0); if (n > 0 && n < 1) return "<1h"; return n < 24 ? `${Math.round(n)}h` : `${(n / 24).toFixed(1)}d`; }
function label(value) { return String(value || "Unlabelled").replaceAll("_", " ").replace(/\b\w/g, (x) => x.toUpperCase()); }
function buildAchievements(p) { const closed = Number(p.closed_trades || 0), wins = Number(p.wins || 0), profit = Number(p.total_profit || 0), winRate = Number(p.win_rate || 0); return [{ icon: "🌱", title: "First Trade", text: "Complete your first profitable sale.", unlocked: wins >= 1 }, { icon: "📚", title: "Getting Serious", text: "Complete 10 trades.", unlocked: closed >= 10 }, { icon: "💰", title: "Coin Maker", text: "Earn 100,000 coins profit.", unlocked: profit >= 100000 }, { icon: "🏆", title: "Millionaire", text: "Earn 1,000,000 coins profit.", unlocked: profit >= 1000000 }, { icon: "🎯", title: "Sharp Shooter", text: "Maintain an 80% win rate across 20 trades.", unlocked: closed >= 20 && winRate >= 80 }, { icon: "🧠", title: "Market Master", text: "Complete 50 trades.", unlocked: closed >= 50 }]; }
