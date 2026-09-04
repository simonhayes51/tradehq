// The completed-sales chart's range picker and its empty states.
//
// A card holding three days of sales used to offer 7d and 14d buttons
// that drew nothing, which reads as a broken toggle rather than as a
// limit of the data we hold.
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ModalPriceChart from "./ModalPriceChart";
import { useFutggPlayerSales } from "../../hooks/useFutggMarket";
import { useSalesCandles } from "../../hooks/useSalesCandles";

vi.mock("../../hooks/useFutggMarket", () => ({ useFutggPlayerSales: vi.fn() }));
vi.mock("../../hooks/useSalesCandles", () => ({ useSalesCandles: vi.fn() }));

const HOUR = 3600 * 1000;
const DAY = 24 * HOUR;

function sale(msAgo, price) {
  return { approximate_sold_at: new Date(Date.now() - msAgo).toISOString(), sold_price: price };
}

// `days` lets the mock answer each window with the rows that fall in it,
// the way the endpoint does.
function mockSales(rows, extra = {}) {
  useFutggPlayerSales.mockImplementation((cardId, days) => {
    if (!cardId) return { data: undefined, isLoading: false, isError: false };
    const cutoff = Date.now() - (days ?? 14) * DAY;
    return {
      data: {
        items: rows.filter((r) => new Date(r.approximate_sold_at).getTime() >= cutoff),
        ...extra,
      },
      isLoading: false,
      isError: false,
    };
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  useSalesCandles.mockReturnValue({ data: null, isLoading: false, isError: false });
});

function renderChart() {
  return render(<ModalPriceChart cardId={1} entryPrice={9000} targetPrice={12000} fairValue={10000} source="futgg" />);
}

describe("range availability", () => {
  it("disables ranges the card has no history for", async () => {
    // Three days of sales only.
    mockSales([sale(HOUR, 10000), sale(2 * DAY, 10200), sale(3 * DAY, 9900)]);
    renderChart();
    await waitFor(() => expect(screen.getByRole("button", { name: "14d" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "7d" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "24h" })).toBeEnabled();
  });

  it("says why a range is unavailable rather than just greying it out", async () => {
    mockSales([sale(HOUR, 10000), sale(2 * DAY, 10200)]);
    renderChart();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "14d" })).toHaveAttribute("title", expect.stringMatching(/we only hold/i)),
    );
  });

  it("falls back to the widest supported range instead of stranding the reader on an empty chart", async () => {
    mockSales([sale(HOUR, 10000), sale(4 * HOUR, 10100), sale(8 * HOUR, 9950)]);
    renderChart();
    // Default selection is 7d, which this card cannot answer.
    await waitFor(() => expect(screen.getByRole("button", { name: "24h" })).toHaveAttribute("aria-pressed", "true"));
  });

  it("leaves every range enabled when the card has full coverage", async () => {
    const rows = Array.from({ length: 40 }, (_, i) => sale(i * 8 * HOUR, 10000 + i * 10));
    mockSales(rows);
    renderChart();
    await waitFor(() => expect(screen.getByRole("button", { name: "14d" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "7d" })).toBeEnabled();
  });
});

describe("typical-sale indicator", () => {
  it("states the median and the middle half so an outlier cannot set the takeaway", async () => {
    const rows = [
      ...Array.from({ length: 19 }, (_, i) => sale((i + 1) * 6 * HOUR, 10000 + i * 20)),
      sale(HOUR, 250000), // one wildly overpriced sale
    ];
    mockSales(rows);
    renderChart();
    expect(await screen.findByText(/Typical sale/)).toBeInTheDocument();
    expect(screen.getByText(/middle half between/)).toBeInTheDocument();
    expect(screen.getByText(/outlying sale is off the top or bottom of this scale/)).toBeInTheDocument();
  });

  it("says nothing about a typical sale when there is only one", async () => {
    mockSales([sale(HOUR, 10000)]);
    renderChart();
    await waitFor(() => expect(screen.queryByText(/Typical sale/)).not.toBeInTheDocument());
  });
});

describe("empty windows", () => {
  it("distinguishes 'we have not looked' from 'nobody bought it'", async () => {
    useFutggPlayerSales.mockReturnValue({
      data: { items: [], sales_never_checked: true },
      isLoading: false,
      isError: false,
    });
    renderChart();
    expect(await screen.findByText(/haven't collected sales for this card yet/i)).toBeInTheDocument();
  });

  it("renders a loading state rather than an empty verdict", async () => {
    useFutggPlayerSales.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    const { container } = renderChart();
    expect(container.querySelector(".dash-chart-loading")).toBeTruthy();
  });
});
