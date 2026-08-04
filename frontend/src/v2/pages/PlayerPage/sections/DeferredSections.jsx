// src/v2/pages/PlayerPage/sections/DeferredSections.jsx
//
// Investment Score/Risk Rating (now ScoresSection) and Expected ROI/
// Holding Period (now HeaderSection's hero) moved to real sections in
// earlier phases. Suggested Alternatives correctly stays deferred - no
// similar-card engine exists in this design; recommendations.similar_events
// is about related market events, not alternative cards to buy instead.
//
// A "Historical Matches" (win rate / avg profit / avg hold time for
// similar past setups) panel was considered and deliberately dropped
// rather than shown as a second empty "coming soon" box:
// recommendation_engine.py has no backtest-outcome aggregation anywhere,
// and an empty-looking panel sitting in an otherwise data-dense layout
// reads as broken, not "in progress." One honestly-labeled placeholder
// is enough - a second one actively hurts the page.
import DeferredPlaceholder from "../../../components/DeferredPlaceholder";

export default function DeferredSections() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <DeferredPlaceholder title="Suggested Alternatives" />
    </div>
  );
}
