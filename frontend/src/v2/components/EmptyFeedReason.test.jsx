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

// Mirrors the real /market/freshness response shape: `stale`/`age_hours`
// arrive already computed by the backend, per the worker's own cadence
// (app/services/readiness.py's COLLECTOR_SILENT_HOURS_BY_WORKER) - the
// component trusts them rather than re-deriving a second, flat-threshold
// answer from `last_run_at` itself.
function heartbeat(worker, hoursAgo, { stale, ok = true } = {}) {
  return {
    worker,
    last_run_at: new Date(Date.now() - hoursAgo * HOURS).toISOString(),
    ok,
    age_hours: hoursAgo,
    stale,
  };
}

function healthy(overrides = {}) {
  return {
    cards_priced: 1200,
    cards_stale: 40,
    stale_sales_by_priority_tier: { CRITICAL: 5, HIGH: 10 },
    heartbeats: [heartbeat("snapshot", 5 / 60, { stale: false })],
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

  it("names PRICE data when that is what is stale", () => {
    freshness.value = {
      data: healthy({ cards_priced: 1000, cards_stale: 900, stale_sales_by_priority_tier: {} }),
      refetch: vi.fn(),
    };
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(screen.getByText(/price data is behind/i)).toBeInTheDocument();
  });

  it("does not count DORMANT's own, by-design backlog as sales data being behind", () => {
    // Exact live production payload that prompted this fix: DORMANT is
    // allowed to run ~67h behind by design (priority_config.py's own
    // tier_sales_interval_minutes), and a candidate scan ordered by
    // sales_count would never surface a DORMANT-tier card as an
    // opportunity regardless of its sales freshness. Summed across every
    // tier this crossed the 50%-of-priced gate by only 245 rows (7,718
    // vs a 7,473 threshold on 14,946 priced cards) - entirely accounted
    // for by DORMANT's own 1,566. CRITICAL/HIGH/NORMAL/LOW together
    // (6,152) are well under half of 14,946, so the feed should not have
    // called this "our sales data is behind" at all.
    freshness.value = {
      data: healthy({
        cards_priced: 14946,
        cards_stale: 2070,
        stale_sales_by_priority_tier: { CRITICAL: 1096, HIGH: 4264, NORMAL: 792, LOW: 0, DORMANT: 1566 },
      }),
      refetch: vi.fn(),
    };
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(document.body.textContent).not.toMatch(/sales data is behind/i);
    expect(document.body.textContent).not.toMatch(/on our side/i);
  });

  it("does not call a sales backlog an outage while collectors are reporting", () => {
    freshness.value = {
      data: healthy({
        cards_priced: 14950,
        cards_stale: 100,
        stale_sales_by_priority_tier: { CRITICAL: 1718, HIGH: 3924, NORMAL: 1884, LOW: 44, DORMANT: 2950 },
        heartbeats: [
          heartbeat("futgg_bulk_price_sync", 0.1, { stale: false }),
          heartbeat("futgg_sales_sync", 0.1, { stale: false }),
        ],
      }),
      refetch: vi.fn(),
    };
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(document.body.textContent).not.toMatch(/sales data is behind/i);
    expect(document.body.textContent).not.toMatch(/on our side/i);
    expect(screen.getByText(/doesn't see a trade worth making yet/i)).toBeInTheDocument();
  });

  it("reports stale required metadata sync while ignoring optional sales sync", () => {
    // Price freshness gates this price-based surface. The daily player
    // catalogue sync is still required for card metadata, but a stopped
    // retired SalesSync must not make healthy BIN prices look unavailable.
    freshness.value = {
      data: healthy({
        heartbeats: [
          heartbeat("futgg_bulk_price_sync", 0.5, { stale: false }),
          heartbeat("futgg_player_sync", 40, { stale: true }),
          heartbeat("futgg_sales_sync", 21 * 24, { stale: true }),
        ],
      }),
      refetch: vi.fn(),
    };
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(screen.getByText(/hasn't reported in/i)).toBeInTheDocument();
    // A pipeline is as fresh as its stalest stage.
    expect(screen.getByText(/40h/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/504h/);
  });

  it("keeps the price feed available when SalesSync is stale", () => {
    // This matches the live incident: current bulk price sync, an 11h-old
    // SalesSync heartbeat, and recent prices. SalesSync is no longer used and must not produce the market-data outage state.
    freshness.value = {
      data: healthy({
        heartbeats: [
          heartbeat("futgg_bulk_price_sync", 0.1, { stale: false }),
          heartbeat("futgg_sales_sync", 11.1, { stale: true, ok: false }),
        ],
      }),
      refetch: vi.fn(),
    };
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(screen.getByText(/doesn't see a trade worth making yet/i)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/hasn't reported in|on our side|behind/i);
  });

  it("does not flag a daily job for running on its own normal cadence", () => {
    // Production false positive this was written to fix:
    // futgg_player_sync is a one-shot job meant to run once a day. At
    // 14.5h since its last run - and again checked at 22h, still well
    // inside a day - it is having an entirely ordinary day, not an
    // outage, and the backend now marks it stale:false to say so. This
    // component must trust that rather than re-deriving its own flat
    // threshold and flagging it anyway.
    freshness.value = {
      data: healthy({
        heartbeats: [
          heartbeat("futgg_player_sync", 14.5, { stale: false }),
          heartbeat("futgg_bulk_price_sync", 0.1, { stale: false }),
          heartbeat("futgg_sales_sync", 0.1, { stale: false }),
        ],
      }),
      refetch: vi.fn(),
    };
    render(<EmptyFeedReason noun="opportunities" filtersActive={false} />);
    expect(document.body.textContent).not.toMatch(/hasn't reported in/i);
    expect(document.body.textContent).not.toMatch(/on our side/i);
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
