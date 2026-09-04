// src/v2/components/StatusLegend.jsx
//
// One shared explanation of what LIVE / BUY / WATCH / WAIT / AVOID mean,
// reused everywhere a recommendation badge appears (Home, Trade Finder,
// Opportunities, Player Detail) instead of each page inventing its own
// wording or leaving the labels unexplained. A native `title` attribute
// keeps this a zero-dependency tooltip - no popover library, works with
// keyboard focus and screen readers via the accessible name below.
import { Info } from "lucide-react";

export const STATUS_MEANINGS = {
  LIVE: "Prices on this page are refreshed from the live market, not a cached snapshot.",
  BUY: "The engine has a real entry price and expects a worthwhile profit at today's price.",
  "BUY NOW": "The engine has a real entry price and expects a worthwhile profit at today's price.",
  WATCH: "Not buyable at a sensible price right now. Worth tracking - it's within reach of a real entry.",
  WAIT: "A useful card, but the current price doesn't leave enough margin. Wait for a better entry.",
  AVOID: "The current price does not leave room for a reasonable profit after EA's tax. Not a target.",
  SELL: "If you're already holding this card, the model says the position is worth closing now.",
};

const LABEL = Object.keys(STATUS_MEANINGS).filter((k) => k !== "BUY NOW");

export default function StatusLegend({ className = "" }) {
  return (
    <span
      className={`status-legend ${className}`}
      tabIndex={0}
      role="button"
      aria-label={`What do these labels mean? ${LABEL.map((k) => `${k}: ${STATUS_MEANINGS[k]}`).join(" ")}`}
      title={LABEL.map((k) => `${k} — ${STATUS_MEANINGS[k]}`).join("\n")}
    >
      <Info size={13} />
    </span>
  );
}
