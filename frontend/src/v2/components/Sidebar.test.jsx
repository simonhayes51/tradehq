// The Free -> Elite path through the navigation.
//
// Before this pass /billing was a route with nothing in the signed-in
// app linking to it: a Free member could only reach it by happening to
// hit a lock. These tests pin the persistent entry point, and that a
// locked item is a working link rather than an inert label.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Sidebar from "./Sidebar";
import { useAuth } from "../../context/AuthContext";
import { useEntitlements } from "../../context/EntitlementsContext";
import { useAiChatAvailability } from "../hooks/useAiChatAvailability";

vi.mock("../../context/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("../../context/EntitlementsContext", () => ({ useEntitlements: vi.fn() }));
vi.mock("../hooks/useAiChatAvailability", () => ({ useAiChatAvailability: vi.fn() }));

const ELITE_FEATURES = ["trade_finder", "opportunity_feed", "ai_chat", "full_track_record"];

function setup({ authenticated = true, tier = "free", features = [], isAdmin = false } = {}) {
  useAuth.mockReturnValue({ isAuthenticated: authenticated });
  useEntitlements.mockReturnValue({ isAdmin, features, tier, limits: {} });
  useAiChatAvailability.mockReturnValue({ data: { configured: true } });
  return render(<MemoryRouter><Sidebar /></MemoryRouter>);
}

beforeEach(() => vi.clearAllMocks());

describe("sidebar branding", () => {
  it("uses the real EAFC.Live logo instead of the EL placeholder", () => {
    setup();
    const brand = screen.getByRole("link", { name: "EAFC.Live home" });
    expect(brand.querySelector("img")).toHaveAttribute("src", "/icons/icon-192x192.png");
    expect(brand.textContent).not.toContain("EL");
  });
});

describe("upgrade entry point", () => {
  it("gives a Free account one persistent route to billing", () => {
    setup({ tier: "free", features: [] });
    const upgrade = screen.getByRole("link", { name: /upgrade to elite/i });
    expect(upgrade).toHaveAttribute("href", "/billing?unlock=elite");
  });

  it("shows exactly one - it is an entry point, not a banner farm", () => {
    setup({ tier: "free", features: [] });
    expect(screen.getAllByRole("link", { name: /upgrade to elite/i })).toHaveLength(1);
  });

  it("does not nag an Elite member", () => {
    setup({ tier: "elite", features: ELITE_FEATURES });
    expect(screen.queryByRole("link", { name: /upgrade to elite/i })).not.toBeInTheDocument();
  });

  it("does not nag an admin", () => {
    setup({ tier: "free", features: [], isAdmin: true });
    expect(screen.queryByRole("link", { name: /upgrade to elite/i })).not.toBeInTheDocument();
  });

  it("asks a signed-out visitor to make an account first, not to pay", () => {
    setup({ authenticated: false, tier: "free", features: [] });
    expect(screen.queryByRole("link", { name: /upgrade to elite/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /start free/i })).toBeInTheDocument();
  });
});

describe("locked nav items", () => {
  it("keeps a locked item as a real link to the feature's own page", () => {
    setup({ tier: "free", features: [] });
    // The accessible name carries the state; the href is the feature
    // page, which renders the UpgradePrompt written for that feature.
    const finder = screen.getByRole("link", { name: /trade finder/i });
    expect(finder).toHaveAttribute("href", "/trade-finder");
    expect(finder.className).toContain("nav-locked");
    expect(finder.textContent).toMatch(/Elite feature/);
  });

  it("drops the lock once the feature is entitled", () => {
    setup({ tier: "elite", features: ELITE_FEATURES });
    const finder = screen.getByRole("link", { name: /trade finder/i });
    expect(finder.className).not.toContain("nav-locked");
    expect(finder.textContent).not.toMatch(/Elite feature/);
  });
});
