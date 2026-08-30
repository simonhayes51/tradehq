// src/v2/lib/limits.js
//
// Display copies of the server's allowances.
//
// These are for COPY ONLY - "Free members can keep 10 cards" - and are
// never the thing that enforces anything. Enforcement lives in
// app/auth/entitlements.py and every limit is checked server-side; a
// number here that drifts from the server produces wrong copy, not a
// bypass.
//
// Prefer `limits` from useEntitlements() where you have it, which is the
// server's own answer for THIS user. Use these only where no entitlement
// context is available - the signed-out pricing page, the signup page.
export const FREE_LIMITS = {
  dailyOpportunities: 2,
  watchlist: 10,
  alerts: 3,
  openTrades: 5,
  historyDays: 30,
  trackRecordDays: 30,
  eliteWatchlist: 500,
  eliteAlerts: 100,
};

// One name for the paid plan, everywhere. The product previously called
// it "Premium" on the billing page and "ELITE" in the sidebar and account
// page, which reads as two different products.
export const PAID_PLAN_NAME = "Elite";
