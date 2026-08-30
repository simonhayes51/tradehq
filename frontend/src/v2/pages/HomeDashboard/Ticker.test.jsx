// The ticker previously rendered every market event as `${title} —
// EVENT`, producing rows like "Lionel Messi — EVENT": the internal enum
// name in place of a description, and no indication of what happened.
// These tests pin the replacement to saying something.
import { describe, expect, it, afterEach, vi } from "vitest";
import { eventTickerRow } from "./HomeDashboard";

const NOW = new Date("2026-08-30T12:00:00Z");

function at(hoursFromNow) {
  return new Date(NOW.getTime() + hoursFromNow * 3600_000).toISOString();
}

function withFrozenClock(fn) {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  try {
    return fn();
  } finally {
    vi.useRealTimers();
  }
}

afterEach(() => vi.useRealTimers());

describe("eventTickerRow", () => {
  it("never emits the raw enum name", () => {
    const row = withFrozenClock(() =>
      eventTickerRow({ kind: "sbc", title: "Lionel Messi", firstSeenAt: at(-2) }),
    );
    expect(`${row.label} ${row.value}`).not.toContain("EVENT");
  });

  it("says what happened, not just the title", () => {
    const row = withFrozenClock(() =>
      eventTickerRow({ kind: "sbc", title: "Lionel Messi", firstSeenAt: at(-2) }),
    );
    expect(row.label).toBe("New sbc");
    expect(row.value).toBe("Lionel Messi");
  });

  it("prefers an imminent expiry over 'new', because expiry is the actionable fact", () => {
    const row = withFrozenClock(() =>
      eventTickerRow({ kind: "sbc", title: "Winter Wildcards", firstSeenAt: at(-1), endsAt: at(6) }),
    );
    expect(row.label).toBe("SBC ends in 6h");
  });

  it("drops events that have already finished", () => {
    const row = withFrozenClock(() =>
      eventTickerRow({ kind: "promo", title: "Old Promo", endsAt: at(-3), firstSeenAt: at(-100) }),
    );
    expect(row).toBeNull();
  });

  it("announces an upcoming event with its countdown", () => {
    const row = withFrozenClock(() =>
      eventTickerRow({ kind: "promo", title: "Team of the Season", startsAt: at(20), firstSeenAt: at(-200) }),
    );
    expect(row.label).toBe("Promo starts in 20h");
  });

  it("describes an ongoing event rather than dropping it", () => {
    const row = withFrozenClock(() =>
      eventTickerRow({ kind: "promo", title: "Ongoing Promo", startsAt: at(-100), firstSeenAt: at(-200) }),
    );
    expect(row.label).toBe("Promo live now");
  });

  it("skips an unmapped kind rather than printing it", () => {
    // The failure mode being prevented: a kind nobody has written copy
    // for must not fall back to showing the enum value itself.
    expect(eventTickerRow({ kind: "some_new_internal_kind", title: "Thing" })).toBeNull();
  });

  it("skips an event with no title", () => {
    expect(eventTickerRow({ kind: "sbc", title: "" })).toBeNull();
  });
});
