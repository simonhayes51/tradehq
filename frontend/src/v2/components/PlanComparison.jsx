// src/v2/components/PlanComparison.jsx
//
// The Free vs Elite table, defined once and used by the pricing page, the
// signup page and every locked state that needs to explain the
// difference.
//
// Every row is a concrete, checkable difference. "More tools" and
// "enhanced access" are not rows, because a customer cannot tell whether
// they want them.
import { Check, X } from "lucide-react";
import { FREE_LIMITS } from "../lib/limits";

export const PLAN_ROWS = [
  {
    label: "Daily opportunities",
    free: `${FREE_LIMITS.dailyOpportunities} complete picks a day`,
    elite: "The full live feed, all day",
  },
  // Search itself is public. What the account adds is the numbers behind
  // the card, which is why these two rows are worded as the difference
  // rather than as a bare tick: a row that reads "included" on Free, when
  // it is also available with no account at all, is not a reason to sign
  // up for anything.
  { label: "Search any card", free: "Everyone, no account needed", elite: true },
  { label: "Exact live price", free: true, elite: true },
  { label: "What a card is really worth", free: true, elite: true },
  { label: "Completed sales & price history", free: true, elite: true },
  {
    label: "Trade Finder",
    free: false,
    elite: "Search every trade by budget, risk, position and return",
  },
  {
    label: "Watchlist",
    free: `${FREE_LIMITS.watchlist} cards`,
    elite: `${FREE_LIMITS.eliteWatchlist} cards`,
  },
  {
    label: "Price alerts",
    free: `${FREE_LIMITS.alerts} active alerts`,
    elite: `${FREE_LIMITS.eliteAlerts} active alerts`,
  },
  {
    label: "Portfolio",
    free: `${FREE_LIMITS.openTrades} open trades, ${FREE_LIMITS.historyDays} days of history`,
    elite: "Unlimited trades and full history",
  },
  {
    label: "Track record",
    free: `Last ${FREE_LIMITS.trackRecordDays} days, headline results`,
    elite: "Every published call, every window, full ledger",
  },
  { label: "AI trading assistant", free: false, elite: "Ask about any card or budget" },
  { label: "Members-only Discord", free: false, elite: "The Elite channel in the EAFC Live Discord" },
];

function Cell({ value }) {
  if (value === true) return <span className="plan-yes"><Check size={15} /> Included</span>;
  if (value === false) return <span className="plan-no"><X size={15} /> Not included</span>;
  return <span className="plan-detail">{value}</span>;
}

export default function PlanComparison({ elitePrice, className = "" }) {
  return (
    <div className={`plan-comparison ${className}`.trim()}>
      <div className="plan-comparison-head">
        <span />
        <span className="plan-col-free">Free</span>
        <span className="plan-col-elite">
          Elite{elitePrice ? <small>{elitePrice}</small> : null}
        </span>
      </div>
      {PLAN_ROWS.map((row) => (
        <div className="plan-comparison-row" key={row.label}>
          <span className="plan-row-label">{row.label}</span>
          <Cell value={row.free} />
          <Cell value={row.elite} />
        </div>
      ))}
    </div>
  );
}
