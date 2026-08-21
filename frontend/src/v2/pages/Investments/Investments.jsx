// src/v2/pages/Investments/Investments.jsx
//
// Investment Engine surface - deliberately a SEPARATE page/nav item from
// Opportunities/Trade Finder, never merged with them. Opportunities and
// Trade Finder answer "is there money in this listing right now" (a
// single mispriced BIN, act in minutes). This page answers a different
// question: "is this market segment likely to appreciate over the next
// 1-3 days, with enough supply to build a real position" (a market-cycle
// call, not a snipe). See backend/app/routers/v2/investments.py's module
// docstring for the full Trade-vs-Investment distinction this mirrors.
//
// The Investment Engine can be disabled entirely (INVESTMENT_ENGINE_
// ENABLED unset) or running in shadow mode (calculating but publishing
// nothing) on any given deployment - both cases render as a calm empty
// state here, never an error banner, since "nothing published yet" is
// the expected steady state before/just after a launch.
import { useState } from "react";
import { AlertTriangle, ArrowRight, Gauge, LineChart, ShieldAlert, Wallet } from "lucide-react";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import {
  useInvestmentBudgetPlan,
  useInvestmentMarketState,
  useInvestmentSignals,
} from "../../hooks/useInvestments";
import { PageHead } from "../Players/Players";
import "../TradeFinder/trade-finder.css";
import "../Opportunities/opportunities.css";
import "./investments.css";

// Plain-language copy for each cohort regime state - no drawdown
// percentiles or severity scores surfaced here by design (per the
// product spec: "understandable without exposing statistical jargon").
const STATE_COPY = {
  NORMAL: { label: "Normal", tone: "neutral", copy: "No unusual market-cycle activity detected right now." },
  OVERHEATED: { label: "Overheated", tone: "warn", copy: "Prices look inflated by launch hype. Avoid buying into the excitement — this is not an entry point." },
  CRASHING: { label: "Crashing", tone: "danger", copy: "Prices are actively falling. Cheaper is not the same as undervalued — do not buy just because it's dropped." },
  WATCH: { label: "Watch", tone: "warn", copy: "The decline has reached an extreme region. Too early to buy — watching for the fall to slow down." },
  BOTTOMING: { label: "Bottoming", tone: "warn", copy: "The decline is decelerating. Confirming with live sales and listing data before opening the window." },
  INVESTMENT_WINDOW: { label: "Investment Window Open", tone: "good", copy: "Conditions historically favour accumulation. Investable cards are listed below." },
  REBOUND: { label: "Investment Window Open", tone: "good", copy: "The rebound is in progress. Existing positions are being monitored for their exit." },
  EXIT: { label: "Closing", tone: "neutral", copy: "This window is closing — new positions are no longer being opened here." },
  CLOSED: { label: "Closed", tone: "neutral", copy: "No active window for this segment." },
  LAUNCH_WINDOW_OPEN: { label: "Accumulation Window Open", tone: "good", copy: "Early in the launch window — a good time to build a position for a multi-day hold." },
  LAUNCH_WINDOW_CLOSED: { label: "Accumulation Window Closed", tone: "neutral", copy: "Outside the early-launch accumulation window for this segment." },
};

const COHORT_LABELS = {
  premium_meta: "Premium & Meta",
  mid_tier_meta: "Mid-Tier Meta",
  desirable_starter: "Desirable Starters",
  ordinary: "Ordinary",
  likely_fodder: "Likely Fodder",
};

const CONFIDENCE_LABEL = (score) => {
  if (score == null) return "—";
  const pct = Number(score) * 100;
  if (pct >= 70) return "High";
  if (pct >= 40) return "Medium";
  return "Low";
};

const TIER_LABEL = (tier) => {
  if (!tier) return "—";
  const t = String(tier).toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
};

