import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./axios", () => ({ default: { post: vi.fn(() => Promise.resolve({})) } }));
vi.mock("./v2/hooks/useFutggMarket", () => ({
  useFutggOpportunityPreview: () => ({ data: { item: null }, isLoading: false }),
  useFutggMarketFreshness: () => ({ data: { cards_priced: 12000 } }),
}));

import PublicSite from "./PublicSite";

describe("public homepage navigation", () => {
  beforeEach(() => {
    window.location.hash = "#/";
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("scrolls How it works without changing the HashRouter route", () => {
    render(<PublicSite />);
    fireEvent.click(screen.getByRole("button", { name: "How it works" }));
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(window.location.hash).toBe("#/");
  });

  it("keeps See live opportunities as an in-page scroll", () => {
    render(<PublicSite />);
    fireEvent.click(screen.getByRole("button", { name: "See live opportunities" }));
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(window.location.hash).toBe("#/");
  });

  it("keeps the launch links wired to their intended destinations", () => {
    render(<PublicSite />);
    expect(screen.getAllByRole("link", { name: "Pricing" })[0]).toHaveAttribute("href", "https://app.eafc.live/#/billing");
    expect(screen.getAllByRole("link", { name: "Log in" })[0]).toHaveAttribute("href", "https://app.eafc.live/#/login");
    expect(screen.getAllByRole("link", { name: /Start free/ })[0]).toHaveAttribute("href", "https://app.eafc.live/#/signup");
    expect(screen.getAllByRole("link", { name: "Discord" })[0].getAttribute("href")).toMatch(/^https:\/\/discord\.gg\//);
    expect(screen.getByRole("link", { name: "Privacy" })).toHaveAttribute("href", "#/privacy");
    expect(screen.getByRole("link", { name: "Terms" })).toHaveAttribute("href", "#/terms");
  });
});
