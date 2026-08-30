# Companion authentication: what was verified, and what could not be

Phase 2 gated `/api/v2/companion/card/{id}` and switched both clients to
send credentials. That closed the bypass, and made the paid overlay depend
on a **cross-site cookie**. This is the verification of that, and what was
done about the parts that cannot be relied on.

## First, a correction to the premise

The objective asks whether browsers attach the application cookie "from
the FUT.GG page context". They do not, because **neither client runs on
FUT.GG**. Both run inside EA's own Web App:

| | page context | calls |
|---|---|---|
| Extension | `https://*.ea.com/*ultimate-team*web-app*` | `https://api.eafc.live` |
| Userscript | `https://www.ea.com/*/ea-sports-fc/ultimate-team/web-app/*` | `https://api.eafc.live` |

FUT.GG is the *market data source* the backend scrapes, not a page the
companion is injected into. The cross-site problem is real either way —
`www.ea.com` → `api.eafc.live` is third-party — so everything below still
applies.

## Verified, statically and by test

| | finding |
|---|---|
| **Cookie SameSite** | `none` in production, `lax` otherwise (`main.py::_session_middleware_kwargs`). `SameSite=None` is what permits third-party delivery at all. |
| **Cookie Secure** | `https_only=IS_PROD`. Mandatory with `SameSite=None`; browsers reject the pair otherwise. |
| **Cookie domain** | `SESSION_COOKIE_DOMAIN` / `COOKIE_DOMAIN`, applied only when set. Must resolve to something covering `api.eafc.live`. |
| **CORS allow-list** | Explicit list plus a subdomain regex. Not a wildcard, and `allow_credentials=True`. |
| **Extension host permission** | `https://api.eafc.live/*` present in `manifest.json`. |
| **Extension request origin** | The fetch is in the **MV3 service worker**, not a content script. A content script's fetch carries the *page's* origin for CORS; the service worker's does not, and host permissions exempt it. No `chrome-extension://` origin was added to the backend allow-list — it isn't needed, and adding one would widen the allow-list for every caller. |
| **Userscript `@connect`** | `api.eafc.live` declared. Managers refuse `GM_xmlhttpRequest` to undeclared hosts. |
| **Preflight** | Both clients send a simple `GET` with `Accept` and (now) `Authorization`. `Authorization` makes it non-simple, so a preflight occurs; `allow_headers=["*"]` and `allow_methods=["*"]` cover it. Extension service-worker requests bypass CORS entirely. |
| **No EA credential is forwarded** | Asserted by test across both clients: no `X-UT-SID`, no phishing token, no nucleus id. |
| **Response per state** | Asserted by test — see the table below. |

### What each state gets back

| state | response |
|---|---|
| **Signed out** | Identity, rating, version, an **approximate** reference price, `locked: true`, `requires_account: true`, and a `signup_url`. No exact price, fair value, sales median, verdict, confidence, liquidity, trend or risk. |
| **Free** | The real reference price and verdict — that is what the account is for — with the executable plan withheld except on that day's two free picks. `upgrade_url` present. |
| **Elite** | The complete payload, untouched. |
| **Expired subscription** | Resolves to Free on the next request. Entitlements are recomputed from stored billing state on every call (60s cache), never carried in a credential. |

## What could NOT be verified here, and why

**Whether a browser actually attaches the cookie.** That is a property of
Chrome, Safari, Tampermonkey, Violentmonkey and the iOS Userscripts app —
not of this codebase — and it cannot be established from a test suite or
from this build environment.

Two specific risks:

1. **Third-party cookie phase-out.** The extension's service-worker fetch
   with host permissions is the strongest position available, but it rests
   on behaviour that has changed repeatedly and is changing again.

2. **`GM_xmlhttpRequest` + `withCredentials` is not portable.** Support
   differs *between managers*. This codebase has already been bitten by
   exactly that class of difference: `src/api-client.js` documents a live
   iOS case where one manager implemented `GM.xmlHttpRequest` and not
   `GM_xmlhttpRequest`, found only on a real device. There is no honest
   version of "test it once and rely on it".

Neither cookie nor CORS security was weakened to work around this, as the
objective requires.

## What was built instead

A **short-lived, read-only bearer token** — the explicit flow the
objective calls for when cookies cannot be relied on.

```
POST /api/v2/companion/session      (session cookie, first-party, on app.eafc.live)
  -> { token, expiresAt, expiresAtIso, scope: "companion:read", ttlHours }

GET  /api/v2/companion/card/{id}
  Authorization: Bearer <token>     (a header cannot be declined)
```

Design decisions, and why:

- **Minted where the cookie is first-party.** The member is on
  `app.eafc.live` when they click Connect, so there is no cross-site
  question at the moment of issue.
- **Short-lived.** `COMPANION_TOKEN_TTL_HOURS`, default 24. Explicitly
  **not** a permanent API key in browser-accessible storage.
- **Read-only.** Scope `companion:read` grants exactly the card payload
  the overlay already renders. It cannot write, spend, or reach anything
  else.
- **Not the existing extension token.** That one is scoped `trade:ingest`,
  carries a 30-day TTL, and is a *write* credential. Reusing it would put
  a long-lived write token in every companion user's hands.
- **Scope is checked explicitly**, not inferred from the signature: the
  signing key is shared, so a valid signature says nothing about what a
  token was issued *for*. A `trade:ingest` token is refused here.
- **The token says who, never what.** Entitlements are resolved from
  stored billing state on every request. A token minted while someone was
  Elite gives them nothing once the subscription lapses, and the payload
  carries no tier, plan or feature list.
- **A live cookie session wins.** Someone signed in on this browser is who
  they are; a stale token in an extension must not override that.
- **Revocation.** Rotating `JWT_PRIVATE_KEY` invalidates every outstanding
  companion token at once — the only revocation a stateless token has, and
  worth being able to reach for.
- **Bearer path is companion-only.** It is not added to the rest of the
  API; one more way to be authenticated belongs where it is needed.
- **Storage is host-private.** `chrome.storage.local` for the extension
  (the EA page cannot read it) and GM storage for the userscript (likewise).
  Never page `localStorage`.
- **Issued by POST.** It mints a credential, so it must not be reachable
  by a link, a prefetch or an `<img>` tag.
- **Handed over automatically where possible.** `externally_connectable`
  is scoped to `https://app.eafc.live/*`, so the app can push the token
  straight into an installed extension; the copy-paste path covers the
  userscript and any browser where that fails.

Both clients still send the cookie as well. Whichever the browser honours
wins, and the reliable path is there when it does not.

## Configuration

| variable | default | purpose |
|---|---|---|
| `COMPANION_TOKEN_TTL_HOURS` | 24 | how long a companion token lasts |
| `JWT_PRIVATE_KEY` | — | signing key; rotating it revokes every outstanding token |
| `VITE_COMPANION_EXTENSION_IDS` | — | comma-separated extension ids the app may push a token to |

## Left to verify on real devices

This is a checklist for the launch smoke test, not a claim:

1. Chrome + extension, signed in → full payload for an Elite account.
2. Chrome + extension, signed out → locked payload with `signup_url`.
3. Tampermonkey (desktop), token connected → full payload.
4. Tampermonkey (desktop), **no** token, cookie only → confirms whether
   `withCredentials` works there.
5. iOS Userscripts app, token connected → full payload. This is the
   manager most likely to need the token.
6. An expired token → locked payload and a reconnect prompt, **not** an
   error.
