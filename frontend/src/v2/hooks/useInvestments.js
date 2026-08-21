// src/v2/hooks/useInvestments.js
//
// Hooks for the Investment Engine's GET /api/v2/investments/* endpoints
// (backend/app/routers/v2/investments.py) - a separate surface from
// useFutggMarket.js's opportunities/trade-finder hooks. See that
// router's own docstring for the Trade-vs-Investment distinction this
// mirrors in the UI (separate page, separate nav item, never merged).
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

// Same defensive request options useFutggMarket.js uses: the Investment
// Engine can be disabled (INVESTMENT_ENGINE_ENABLED unset) or simply have
// nothing published yet (shadow mode) on any given deployment, and
// neither case should be treated as an app-breaking error.
const REQUEST_OPTS = { __skipAuthRedirect: true, __noRetry: true, timeout: 8_000 };

function cleanParams(params = {}) {
  const out = {};
  for (const [key, value] of Object.entries(params)) {
    if (value === "" || value === null || value === undefined) continue;
    out[key] = value;
  }
  return out;
}

// GET /api/v2/investments/market-state - the plain-language regime
// indicator ("CRASHING" / "BOTTOMING" / "INVESTMENT WINDOW OPEN").
export function useInvestmentMarketState(params = {}) {
  const clean = cleanParams(params);
  return useQuery({
    queryKey: ["v2", "investments", "market-state", clean],
    queryFn: async ({ signal }) =>
      (await api.get("/api/v2/investments/market-state", { params: clean, signal, ...REQUEST_OPTS })).data,
    retry: false,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

// GET /api/v2/investments/signals - active (published) investment
// candidates.
export function useInvestmentSignals(params = {}, { enabled = true } = {}) {
  const clean = cleanParams({ status: "active", ...params });
  return useQuery({
    queryKey: ["v2", "investments", "signals", clean],
    queryFn: async ({ signal }) =>
      (await api.get("/api/v2/investments/signals", { params: clean, signal, ...REQUEST_OPTS })).data,
    enabled,
    retry: false,
    staleTime: 30_000,
  });
}

// GET /api/v2/investments/ledger - closed signals, winners and losers
// alike (the Investment Ledger).
export function useInvestmentLedger(params = {}) {
  const clean = cleanParams(params);
  return useQuery({
    queryKey: ["v2", "investments", "ledger", clean],
    queryFn: async ({ signal }) =>
      (await api.get("/api/v2/investments/ledger", { params: clean, signal, ...REQUEST_OPTS })).data,
    retry: false,
    staleTime: 60_000,
  });
}

// GET /api/v2/investments/summary - published track record per strategy.
export function useInvestmentSummary(windowDays = 90) {
  return useQuery({
    queryKey: ["v2", "investments", "summary", windowDays],
    queryFn: async ({ signal }) =>
      (await api.get("/api/v2/investments/summary", { params: { window_days: windowDays }, signal, ...REQUEST_OPTS })).data,
    retry: false,
    staleTime: 60_000,
  });
}

// GET /api/v2/investments/budget-plan?amount=... - "I have N coins,
// where can I deploy it". Only fetched once a budget amount is chosen.
export function useInvestmentBudgetPlan(amount, strategy) {
  const clean = cleanParams({ amount, strategy });
  return useQuery({
    queryKey: ["v2", "investments", "budget-plan", clean],
    queryFn: async ({ signal }) =>
      (await api.get("/api/v2/investments/budget-plan", { params: clean, signal, ...REQUEST_OPTS })).data,
    enabled: !!amount && amount > 0,
    retry: false,
    staleTime: 30_000,
  });
}
