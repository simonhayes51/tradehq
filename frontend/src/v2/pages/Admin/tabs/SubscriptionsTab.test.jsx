import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SubscriptionsTab from "./SubscriptionsTab";
import { useAdminSubscriptions } from "../../../hooks/useAdminSubscriptions";
import { api } from "../../../lib/api";

vi.mock("../../../hooks/useAdminSubscriptions", () => ({ useAdminSubscriptions: vi.fn() }));
vi.mock("../../../lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));

beforeEach(() => {
  vi.clearAllMocks();
  useAdminSubscriptions.mockReturnValue({ data: { items: [] }, isLoading: false, error: null });
});

describe("manual Elite access", () => {
  it("searches users and grants a time-limited Elite override without touching Stripe", async () => {
    api.get.mockResolvedValue({
      data: { users: [{ id: "u1", username: "Tester", tier: "basic", plan: "free", premium_until: null }] },
    });
    api.post.mockResolvedValue({
      data: { user_id: "u1", username: "Tester", tier: "elite", premium_until: "2026-10-08T12:00:00Z" },
    });

    render(<SubscriptionsTab />);
    fireEvent.change(screen.getByPlaceholderText(/Username, Discord ID or user ID/i), { target: { value: "Tester" } });
    fireEvent.click(screen.getByRole("button", { name: /Search/i }));

    expect(await screen.findByText("Tester")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Grant duration for Tester/i), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "Grant Elite" }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/admin/users/u1/tier", { tier: "elite", days: 30 }));
    expect(await screen.findByText(/now has manual Elite access/i)).toBeInTheDocument();
  });

  it("removes only the manual grant", async () => {
    api.get.mockResolvedValue({
      data: { users: [{ id: "u2", username: "Paid User", tier: "elite", plan: "elite", premium_until: null }] },
    });
    api.post.mockResolvedValue({
      data: { user_id: "u2", username: "Paid User", tier: "basic", premium_until: null },
    });

    render(<SubscriptionsTab />);
    fireEvent.change(screen.getByPlaceholderText(/Username, Discord ID or user ID/i), { target: { value: "Paid User" } });
    fireEvent.click(screen.getByRole("button", { name: /Search/i }));
    expect(await screen.findByText("Paid User")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Remove manual grant/i }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/admin/users/u2/tier", { tier: "free" }));
    expect(await screen.findByText(/Stripe subscription remains untouched/i)).toBeInTheDocument();
  });
});
