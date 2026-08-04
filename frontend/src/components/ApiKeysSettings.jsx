// src/components/ApiKeysSettings.jsx
//
// Self-serve management for the public/paid historical-data API
// (/api/public/v1/*) - create, list, and revoke API keys.
import React, { useEffect, useState } from "react";
import { Key, Trash2, Copy, Loader2 } from "lucide-react";
import { apiFetch } from "../api/http";

export default function ApiKeysSettings() {
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newKeyName, setNewKeyName] = useState("");
  const [justCreated, setJustCreated] = useState(null); // { key, keyPrefix }
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const res = await apiFetch("/api/api-keys");
      setKeys(Array.isArray(res?.items) ? res.items : []);
    } catch (e) {
      console.error("Failed to load API keys:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async (e) => {
    e.preventDefault();
    setCreating(true);
    setError("");
    try {
      const res = await apiFetch("/api/api-keys", {
        method: "POST",
        body: { name: newKeyName || undefined },
      });
      setJustCreated({ key: res.key, keyPrefix: res.keyPrefix });
      setNewKeyName("");
      await load();
    } catch (err) {
      setError(err?.message || "Failed to create key");
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (id) => {
    try {
      await apiFetch(`/api/api-keys/${id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      console.error("Failed to revoke key:", e);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center mb-3">
          <Key className="h-5 w-5 text-gray-400 mr-2" />
          <h4 className="text-sm font-medium text-white">Public API Access</h4>
        </div>
        <p className="text-sm text-gray-400 mb-4">
          Programmatic, read-only access to real BIN/sales history for external tools -
          see <code className="text-gray-300">docs/public-api.md</code> for endpoints. Requires Premium.
        </p>
      </div>

      {justCreated && (
        <div className="border border-emerald-700 bg-emerald-900/20 rounded-lg p-4">
          <p className="text-sm text-emerald-300 font-medium mb-2">
            Key created - copy it now, it won't be shown again:
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 bg-black/40 rounded px-2 py-1.5 text-xs text-white break-all">
              {justCreated.key}
            </code>
            <button
              type="button"
              onClick={() => navigator.clipboard?.writeText(justCreated.key)}
              className="p-1.5 rounded-md bg-gray-800 hover:bg-gray-700 text-white"
              title="Copy"
            >
              <Copy className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      <form onSubmit={handleCreate} className="flex flex-wrap gap-3">
        <input
          value={newKeyName}
          onChange={(e) => setNewKeyName(e.target.value)}
          placeholder="Key name (optional, e.g. 'my discord bot')"
          className="flex-1 min-w-[200px] px-3 py-2 rounded-md bg-black/40 border border-gray-700 text-white text-sm"
        />
        <button
          type="submit"
          disabled={creating}
          className="inline-flex items-center px-4 py-2 rounded-md bg-lime-500/90 hover:bg-lime-500 text-black text-sm font-semibold disabled:opacity-50"
        >
          {creating ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Key className="h-4 w-4 mr-2" />}
          Create Key
        </button>
      </form>
      {error && <div className="text-sm text-red-400">{error}</div>}

      {loading ? (
        <div className="text-gray-400 text-sm">Loading keys…</div>
      ) : keys.length === 0 ? (
        <div className="text-gray-400 text-sm">No API keys yet.</div>
      ) : (
        <ul className="space-y-2">
          {keys.map((k) => (
            <li
              key={k.id}
              className="flex items-center justify-between border border-gray-800 rounded-lg px-3 py-2 text-sm"
            >
              <div>
                <div className="text-white font-medium">{k.name || "Untitled key"}</div>
                <div className="text-xs text-gray-500">
                  {k.key_prefix}… • {k.rate_limit_per_minute} req/min •{" "}
                  {k.revoked_at ? (
                    <span className="text-red-400">Revoked</span>
                  ) : (
                    <span className="text-emerald-400">Active</span>
                  )}
                  {k.last_used_at && ` • last used ${new Date(k.last_used_at).toLocaleString()}`}
                </div>
              </div>
              {!k.revoked_at && (
                <button
                  onClick={() => handleRevoke(k.id)}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-red-600/80 hover:bg-red-600 text-white text-xs"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
