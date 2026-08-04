// src/v2/pages/HealthCheck.jsx
//
// Day-1 smoke test (see the v2 plan, Phase 1): proves the same-origin
// session cookie is shared between v1 and v2 with zero extra login step,
// before anything gated gets built on top of that assumption. Reuses the
// already-live GET /api/entitlements - no new backend endpoint needed.
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export default function HealthCheck() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["v2", "healthcheck", "entitlements"],
    queryFn: async () => (await api.get("/api/entitlements")).data,
  });

  return (
    <div className="p-6 max-w-xl">
      <h1 className="text-lg font-semibold mb-4">v2 session smoke test</h1>
      <p className="text-sm text-[var(--v2-muted)] mb-4">
        This calls GET /api/entitlements from inside /v2. If you're logged in
        on v1 in this same browser, the tier/user_id below should match what
        v1's own nav shows - with no separate login.
      </p>
      {isLoading && <p>Loading...</p>}
      {error && <p className="text-[var(--v2-negative)]">Error: {String(error)}</p>}
      {data && (
        <pre className="text-xs bg-[var(--v2-card)] border border-[var(--v2-border)] rounded-lg p-4 overflow-auto">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
}
