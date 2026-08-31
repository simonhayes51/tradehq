// An empty feed has three possible explanations and they are not
// interchangeable. Every market surface used to give the same one:
// "No current opportunities match those filters" - which is wrong when no
// filters are set, and actively misleading when the real cause is our own
// price data being hours behind, because it blames the reader for a
// pipeline outage.
//
// This is the exact ambiguity that made a live "no opportunities anywhere"
// report impossible to triage from the screen alone.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const freshness = vi.hoisted(() => ({ value: { data: undefined, refetch: vi.fn() } }));
vi.mock("../hooks/useFutggMarket", () => ({ useFutggMarketFreshness: () => freshness.value }));

import EmptyFeedReason from "./EmptyFeedReason";

const HOURS = 3_600_000;

function healthy(overrides = {}) {
  return {
    cards_priced: 1200,
    cards_stale: 40,
    heartbeats: [{ worker: "snapshot", last_run_at: new Date(Date.now() - 5 * 60_000).toISOString(), ok: true }],
    ...overrides,
  };
}

beforeEach(() => { freshness.value = { data: healthy(), refetch: vi.fn() }; });

describe("when the reader set filters", () => {
  it("blames the filters, because that is the explanation", () => {
    render(<EmptyFeedReason noun="trades" filtersActive onClearFilters={() => {}} />);
    expect(screen.getByText(/No current trades match those filters/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Clear filters/i })).toBeInTheDocument();
  });

  it("says so even while the data is stale, because their filters come first", () => {
    freshness.value = { data: healthy({ cards_priced: 0 }), refetch: vi.fn() };
    render(<EmptyFeedReason noun="trades" filtersActive onClearFilters={() => {}} />);
    expect(screen.getByText(/match those filters/i)).toBeInTheDocument();
  });
});

describe("when our data is the problem", () => {
  it("says so plainly when there are no priced cards at all", () => {
    freshness.value = { data: healthy({ cards_priced: 0 }), refetch: vi.fn() };
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(screen.getByText(/don't have live prices right now/i)).toBeInTheDocument();
    expect(screen.getByText(/on our side/i)).toBeInTheDocument();
  });

  it("says so when most of what we have is stale", () => {
    freshness.value = { data: healthy({ cards_priced: 1000, cards_stale: 900 }), refetch: vi.fn() };
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(screen.getByText(/prices are behind/i)).toBeInTheDocument();
  });

  it("says so when the collector has stopped reporting in", () => {
    // The earliest signal that the feed is about to go quiet for the
    // wrong reason.
    freshness.value = {
      data: healthy({
        heartbeats: [{ worker: "snapshot", last_run_at: new Date(Date.now() - 9 * HOURS).toISOString(), ok: true }],
      }),
      refetch: vi.fn(),
    };
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(screen.getByText(/prices are behind/i)).toBeInTheDocument();
    expect(screen.getByText(/9h ago/)).toBeInTheDocument();
  });

  it("never blames the reader's filters when they set none", () => {
    freshness.value = { data: healthy({ cards_priced: 0 }), refetch: vi.fn() };
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(document.body.textContent).not.toMatch(/those filters/i);
  });

  it("offers a re-check rather than a dead end", () => {
    freshness.value = { data: healthy({ cards_priced: 0 }), refetch: vi.fn() };
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(screen.getByRole("button", { name: /Check again/i })).toBeInTheDocument();
  });
});

describe("when the market is simply quiet", () => {
  it("says the engine found nothing, and does not dress it up as a fault", () => {
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(screen.getByText(/doesn't see a trade worth making yet/i)).toBeInTheDocument();
    // An empty feed on a quiet market is the product working.
    expect(document.body.textContent).not.toMatch(/on our side|behind/i);
  });

  it("says prices are current, so the reader knows which case this is", () => {
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(screen.getByText(/Prices are current/i)).toBeInTheDocument();
  });

  it("does not claim a fault while freshness is still loading", () => {
    // Unknown is not "broken" - a spinner's worth of missing data must
    // not produce an outage message.
    freshness.value = { data: undefined, refetch: vi.fn() };
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(document.body.textContent).not.toMatch(/on our side/i);
  });
});
