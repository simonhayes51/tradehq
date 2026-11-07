import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PageBanner from "./PageBanner";

describe("V3 editorial page banner", () => {
  it("renders the page identity and a working navigation destination", () => {
    render(<MemoryRouter><PageBanner kicker="TRADING DESK" title="THE SHORTLIST." description="Live opportunities" action="FINDER" actionTo="/trade-finder" /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "THE SHORTLIST." })).toBeTruthy();
    expect(screen.getByText("Live opportunities")).toBeTruthy();
    expect(screen.getByRole("link", { name: /FINDER/i }).getAttribute("href")).toBe("/trade-finder");
  });
  it("renders without an optional action", () => {
    render(<MemoryRouter><PageBanner title="MY PORTFOLIO" /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "MY PORTFOLIO" })).toBeTruthy();
  });
});
