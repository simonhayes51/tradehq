import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./axios", () => ({ default: { post: vi.fn(() => Promise.resolve({})) } }));

const billingPlans = vi.hoisted(() => ({ value: { data: undefined } }));
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
    billingPlans.value = { data: undefined };
  });

  it("keeps public section navigation inside the homepage", () => {
    renderSite();
    fireEvent.click(screen.getAllByRole("button", { name: "How it works" })[0]);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(window.location.hash).toBe("#/");
  });

  it("keeps account CTAs pointed at the app", () => {
    renderSite();
    expect(screen.getAllByRole("link", { name: "Log in" })[0]).toHaveAttribute("href", "https://app.eafc.live/#/login");
    expect(screen.getAllByRole("link", { name: /Create free account/ })[0]).toHaveAttribute("href", "https://app.eafc.live/#/signup");
  });
});

describe("public Discord journeys", () => {
  beforeEach(() => {
    window.location.hash = "#/";
    Element.prototype.scrollIntoView = vi.fn();
    billingPlans.value = { data: undefined };
  });

  it("uses the Discord application install flow for Add to Discord and server-plan CTAs", () => {
    renderSite();
    const installLinks = [
      ...screen.getAllByRole("link", { name: /Add to Discord/i }),
      ...screen.getAllByRole("link", { name: /Set up Server Feed/i }),
    ];
    expect(installLinks.length).toBeGreaterThanOrEqual(3);
    for (const link of installLinks) {
      expect(link.getAttribute("href")).toMatch(/^https:\/\/discord\.com\/oauth2\/authorize\?/);
      expect(link.getAttribute("href")).toContain("client_id=1408193521986437251");
    }
  });

  it("keeps the Community Discord link as the support-server invite", () => {
    renderSite();
    const community = screen.getByRole("link", { name: "Discord" });
    expect(community.getAttribute("href")).toMatch(/^https:\/\/discord\.gg\//);
  });
});

describe("public guides", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    billingPlans.value = { data: undefined };
  });

  it("renders the guide hub as a public route", () => {
    window.location.hash = "#/guides";
    renderSite();
    expect(screen.getByRole("heading", { name: "Get set up without the guesswork." })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Set up the Discord Server Feed/ })).toHaveAttribute("href", "#/guides/discord-server-feed");
  });

  it("uses the bot install link from the Discord setup guide", () => {
    window.location.hash = "#/guides/discord-server-feed";
    renderSite();
    const add = screen.getByRole("link", { name: /Add to Discord/ });
    expect(add.getAttribute("href")).toMatch(/^https:\/\/discord\.com\/oauth2\/authorize\?/);
    expect(screen.getAllByText("/feed test").length).toBeGreaterThan(0);
  });
});

describe("public personal pricing", () => {
  beforeEach(() => {
    window.location.hash = "#/";
    Element.prototype.scrollIntoView = vi.fn();
    billingPlans.value = { data: undefined };
  });

  it("shows the price Stripe is actually configured with", () => {
    billingPlans.value = {
      data: {
        plans: {
          monthly: { configured: true, price_display: "£4.99/month" },
          season: { configured: true, price_display: "£29/season" },
        },
      },
    };
    renderSite();
    const pricing = document.getElementById("pricing");
    expect(within(pricing).getByText("£4.99/month")).toBeInTheDocument();
    expect(within(pricing).getByText("or £29/season")).toBeInTheDocument();
  });

  it("never invents a personal Elite price when Stripe pricing is unavailable", () => {
    billingPlans.value = { data: { plans: { monthly: { configured: false } }, any_configured: false } };
    renderSite();
    const personal = document.querySelector(".pub-personal-pricing");
    expect(within(personal).getByText("Pricing unavailable")).toBeInTheDocument();
    expect(within(personal).queryByText(/£3\/month|£25\/season/)).not.toBeInTheDocument();
  });

  it("sends a signed-out Elite prospect through free signup first and returns them to billing", () => {
    billingPlans.value = { data: { plans: { monthly: { configured: true, price_display: "£3/month" } } } };
    renderSite();
    const elite = screen.getByRole("link", { name: "Create account for Elite" });
    expect(elite.getAttribute("href")).toMatch(/^https:\/\/app\.eafc\.live\/#\/signup\?from=/);
    expect(decodeURIComponent(elite.getAttribute("href"))).toContain("/billing?unlock=elite");
  });

  it("calls the paid plan Elite and says Free is permanent rather than a trial", () => {
    renderSite();
    expect(screen.getAllByText(/Elite/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Premium/)).not.toBeInTheDocument();
    expect(screen.getByText(/Free permanently, not a trial/i)).toBeInTheDocument();
  });
});
