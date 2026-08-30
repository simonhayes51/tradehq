// The locked state is the moment a customer decides whether to pay, so
// what it says is a product surface in its own right - not decoration
// around a hidden button.
//
// These tests pin the three things that were wrong before: the prompt
// asked signed-out visitors to buy before they had an account, it never
// said what the feature was for, and it stated a price that was typed
// into the page rather than read from Stripe.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import UpgradePrompt from "./UpgradePrompt";
import { GATED } from "../lib/gatedFeatures";

const auth = vi.hoisted(() => ({ value: { isAuthenticated: true } }));
vi.mock("../../context/AuthContext", () => ({ useAuth: () => auth.value }));

const plans = vi.hoisted(() => ({ value: { data: undefined } }));
vi.mock("../hooks/useBillingPlans", async (importOriginal) => ({
  ...(await importOriginal()),
  useBillingPlans: () => plans.value,
}));

const tracked = vi.hoisted(() => ({ calls: [] }));
vi.mock("../../lib/analytics", async (importOriginal) => ({
  ...(await importOriginal()),
  track: (...args) => tracked.calls.push(args),
  trackOnce: (...args) => tracked.calls.push(args),
}));

function renderPrompt(props) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <UpgradePrompt {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  auth.value = { isAuthenticated: true };
  plans.value = { data: undefined };
  tracked.calls = [];
});

describe("UpgradePrompt", () => {
  it("answers what the feature is, why it is paid and what you get", () => {
    renderPrompt(GATED.trade_finder);
    expect(screen.getByRole("heading", { name: GATED.trade_finder.title })).toBeInTheDocument();
    expect(screen.getByText(GATED.trade_finder.what)).toBeInTheDocument();
    expect(screen.getByText(GATED.trade_finder.why)).toBeInTheDocument();
    for (const item of GATED.trade_finder.youGet) {
      expect(screen.getByText(item)).toBeInTheDocument();
    }
  });

  it("sends a signed-out visitor to the free account, not to checkout", () => {
    auth.value = { isAuthenticated: false };
    renderPrompt(GATED.trade_finder);
    const cta = screen.getByRole("link", { name: /Create free account/ });
    expect(cta.getAttribute("href")).toMatch(/^\/signup/);
    expect(screen.queryByRole("link", { name: /Upgrade to Elite/ })).not.toBeInTheDocument();
    expect(screen.getByText(/no card needed/i)).toBeInTheDocument();
  });

  it("sends a signed-in free member to billing, tagged with the feature they hit", () => {
    renderPrompt(GATED.ai_chat);
    const cta = screen.getByRole("link", { name: /Upgrade to Elite/ });
    expect(cta.getAttribute("href")).toBe("/billing?unlock=ai_chat");
  });

  it("shows the live Stripe price on the prompt itself", () => {
    plans.value = { data: { plans: { monthly: { configured: true, price_display: "£4.99/month" } } } };
    renderPrompt(GATED.trade_finder);
    expect(screen.getByText(/£4\.99\/month/)).toBeInTheDocument();
  });

  it("says nothing about price rather than guessing when Stripe is unconfigured", () => {
    plans.value = { data: { plans: { monthly: { configured: false } }, any_configured: false } };
    renderPrompt(GATED.trade_finder);
    expect(screen.queryByText(/£/)).not.toBeInTheDocument();
    // The CTA still works - an unreachable price must not remove the
    // only route to paying.
    expect(screen.getByRole("link", { name: /Upgrade to Elite/ })).toBeInTheDocument();
  });

  it("records that the prompt was seen, with no personal data attached", () => {
    renderPrompt(GATED.full_track_record);
    expect(tracked.calls.length).toBeGreaterThan(0);
    const [name, props] = tracked.calls[0];
    expect(name).toBe("upgrade_prompt_viewed");
    expect(props).toEqual({ feature: "full_track_record" });
  });

  it("never uses vague copy in any gated feature's definition", () => {
    // "More tools", "enhanced access" and "unlock more" are the labels
    // this pass exists to remove - a customer cannot decide whether they
    // want them.
    const vague = /more tools|enhanced access|unlock more|and more\b|premium features/i;
    for (const [key, entry] of Object.entries(GATED)) {
      const text = [entry.title, entry.what, entry.why, ...(entry.youGet || [])].join(" ");
      expect(vague.test(text), `${key} uses vague copy`).toBe(false);
      expect(entry.feature).toBe(key);
      expect(entry.youGet.length).toBeGreaterThanOrEqual(2);
    }
  });
});
