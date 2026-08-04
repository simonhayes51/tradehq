// src/axios.js
import axios from "axios";

/**
 * Axios instance with:
 * - Base URL from VITE_API_URL (fallback: https://api.futhub.co.uk)
 * - withCredentials cookies (Starlette session)
 * - 10s timeout
 * - Idempotent GET retries (max 2; exponential backoff + jitter)
 * - 429 Retry-After honouring
 * - Premium gate (HTTP 402) → dispatches `premium:blocked` event
 * - Normalised error: err.userMessage
 *
 * To disable retries per request: api.get("/x", { __noRetry: true })
 * To change max retries per request: api.get("/x", { __maxRetries: 1 })
 */

const FALLBACK_API = "https://api.futhub.co.uk";

const base =
  (import.meta.env?.VITE_API_URL && import.meta.env.VITE_API_URL.replace(/\/$/, "")) ||
  FALLBACK_API;

if (import.meta.env.DEV) {
  console.log("🔧 Axios env:", {
    VITE_API_URL: import.meta.env?.VITE_API_URL,
    base,
    mode: import.meta.env.MODE,
  });
}

const api = axios.create({
  baseURL: base, // no trailing slash; callers pass "/api/..."
  withCredentials: true,
  timeout: 10000,
});

// -------- helpers ------------------------------------------------------------

const IDEMPOTENT = new Set(["get", "head", "options"]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const backoff = (attempt) => {
  // attempt 0 → 300ms, 1 → 600ms, 2 → 1200ms (+ jitter 0–150ms)
  const base = 300 * Math.pow(2, attempt);
  return base + Math.floor(Math.random() * 150);
};

function parseRetryAfter(headerValue) {
  if (!headerValue) return null;
  // If number → seconds
  const asNum = Number(headerValue);
  if (!Number.isNaN(asNum)) return Math.max(0, asNum * 1000);

  // Otherwise, HTTP-date
  const when = Date.parse(headerValue);
  if (!Number.isNaN(when)) {
    const diff = when - Date.now();
    return diff > 0 ? diff : 0;
  }
  return null;
}

function getUserFriendlyMessage(status, originalMessage) {
  switch (status) {
    case 401: return "Please log in to continue";
    case 402: return "This feature requires Premium";
    case 403: return "You do not have permission to perform this action";
    case 404: return "The requested resource was not found";
    case 429: return "You’re doing that too fast. Please try again in a moment";
    case 500: return "Server error. Please try again later";
    case undefined: return "Network error. Please check your connection";
    default: return originalMessage || "An unexpected error occurred";
  }
}

// -------- interceptors -------------------------------------------------------

// Request
api.interceptors.request.use(
  (config) => {
    // default headers
    config.headers["Accept"] = config.headers["Accept"] || "application/json";
    if (!config.headers["Content-Type"] && !(config.data instanceof FormData)) {
      config.headers["Content-Type"] = "application/json";
    }

    // ensure no double slashes in path (keeps protocol intact)
    if (config.url) config.url = config.url.replace(/([^:]\/)\/+/g, "$1");

    // retry metadata
    if (config.__retryCount == null) config.__retryCount = 0;
    if (config.__maxRetries == null) config.__maxRetries = 2; // GET/HEAD/OPTIONS only

    if (import.meta.env.DEV) {
      console.log(`[axios] → ${config.method?.toUpperCase()} ${config.baseURL}${config.url}`);
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response (success)
api.interceptors.response.use(
  (response) => {
    if (import.meta.env.DEV) {
      console.log("[axios] ←", response.status, response.config.url);
    }
    return response;
  },

  // Response (error) + retry + premium gate
  async (error) => {
    const cfg = error.config || {};
    const method = (cfg.method || "get").toLowerCase();
    const status = error.response?.status;
    const detail = error.response?.data?.detail;

    const rawMessage =
      (typeof detail === "string" && detail) ||
      detail?.message ||
      error.message;

    if (import.meta.env.DEV) {
      console.error("🚨 Axios error:", {
        status,
        message: rawMessage,
        url: cfg.url,
        baseURL: cfg.baseURL,
      });
    }

    // --- 402 Premium gate ---
    if (status === 402 && detail?.feature) {
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("premium:blocked", { detail }));
      }
      const enhanced402 = { ...error, userMessage: getUserFriendlyMessage(402, detail?.message) };
      return Promise.reject(enhanced402);
    }

    // --- 401: redirect to login ---
    // v1's authed pages all sit behind PrivateRoute, which already keeps
    // logged-out visitors away before any API call happens - so a 401
    // there really does mean "the session just expired, bounce to
    // login." v2's pages are public-first with optional authed widgets
    // (e.g. a Watchlist section on an otherwise-public dashboard), where
    // a 401 is an expected, handled response, not a reason to hijack the
    // whole page. __skipAuthRedirect opts a request out, same pattern as
    // __noRetry/__maxRetries below.
    if (status === 401 && !cfg.__skipAuthRedirect) {
      if (typeof window !== "undefined") window.location.href = "/login";
      const enhanced401 = { ...error, userMessage: getUserFriendlyMessage(401) };
      return Promise.reject(enhanced401);
    }

    // --- 429: honour Retry-After for idempotent methods ---
    if (status === 429 && IDEMPOTENT.has(method) && !cfg.__noRetry) {
      const waitMs = parseRetryAfter(error.response.headers?.["retry-after"]) ??
                     backoff(cfg.__retryCount || 0);
      if ((cfg.__retryCount || 0) < (cfg.__maxRetries || 0)) {
        cfg.__retryCount = (cfg.__retryCount || 0) + 1;
        await sleep(waitMs);
        return api(cfg);
      }
    }

    // --- Network/timeout/5xx: retry idempotent methods ---
    const isNetworkError = !error.response;
    const isTimeout = error.code === "ECONNABORTED";
    const is5xx = status >= 500 && status <= 599;

    if ((isNetworkError || isTimeout || is5xx) && IDEMPOTENT.has(method) && !cfg.__noRetry) {
      if ((cfg.__retryCount || 0) < (cfg.__maxRetries || 0)) {
        const attempt = cfg.__retryCount || 0;
        cfg.__retryCount = attempt + 1;
        await sleep(backoff(attempt));
        return api(cfg);
      }
    }

    // Normalise and bubble up
    const enhancedError = { ...error, userMessage: getUserFriendlyMessage(status, rawMessage) };
    return Promise.reject(enhancedError);
  }
);

export default api;
