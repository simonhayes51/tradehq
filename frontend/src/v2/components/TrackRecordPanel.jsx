// src/v2/components/TrackRecordPanel.jsx
//
// HOTFIX: the previous version of this panel presented background-
// scanner signal validation (every buy/strong_buy call the engine
// evaluated, ~10-minute cadence, no dedup across passes - 8,000+ in a
// single week) as "our calls", right next to copy admitting these were
// "not a personal record of trades you were shown". Those two
// statements conflicted, and internal model volume is not a customer-
// facing track record.
//
// This panel now shows two genuinely distinct things, never blended:
//
//   1. PUBLISHED (primary) - recommendations actually posted to real
//      users via the Discord Proof Ledger (GET /api/v2/track-record's
//      `published` key, backed by app/services/discord_daily_proof.py's
//      published_track_record() - only discord_daily_proof_publications
//      rows with status='sent', i.e. provably delivered). This is the
//      real "did our calls work" answer.
//   2. MODEL VALIDATION (secondary, collapsed by default) - the same
//      scanner dataset the old panel over-presented, now labelled with
//      "signal"/"entry reached" language rather than "our calls"/
//      "entered trades", so it cannot be mistaken for the primary
//      section.
import { useState } from "react";
import { useTrackRecord, useRecentOutcomes } from "../hooks/useTrackRecord";
import EmptyState from "./EmptyState";
import CoinValue from "./CoinValue";
import { versionLabel } from "../lib/format";

const WINDOWS = [
  ["1d", "24 hours"],
  ["7d", "7 days"],
  ["30d", "30 days"],
  ["all", "Current season"],
];

// Must match app/routers/v2/recommendations.py's _TRACK_RECORD_WINDOW_DAYS.
const WINDOW_DAYS = { "1d": 1, "7d": 7, "30d": 30, all: 730 };

// A brand-new Discord Proof Ledger (tracking for less time than the
// selected window covers) will show byte-identical numbers across every
// window wider than its actual history - there is nothing older to
// differ on yet. Without this, that reads as a bug ("7d/30d/90d/All
// time are all the same, is this broken?") rather than the honest,
// expected state of a feature that only just started publishing.
function daysSince(iso) {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return Math.max(0, Math.floor((Date.now() - then) / 86_400_000));
}

// "Never entered" is offered explicitly. It is the single most flattering
// thing a track record can quietly omit - a call whose buy price was
// never available is not a win, and hiding the category makes the win/
// loss split look better than the record actually is.
const RESULT_FILTERS = [
  ["all", "All"],
  ["wins", "Target hit"],
  ["losses", "Target not reached"],
  ["no_entry", "Never entered"],
];

const RESULT_COPY = {
  target_hit: { text: "Target hit", tone: "positive" },
  target_miss: { text: "Target not reached", tone: "" },
  win: { text: "Up at 72h", tone: "positive" },
  loss: { text: "Target not reached", tone: "" },
  flat: { text: "Target not reached", tone: "" },
  no_entry: { text: "No entry", tone: "muted" },
};

function fmtPct(value) {
  return value == null ? "—" : `${Number(value).toFixed(1)}%`;
}

function fmtMinutes(value) {
  if (value == null) return "—";
  const hrs = Math.floor(value / 60);
  const mins = Math.round(value % 60);
  if (hrs === 0) return `${mins}m`;
  return `${hrs}h ${mins}m`;
}

function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleDateString() : "—";
}

function StatTile({ label, value, sub, tone }) {
  return (
    <div className="track-record-card compact">
      <span className="track-record-label">{label}</span>
      <strong className={tone || ""}>{value}</strong>
      {sub ? <small>{sub}</small> : null}
    </div>
  );
}

