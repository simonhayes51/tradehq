// src/v2/components/TrackRecordPanel.jsx
//
// The single highest-leverage trust feature an AI trading tool can
// ship: a real, honest hit-rate instead of asking anyone to just take
// "AI-powered" on faith.
//
// WHAT THIS ACTUALLY MEASURES - read before changing the copy below.
// Every number here comes from GET /api/v2/track-record and
// /api/v2/track-record/recent, backed by app/services/
// futgg_recommendation_store.py's track_record()/recent_outcomes().
// These are graded outcomes of every buy/strong_buy signal the
// background scanner issued (roughly every 10 minutes, across the
// whole candidate pool) - NOT a log of recommendations a specific user
// was shown. There is no "published to a user" concept anywhere in this
// pipeline. This is automated signal validation, not a personal trade
// history, and the copy below must not blur that line - see the
// PR description for the full audit this was rebuilt from.
//
// "No entry" (the recommended buy price was never actually reached) is
// excluded from win rate and profit stats - counting a never-purchasable
// call as a win or a loss would both be wrong. It is always shown as its
// own separate count, never folded into "losses".
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
  return value == null ? "—" : `${value >= 0 ? "" : ""}${Number(value).toFixed(1)}%`;
}

function fmtMinutes(value) {
  if (value == null) return "—";
  const hrs = Math.floor(value / 60);
  const mins = Math.round(value % 60);
  if (hrs === 0) return `${mins}m`;
  return `${hrs}h ${mins}m`;
}

function StatTile({ label, value, sub, tone }) {
  return (
    <div className="track-record-card">
      <span className="track-record-label">{label}</span>
      <strong className={tone || ""}>{value}</strong>
      {sub ? <small>{sub}</small> : null}
    </div>
  );
}

export default function TrackRecordPanel() {
  const [window, setWindow] = useState("90d");
  const [resultFilter, setResultFilter] = useState("all");
  const { data, isLoading, isError } = useTrackRecord(window);
  const recentQuery = useRecentOutcomes(window, resultFilter, 12);

  if (isLoading || isError) return null;

  const summary = data?.summary;
  if (!summary) return null;

  const outcomes = recentQuery.data?.outcomes || [];

  return (
    <section className="track-record-panel">
      <div className="section-head compact">
        <div>
          <span>TRACK RECORD</span>
          <h2>Did our calls actually work?</h2>
        </div>
        <div className="track-record-window-tabs">
          {WINDOWS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={window === key ? "active" : ""}
              onClick={() => setWindow(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <p className="track-record-disclosure">
        These are automated buy/strong-buy signals the engine issued, graded against real
        chronological market data - not a personal record of trades you were shown. Every rate
        is shown next to the real count it's based on.
      </p>

      {!summary.has_enough_data ? (
        <EmptyState
          compact
          text={`Not enough graded outcomes yet in this window (${summary.total_graded}/${summary.min_sample}). Try a longer window.`}
        />
      ) : (
        <>
          <div className="track-record-grid">
            <StatTile label="Graded calls" value={summary.total_graded.toLocaleString()} sub="Every actionable signal graded in this window" />
            <StatTile label="Entered" value={summary.entered.toLocaleString()} sub={`${fmtPct(summary.entry_rate_pct)} of graded calls reached the buy price`} />
            <StatTile label="No entry" value={summary.no_entry.toLocaleString()} sub="Buy price never reached - excluded from win rate & profit" tone="muted" />
            <StatTile
              label="Win rate"
              value={fmtPct(summary.win_rate_pct)}
              sub={`of ${summary.entered.toLocaleString()} entered trades`}
              tone={summary.win_rate_pct >= 50 ? "positive" : ""}
            />
            <StatTile label="Hit exact target" value={fmtPct(summary.target_hit_rate_pct)} sub="Stricter than win rate - price crossed the exact sell target" />
            <StatTile label="Avg ROI" value={fmtPct(summary.avg_roi_pct)} sub="After EA's 5% tax, entered trades only" tone={summary.avg_roi_pct >= 0 ? "positive" : "negative"} />
            <StatTile label="Median ROI" value={fmtPct(summary.median_roi_pct)} sub="After tax, entered trades only" />
            <StatTile label="Avg time to target" value={fmtMinutes(summary.avg_hold_minutes)} sub="For trades that hit their exact target" />
          </div>

          <div className="track-record-recent">
            <div className="track-record-recent-head">
              <h3>Recent outcomes</h3>
              <div className="track-record-result-tabs">
                {RESULT_FILTERS.map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    className={resultFilter === key ? "active" : ""}
                    onClick={() => setResultFilter(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            {recentQuery.isLoading ? (
              <EmptyState compact text="Loading recent outcomes…" />
            ) : outcomes.length ? (
              <div className="track-record-outcomes">
                {outcomes.map((o) => {
                  const copy = RESULT_COPY[o.result] || { text: o.result, tone: "" };
                  return (
                    <div className="track-record-outcome-row" key={o.outcome_id}>
                      {o.image_url ? (
                        <img className="track-record-outcome-art" src={o.image_url} alt={o.player_name || "Card"} />
                      ) : (
                        <div className="track-record-outcome-art placeholder" aria-hidden="true" />
                      )}
                      <div className="track-record-outcome-copy">
                        <div className="track-record-outcome-title">
                          <strong>{o.player_name || "Unknown card"}</strong>
                          <span className={`outcome-badge ${copy.tone}`}>{copy.text}</span>
                        </div>
                        <small>
                          {o.rating ? `${o.rating} OVR · ` : ""}
                          {o.rarity || "Card"} · detected {o.detected_at ? new Date(o.detected_at).toLocaleDateString() : "—"}
                        </small>
                      </div>
                      <div className="track-record-outcome-numbers">
                        <span>
                          Buy below <CoinValue value={o.buy_threshold} />
                        </span>
                        {o.entry_achieved ? (
                          <>
                            <span>
                              Entered <CoinValue value={o.entry_price} />
                            </span>
                            <span className={o.net_profit_after_tax >= 0 ? "positive" : "negative"}>
                              <CoinValue value={o.net_profit_after_tax} signed /> ({fmtPct(o.realised_roi_pct)})
                            </span>
                          </>
                        ) : (
                          <span className="muted">Buy price never reached</span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <EmptyState compact text="No outcomes match this filter in this window yet." />
            )}
          </div>
        </>
      )}

      <details className="track-record-methodology-detail">
        <summary>How this is calculated</summary>
        <p className="track-record-methodology">
          Every actionable recommendation is frozen the moment it's made and graded later against
          the real, chronological listing history - entry must happen before exit, and the best
          price in the window is never used with hindsight. "No entry" means the recommended buy
          price was never actually available, and those calls are excluded from win rate and
          profit statistics entirely rather than counted as a loss. All profit and ROI figures are
          after EA's 5% sale tax. These are hypothetical results based on real market data - they
          do not guarantee any individual user actually achieved these prices, since real execution
          depends on timing, competition for the listing, and platform. Percentages are withheld
          entirely below {summary?.min_sample ?? 20} graded outcomes for that window.
        </p>
      </details>
    </section>
  );
}
