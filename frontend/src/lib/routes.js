// src/lib/routes.js
//
// The one piece of URL logic worth isolating from App.jsx's route table:
// translating a historical /v2/* path to its current equivalent.
//
// "/v2" was internal versioning that leaked into every URL a customer
// saw, shared or bookmarked. It is gone from the address bar, but the old
// URLs are not gone from the world - this backend has posted /#/v2/...
// deep links into Discord and used them as Stripe return URLs - so the
// translation is a permanent contract, not a migration step, and it is
// tested as one.
export function legacyV2Target(pathname) {
  const rest = String(pathname || "").replace(/^\/v2(?=\/|$)/, "");
  return rest || "/";
}
