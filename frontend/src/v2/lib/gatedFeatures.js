// src/v2/lib/gatedFeatures.js
//
// What every locked surface says, in one file.
//
// Written to answer the question a customer actually has at a lock -
// "what is this and why can't I see it" - not to sell. Each entry is
// concrete enough that someone could decide they DON'T want it, which is
// the test of whether copy is honest.
//
// `feature` matches the backend's FEATURE_MIN_TIER key, so a gate here
// and the 402 that would come back from the API name the same thing.
export const GATED = {
  trade_finder: {
    feature: "trade_finder",
    title: "Trade Finder is part of Elite",
    what:
      "Search every live-priced card at once — set your budget, minimum return, risk level, " +
      "position and rarity, and sort the whole market by what matters to you.",
    why:
      "It searches the full eligible universe rather than a shortlist, which is the difference " +
      "between the daily picks and finding your own trades.",
    youGet: [
      "Every card the engine can price, filterable and sortable",
      "Five preset strategies: best overall, quick flips, low risk, big profit, budget picks",
      "Live entry state, so you can tell a trade you can take now from one to watch",
    ],
  },
  opportunity_feed: {
    feature: "opportunity_feed",
    title: "The full opportunities feed is part of Elite",
    what:
      "Every buy signal the engine finds, all day, with the entry price, sell target and " +
      "expected profit on each one.",
    why:
      "Free includes two complete picks a day so you can judge whether the calls are any good. " +
      "The full feed is the product itself.",
    youGet: [
      "Every opportunity as it appears, not two a day",
      "Exact buy-below and sell-target prices on all of them",
      "Refreshed as the market moves, not on a daily cycle",
    ],
  },
  ai_chat: {
    feature: "ai_chat",
    title: "The trading assistant is part of Elite",
    what:
      "Ask about any card or budget in plain English and get an answer built from live market " +
      "data — never invented, and never a prediction.",
    why: "Every message costs a real model call, and every answer reads the full paid dataset.",
    youGet: [
      "\"Should I buy Mbappé 92?\" — with the live price, verdict and how old the price is",
      "\"Who should I buy for 40k?\" — real trades that fit, right now",
      "\"How accurate are your recommendations?\" — from the published record",
    ],
  },
  full_track_record: {
    feature: "full_track_record",
    title: "The complete track record is part of Elite",
    what:
      "Every published recommendation we've graded, across every time window, with the full " +
      "chronological ledger — wins, losses, break-evens and the ones that never reached their buy price.",
    why:
      "The headline results stay public because you should be able to judge us before paying. " +
      "The full ledger is a working tool for deciding which kinds of calls to follow.",
    youGet: [
      "7-day, 30-day, 90-day and all-time windows",
      "Every individual call with its entry, exit and result",
      "Filter by win, loss or never-entered",
    ],
  },
  realtime_alerts: {
    feature: "realtime_alerts",
    title: "More price alerts are part of Elite",
    what: "Get told when a card you're tracking moves, without watching the market yourself.",
    why: "Every alert rule is evaluated continuously against live prices.",
    youGet: ["100 active alerts instead of 3", "Price and sales-activity triggers", "Alerts on every card you track"],
  },
  watchlist: {
    feature: "watchlist",
    title: "A bigger watchlist is part of Elite",
    what: "Track more cards and keep watching the ones you already have.",
    why: "Each watched card is priced and re-checked on its own schedule.",
    youGet: ["500 cards instead of 10", "Price alerts on any of them", "Movement since you added each card"],
  },
  trade_journal: {
    feature: "trade_journal",
    title: "Full portfolio history is part of Elite",
    what: "Keep every trade you've logged and see your real profit over any period.",
    why: "Free keeps recent history so you can see the tool work; Elite keeps all of it.",
    youGet: ["Unlimited open trades", "Your complete history, not the last 30 days", "Profit over any period"],
  },
};
