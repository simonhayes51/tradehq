import "./ExitEvidence.css";

function pct(value, digits = 0) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${Number(value).toFixed(digits)}%`;
}

function duration(minutes) {
  if (minutes == null || Number.isNaN(Number(minutes))) return "—";
  const total = Math.max(0, Math.round(Number(minutes)));
  if (total < 60) return `${total}m`;
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  return mins ? `${hours}h ${mins}m` : `${hours}h`;
}

function cohortLabel(value) {
  const labels = {
    "liquidity+target+trend+tier": "Very similar calls",
    "liquidity+target+trend": "Similar liquidity, target and trend",
    "liquidity+target": "Similar liquidity and target",
    liquidity: "Similar liquidity",
    overall: "All comparable calls",
  };
  return labels[value] || "Comparable calls";
}

export default function ExitEvidence({ evidence }) {
  // Locked/free cards deliberately receive no exit_quality field from the
  // backend. Render nothing rather than a blurred/placeholder panel: there is
  // no premium data in the response to accidentally expose in devtools.
  if (!evidence) return null;

  if (evidence.status !== "available") {
    return (
      <section className="exit-evidence is-warming" aria-label="Exit evidence">
        <div className="exit-evidence-heading">
          <div>
            <span className="exit-evidence-eyebrow">EXIT EVIDENCE</span>
            <h3>Still building a reliable sample</h3>
          </div>
        </div>
        <p>{evidence.message || "Historical exit evidence is temporarily unavailable."}</p>
        {evidence.minimum_sample ? (
          <small>We need at least {evidence.minimum_sample} comparable graded calls before showing a rate.</small>
        ) : null}
      </section>
    );
  }

  const ci = Array.isArray(evidence.target_hit_call_rate_ci95_pct)
    ? evidence.target_hit_call_rate_ci95_pct
    : null;

  return (
    <section className="exit-evidence" aria-label="Exit evidence">
      <div className="exit-evidence-heading">
        <div>
          <span className="exit-evidence-eyebrow">EXIT EVIDENCE</span>
          <h3>{cohortLabel(evidence.cohort)}</h3>
        </div>
        <span className={`exit-evidence-strength ${evidence.evidence_strength || "limited"}`}>
          {evidence.evidence_strength || "limited"} evidence
        </span>
      </div>

      <div className="exit-evidence-grid">
        <div>
          <span>Hit sell target</span>
          <strong>{pct(evidence.target_hit_call_rate_pct)}</strong>
          <small>{ci ? `95% range ${pct(ci[0])} to ${pct(ci[1])}` : "95% range unavailable"}</small>
        </div>
        <div>
          <span>Typical time to target</span>
          <strong>{duration(evidence.median_minutes_to_target)}</strong>
          <small>{evidence.horizon || "24h"} grading horizon</small>
        </div>
        <div>
          <span>Entry achieved</span>
          <strong>{pct(evidence.entry_rate_pct)}</strong>
          <small>Across all comparable calls</small>
        </div>
        <div>
          <span>Sample</span>
          <strong>{Number(evidence.sample_size || 0).toLocaleString()}</strong>
          <small>Exact engine configuration</small>
        </div>
      </div>

      <p className="exit-evidence-note">
        Historical behaviour of comparable recommendations, not a guarantee that this card will reach its target.
      </p>
    </section>
  );
}
