// src/v2/components/TrackRecordSummary.jsx
//
// The homepage's proof-of-performance strip.
//
// This replaces a full TrackRecordPanel mounted directly on Home: window
// tabs, a six-tile grid, two recent-outcome columns, a paginated
// chronological ledger, a collapsible "model signal validation" section
// and a methodology accordion - the single largest thing on the page, and
// almost all of it detail a customer does not need in order to judge
// whether the recommendations have been useful.
//
// What survives here is the four numbers that answer that question, over
// one fixed window, with the caveat that must always accompany them.
// Everything else moved to /track-record (TrackRecordPanel), one click
// away, unchanged.
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { useTrackRecord } from "../hooks/useTrackRecord";
import CoinValue from "./CoinValue";

const WINDOW = "90d";

function fmtPct(value) {
  return value == null ? "—" : `${Number(value).toFixed(1)}%`;
}

function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleDateString() : "—";
}

export default function TrackRecordSummary() {
  const { data, isLoading, isError } = useTrackRecord(WINDOW);
  const published = data?.published;

  // Nothing to prove yet is not an error state and is not worth a
  // skeleton on the homepage - render nothing rather than a box that
  // says "we have no evidence".
  if (isLoading || isError || !published) return null;

  if (!published.has_published_data || !published.has_enough_data) {
    return (
      <section className="track-record-strip is-empty">
        <div className="track-record-strip-head">
          <span>TRACK RECORD</span>
          <h2>Did our published calls work?</h2>
        </div>
        <p className="track-record-strip-note">
          {published.tracking_since
            ? `We started publishing recommendations on ${fmtDate(published.tracking_since)}. Results are withheld until enough have been graded to report them honestly (${published.published_opportunities ?? 0} of ${published.min_sample ?? 20} so far).`
            : "Results appear here once published recommendations have been graded against real market prices."}
        </p>
        <Link className="track-record-strip-link" to="/track-record">
          How this is measured <ArrowRight size={14} />
        </Link>
      </section>
    );
  }

  return (
    <section className="track-record-strip">
      <div className="track-record-strip-head">
        <span>TRACK RECORD</span>
        <h2>Did our published calls work?</h2>
        <small>Last 90 days</small>
      </div>
      <div className="track-record-strip-grid">
        <div>
          <span>Win rate</span>
          <strong className={published.win_rate_pct >= 50 ? "positive" : ""}>{fmtPct(published.win_rate_pct)}</strong>
          <small>{published.wins} won · {published.losses} lost</small>
        </div>
        <div>
          <span>Median return</span>
          <strong>{fmtPct(published.median_roi_pct)}</strong>
          <small>After EA's 5% tax</small>
        </div>
        <div>
          <span>Combined result</span>
          <strong className={Number(published.total_profit_after_tax) >= 0 ? "positive" : "negative"}>
            <CoinValue value={published.total_profit_after_tax} signed />
          </strong>
          <small>One card per call</small>
        </div>
        <div>
          <span>Calls graded</span>
          <strong>{Number(published.published_opportunities).toLocaleString()}</strong>
          <small>Published to users</small>
        </div>
      </div>
      <p className="track-record-strip-note">
        Hypothetical results from real market prices for recommendations we actually sent. They are
        not a record of what any individual made.
      </p>
      <Link className="track-record-strip-link" to="/track-record">
        See every call and how this is measured <ArrowRight size={14} />
      </Link>
    </section>
  );
}
