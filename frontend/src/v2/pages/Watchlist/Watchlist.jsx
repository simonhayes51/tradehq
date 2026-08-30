import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Bell, RefreshCw, Star, Trash2 } from "lucide-react";
import { useQueryClient, useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { deleteWatch, refreshWatch, getAlerts, createAlert, deleteAlert } from "../../../api/watchlist";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import { useWatchlist } from "../../hooks/useWatchlist";
import { PageHead } from "../Players/Players";
import { versionLabel } from "../../lib/format";
import "../../styles/v2-destinations.css";

export default function Watchlist() {
  const queryClient = useQueryClient();
  const query = useWatchlist();
  const items = Array.isArray(query.data) ? query.data : query.data?.items || [];
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["v2", "watchlist"] });
  // Every one of these used to be a bare `await x(); refresh();`. A
  // failure left the row on screen with no explanation, so a removal that
  // did not happen looked identical to one that did - the same class of
  // silent failure that made alerts feel broken. Each now reports.
  const [busyItem, setBusyItem] = useState(null);
  async function remove(id, name) {
    setBusyItem(id);
    try {
      await deleteWatch(id);
      refresh();
      toast.success(`${name || "Card"} removed from your watchlist`);
    } catch {
      toast.error("Couldn't remove that card. Try again.");
    } finally {
      setBusyItem(null);
    }
  }
  async function update(id) {
    setBusyItem(id);
    try {
      await refreshWatch(id);
      refresh();
    } catch {
      toast.error("Couldn't refresh that price right now.");
    } finally {
      setBusyItem(null);
    }
  }

  // Price/liquidity threshold alerts - separate from the watch list
  // itself (backend: /api/watchlist-alerts, a live poll loop already
  // running in main.py). v1 has had this UI for a while; v2 didn't.
  const alertsQuery = useQuery({
    queryKey: ["v2", "watchlist", "alerts"],
    queryFn: async () => {
      const data = await getAlerts({ __skipAuthRedirect: true, __noRetry: true, timeout: 8_000 });
      return Array.isArray(data?.items) ? data.items : [];
    },
    retry: false,
    staleTime: 30_000,
  });
  const alerts = alertsQuery.data || [];
  const refreshAlerts = () => queryClient.invalidateQueries({ queryKey: ["v2", "watchlist", "alerts"] });

  const [alertsOpen, setAlertsOpen] = useState(false);
  const [alertTarget, setAlertTarget] = useState(null);
  const [alertForm, setAlertForm] = useState({ metric: "price", rise_pct: 10, fall_pct: 10, cooloff_minutes: 30 });
  const [busyAlert, setBusyAlert] = useState(false);

  function openAlertModal(item) {
    setAlertTarget(item);
    setAlertForm({ metric: "price", rise_pct: 10, fall_pct: 10, cooloff_minutes: 30 });
  }

  async function handleCreateAlert(e) {
    e.preventDefault();
    if (!alertTarget) return;
    setBusyAlert(true);
    try {
      await createAlert({
        card_id: Number(alertTarget.card_id),
        platform: String(alertTarget.platform || "ps").toLowerCase(),
        metric: alertForm.metric,
        rise_pct: Number(alertForm.rise_pct) || 0,
        fall_pct: Number(alertForm.fall_pct) || 0,
        cooloff_minutes: Number(alertForm.cooloff_minutes) || 30,
      });
      setAlertTarget(null);
      refreshAlerts();
      setAlertsOpen(true);
      toast.success("Alert saved. We'll notify you when it triggers.");
    } catch {
      toast.error("Couldn't save that alert. Check the values and try again.");
    } finally {
      setBusyAlert(false);
    }
  }

  async function handleDeleteAlert(id) {
    setBusyAlert(true);
    try {
      await deleteAlert(id);
      refreshAlerts();
      toast.success("Alert rule deleted");
    } catch {
      toast.error("Couldn't delete that alert rule. Try again.");
    } finally {
      setBusyAlert(false);
    }
  }

  return (
    <main className="v2-destination">
      <PageHead
        eyebrow="YOUR WATCHLIST"
        title="Cards you’re tracking"
        copy="Live prices and movement for the cards you care about."
        action={
          <div className="v2-watch-head-actions">
            <button type="button" className="v2-alerts-toggle" onClick={() => setAlertsOpen((v) => !v)}>
              <Bell size={16} />
              Alert rules
              {alerts.length > 0 && <span className="v2-alerts-count">{alerts.length}</span>}
            </button>
            <Link className="v2-primary-link" to="/players">Add a player</Link>
          </div>
        }
      />

      {alertsOpen && (
        <section className="v2-alerts-panel">
          <h2>Your alert rules</h2>
          {/* Says where the resulting notifications actually appear.
              "show up on Home" was only half true and gave no way to act
              on them - they now live in the bell menu, where they can be
              read and dismissed. */}
          <p className="muted v2-alerts-panel-sub">
            The conditions we watch for. When one triggers, the notification appears in the bell
            menu at the top of Home, where you can read and dismiss it. Deleting a rule here stops
            future notifications; it doesn't remove ones you've already had.
          </p>
          {alerts.length === 0 ? (
            <p className="muted">No alert rules yet. Click "Alert" on a watched card to get notified on a price move.</p>
          ) : (
            <ul>
              {alerts.map((a) => (
                <li key={a.id}>
                  <div>
                    {/* Never the raw id. The backend resolves the name;
                        if it can't (a card that has left the catalogue),
                        say so in words - an id here is developer UI. */}
                    <strong>{a.player_name || "Card no longer in our catalogue"}</strong>
                    <span className="muted"> ({String(a.platform || "ps").toUpperCase()})</span>
                    <div className="v2-alert-detail">
                      Notify on a {a.metric === "liquidity" ? "sales activity" : "price"} move of {Number(a.rise_pct).toFixed(0)}% up or {Number(a.fall_pct).toFixed(0)}% down
                      {a.last_alert_at ? ` • last notified ${new Date(a.last_alert_at).toLocaleString()}` : " • not triggered yet"}
                    </div>
                  </div>
                  <button type="button" className="danger" disabled={busyAlert} onClick={() => handleDeleteAlert(a.id)}>
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {query.isLoading ? <EmptyState icon={<Star size={28} />} text="Loading watchlist…" /> : query.error?.response?.status === 401 ? <EmptyState icon={<Star size={28} />} text="Sign in to view your watchlist." action={<Link to="/login">Sign in</Link>} /> : query.isError ? <EmptyState icon={<Star size={28} />} error text="Your watchlist could not be loaded." action={<button onClick={() => query.refetch()}>Try again</button>} /> : items.length ? (
        <section className="v2-watch-grid">
          {items.map((item) => (
            <article className="v2-watch-card" key={item.id}>
              <div className="v2-watch-top"><Star size={17} fill="currentColor"/><span>WATCHING</span><small>{String(item.platform || "ps").toUpperCase()}</small></div>
              <Link className="v2-watch-identity" to={`/players/${item.card_id}`}>
                <img src={item.generated_card_url || item.composited_image_url || item.image_url || "/img/card-placeholder.png"} alt="" />
                <div><h2>{item.player_name}</h2><div className="v2-meta-pills"><span>{item.rating || "—"} OVR</span><span>{versionLabel(item.version)}</span></div></div>
                <ArrowRight size={18}/>
              </Link>
              <div className="v2-watch-values">
                <div><span>NOW</span><CoinValue value={item.current_price} /></div>
                <div><span>WHEN ADDED</span><CoinValue value={item.started_price} /></div>
              </div>
              <div className="v2-watch-change"><span>Movement since added</span><b className={item.change_pct == null ? "" : Number(item.change_pct) >= 0 ? "positive" : "negative"}>{item.change_pct == null ? "—" : `${Number(item.change_pct) > 0 ? "+" : ""}${Number(item.change_pct).toFixed(1)}%`}</b></div>
              <div className="v2-watch-actions">
                <button onClick={() => openAlertModal(item)} title="Set price/liquidity alert"><Bell size={15} /> Alert</button>
                <button onClick={() => update(item.id)} disabled={busyItem === item.id}><RefreshCw size={15} /> Refresh</button>
                <button className="danger" onClick={() => remove(item.id, item.player_name)} disabled={busyItem === item.id}><Trash2 size={15} /> Remove</button>
              </div>
            </article>
          ))}
        </section>
      ) : <EmptyState icon={<Star size={28} />} text="Your watchlist is empty." action={<Link to="/players">Find a player</Link>} />}

      {alertTarget && (
        <div className="v2-modal-backdrop" onClick={() => setAlertTarget(null)}>
          <div className="v2-modal" onClick={(e) => e.stopPropagation()}>
            <div className="v2-modal-head">
              <h2>Set Alert — {alertTarget.player_name}</h2>
              <button type="button" className="v2-modal-close" onClick={() => setAlertTarget(null)} aria-label="Close">✕</button>
            </div>
            <form onSubmit={handleCreateAlert}>
              <p className="muted v2-alert-modal-intro">
                Notify me when this card{alertTarget.current_price != null ? <> (reference: <strong>{Number(alertTarget.current_price).toLocaleString()}</strong> coins)</> : null}:
              </p>
              <div className="v2-modal-grid">
                <label>
                  Rises by (%)
                  <input type="number" min="0" step="0.5" value={alertForm.rise_pct} onChange={(e) => setAlertForm((f) => ({ ...f, rise_pct: e.target.value }))} />
                </label>
                <label>
                  Drops by (%)
                  <input type="number" min="0" step="0.5" value={alertForm.fall_pct} onChange={(e) => setAlertForm((f) => ({ ...f, fall_pct: e.target.value }))} />
                </label>
              </div>
              <details className="v2-alert-advanced">
                <summary>Advanced settings</summary>
                <label>
                  What to track
                  <select value={alertForm.metric} onChange={(e) => setAlertForm((f) => ({ ...f, metric: e.target.value }))}>
                    <option value="price">Price</option>
                    <option value="liquidity">Sales activity (sales/hour)</option>
                  </select>
                </label>
                <label>
                  Minimum time between notifications (minutes)
                  <input type="number" min="1" value={alertForm.cooloff_minutes} onChange={(e) => setAlertForm((f) => ({ ...f, cooloff_minutes: e.target.value }))} />
                </label>
              </details>
              <button type="submit" className="v2-modal-submit" disabled={busyAlert}>
                {busyAlert ? "Saving…" : "Save alert"}
              </button>
            </form>
          </div>
        </div>
      )}
    </main>
  );
}
