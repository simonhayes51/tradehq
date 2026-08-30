// src/v2/hooks/useBillingPlans.js
//
// GET /api/billing/plans - the ONE source of what Elite costs.
//
// The price used to be typed into the Billing page ("£3"), typed again
// into the backend's subscription response ("£9.99/month"), and charged
// by Stripe as a third number entirely. Anything that shows a price now
// reads it from here, so a change in Stripe reaches every upgrade prompt
// without a deploy.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function useBillingPlans() {
  return useQuery({
    queryKey: ["billing", "plans"],
    queryFn: async () =>
      (await api.get("/api/billing/plans", {
        __skipAuthRedirect: true,
        __noRetry: true,
        timeout: 8000,
      })).data,
    retry: false,
    // Pricing changes on a human timescale, and this is read by nearly
    // every locked state in the app - once per session is plenty.
    staleTime: 15 * 60_000,
  });
}

/**
 * The headline price to show, or null.
 *
 * Returns null rather than a placeholder when Stripe is unconfigured or
 * unreachable: a made-up price is worse than no price, and every caller
 * here renders "Pricing unavailable" instead of inventing one.
 */
export function headlinePrice(plans, cycle = "monthly") {
  const plan = plans?.plans?.[cycle];
  if (!plan?.configured || !plan.price_display) return null;
  return plan.price_display;
}
