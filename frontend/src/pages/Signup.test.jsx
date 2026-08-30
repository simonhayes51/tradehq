// Signup is the top of the funnel and the page most likely to be reached
// in a broken state (a cancelled OAuth round trip, a double tap on a slow
// phone, a deep link from a locked feature). These tests cover what the
// page says and what it does when things go wrong, not its layout.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const auth = vi.hoisted(() => ({
  value: { isAuthenticated: false, login: vi.fn(), checkAuthStatus: vi.fn() },
}));
vi.mock("../context/AuthContext", () => ({ useAuth: () => auth.value }));

vi.mock("../v2/hooks/useBillingPlans", async (importOriginal) => ({
  ...(await importOriginal()),
  useBillingPlans: () => ({ data: { plans: { monthly: { configured: true, price_display: "£3/month" } } } }),
}));

const tracked = vi.hoisted(() => ({ calls: [] }));
vi.mock("../lib/analytics", async (importOriginal) => ({
  ...(await importOriginal()),
  track: (...args) => tracked.calls.push(args),
  trackOnce: (...args) => tracked.calls.push(args),
}));

import Signup from "./Signup";

function renderSignup(route = "/signup") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="/signup" element={<Signup />} />
          <Route path="/welcome" element={<p>ONBOARDING</p>} />
          <Route path="/trade-finder" element={<p>TRADE FINDER</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  tracked.calls = [];
  auth.value = { isAuthenticated: false, login: vi.fn(), checkAuthStatus: vi.fn() };
});

describe("Signup copy", () => {
  it("leads with the free account and says no card is needed", () => {
    renderSignup();
    expect(screen.getByRole("heading", { name: /Create your free account/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create free account" })).toBeInTheDocument();
    expect(screen.getByText(/No card required/i)).toBeInTheDocument();
  });

  it("never describes the free plan as a trial", () => {
    renderSignup();
    const body = document.body.textContent;
    expect(body).not.toMatch(/free trial|7 days free|trial period/i);
    expect(screen.getByText(/isn't a trial and it doesn't expire/i)).toBeInTheDocument();
  });

  it("states the daily allowance concretely rather than as unlock more", () => {
    renderSignup();
    expect(screen.getByText(/2 complete trade ideas every day/i)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/unlock more|more tools|enhanced access/i);
  });

  it("does not promise Discord is required to be a free member", () => {
    renderSignup();
    expect(screen.getByText(/never your EA login/i)).toBeInTheDocument();
  });
});

describe("Signup failure handling", () => {
  it("explains a cancelled Discord sign-in instead of looking like a fresh page", () => {
    renderSignup("/signup?error=access_denied");
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/cancelled the Discord sign-in/i);
    expect(alert.textContent).toMatch(/Nothing was created/i);
  });

  it("explains an expired sign-in link as a restart, not a fault", () => {
    renderSignup("/signup?error=expired_state");
    expect(screen.getByRole("alert").textContent).toMatch(/had expired.*start again/i);
  });

  it("explains a failed token exchange as temporary and retryable", () => {
    renderSignup("/signup?error=exchange_failed");
    expect(screen.getByRole("alert").textContent).toMatch(/didn't finish.*try again/i);
  });

  it("never renders an error code it does not recognise", () => {
    // The code arrives on a URL anyone can construct. An unknown one must
    // fall back to our own sentence, never be echoed onto the page.
    renderSignup("/signup?error=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E");
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe(
      "Something went wrong on our side while signing you in. Please try again.",
    );
  });

  it("shows an error rather than a dead button when login throws", async () => {
    auth.value.login = vi.fn(() => { throw new Error("popup blocked"); });
    renderSignup();
    await userEvent.click(screen.getByRole("button", { name: "Create free account" }));
    expect(screen.getByRole("alert").textContent).toMatch(/Couldn't open Discord sign-in/i);
    // The button must come back, or the page is a dead end.
    expect(screen.getByRole("button", { name: "Create free account" })).toBeEnabled();
  });

  it("cannot be double-submitted on a slow connection", async () => {
    renderSignup();
    const button = screen.getByRole("button", { name: "Create free account" });
    await userEvent.click(button);
    await userEvent.click(button);
    await userEvent.click(button);
    expect(auth.value.login).toHaveBeenCalledTimes(1);
  });
});

describe("Signup routing", () => {
  it("sends a brand-new account to onboarding", () => {
    auth.value.isAuthenticated = true;
    renderSignup();
    expect(screen.getByText("ONBOARDING")).toBeInTheDocument();
  });

  it("returns an already-signed-in visitor to the page that sent them", () => {
    auth.value.isAuthenticated = true;
    renderSignup("/signup?from=%2Ftrade-finder");
    expect(screen.getByText("TRADE FINDER")).toBeInTheDocument();
  });

  it("carries the origin through to the sign-in link so it survives either route", () => {
    renderSignup("/signup?from=%2Ftrade-finder");
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href"))
      .toBe("/login?from=%2Ftrade-finder");
  });

  it("records where the signup was reached from, with no identity attached", () => {
    renderSignup("/signup?from=%2Ftrade-finder");
    const [name, props] = tracked.calls[0];
    expect(name).toBe("signup_viewed");
    expect(props).toEqual({ surface: "gated" });
  });
});
