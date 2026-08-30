// What happens when the client's idea of a member's plan stops matching
// the server's. A 402 is the server saying "you may not do this" - the
// one signal that is always right - and until now nothing listened to it,
// so a member whose subscription had just ended kept a fully-unlocked
// interface until the five-minute poll caught up.
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { EntitlementsProvider, useEntitlements } from "./EntitlementsContext";
import api from "../axios";

vi.mock("../axios", () => ({ default: { get: vi.fn() } }));
vi.mock("react-hot-toast", () => ({ default: { error: vi.fn(), success: vi.fn() } }));

function Probe() {
  const { tier, loading } = useEntitlements();
  return <span data-testid="tier">{loading ? "loading" : tier}</span>;
}

function renderProvider() {
  return render(
    <EntitlementsProvider>
      <Probe />
    </EntitlementsProvider>,
  );
}

const ELITE = { data: { tier: "elite", is_premium: true, is_admin: false, features: ["opportunity_feed"], roles: [], limits: {} } };
const FREE = { data: { tier: "free", is_premium: false, is_admin: false, features: [], roles: [], limits: {} } };

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});
afterEach(() => { vi.useRealTimers(); });

describe("a 402 refreshes what the client believes", () => {
  it("re-asks the server when any endpoint refuses on plan grounds", async () => {
    api.get.mockResolvedValue(ELITE);
    renderProvider();
    await waitFor(() => expect(screen.getByTestId("tier").textContent).toBe("elite"));
    expect(api.get).toHaveBeenCalledTimes(1);

    // 15s is MIN_REFRESH_GAP - past it, the throttled refresh runs.
    await act(async () => { vi.advanceTimersByTime(16_000); });
    await act(async () => {
      window.dispatchEvent(new CustomEvent("premium:blocked", { detail: { feature: "trade_finder" } }));
    });
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
  });

  it("costs one refresh for a burst of 402s, not one per locked card", async () => {
    api.get.mockResolvedValue(ELITE);
    renderProvider();
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1));

    await act(async () => { vi.advanceTimersByTime(16_000); });
    await act(async () => {
      for (let i = 0; i < 10; i += 1) {
        window.dispatchEvent(new CustomEvent("premium:blocked", { detail: { feature: "trade_finder" } }));
      }
    });
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it("never downgrades the tier locally - the server's answer is the answer", async () => {
    // A single 402 from one endpoint is not evidence of a plan change. If
    // the refresh still says elite, the client stays elite.
    api.get.mockResolvedValue(ELITE);
    renderProvider();
    await waitFor(() => expect(screen.getByTestId("tier").textContent).toBe("elite"));

    await act(async () => { vi.advanceTimersByTime(16_000); });
    await act(async () => {
      window.dispatchEvent(new CustomEvent("premium:blocked", { detail: { feature: "ai_chat" } }));
    });
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("tier").textContent).toBe("elite");
  });

  it("stops listening once unmounted", async () => {
    api.get.mockResolvedValue(ELITE);
    const { unmount } = renderProvider();
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1));
    unmount();

    await act(async () => { vi.advanceTimersByTime(16_000); });
    await act(async () => {
      window.dispatchEvent(new CustomEvent("premium:blocked", { detail: { feature: "trade_finder" } }));
    });
    expect(api.get).toHaveBeenCalledTimes(1);
  });
});

describe("an expired subscription", () => {
  it("clears the tab rather than leaving premium data on screen", async () => {
    const toast = (await import("react-hot-toast")).default;
    const reload = vi.fn();
    const original = window.location;
    delete window.location;
    window.location = { ...original, reload };

    api.get.mockResolvedValueOnce(ELITE).mockResolvedValue(FREE);
    renderProvider();
    await waitFor(() => expect(screen.getByTestId("tier").textContent).toBe("elite"));

    await act(async () => { vi.advanceTimersByTime(16_000); });
    await act(async () => {
      window.dispatchEvent(new CustomEvent("premium:blocked", { detail: { feature: "trade_finder" } }));
    });

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    // Named for the plan the product actually sells.
    expect(toast.error.mock.calls[0][0]).toMatch(/Elite/);
    expect(toast.error.mock.calls[0][0]).not.toMatch(/premium/i);

    await act(async () => { vi.advanceTimersByTime(2_000); });
    expect(reload).toHaveBeenCalled();
    window.location = original;
  });
});
