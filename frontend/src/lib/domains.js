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

// Community/support server invite. This is NOT the application-install
// link: using a discord.gg URL behind an "Add to Discord" button sends a
// server owner into our community instead of installing EAFC.Live into
// theirs.
export const DISCORD_INVITE_URL =
  import.meta.env.VITE_DISCORD_INVITE_URL || "https://discord.gg/3sSpu3rDgn";

// Guild install for the EAFC.Live bot. The default permissions are the
// minimum the Server Feed guide promises: View Channel (1024), Send
// Messages (2048) and Embed Links (16384) = 19456. Slash commands are
// included through applications.commands. Keep an env override so the
// install URL can be replaced without a code deploy if the Discord app or
// permission set ever changes.
export const DISCORD_BOT_INSTALL_URL =
  import.meta.env.VITE_DISCORD_BOT_INSTALL_URL ||
  "https://discord.com/oauth2/authorize?client_id=1408193521986437251&permissions=19456&scope=bot%20applications.commands";

export function isPublicHost(hostname = window.location.hostname) {
  return hostname === PUBLIC_HOSTNAME || hostname === `www.${PUBLIC_HOSTNAME}`;
}
