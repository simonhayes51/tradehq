// src/lib/domains.js
//
// Domain split: eafc.live is the public marketing site, app.eafc.live is
// the trading application. Both are served from the SAME frontend build
// (see App.jsx) - this just tells that one build which host it's running
// on, at runtime, via window.location. Centralised here so PublicSite.jsx
// and HomeDashboard.jsx's redirect-to-public-site logic can't drift apart
// on what "the public host" means.
export const PUBLIC_HOSTNAME = "eafc.live";
export const APP_ORIGIN = "https://app.eafc.live";
export const DISCORD_INVITE_URL =
  import.meta.env.VITE_DISCORD_INVITE_URL || "https://discord.gg/3sSpu3rDgn";

export function isPublicHost(hostname = window.location.hostname) {
  return hostname === PUBLIC_HOSTNAME || hostname === `www.${PUBLIC_HOSTNAME}`;
}