export default function Investments() {
  const [budget, setBudget] = useState("");
  const marketQuery = useInvestmentMarketState();
  const signalsQuery = useInvestmentSignals({ page_size: 40 });
  const budgetAmount = budget && Number(budget) > 0 ? Number(budget) : undefined;
  const budgetQuery = useInvestmentBudgetPlan(budgetAmount);

  const states = Array.isArray(marketQuery.data?.states) ? marketQuery.data.states : [];
  const signals = Array.isArray(signalsQuery.data?.signals) ? signalsQuery.data.signals : [];
  const notDeployed = [marketQuery, signalsQuery].some(
    (q) => q.isError && [404, undefined].includes(q.error?.response?.status),
  );

  return (
    <main className="v2-destination investments-page">
      <PageHead
        eyebrow="INVESTMENTS"
        title="Where the market is likely to move, not just what's mispriced today"
        copy="This is different from Opportunities and Trade Finder — those find a single listing worth flipping right now. Investments looks at whole market segments (a cohort of cards) going through a cycle — a launch crash, a bottom, a rebound — and flags cards worth building a multi-day position in, with enough supply to actually do it."
      />

      <section className="inv-regime-strip" aria-label="Market cycle state">
        {marketQuery.isLoading ? (
          <EmptyState compact icon={<Gauge size={20} />} text="Reading current market cycle state…" />
        ) : notDeployed || !states.length ? (
          <EmptyState
            compact
            icon={<LineChart size={20} />}
            text="No investment-cycle signal is available right now. This shows up automatically once a market segment enters an unusual cycle (a launch crash, a bottom, a rebound)."
          />
        ) : (
          states.map((state) => <RegimeCard key={`${state.strategy_key}-${state.cohort_key}`} state={state} />)
        )}
      </section>

      <section className="inv-budget" aria-label="Budget planner">
        <div className="inv-budget-head">
          <Wallet size={16} />
          <div>
            <h2>How much do you have to deploy?</h2>
            <p>Enter a coin budget and it's spread across the active investments below — never all in one card.</p>
          </div>
        </div>
        <div className="inv-budget-input">
          <input
            type="number"
            min="0"
            step="1000"
            placeholder="e.g. 250000"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            aria-label="Budget in coins"
          />
          {budgetAmount ? <CoinValue value={budgetAmount} /> : null}
        </div>
        {budgetAmount && budgetQuery.data ? (
          <div className="inv-budget-result">
            <span>
              Allocating <CoinValue value={budgetQuery.data.allocated} /> across {budgetQuery.data.allocations?.length ?? 0} card{budgetQuery.data.allocations?.length === 1 ? "" : "s"}
            </span>
            {budgetQuery.data.unallocated > 0 ? (
              <span className="inv-budget-unallocated"><CoinValue value={budgetQuery.data.unallocated} /> left unallocated</span>
            ) : null}
          </div>
        ) : null}
      </section>

      <div className="finder-result-head">
        <div>
          <LineChart size={16} />
          <strong>
            {signalsQuery.isLoading
              ? "Scanning for investment candidates…"
              : `${signals.length} active investment${signals.length === 1 ? "" : "s"}`}
          </strong>
        </div>
      </div>

      {signalsQuery.isLoading ? (
        <EmptyState icon={<LineChart size={25} />} text="Checking active investment signals…" />
      ) : notDeployed ? (
        <EmptyState
          icon={<ShieldAlert size={25} />}
          text="Investments aren't available on this deployment yet."
        />
      ) : signalsQuery.isError ? (
        <EmptyState icon={<ShieldAlert size={25} />} error text="Investments could not be loaded." action={<button onClick={() => signalsQuery.refetch()}>Try again</button>} />
      ) : signals.length ? (
        <section className="finder-grid inv-grid">
          {signals
            .filter((s) => !budgetAmount || (s.max_buy != null && s.max_buy <= budgetAmount))
            .map((signal) => (
              <InvestmentCard key={signal.id} signal={signal} />
            ))}
        </section>
      ) : (
        <EmptyState
          icon={<LineChart size={25} />}
          text="No active investment candidates right now. This is normal outside a market cycle — check back once a segment enters a crash, bottom, or accumulation window above."
        />
      )}
    </main>
  );
}

