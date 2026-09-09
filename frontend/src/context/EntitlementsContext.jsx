// src/context/EntitlementsContext.jsx
import React, { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import toast from "react-hot-toast";
import api from "../axios";

const EntitlementsContext = createContext(null);

const CHECK_INTERVAL = 5 * 60 * 1000; // 5 minutes
// window `focus` and `visibilitychange` both fire on a single tab-switch,
// so without this a refresh gets triggered twice back to back.
const MIN_REFRESH_GAP = 15 * 1000;

// Conservative defaults only, used before the first response lands and if
// a request fails. Deliberately the FREE numbers: assuming less access
// than the user has shows an upgrade prompt for a moment, whereas
// assuming more shows a paid feature that then fails at the API.
// The real numbers always come from the server, which is also the only
// thing that enforces them.
const DEFAULT_LIMITS = {
  watchlist_max: 10,
  alerts_max: 3,
  open_trades_max: 5,
  daily_opportunities: 2,
  portfolio_history_days: 30,
  track_record_window_days: 30,
  trending: { timeframes: ["24h"], limit: 5, smart: false },
};

export function EntitlementsProvider({ children }) {
  const [state, setState] = useState({
    loading: true,
    isPremium: false,
    isAdmin: false,
    // "free" | "elite" - the product has exactly these two signed-in
    // states, and the server is the one that decides which. Exposed so
    // components stop inferring a plan from a feature flag.
    tier: "free",
    features: [],
    limits: DEFAULT_LIMITS,
    roles: [],
    // Why the effective tier was granted. During the FC27 preseason
    // giveaway this is "preseason", which is intentionally distinct from
    // a Stripe trial/subscription or an admin/manual grant.
    accessSource: "anonymous",
    promo: null,
    lastCheck: null,
  });

  const lastCheckRef = useRef(0);
  const wasPremiumRef = useRef(false);
  const previousAccessSourceRef = useRef("anonymous");
  const loadedOnceRef = useRef(false);

  async function refreshEntitlements({ force = false } = {}) {
    if (!force && Date.now() - lastCheckRef.current < MIN_REFRESH_GAP) return;
    lastCheckRef.current = Date.now();

    try {
      // Public endpoint - compute_entitlements() on the backend defaults
      // gracefully for anonymous callers rather than 401ing, but
      // __skipAuthRedirect is set anyway since v2's pages are
      // public-first: a stray 401 here must never hijack a logged-out
      // visitor into a /login redirect (see src/axios.js's own note on
      // this exact distinction).
      const { data } = await api.get("/api/entitlements", { __skipAuthRedirect: true });
      const isPremium = Boolean(data?.is_premium);
      const accessSource = data?.access_source || (data?.user_id ? (isPremium ? "paid_or_granted" : "free") : "anonymous");

      // If a previously-premium session just lost premium (subscription
      // lapsed, refund, or the time-bounded preseason access expired),
      // cached premium-only data in memory could now be stale/wrong, so a
      // reload is the safe move. Keep the message accurate about WHY the
      // access ended instead of calling the preseason giveaway a subscription.
      if (loadedOnceRef.current && wasPremiumRef.current && !isPremium) {
        const message = previousAccessSourceRef.current === "preseason"
          ? "Your free preseason Elite access has ended. Refreshing your access…"
          : "Your Elite subscription has ended. Refreshing your access…";
        toast.error(message, { duration: 4000 });
        setTimeout(() => window.location.reload(), 1500);
        return;
      }

      wasPremiumRef.current = isPremium;
      previousAccessSourceRef.current = accessSource;
      loadedOnceRef.current = true;

      setState({
        loading: false,
        isPremium,
        tier: data?.tier === "elite" ? "elite" : "free",
        isAdmin: Boolean(data?.is_admin),
        features: Array.isArray(data?.features) ? data.features : [],
        limits: data?.limits || DEFAULT_LIMITS,
        roles: Array.isArray(data?.roles) ? data.roles : [],
        accessSource,
        promo: data?.promo || null,
        lastCheck: new Date(),
      });
    } catch (error) {
      console.error("Failed to refresh entitlements:", error);
      setState((s) => ({ ...s, loading: false }));
    }
  }

  // Initial load
  useEffect(() => {
    refreshEntitlements({ force: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-refresh every 5 minutes
  useEffect(() => {
    const interval = setInterval(() => refreshEntitlements({ force: true }), CHECK_INTERVAL);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Refresh when the tab regains focus/visibility - registered once
  // (not tied to state) so switching tabs doesn't churn the listeners.
  useEffect(() => {
    const handleWake = () => {
      if (!document.hidden) refreshEntitlements();
    };
    window.addEventListener("focus", handleWake);
    document.addEventListener("visibilitychange", handleWake);
    return () => {
      window.removeEventListener("focus", handleWake);
      document.removeEventListener("visibilitychange", handleWake);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A 402 from any endpoint is the server saying this client's idea of
  // what it may do is wrong - which is precisely the stale-entitlement
  // case, and the one signal that is always right. axios dispatches
  // `premium:blocked` on every 402 (see src/axios.js) and, until now,
  // nothing listened: a member whose subscription had just ended kept a
  // fully-unlocked interface, hitting 402 after 402, until the five-minute
  // poll or a tab focus happened to catch up.
  //
  // Deliberately a refresh, never a downgrade applied locally: the server
  // stays the only thing that decides the tier. If the refresh comes back
  // saying they are still Elite, nothing changes - a single 402 from one
  // endpoint is not evidence of a plan change on its own.
  useEffect(() => {
    // Throttled, not forced: one page can render many locked surfaces
    // and produce a burst of 402s, which should cost one refresh, not
    // one per card. MIN_REFRESH_GAP is 15s.
    const handleBlocked = () => refreshEntitlements();
    window.addEventListener("premium:blocked", handleBlocked);
    return () => window.removeEventListener("premium:blocked", handleBlocked);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = useMemo(() => ({ ...state, refreshEntitlements }), [state]);

  return <EntitlementsContext.Provider value={value}>{children}</EntitlementsContext.Provider>;
}

export function useEntitlements() {
  const ctx = useContext(EntitlementsContext);
  if (!ctx) throw new Error("useEntitlements must be used within EntitlementsProvider");
  return ctx;
}