function LedgerRow({ item, result }) {
  const copy = RESULT_COPY[result] || { text: result, tone: "" };
  // A recommendation whose buy price was never reached has no entry, no
  // profit and no ROI - printing "Entered —" and a 0 next to it states
  // three things that did not happen. Say what actually happened instead.
  const noEntry = result === "no_entry" || item.entry_price == null;
  // The badge and the number must never disagree. Colour is driven by the
  // same value the badge is, not by an independent >= 0 test on profit -
  // that independence is how a row could read "Loss" in red beside a
  // green positive figure.
  const targetMiss = result === "target_miss";
  const profitTone = noEntry ? "" : result === "win" || result === "target_hit" ? "positive" : result === "loss" ? "negative" : "";
  return (
    <div className="track-record-outcome-row">
      <div className="track-record-outcome-copy">
        <div className="track-record-outcome-title">
          <strong>{item.player_name || "Unnamed card"}</strong>
          <span className={`outcome-badge ${copy.tone}`}>{copy.text}</span>
        </div>
        <small>
          {item.card_rating ? `${item.card_rating} OVR · ` : ""}
          {versionLabel(item.rarity)} · called {fmtDate(item.detected_at)}
        </small>
      </div>
      <div className="track-record-outcome-numbers">
        {noEntry ? (
          <span className="muted">Never reached the buy price</span>
        ) : targetMiss ? (
          <>
            <span>Entered <CoinValue value={item.entry_price} /></span>
            {item.peak_price != null ? (
              <span>
                Peak <CoinValue value={item.peak_price} /> · best observed return{" "}
                <CoinValue value={item.best_available_profit_after_tax} signed /> ({fmtPct(item.best_available_roi_pct)})
              </span>
            ) : <span className="muted">Target was not reached during tracking</span>}
          </>
        ) : (
          <>
            <span>
              Entered <CoinValue value={item.entry_price} />
            </span>
            <span className={profitTone}>
              <CoinValue value={item.net_profit_after_tax} signed /> ({fmtPct(item.realised_roi_pct)})
            </span>
          </>
        )}
      </div>
    </div>
  );
}

function PublishedEmptyState({ published }) {
  if (published?.tracking_since) {
    return (
      <EmptyState
        compact
        text={`Tracking published recommendations since ${fmtDate(published.tracking_since)} - not enough sent yet in this window. Try a longer period.`}
      />
    );
  }
  return (
    <EmptyState
      compact
      text="No published recommendations tracked yet. This section will fill in as recommendations are actually sent to users."
    />
  );
}

function ModelValidationSection({ window }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading, isError } = useTrackRecord(window);
  const mv = data?.model_validation;

  return (
    <details className="track-record-model-validation" open={open} onToggle={(e) => setOpen(e.target.open)}>
      <summary>Model signal validation (technical detail, not customer trades)</summary>
      {isLoading || isError || !mv ? null : (
        <div className="track-record-mv-body">
          <p className="track-record-disclosure">
            A much larger, automated dataset: every buy/strong-buy signal the engine evaluated on its
            own scanning cadence, whether or not it was ever shown to a user. Useful as a technical
            check on the engine, not a customer trade history - so this uses "signal"/"entry reached"
            language, never "our calls" or "entered trades".
          </p>
          {mv.graded_signals ? (
            <div className="track-record-mv-grid">
              <StatTile label="Graded signals" value={mv.graded_signals.toLocaleString()} />
              <StatTile label="Entry reached" value={mv.entry_reached.toLocaleString()} sub={`${fmtPct(mv.entry_reached_rate_pct)} of graded signals`} />
              <StatTile label="Entry not reached" value={mv.entry_not_reached.toLocaleString()} tone="muted" />
              <StatTile label="Profitable rate" value={mv.has_enough_data ? fmtPct(mv.profitable_rate_pct) : "—"} sub={mv.has_enough_data ? "of entry-reached signals" : `Below ${mv.min_sample} minimum`} />
              <StatTile label="Median ROI" value={mv.has_enough_data ? fmtPct(mv.median_roi_pct) : "—"} sub="After tax" />
              <StatTile label="Avg hold" value={mv.has_enough_data ? fmtMinutes(mv.avg_hold_minutes) : "—"} sub="To exact target" />
            </div>
          ) : (
            <EmptyState compact text="No graded signals in this window." />
          )}
        </div>
      )}
    </details>
  );
}