function RegimeCard({ state }) {
  const copy = STATE_COPY[state.state] || { label: state.state, tone: "neutral", copy: "" };
  const cohortLabel = COHORT_LABELS[state.cohort_key] || state.cohort_key;
  return (
    <div className={`inv-regime-card tone-${copy.tone}`}>
      <div className="inv-regime-card-head">
        <span className="inv-regime-cohort">{cohortLabel}</span>
        <span className={`inv-regime-badge tone-${copy.tone}`}>{copy.label}</span>
      </div>
      <p>{copy.copy}</p>
    </div>
  );
}

function InvestmentCard({ signal }) {
  const roi = signal.expected_roi_pct != null ? Number(signal.expected_roi_pct) : null;
  const confidence = CONFIDENCE_LABEL(signal.confidence);
  const supply = TIER_LABEL(signal.investability);
  const holdMin = signal.expected_hold_hours_min != null ? Math.round(signal.expected_hold_hours_min / 24) : null;
  const holdMax = signal.expected_hold_hours_max != null ? Math.round(signal.expected_hold_hours_max / 24) : null;
  const reasons = Array.isArray(signal.rationale) ? signal.rationale : [];
  const risks = Array.isArray(signal.risk_flags) ? signal.risk_flags : [];

  return (
    <div className="finder-card inv-card">
      <div className="finder-card-top">
        <div>
          <h2>{signal.name || "Unknown player"}</h2>
          <div className="v2-meta-pills">
            <span>{signal.rating ?? "—"} OVR</span>
            <span>{COHORT_LABELS[signal.cohort] || signal.cohort}</span>
          </div>
        </div>
        <span className={`inv-confidence conf-${confidence.toLowerCase()}`}>{confidence}<small>CONFIDENCE</small></span>
      </div>

      <div className="finder-signals">
        <div className="entry"><span>BUY ≤</span><CoinValue value={signal.max_buy} /></div>
        <ArrowRight size={18} />
        <div className="profit"><span>TARGET</span><CoinValue value={signal.target_price} /><small>{roi == null ? "—" : `+${roi.toFixed(1)}%`} after tax</small></div>
      </div>

      <div className="opp-targets inv-targets">
        <div><span>Expected hold</span><b>{holdMin != null && holdMax != null ? `${holdMin}–${holdMax} day${holdMax === 1 ? "" : "s"}` : "—"}</b></div>
        <div><span>Supply</span><b className={`supply-${supply.toLowerCase()}`}>{supply}</b></div>
      </div>

      {reasons.length ? (
        <div className="inv-why">
          <span>Why now</span>
          <ul>{reasons.slice(0, 3).map((r, i) => <li key={i}>{humanizeReason(r)}</li>)}</ul>
        </div>
      ) : null}

      {risks.length ? (
        <div className="inv-risk">
          <span><AlertTriangle size={13} /> Risk</span>
          <ul>{risks.slice(0, 3).map((r, i) => <li key={i}>{humanizeReason(r)}</li>)}</ul>
        </div>
      ) : null}
    </div>
  );
}

// Rationale/risk_flags come through as short machine-ish tokens
// (e.g. "cohort_state=INVESTMENT_WINDOW", "fc26_prior_still_heavily_
// weighted") - turn the common ones into a plain sentence rather than
// showing raw snake_case to users.
function humanizeReason(token) {
  if (typeof token !== "string") return String(token);
  const known = {
    cohort_regime_open: "The wider market segment just entered its investment window.",
    decline_decelerating: "The price decline has slowed down significantly.",
    within_launch_window: "Still early in the launch accumulation window.",
    fc26_prior_still_heavily_weighted: "Based partly on last year's launch pattern — still being confirmed by live data.",
  };
  if (known[token]) return known[token];
  if (token.includes("=")) {
    const [key, value] = token.split("=");
    return `${key.replace(/_/g, " ")}: ${value}`;
  }
  return token.replace(/_/g, " ");
}
