// src/v2/components/CompanionConnect.jsx
//
// Connecting the browser extension / userscript to this account.
//
// WHY THIS EXISTS AT ALL
//
// The companion runs inside the EA Web App on www.ea.com and calls
// api.eafc.live, so the session cookie has to travel third-party. The
// extension's service worker is the stronger of the two positions, but
// third-party cookie behaviour keeps changing; the userscript's
// GM_xmlhttpRequest withCredentials support differs between managers
// outright. Neither is something to bet the paid overlay on.
//
// So the member mints a short-lived token HERE, where the cookie is
// first-party and unambiguous, and the companion sends it as a bearer
// header - which nothing can decline to attach.
//
// The token is deliberately short-lived and read-only. It is not an API
// key: it expires, and rotating the server's signing key revokes every
// one that is outstanding.
/* global chrome */
// `chrome` exists only when this page is open in a Chromium browser with
// the extension installed. Every use below is guarded, so the global
// simply being absent is a supported case, not an error.
import { useState } from "react";
import { Copy, Check, Puzzle, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import "./companion-connect.css";

// Only our own extension may be handed a token, and only from this
// origin (manifest.json's externally_connectable).
const EXTENSION_IDS = (import.meta.env.VITE_COMPANION_EXTENSION_IDS || "")
  .split(",").map((id) => id.trim()).filter(Boolean);

export default function CompanionConnect() {
  const [token, setToken] = useState(null);
  const [expiresAt, setExpiresAt] = useState(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const [pushedToExtension, setPushedToExtension] = useState(false);

  async function connect() {
    if (busy) return;
    setBusy(true);
    setError("");
    setCopied(false);
    try {
      const { data } = await api.post("/api/v2/companion/session", null, {
        __skipAuthRedirect: true,
        __noRetry: true,
      });
      setToken(data.token);
      setExpiresAt(data.expiresAtIso);

      // Hand it straight to the extension if it is installed, so the
      // common case needs no copying at all. Silently a no-op when it
      // isn't - chrome.runtime is absent, or the id doesn't match.
      if (typeof chrome !== "undefined" && chrome?.runtime?.sendMessage) {
        for (const id of EXTENSION_IDS) {
          try {
            chrome.runtime.sendMessage(
              id,
              { type: "eafc-live:companion-token", token: data.token, expiresAt: data.expiresAt },
              (reply) => { if (reply?.ok) setPushedToExtension(true); },
            );
          } catch { /* extension not installed; the copy path still works */ }
        }
      }
    } catch (requestError) {
      setError(
        requestError?.response?.status === 401
          ? "Sign in first, then connect the companion."
          : "Couldn't create a companion token. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
    } catch {
      setError("Couldn't copy automatically — select the token and copy it manually.");
    }
  }

  return (
    <section className="companion-connect">
      <div className="companion-connect-head">
        <Puzzle size={18} />
        <div>
          <strong>Browser companion</strong>
          <p>
            Shows your EAFC.Live analysis on the EA Web App transfer market. Connect it so the
            overlay knows it&apos;s you.
          </p>
        </div>
      </div>

      {error ? <p className="companion-connect-error" role="alert">{error}</p> : null}

      {token ? (
        <div className="companion-connect-token">
          {pushedToExtension ? (
            <p className="companion-connect-ok">
              <Check size={15} /> Sent to your installed extension. Reload the EA Web App tab.
            </p>
          ) : (
            <>
              <label htmlFor="companion-token">Paste this into the companion&apos;s settings</label>
              <div className="companion-connect-row">
                <input id="companion-token" readOnly value={token} onFocus={(e) => e.target.select()} />
                <button type="button" onClick={copy}>
                  {copied ? <><Check size={14} /> Copied</> : <><Copy size={14} /> Copy</>}
                </button>
              </div>
            </>
          )}
          <p className="companion-connect-expiry">
            {/* Said plainly, because "it stopped working" is otherwise
                indistinguishable from a fault. */}
            Expires {expiresAt ? new Date(expiresAt).toLocaleString() : "soon"}. Come back here and
            connect again when it does — the overlay will tell you.
          </p>
          <button type="button" className="companion-connect-again" onClick={connect} disabled={busy}>
            <RefreshCw size={14} /> Get a new one
          </button>
        </div>
      ) : (
        <button type="button" className="companion-connect-cta" onClick={connect} disabled={busy}>
          {busy ? "Connecting…" : "Connect companion"}
        </button>
      )}
    </section>
  );
}
