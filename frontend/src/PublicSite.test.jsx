import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./axios", () => ({ default: { post: vi.fn(() => Promise.resolve({})) } }));
vi.mock("./v2/hooks/useFutggMarket", () => ({
  useFutggOpportunityPreview: () => ({ data: { item: null }, isLoading: false }),
  useFutggMarketFreshness: () => ({ data: { cards_priced: 12000 } }),
}));

// The pricing table and the track-record summary both read live API data
// (GET /api/billing/plans, GET /api/v2/track-record). They are mocked per
// test below; a real QueryClient is still provided because that is how
// main.jsx mounts this tree, and a hook added here without one would
// crash the public homepage in production, not just this file.
const trackRecord = vi.hoisted(() => ({ value: { data: undefined } }));
const billingPlans = vi.hoisted(() => ({ value: { data: undefined } }));
vi.mock("./v2/hooks/useTrackRecord", () => ({ useTrackRecord: () => trackRecord.value }));
vi.mock("./v2/hooks/useBillingPlans", async (importOriginal) => ({
  ...(await importOriginal()),
  useBillingPlans: () => billingPlans.value,
}));

import PublicSite from "./PublicSite";

function renderSite() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PublicSite />
    </QueryClientProvider>,
  );
}

describe("public homepage navigation", () => {
  beforeEach(() => {
    window.location.hash = "#/";
    Element.prototype.scrollIntoView = vi.fn();
    trackRecord.value = { data: undefined };
    billingPlans.value = { data: undefined };
  });

  it("scrolls How it works without changing the HashRouter route", () => {
    renderSite();
    fireEvent.click(screen.getAllByRole("button", { name: "How it works" })[0]);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(window.location.hash).toBe("#/");
  });

  it("keeps See live opportunities as an in-page scroll", () => {
    renderSite();
    fireEvent.click(screen.getByRole("button", { name: "See live opportunities" }));
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(window.location.hash).toBe("#/");
  });

  it("keeps the launch links wired to their intended destinations", () => {
    renderSite();
    // Personal/Server pricing are now in-page nav items (both the personal
    // and Discord pricing sections live on this page) rather than a jump
    // straight to the app - covered by the "scrolls to the pricing
    // section" test below. Billing (manage an existing subscription) is
    // still a real link to the app.
    expect(screen.getByRole("link", { name: "Billing" })).toHaveAttribute("href", "https://app.eafc.live/#/billing");
    expect(screen.getAllByRole("link", { name: "Log in" })[0]).toHaveAttribute("href", "https://app.eafc.live/#/login");
    expect(screen.getAllByRole("link", { name: /Create free account/ })[0]).toHaveAttribute("href", "https://app.eafc.live/#/signup");
    expect(screen.getAllByRole("link", { name: "Discord" })[0].getAttribute("href")).toMatch(/^https:\/\/discord\.gg\//);
    expect(screen.getByRole("link", { name: "Privacy" })).toHaveAttribute("href", "#/privacy");
    expect(screen.getByRole("link", { name: "Terms" })).toHaveAttribute("href", "#/terms");
  });

  it("scrolls the nav's Personal pricing link to the on-page pricing section", () => {
    renderSite();
    fireEvent.click(screen.getAllByRole("button", { name: "Personal pricing" })[0]);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(window.location.hash).toBe("#/");
  });

  it("scrolls the nav's Server pricing link to the on-page Discord pricing section", () => {
    renderSite();
    fireEvent.click(screen.getAllByRole("button", { name: "Server pricing" })[0]);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(window.location.hash).toBe("#/");
  });
});

describe("public guides", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    trackRecord.value = { data: undefined };
    billingPlans.value = { data: undefined };
  });

  it("links to the guide hub from the public navigation", () => {
    window.location.hash = "#/";
    renderSite();
    expect(screen.getAllByRole("link", { name: "Guides" })[0]).toHaveAttribute("href", "#/guides");
  });

  it("renders the guide hub as a real public route", () => {
    window.location.hash = "#/guides";
    renderSite();
    expect(screen.getByRole("heading", { name: "Get set up without the guesswork." })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Set up the Discord Server Feed/ })).toHaveAttribute("href", "#/guides/discord-server-feed");
  });

  it("renders the full Discord setup walkthrough", () => {
    window.location.hash = "#/guides/discord-server-feed";
    renderSite();
    expect(screen.getByRole("heading", { name: "Set up automatic trading posts in your server." })).toBeInTheDocument();
    expect(screen.getAllByText("/feed test").length).toBeGreaterThan(0);
    expect(screen.getByText(/Server Feed\+ is up to 15/)).toBeInTheDocument();
  });
});

