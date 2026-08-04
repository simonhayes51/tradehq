// src/v2/components/TrackRecordPanel.jsx
//
// The single highest-leverage trust feature an AI trading tool can
// ship: a real, honest hit-rate instead of asking anyone to just take
// "AI-powered" on faith. Every number here comes straight from
// GET /api/v2/track-record (aggregated ml_labels - real closed-window
// outcomes, not a backtest or a projection) - a strategy with too few
// closed windows shows "Not enough data yet" rather than a number, on
// purpose: a fabricated-looking 0%/100% from a tiny sample would be
// worse than admitting there isn't a track record yet.
import { useTrackRecord } from "../hooks/useTrackRecord";

export default function TrackRecordPanel() {
  const { data, isLoading, isError } = useTrackRecord();
  if (isLoading || isError) return null;

  const strategies = data?.strategies || [];
  if (!strategies.length) return null;

  return (
    <section className="track-record-panel">
      <div className="section-head compact">
        <div>
          <span>TRACK RECORD</span>
          <h2>Did our calls actually work?</h2>
        </div>
      </div>
      <div className="track-record-grid">
        {strategies.map((s) => (
          <div className="track-record-card" key={s.strategy}>
            <span className="track-record-label">{s.label}</span>
            {s.hasEnoughData ? (
              <>
                <strong className={s.hitRatePct >= 50 ? "positive" : ""}>{s.hitRatePct}%</strong>
                <small>hit target · {s.sampleSize} closed calls</small>
              </>
            ) : (
              <>
                <strong className="muted">—</strong>
                <small>Not enough data yet ({s.sampleSize}/20 closed calls)</small>
              </>
            )}
          </div>
        ))}
      </div>
      {data?.methodology ? <p className="track-record-methodology">{data.methodology}</p> : null}
    </section>
  );
}