export default function TrackRecordPanel() {
  const [window, setWindow] = useState("all");
  const [resultFilter, setResultFilter] = useState("all");
  const [ledgerOpen, setLedgerOpen] = useState(false);
  // Starts at the required 5, grows by 5 on "Load more" - avoids ever
  // fetching/rendering an unbounded list up front, per the mobile
  // rebuild's list-limiting requirement.
  const [ledgerLimit, setLedgerLimit] = useState(5);
  const trackRecordQuery = useTrackRecord(window);
  const { data, isLoading, isError } = trackRecordQuery;
  const recentQuery = useRecentOutcomes(window, resultFilter, ledgerLimit, { enabled: ledgerOpen });

  if (isLoading) {
    return (
      <section className="track-record-panel">
        <div className="v2-page-state" role="status">Loading published results…</div>
      </section>
    );
  }

  if (isError) {
    return (
      <section className="track-record-panel">
        <EmptyState
          compact
          error
          title="The track record could not be loaded"
          text="Your other market tools are still available. Try loading the published results again."
          action={<button type="button" onClick={() => trackRecordQuery.refetch()}>Try again</button>}
        />
      </section>
    );
  }

  const published = data?.published;
  if (!published) {
    return (
      <section className="track-record-panel">
        <PublishedEmptyState published={null} />
      </section>
    );
  }

  const trackedDays = daysSince(published.tracking_since);
  const windowNarrowerThanHistory = trackedDays != null && trackedDays < WINDOW_DAYS[window];
  // Keep the UI safe during a rolling deploy or while an old API response
  // is still in the process cache. These aliases can be removed once every
  // environment has served the target-based response at least once.
  const targetHits = Number(published.target_hits ?? published.wins ?? 0);
  const targetMisses = Number(published.target_not_reached ?? published.losses ?? 0);
  const targetHitRate = published.target_hit_rate_pct ?? published.win_rate_pct;
  const recentTargetHits = published.recent_target_hits ?? published.recent_wins ?? [];
  const recentTargetMisses = published.recent_target_misses ?? published.recent_losses ?? [];

  return (
    <section className="track-record-panel">
      <div className="section-head compact">
        <div>
          {/* No eyebrow/title here any more: this panel now only renders
              inside /track-record, whose PageHead already says what
              the page is. Repeating it produced two headings for one
              thing. The homepage's summary is TrackRecordSummary. */}
          <span>PUBLISHED RESULTS</span>
          <h2>Choose a period</h2>
        </div>
        <div className="track-record-window-tabs">
          {WINDOWS.map(([key, label]) => (
            <button key={key} type="button" className={window === key ? "active" : ""} onClick={() => setWindow(key)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <p className="track-record-disclosure">
        FC{data?.game_year || published.game_year || 27} recommendations actually sent to users via
        Discord. We record whether the published target was reached, not a made-up sale at the end
        of the tracking window. Individual results still depend on entry and exit timing.
      </p>

      {published.has_published_data && windowNarrowerThanHistory ? (
        <p className="track-record-disclosure small track-record-tracking-note">
          FC{data?.game_year || published.game_year || 27} tracking began {fmtDate(published.tracking_since)}
          {trackedDays === 0 ? " (today)" : ` (${trackedDays} day${trackedDays === 1 ? "" : "s"} ago)`}.
          Wider periods currently contain the same current-season history.
        </p>
      ) : null}

      {!published.has_published_data ? (
        <PublishedEmptyState published={published} />
      ) : !published.has_enough_data ? (
        <EmptyState compact text={`Not enough published outcomes yet in this window (${published.published_opportunities}/${published.min_sample}). Try a longer window.`} />
      ) : (
        <>
          <div className="track-record-grid compact-grid">
            <StatTile label="Target hits" value={targetHits.toLocaleString()} tone="positive" />
            <StatTile label="Target not reached" value={targetMisses.toLocaleString()} />
            <StatTile label="Target hit rate" value={fmtPct(targetHitRate)} tone={targetHitRate >= 50 ? "positive" : ""} />
            <StatTile label="Calls tracked" value={published.published_opportunities.toLocaleString()} />
            <StatTile label="Median time to target" value={fmtMinutes(published.median_time_to_target_minutes)} />
          </div>
          <p className="track-record-disclosure small">
            {published.published_opportunities.toLocaleString()} recommendations graded, all of which
            reached their buy price. A target not reached is not labelled a loss because EAFC.Live
            cannot know when an individual user chose to sell.
          </p>

          <div className="track-record-recent-split">
            <div className="track-record-recent-col">
              <h3>Recent target hits</h3>
              {recentTargetHits.length ? (
                <div className="track-record-outcomes">
                  {recentTargetHits.map((item) => (
                    <LedgerRow key={item.outcome_id} item={item} result="target_hit" />
                  ))}
                </div>
              ) : (
                <EmptyState compact text="No recent target hits in this period." />
              )}
            </div>
            <div className="track-record-recent-col">
              <h3>Recent targets not reached</h3>
              {recentTargetMisses.length ? (
                <div className="track-record-outcomes">
                  {recentTargetMisses.map((item) => (
                    <LedgerRow key={item.outcome_id} item={item} result="target_miss" />
                  ))}
                </div>
              ) : (
                <EmptyState compact text="Every tracked call in this period reached its target." />
              )}
            </div>
          </div>
        </>
      )}

      <details
        className="track-record-recent"
        open={ledgerOpen}
        onToggle={(e) => setLedgerOpen(e.target.open)}
      >
        <summary>
          <h3>Full chronological ledger (all graded signals)</h3>
        </summary>
        <div className="track-record-recent-head">
          <div className="track-record-result-tabs">
            {RESULT_FILTERS.map(([key, label]) => (
              <button key={key} type="button" className={resultFilter === key ? "active" : ""} onClick={() => setResultFilter(key)}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <p className="track-record-disclosure small">
          A different, much larger technical dataset from the published figures above: every buy signal the
          engine graded in this window, including ones that were never sent to anyone. Useful for
          model checking, not a record of user trades. "Up" and "down" describe the 72-hour market
          mark only, not what any user made. Shows {ledgerLimit} rows at a time.
        </p>
        {recentQuery.isLoading ? (
          <EmptyState compact text="Loading…" />
        ) : recentQuery.data?.outcomes?.length ? (
          <>
            <div className="track-record-outcomes">
              {recentQuery.data.outcomes.map((o) => (
                <LedgerRow key={o.outcome_id} item={{
                  outcome_id: o.outcome_id, player_name: o.player_name, card_rating: o.rating,
                  rarity: o.rarity, detected_at: o.detected_at, entry_price: o.entry_price,
                  net_profit_after_tax: o.net_profit_after_tax, realised_roi_pct: o.realised_roi_pct,
                }} result={o.result} />
              ))}
            </div>
            {recentQuery.data.outcomes.length >= ledgerLimit ? (
              <button type="button" className="track-record-load-more" onClick={() => setLedgerLimit((n) => n + 5)}>
                Load more
              </button>
            ) : null}
          </>
        ) : (
          <EmptyState compact text="No outcomes match this filter in this window yet." />
        )}
      </details>

      <ModelValidationSection window={window} />

      <details className="track-record-methodology-detail">
        <summary>How this is calculated</summary>
        <p className="track-record-methodology">
          Published figures come only from recommendations actually posted to the Discord Proof
          Ledger for the current EA FC season only. Every recommendation is frozen when it is made
          and checked against chronological listing history. Entry must happen before the target.
          Calls that do not hit the target remain "target not reached" rather than being converted
          into a fictional loss at the final observed price. Peak figures are market observations,
          not guaranteed executions.
          Percentages are withheld entirely below {published?.min_sample ?? 20} graded outcomes for
          that window.
        </p>
      </details>
    </section>
  );
}
