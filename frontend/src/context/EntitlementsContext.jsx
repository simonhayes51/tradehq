// src/context/EntitlementsContext.jsx
import React, { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import toast from "react-hot-toast";
import api from "../axios";

const EntitlementsContext = createContext(null);

const CHECK_INTERVAL = 5 * 60 * 1000; // 5 minutes
// window `focus` and `visibilitychange` both fire on a single tab-switch,
// so without this a refresh gets triggered twice back to back.
const MIN_REFRESH_GAP = 15 * 1000;

const DEFAULT_LIMITS = { watchlist_max: 3, trending: { timeframes: ["24h"], limit: 5, smart: false } };

export function EntitlementsProvider({ children }) {
  const [state, setState] = useState({
    loading: true,
    isPremium: false,
    isAdmin: false,
    features: [],
    limits: DEFAULT_LIMITS,
    roles: [],
    lastCheck: null,
  });

  const lastCheckRef = useRef(0);
  const wasPremiumRef = useRef(false);
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

      // If a previously-premium session just lost premium (subscription
      // lapsed, refund, etc.), cached premium-only data in memory could
      // now be stale/wrong, so a reload is the safe move - but a native
      // alert() is jarring and blocks the tab, so a toast instead, with
      // enough delay to actually be seen before the page reloads.
      if (loadedOnceRef.current && wasPremiumRef.current && !isPremium) {
        toast.error("Your premium subscription has expired. Refreshing your access…", { duration: 4000 });
        setTimeout(() => window.location.reload(), 1500);
        return;
      }

      wasPremiumRef.current = isPremium;
      loadedOnceRef.current = true;

      setState({
        loading: false,
        isPremium,
        isAdmin: Boolean(data?.is_admin),
        features: Array.isArray(data?.features) ? data.features : [],
        limits: data?.limits || DEFAULT_LIMITS,
        roles: Array.isArray(data?.roles) ? data.roles : [],
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

  const value = useMemo(() => ({ ...state, refreshEntitlements }), [state]);

  return <EntitlementsContext.Provider value={value}>{children}</EntitlementsContext.Provider>;
}

export function useEntitlements() {
  const ctx = useContext(EntitlementsContext);
  if (!ctx) throw new Error("useEntitlements must be used within EntitlementsProvider");
  return ctx;
}
