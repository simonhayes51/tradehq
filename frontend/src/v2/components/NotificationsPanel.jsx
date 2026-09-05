// src/v2/components/NotificationsPanel.jsx
//
// The bell menu. Replaces a bell button that merely navigated to the
// Watchlist page and carried a badge counting "the 5 most recent alert
// rows" - a number that never went down, matched nothing on screen, and
// had no relationship to what the user had or hadn't seen.
//
// Alerts here are the fired notifications (alerts_log). Alert RULES stay
// on the Watchlist page, which is where you set them.
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { Bell, Trash2, X } from "lucide-react";
import { useAlerts, useDismissAlert, useDismissAllAlerts, useMarkAlertsRead } from "../hooks/useAlerts";
import EmptyState from "./EmptyState";

export default function NotificationsPanel({ open, onClose, onUnreadCount, rules = [] }) {
  const { data, isLoading, isError, refetch } = useAlerts({ enabled: open });
  const dismiss = useDismissAlert();
  const dismissAll = useDismissAllAlerts();
  const markRead = useMarkAlertsRead();
  const panelRef = useRef(null);
  const markedRef = useRef(false);
  const headingRef = useRef(null);

  const items = data?.items ?? [];
  const unreadCount = data?.unreadCount ?? 0;

  // Let the parent (the bell badge) use the panel's own count once it has
  // one, so the badge and the list can never disagree.
  useEffect(() => {
    if (data) onUnreadCount?.(unreadCount);
  }, [data, unreadCount, onUnreadCount]);

  // Opening the panel is "I have seen these", which clears the badge -
  // it is deliberately NOT the same action as dismissing them, which
  // removes them. Guarded so re-renders while open don't re-fire it.
  useEffect(() => {
    if (!open) { markedRef.current = false; return; }
    if (markedRef.current || !unreadCount) return;
    markedRef.current = true;
    markRead.mutate();
  }, [open, unreadCount, markRead]);

  // Move focus into the panel when it opens and hand it back to the bell
  // when it closes. Without this a keyboard user tabbing after opening
  // the menu continues from the bell into the page BEHIND the panel,
  // which is both confusing and unreachable-feeling.
  useEffect(() => {
    if (!open) return undefined;
    const previouslyFocused = document.activeElement;
    headingRef.current?.focus();
    return () => {
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => event.key === "Escape" && onClose();
    const onClick = (event) => {
      if (panelRef.current && !panelRef.current.contains(event.target)) onClose();
    };
    window.addEventListener("keydown", onKey);
    // `true` (capture): the bell button's own click must not immediately
    // re-close the panel it just opened.
    document.addEventListener("mousedown", onClick, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick, true);
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="v2-notifications" ref={panelRef} role="dialog" aria-label="Alerts">
      <header className="v2-notifications-head">
        <h2 ref={headingRef} tabIndex={-1}><Bell size={15} /> Alerts</h2>
        <div className="v2-notifications-head-actions">
          {items.length ? (
            <button type="button" onClick={() => dismissAll.mutate()} disabled={dismissAll.isPending}>
              Clear all
            </button>
          ) : null}
          <button type="button" onClick={onClose} aria-label="Close alerts"><X size={15} /></button>
        </div>
      </header>

      {isLoading ? (
        <EmptyState compact text="Loading your alerts…" />
      ) : isError ? (
        <EmptyState compact error text="Your alerts couldn't be loaded." action={<button type="button" onClick={() => refetch()}>Try again</button>} />
      ) : items.length ? (
        <ul className="v2-notifications-list">
          {items.map((alert) => (
            <li key={alert.id} className={alert.unread ? "unread" : ""}>
              <Link to={alert.href} onClick={onClose} className="v2-notification-body">
                <strong>{alert.title}</strong>
                {alert.movement ? <span className="v2-notification-move">{alert.movement}</span> : null}
                <span className="v2-notification-why">{alert.whyItMatters}</span>
                <small>
                  {alert.age}
                  {alert.repeatCount > 1 ? ` · triggered ${alert.repeatCount} times recently` : ""}
                </small>
              </Link>
              <button
                type="button"
                className="v2-notification-dismiss"
                aria-label={`Dismiss alert: ${alert.title}`}
                disabled={dismiss.isPending}
                onClick={() => dismiss.mutate(alert.id)}
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          compact
          text={
            data?.watchedCount
              ? "Nothing has moved enough to alert you yet. We'll let you know when a watched card hits one of your conditions."
              : "You're not watching any cards yet. Add one and we'll tell you when its price moves."
          }
          action={<Link to="/watchlist" onClick={onClose}>Open watchlist</Link>}
        />
      )}

      {rules.length ? (
        <section className="v2-notifications-rules" aria-labelledby="active-alert-rules">
          <h3 id="active-alert-rules">Active rules <span>{rules.length}</span></h3>
          <ul>
            {rules.slice(0, 4).map((rule) => (
              <li key={rule.id}>
                <Link to={`/players/${rule.cardId}`} onClick={onClose}>
                  <strong>{rule.playerName}</strong>
                  <span>
                    {rule.metric === "liquidity" ? "Sales activity" : "Price"}: {Number(rule.risePct).toFixed(0)}% up or {Number(rule.fallPct).toFixed(0)}% down
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <footer className="v2-notifications-foot">
        <Link to="/watchlist" onClick={onClose}>Manage alert rules</Link>
      </footer>
    </div>
  );
}
