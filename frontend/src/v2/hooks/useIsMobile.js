// src/v2/hooks/useIsMobile.js
//
// Matches the same 800px breakpoint already used throughout the v2 CSS
// (@media(max-width:800px) in dashboard-v2.css/mobile-polish.css) - one
// definition of "mobile" shared between JS layout decisions (which
// sections to mount) and CSS presentation, so they can't disagree.
import { useEffect, useState } from "react";

export const MOBILE_BREAKPOINT_PX = 800;

export function useIsMobile(breakpoint = MOBILE_BREAKPOINT_PX) {
  const query = `(max-width: ${breakpoint}px)`;
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(query).matches
      : false,
  );

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    const onChange = (event) => setIsMobile(event.matches);
    setIsMobile(mql.matches);
    if (mql.addEventListener) mql.addEventListener("change", onChange);
    else mql.addListener(onChange); // Safari <14 fallback
    return () => {
      if (mql.removeEventListener) mql.removeEventListener("change", onChange);
      else mql.removeListener(onChange);
    };
  }, [query]);

  return isMobile;
}
