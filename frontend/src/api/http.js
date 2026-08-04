// src/api/http.js

// Normalise base like: https://api.futhub.co.uk (no trailing slash)
const API_BASE = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");

// Join base + path and attach query params
function buildUrl(path, query) {
  const p = path.startsWith("/") ? path : `/${path}`;
  const url = new URL(API_BASE ? API_BASE + p : p, API_BASE || window.location.origin);

  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === "") continue;
      // Allow arrays by repeating keys; everything else toString()
      if (Array.isArray(v)) {
        v.forEach((vv) => url.searchParams.append(k, String(vv)));
      } else {
        url.searchParams.append(k, String(v));
      }
    }
  }
  return url.toString();
}

// Single fetch call with network-error normalization
async function doFetch(url, init) {
  try {
    return await fetch(url, init);
  } catch (err) {
    const msg = err?.message || String(err);
    throw new Error(`network error @ ${url}: ${msg}`);
  }
}

/**
 * apiFetch("/api/xyz", { method, body, query, headers, credentials, retry, retryDelayMs })
 * - Forces absolute URLs using VITE_API_URL
 * - Sends cookies by default (credentials: 'include')
 * - Parses JSON if content-type is application/json
 * - Distinguishes network errors from HTTP errors
 * - Retries once on network errors (configurable)
 */
export async function apiFetch(path, opts = {}) {
  const {
    method = "GET",
    body,
    query,
    headers = {},
    credentials = "include",
    retry = 1,            // retry once on network error
    retryDelayMs = 500,   // backoff for retry
  } = opts;

  const url = buildUrl(path, query);

  // Helpful in Dev/Prod debugging to verify the final absolute URL
  if (typeof window !== "undefined") {
    // eslint-disable-next-line no-console
    console.log("[apiFetch]", method, url);
  }

  const init = { method, headers: { Accept: "application/json", ...headers }, credentials };

  if (body !== undefined) {
    if (body instanceof FormData) {
      init.body = body; // browser sets the correct multipart boundary
    } else {
      init.headers["Content-Type"] = init.headers["Content-Type"] || "application/json";
      init.body = typeof body === "string" ? body : JSON.stringify(body);
    }
  }

  let res;
  try {
    res = await doFetch(url, init);
  } catch (e) {
    if (retry > 0) {
      await new Promise((r) => setTimeout(r, retryDelayMs));
      res = await doFetch(url, init);
    } else {
      throw e;
    }
  }

  const ct = res.headers.get("content-type") || "";
  const isJson = ct.includes("application/json");
  const data = isJson ? await res.json().catch(() => null) : await res.text();

  if (!res.ok) {
    const msg =
      (data && (data.detail || data.error || data.message)) ||
      `${res.status} ${res.statusText}`;
    throw new Error(`${msg} @ ${url}`);
  }

  return isJson ? (data ?? {}) : data;
}

// Optional: tiny helper for GET with query
export function get(path, query, opts) {
  return apiFetch(path, { ...opts, method: "GET", query });
}

// Optional: tiny helper for POST JSON
export function post(path, body, opts) {
  return apiFetch(path, { ...opts, method: "POST", body });
}