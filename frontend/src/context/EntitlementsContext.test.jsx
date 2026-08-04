import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import toast from "react-hot-toast";
import api from "../axios";
import { EntitlementsProvider, useEntitlements } from "./EntitlementsContext";

vi.mock("../axios", () => ({ default: { get: vi.fn() } }));
vi.mock("react-hot-toast", () => ({ default: { error: vi.fn() } }));

function Probe() {
  const { loading, isPremium, isAdmin } = useEntitlements();
  if (loading) return <div>loading</div>;
  return <div>{`premium:${isPremium} admin:${isAdmin}`}</div>;
}

describe("EntitlementsProvider", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.get.mockReset();
    toast.error.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads entitlements once on mount and skips the login redirect on 401", async () => {
    api.get.mockResolvedValue({ data: { is_premium: true, is_admin: false, features: [], roles: [] } });

    render(<EntitlementsProvider><Probe /></EntitlementsProvider>);

    await waitFor(() => expect(screen.getByText("premium:true admin:false")).toBeInTheDocument());
    expect(api.get).toHaveBeenCalledWith("/api/entitlements", { __skipAuthRedirect: true });
  });

  it("falls back to defaults (not a thrown error) when the request fails", async () => {
    api.get.mockRejectedValue(new Error("network down"));

    render(<EntitlementsProvider><Probe /></EntitlementsProvider>);

    await waitFor(() => expect(screen.getByText("premium:false admin:false")).toBeInTheDocument());
  });

  it("toasts and reloads instead of alert()ing when premium lapses between checks", async () => {
    const reload = vi.fn();
    const originalLocation = window.location;
    Object.defineProperty(window, "location", { configurable: true, value: { ...originalLocation, reload } });

    api.get.mockResolvedValueOnce({ data: { is_premium: true, is_admin: false, features: [], roles: [] } });
    render(<EntitlementsProvider><Probe /></EntitlementsProvider>);
    await waitFor(() => expect(screen.getByText("premium:true admin:false")).toBeInTheDocument());

    // Clear the MIN_REFRESH_GAP dedup window so the focus-triggered
    // refresh below isn't itself skipped as "too soon".
    await vi.advanceTimersByTimeAsync(16_000);

    api.get.mockResolvedValueOnce({ data: { is_premium: false, is_admin: false, features: [], roles: [] } });
    window.dispatchEvent(new Event("focus"));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("premium subscription has expired"),
      expect.any(Object),
    ));

    await vi.advanceTimersByTimeAsync(1500);
    expect(reload).toHaveBeenCalled();

    Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  });
});
