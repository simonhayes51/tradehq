import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("./UpgradePrompt", () => ({
  default: ({ feature, title }) => <div data-testid="upgrade-prompt">{feature}:{title}</div>,
}));

import LimitReachedPrompt from "./LimitReachedPrompt";

function renderPrompt() {
  return render(<MemoryRouter><LimitReachedPrompt /></MemoryRouter>);
}

function block(detail) {
  act(() => {
    window.dispatchEvent(new CustomEvent("premium:blocked", { detail }));
  });
}

describe("LimitReachedPrompt", () => {
  it("ignores ordinary paid-feature 402s that already have an in-page gate", () => {
    renderPrompt();
    block({ feature: "trade_finder", message: "Elite required" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("turns a Free watchlist ceiling into the contextual Elite prompt", () => {
    renderPrompt();
    block({
      error: "limit_reached",
      feature: "watchlist",
      used: 10,
      limit: 10,
      upgradeable: true,
      message: "Free members can keep 10 cards on your watchlist.",
    });

    expect(screen.getByRole("dialog", { name: "Account limit reached" })).toBeInTheDocument();
    expect(screen.getByText("You're using 10 of 10 on your current plan.")).toBeInTheDocument();
    expect(screen.getByTestId("upgrade-prompt")).toHaveTextContent("watchlist:A bigger watchlist is part of Elite");
  });

  it("does not upsell an Elite member who has reached the absolute ceiling", () => {
    renderPrompt();
    block({
      error: "limit_reached",
      feature: "watchlist",
      used: 500,
      limit: 500,
      upgradeable: false,
      message: "You've reached the maximum of 500 cards on your watchlist on Elite.",
    });

    expect(screen.getByText("You've reached the maximum of 500 cards on your watchlist on Elite.")).toBeInTheDocument();
    expect(screen.queryByTestId("upgrade-prompt")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View account" })).toHaveAttribute("href", "/account");
  });
});
