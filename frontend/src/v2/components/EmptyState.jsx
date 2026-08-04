// src/v2/components/EmptyState.jsx
//
// Single shared empty/loading/error placeholder for v2's data panels.
// Before this, Home Dashboard, Club, Players, Watchlist and Trade
// Finder each had their own near-identical component (different CSS
// class, different corner cases), so the same "nothing to show" state
// rendered differently depending which page you were on. This is that
// one component; `.v2-empty-state` in tokens.css is the one style.
export default function EmptyState({ icon, title, text, action, error = false, compact = false }) {
  return (
    <div className={`v2-empty-state${error ? " is-error" : ""}${compact ? " is-compact" : ""}`}>
      {icon}
      {title ? <h2>{title}</h2> : null}
      <p>{text}</p>
      {action}
    </div>
  );
}
