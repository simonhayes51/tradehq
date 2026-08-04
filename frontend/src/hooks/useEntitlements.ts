import { useEffect, useMemo, useState } from "react";

export type Entitlements = {
  authenticated?: boolean;
  is_premium: boolean;
  features?: Record<string, boolean>;
  limits?: Record<string, number | string>;
};

type State =
  | { status: "idle" | "loading" }
  | { status: "ready"; data: Entitlements }
  | { status: "error"; error: Error };

export function useEntitlements() {
  const [state, setState] = useState<State>({ status: "idle" });

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setState({ status: "loading" });
      try {
        // 1) Try /api/auth/me (ideal: includes features/limits + authenticated)
        let res = await fetch("/api/auth/me", { credentials: "include" });
        if (res.ok) {
          const me = await res.json();
          const data: Entitlements = {
            authenticated: !!me?.authenticated ?? true, // some backends omit it
            is_premium: !!me?.entitlements?.is_premium ?? !!me?.is_premium ?? false,
            features: me?.entitlements?.features ?? me?.features ?? undefined,
            limits: me?.entitlements?.limits ?? me?.limits ?? undefined,
          };
          if (!cancelled) setState({ status: "ready", data });
          return;
        }

        // 2) Fallback to /api/watchlist/usage
        if (res.status === 404 || res.status === 500) {
          const alt = await fetch("/api/watchlist/usage", { credentials: "include" });
          if (alt.ok) {
            const js = await alt.json();
            const data: Entitlements = {
              authenticated: true, // this endpoint implies an authed session
              is_premium: !!js?.is_premium,
              limits: { watchlist_max: js?.max },
            };
            if (!cancelled) setState({ status: "ready", data });
            return;
          }
        }

        // 3) If 401, they’re not logged in
        if (res.status === 401) {
          if (!cancelled) setState({
            status: "ready",
            data: { authenticated: false, is_premium: false },
          });
          return;
        }

        throw new Error(`Entitlements HTTP ${res.status}`);
      } catch (e: any) {
        if (!cancelled) setState({ status: "error", error: e });
      }
    };

    run();
    return () => { cancelled = true; };
  }, []);

  const value = useMemo(() => state, [state]);
  return value;
}
