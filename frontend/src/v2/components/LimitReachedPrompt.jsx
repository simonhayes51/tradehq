import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Lock, X } from "lucide-react";
import UpgradePrompt from "./UpgradePrompt";
import { GATED } from "../lib/gatedFeatures";
import "../styles/limit-reached-prompt.css";

/**
 * One handler for quota-style 402 responses.
 *
 * The backend's limit_reached() helper emits a structured 402 with
 * error=limit_reached, feature, used, limit, upgradeable and message.
 * axios.js already turns every structured 402 into premium:blocked.
 * Listening here means every shell page gets the same truthful response
 * at the exact moment a member reaches a real allowance, without each
 * feature inventing its own interpretation of the limit.
 *
 * Feature gates such as Trade Finder also use 402, but they are NOT
 * limit_reached responses and already render their own in-page
 * UpgradePrompt, so they are deliberately ignored here.
 */
export default function LimitReachedPrompt() {
  const [detail, setDetail] = useState(null);

  useEffect(() => {
    const onBlocked = (event) => {
      const next = event?.detail;
      if (next?.error !== "limit_reached") return;
      setDetail(next);
    };
    window.addEventListener("premium:blocked", onBlocked);
    return () => window.removeEventListener("premium:blocked", onBlocked);
  }, []);

  useEffect(() => {
    if (!detail) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") setDetail(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [detail]);

  if (!detail) return null;

  const gated = GATED[detail.feature];
  const used = Number(detail.used);
  const limit = Number(detail.limit);
  const hasCount = Number.isFinite(used) && Number.isFinite(limit);

  return (
    <div className="limit-reached-backdrop" role="presentation" onClick={() => setDetail(null)}>
      <div
        className="limit-reached-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Account limit reached"
        onClick={(event) => event.stopPropagation()}
      >
        <button type="button" className="limit-reached-close" onClick={() => setDetail(null)} aria-label="Close">
          <X size={18} />
        </button>

        {detail.upgradeable !== false && gated ? (
          <>
            {hasCount ? <p className="limit-reached-count">You&apos;re using {used.toLocaleString()} of {limit.toLocaleString()} on your current plan.</p> : null}
            <UpgradePrompt {...gated} compact />
          </>
        ) : (
          <div className="limit-reached-absolute">
            <div className="limit-reached-icon"><Lock size={20} /></div>
            <h2>Account limit reached</h2>
            <p>{detail.message || "You've reached the maximum allowed for this feature."}</p>
            {hasCount ? <p className="limit-reached-count">{used.toLocaleString()} of {limit.toLocaleString()} used.</p> : null}
            <div className="limit-reached-actions">
              <button type="button" onClick={() => setDetail(null)}>Close</button>
              <Link to="/account" onClick={() => setDetail(null)}>View account</Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
