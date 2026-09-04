// src/v2/components/Skeleton.jsx
//
// Shared shimmer-placeholder primitives for "still loading" states that
// should read as the shape of what's coming, not a blank flash or a bare
// spinner. Lifted from the shimmer already used on the Quick Analysis
// page (player-analysis.css's .quick-skeleton) into one reusable system
// under .v2-root (tokens.css) so every page gets the same look instead
// of reinventing it. Purely decorative - aria-hidden, with the real
// "loading" announcement left to the caller's own status text.
export function SkeletonBlock({ className = "", style }) {
  return <div className={`v2-skel v2-skel-block ${className}`} style={style} aria-hidden="true" />;
}

export function SkeletonLine({ width, className = "" }) {
  return <div className={`v2-skel v2-skel-line ${className}`} style={width ? { width } : undefined} aria-hidden="true" />;
}

// Card-shaped placeholders for a grid of real cards - pass the same
// className the real card grid uses (e.g. "finder-grid opp-grid",
// "v2-watch-grid") so the skeleton inherits that grid's own column/gap
// layout instead of needing its own copy of it.
export function SkeletonCards({ count = 4, gridClassName = "", cardClassName = "" }) {
  return (
    <div className={gridClassName} aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className={`v2-skel-card ${cardClassName}`}>
          <div className="v2-skel v2-skel-block v2-skel-card-art" />
          <div className="v2-skel-card-lines">
            <div className="v2-skel v2-skel-line" style={{ width: "70%" }} />
            <div className="v2-skel v2-skel-line" style={{ width: "45%" }} />
            <div className="v2-skel v2-skel-line" style={{ width: "55%" }} />
          </div>
        </div>
      ))}
    </div>
  );
}