describe("public pricing", () => {
  beforeEach(() => {
    window.location.hash = "#/";
    Element.prototype.scrollIntoView = vi.fn();
    trackRecord.value = { data: undefined };
    billingPlans.value = { data: undefined };
  });

  it("shows the price Stripe is actually configured with, not a number typed into the page", () => {
    billingPlans.value = {
      data: {
        plans: {
          monthly: { configured: true, price_display: "£4.99/month" },
          season: { configured: true, price_display: "£29/season" },
        },
      },
    };
    renderSite();
    expect(screen.getByText("£4.99/month or £29/season")).toBeInTheDocument();
  });

  it("shows no price at all rather than a guess when billing is unconfigured", () => {
    billingPlans.value = { data: { plans: { monthly: { configured: false } }, any_configured: false } };
    renderSite();
    // The old page hardcoded "£3/mo or £25/season" - the exact class of
    // claim that must never outlive a price change in Stripe. "£0" on the
    // Free card is not a Stripe number and stays. Scoped to the personal
    // pricing section: the separate Discord Server Feed pricing further
    // down the page is flat-rate product copy, not read from this
    // (personal-Elite-only) billing-plans hook, so it legitimately shows
    // "£9.99/month" regardless of Stripe's personal-plan configuration.
    expect(within(document.getElementById("pricing")).queryByText(/\/month|\/season/)).not.toBeInTheDocument();
  });

  it("calls the paid plan Elite and never Premium", () => {
    renderSite();
    expect(screen.getAllByText(/Elite/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Premium/)).not.toBeInTheDocument();
  });

  it("says the free plan is permanent rather than a trial", () => {
    renderSite();
    expect(screen.getByText(/free permanently, not a trial/i)).toBeInTheDocument();
    expect(screen.queryByText(/7 days free/i)).not.toBeInTheDocument();
  });

  it("states concrete allowances instead of Limited Watchlist", () => {
    renderSite();
    expect(screen.getByText(/10 cards/)).toBeInTheDocument();
    expect(screen.queryByText(/Limited Watchlist/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Unlimited Watchlist/)).not.toBeInTheDocument();
  });
});

describe("public track record", () => {
  beforeEach(() => {
    window.location.hash = "#/";
    Element.prototype.scrollIntoView = vi.fn();
    trackRecord.value = { data: undefined };
    billingPlans.value = { data: undefined };
  });

  it("renders nothing at all when there is no published data", () => {
    trackRecord.value = { data: { published: { has_published_data: false } } };
    renderSite();
    expect(screen.queryByText(/How the calls have actually done/i)).not.toBeInTheDocument();
  });

  it("renders nothing when the sample is too small to quote a rate from", () => {
    trackRecord.value = {
      data: { published: { has_published_data: true, has_enough_data: false, wins: 3, losses: 1 } },
    };
    renderSite();
    expect(screen.queryByText(/How the calls have actually done/i)).not.toBeInTheDocument();
  });

  it("shows the published headline numbers when there is a real record", () => {
    trackRecord.value = {
      data: {
        window_days: 30,
        published: {
          has_published_data: true, has_enough_data: true,
          published_opportunities: 48, wins: 31, losses: 12, win_rate_pct: 72.1,
        },
      },
    };
    renderSite();
    expect(screen.getByText("48")).toBeInTheDocument();
    expect(screen.getByText("72.1%")).toBeInTheDocument();
    // Losses are never hidden on the public page.
    expect(screen.getByText("12")).toBeInTheDocument();
  });
});
