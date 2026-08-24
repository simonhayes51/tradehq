import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useIsMobile, MOBILE_BREAKPOINT_PX } from "./useIsMobile";

function mockMatchMedia(matches) {
  const listeners = new Set();
  const mql = {
    matches,
    media: `(max-width: ${MOBILE_BREAKPOINT_PX}px)`,
    addEventListener: (_type, cb) => listeners.add(cb),
    removeEventListener: (_type, cb) => listeners.delete(cb),
    addListener: (cb) => listeners.add(cb),
    removeListener: (cb) => listeners.delete(cb),
  };
  window.matchMedia = vi.fn().mockReturnValue(mql);
  return {
    fire(nextMatches) {
      mql.matches = nextMatches;
      listeners.forEach((cb) => cb({ matches: nextMatches }));
    },
  };
}

describe("useIsMobile", () => {
  const originalMatchMedia = window.matchMedia;
  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  it("reports mobile when the media query matches at mount", () => {
    mockMatchMedia(true);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("reports desktop when the media query does not match at mount", () => {
    mockMatchMedia(false);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("updates when the viewport crosses the breakpoint", () => {
    const control = mockMatchMedia(false);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
    act(() => control.fire(true));
    expect(result.current).toBe(true);
  });
});
