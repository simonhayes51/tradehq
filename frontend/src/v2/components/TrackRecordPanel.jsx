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

const WINDOWS = [
  ["7d", "7 days"],
  ["30d", "30 days"],
  ["90d", "90 days"],
  ["all", "All time"],
];

const RESULT_FILTERS = [
  ["all", "All"],
  ["wins", "Wins"],
  ["losses", "Losses"],
];

const RESULT_COPY = {
  win: { text: "Win", tone: "positive" },
  loss: { text: "Loss", tone: "negative" },
  flat: { text: "Flat", tone: "" },
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
  return (
    <div className="track-record-outcome-row">
      <div className="track-record-outcome-copy">
        <div className="track-record-outcome-title">
          <strong>{item.player_name}</strong>
          <span className={`outcome-badge ${copy.tone}`}>{copy.text}</span>
        </div>
        <small>
          {item.card_rating ? `${item.card_rating} OVR · ` : ""}
          {item.rarity || "Card"} · detected {fmtDate(item.detected_at)}
        </small>
      </div>
      <div className="track-record-outcome-numbers">
        <span>
          Entered <CoinValue value={item.entry_price} />
        </span>
        <span className={item.net_profit_after_tax >= 0 ? "positive" : "negative"}>
          <CoinValue value={item.net_profit_after_tax} signed /> ({fmtPct(item.realised_roi_pct)})
        </span>
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
  const [window, setWindow] = useState("90d");
  const [resultFilter, setResultFilter] = useState("all");
  const [ledgerOpen, setLedgerOpen] = useState(false);
  // Starts at the required 5, grows by 5 on "Load more" - avoids ever
  // fetching/rendering an unbounded list up front, per the mobile
  // rebuild's list-limiting requirement.
  const [ledgerLimit, setLedgerLimit] = useState(5);
  const { data, isLoading, isError } = useTrackRecord(window);
  const recentQuery = useRecentOutcomes(window, resultFilter, ledgerLimit, { enabled: ledgerOpen });

  if (isLoading || isError) return null;

  const published = data?.published;
  if (!published) return null;

  return (
    <section className="track-record-panel">
      <div className="section-head compact">
        <div>
          <span>TRACK RECORD</span>
          <h2>Did our published calls actually work?</h2>
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
        Recommendations actually sent to users via the Discord Proof Ledger - not internal model
        scoring. Results are hypothetical (based on real market data) and don't guarantee any
        individual user achieved these exact prices.
      </p>

      {!published.has_published_data ? (
        <PublishedEmptyState published={published} />
      ) : !published.has_enough_data ? (
        <EmptyState compact text={`Not enough published outcomes yet in this window (${published.published_opportunities}/${published.min_sample}). Try a longer window.`} />
      ) : (
        <>
          <div className="track-record-grid compact-grid">
            <StatTile label="Wins" value={published.wins.toLocaleString()} tone="positive" />
            <StatTile label="Losses" value={published.losses.toLocaleString()} tone="negative" />
            <StatTile label="Win rate" value={fmtPct(published.win_rate_pct)} tone={published.win_rate_pct >= 50 ? "positive" : ""} />
            <StatTile label="Total profit" value={<CoinValue value={published.total_profit_after_tax} signed />} sub="After tax, one card per call" />
            <StatTile label="Median ROI" value={fmtPct(published.median_roi_pct)} sub="After tax" />
            <StatTile label="Median time to target" value={fmtMinutes(published.median_time_to_target_minutes)} />
          </div>
          <p className="track-record-disclosure small">
            Published opportunities: {published.published_opportunities.toLocaleString()} · entry
            reached: {published.entry_reached.toLocaleString()} (this pipeline only tracks
            recommendations that were actually entered - it does not yet track ones that never
            reached their buy price).
          </p>

          <div className="track-record-recent-split">
            <div className="track-record-recent-col">
              <h3>Recent wins</h3>
              {published.recent_wins?.length ? (
                <div className="track-record-outcomes">
                  {published.recent_wins.map((item) => (
                    <LedgerRow key={item.outcome_id} item={item} result="win" />
                  ))}
                </div>
              ) : (
                <EmptyState compact text="No recent wins in this window." />
              )}
            </div>
            <div className="track-record-recent-col">
              <h3>Recent losses</h3>
              {published.recent_losses?.length ? (
                <div className="track-record-outcomes">
                  {published.recent_losses.map((item) => (
                    <LedgerRow key={item.outcome_id} item={item} result="loss" />
                  ))}
                </div>
              ) : (
                <EmptyState compact text="No recent losses in this window." />
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
          <h3>Full chronological ledger</h3>
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
          Every graded model signal in this window (see Model signal validation below for what that
          means), not limited to published recommendations. Initially limited to {ledgerLimit} rows.
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
          Ledger. Every recommendation is frozen the moment it's made and graded later against the
          real, chronological listing history - entry must happen before exit, and the best price in
          the window is never used with hindsight. All profit and ROI figures are after EA's 5% sale
          tax, assuming one card per published call. These are hypothetical results based on real
          market data - they do not guarantee any individual user actually achieved these prices,
          since real execution depends on timing, competition for the listing, and platform.
          Percentages are withheld entirely below {published?.min_sample ?? 20} graded outcomes for
          that window.
        </p>
      </details>
    </section>
  );
}
