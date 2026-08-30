// The alert-dismissal flow is the thing that was reported as broken, so
// it is tested end to end at the component boundary: the click, the
// optimistic removal, what happens when the request fails, and the badge
// count staying honest.
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NotificationsPanel from "./NotificationsPanel";
import * as alertsApi from "../api/alerts";

vi.mock("../api/alerts");
vi.mock("react-hot-toast", () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

import toast from "react-hot-toast";

const ALERT = {
  id: 7,
  cardId: 117493814,
  title: "Mbappé (91 Icon) is down 13.0% (price)",
  movement: "100,000 → 87,000 coins",
  whyItMatters: "A drop this size is either an entry or a card the market is repricing.",
  age: "10 min ago",
  unread: true,
  repeatCount: 1,
  href: "/players/117493814",
  severity: "medium",
};

function renderPanel(props = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <NotificationsPanel open onClose={() => {}} {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("NotificationsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    alertsApi.getAlerts.mockResolvedValue({ items: [ALERT], unreadCount: 1, watchedCount: 4 });
    alertsApi.dismissAlert.mockResolvedValue({ ok: true });
    alertsApi.markAlertsRead.mockResolvedValue({ ok: true });
    alertsApi.dismissAllAlerts.mockResolvedValue({ ok: true });
  });

  afterEach(() => vi.restoreAllMocks());

  it("shows the resolved player, not a raw card id", async () => {
    renderPanel();
    expect(await screen.findByText(ALERT.title)).toBeInTheDocument();
    expect(screen.queryByText(/117493814/)).not.toBeInTheDocument();
  });

  it("explains the move and how old it is", async () => {
    renderPanel();
    expect(await screen.findByText("100,000 → 87,000 coins")).toBeInTheDocument();
    expect(screen.getByText(ALERT.whyItMatters)).toBeInTheDocument();
    expect(screen.getByText("10 min ago")).toBeInTheDocument();
  });

  it("removes the alert and it stays gone after the list refetches", async () => {
    // Both halves matter. The optimistic write makes it disappear at
    // once; the refetch afterwards proves the removal was actually
    // persisted rather than only reflected in local state - which is
    // exactly the case that used to fail, because nothing server-side
    // could remove a fired alert at all.
    let dismissed = false;
    alertsApi.getAlerts.mockImplementation(async () =>
      dismissed
        ? { items: [], unreadCount: 0, watchedCount: 4 }
        : { items: [ALERT], unreadCount: 1, watchedCount: 4 },
    );
    alertsApi.dismissAlert.mockImplementation(async () => { dismissed = true; return { ok: true }; });

    const user = userEvent.setup();
    renderPanel();
    await screen.findByText(ALERT.title);
    await user.click(screen.getByRole("button", { name: /Dismiss alert/ }));
    await waitFor(() => expect(screen.queryByText(ALERT.title)).not.toBeInTheDocument());
    expect(alertsApi.dismissAlert).toHaveBeenCalledWith(7, expect.anything());
    // Give the post-mutation invalidation time to land and re-render.
    await waitFor(() => expect(alertsApi.getAlerts.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(screen.queryByText(ALERT.title)).not.toBeInTheDocument();
  });

  it("puts the alert back and says so when the dismiss request fails", async () => {
    // The failure mode that makes an optimistic UI dangerous: showing a
    // success the user did not get, then having the row silently return.
    alertsApi.dismissAlert.mockRejectedValue(new Error("network down"));
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText(ALERT.title);
    await user.click(screen.getByRole("button", { name: /Dismiss alert/ }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(await screen.findByText(ALERT.title)).toBeInTheDocument();
  });

  it("marks alerts read on open, so the badge reflects what was seen", async () => {
    renderPanel();
    await waitFor(() => expect(alertsApi.markAlertsRead).toHaveBeenCalledTimes(1));
  });

  it("reports the server's unread count upward rather than the list length", async () => {
    // The list is capped and repeat-collapsed; its length is not a badge.
    alertsApi.getAlerts.mockResolvedValue({ items: [ALERT], unreadCount: 12, watchedCount: 4 });
    const onUnreadCount = vi.fn();
    renderPanel({ onUnreadCount });
    await waitFor(() => expect(onUnreadCount).toHaveBeenCalledWith(12));
  });

  it("distinguishes 'nothing has triggered' from 'nothing is watched'", async () => {
    alertsApi.getAlerts.mockResolvedValue({ items: [], unreadCount: 0, watchedCount: 3 });
    renderPanel();
    expect(await screen.findByText(/Nothing has moved enough to alert you yet/)).toBeInTheDocument();

    alertsApi.getAlerts.mockResolvedValue({ items: [], unreadCount: 0, watchedCount: 0 });
    renderPanel();
    expect(await screen.findByText(/not watching any cards yet/)).toBeInTheDocument();
  });

  it("surfaces a load failure instead of rendering an empty feed", async () => {
    alertsApi.getAlerts.mockRejectedValue(new Error("boom"));
    renderPanel();
    expect(await screen.findByText(/couldn't be loaded/i)).toBeInTheDocument();
  });
});
