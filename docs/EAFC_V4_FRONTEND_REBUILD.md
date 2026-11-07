# EAFC.Live — complete frontend replacement (V4 working branch)

## Mandate
Replace every customer-facing frontend layout and component, **not** the backend, data, pricing, trading algorithms, API contracts, database, or Discord services. The approved identity is white / black / electric orange, with editorial presentation on the public website and a compact, information-first trading workspace in the app.

This branch is intentionally **not deployable until all route groups pass the gates below**. No further accumulation of CSS overrides against V2/V3. The new frontend lives in `frontend/src/v4/`; existing production routes stay intact until the corresponding replacement is complete.

## Design constraints
- Mobile first at 320px, 375px, 390px, 430px; desktop 1024px, 1440px.
- No giant hero on logged-in dashboard; show trading actions and real data within the first viewport.
- Light/white content surface for data readability, black app chrome, orange for primary actions, green/red exclusively for profit/loss or status semantics.
- Real prices, evidence, freshness, account and Stripe data only. No fake metrics, stale screenshots presented as live data, or claims of guaranteed returns.
- Navigation is new, with five mobile destinations and desktop rail. Every interactive control must have an accessible focus state.
- Maintain HashRouter deep links, /v2 redirects, auth and entitlement gates, Stripe test/live boundaries, and internal render-only URLs.

## Frontend inventory / required replacements
1. **Foundation:** `src/App.jsx`, `src/index.css`, `src/v2/App.v2.jsx`, legacy Layout, tokens, navigation, mobile menus, typography, icon and PWA brand assets.
2. **Public:** `src/PublicSite.jsx`, public-site styles, landing sections, pricing, FAQ, signup, login, terms/privacy (keep legal wording), companion and demo.
3. **Trading home:** `src/v2/pages/HomeDashboard/`, live dashboard hooks, lead opportunity, market overview, alerts, empty/loading states, detail modal.
4. **Trading tools:** Opportunities, TradeFinder, Players, PlayerPage, Watchlist, Portfolio/Club, TrackRecord, ReleasePriceRises, SBC, Gallery, Chat and remaining legacy tools.
5. **Commercial:** Billing, PreseasonBilling, DiscordFeed, Account, Onboarding, entitlement prompts and upgrade flows.
6. **Operations:** Admin and admin reporting screens; maintain all current permissions.
7. **Visuals:** All icons, favicons, social previews, Discord-facing brand art and email branding where frontend-owned.

## Architecture
- `src/v4/design/`: CSS variables, reset, typography, primitives, responsive utilities.
- `src/v4/components/`: buttons, cards, badges, inputs, tables, dialogs, data states and player artwork.
- `src/v4/layout/`: new public shell, desktop workspace and mobile navigation.
- `src/v4/pages/`: **new JSX**, not old V2 markup under new classes.
- `src/v4/adapters/`: typed/validated view-model adapters around **existing** hooks and API responses. No forked trading calculations.
- `src/v4/tests/`: route smoke tests, viewport/interaction checks, auth/entitlement and payment-state regression tests.

## Delivery gates (all required before merge)
- Complete route-by-route replacement matrix; no customer-facing V2/V3 page rendered by the new router.
- Frontend build, lint and tests green; no console/runtime errors.
- Browser screenshots reviewed for each page at all target viewport sizes.
- Signup, login, free/Elite, existing paid member, expired/cancelled subscription and Stripe checkout/cancel paths verified in test mode.
- Price freshness, conservative buy ceiling, after-tax profit, watchlist, portfolio, Discord feed, deep links and internal image rendering verified.
- Accessibility keyboard/focus/contrast and reduced-motion checks.
- Feature flag / independent frontend entry so rollback is switching back to existing app bundle, not reversing Stripe or data changes.

## Pull request sequence
- PR A: isolated design system, reusable components and visual tests.
- PR B: new public website + auth flows.
- PR C: new workspace shell + dashboard + opportunity detail.
- PR D: trading research, finder, players, watchlist, portfolio, market pages.
- PR E: billing, account, Discord, onboarding and admin.
- PR F: cutover and end-to-end tests, assets and rollback drill.

**Status:** architecture and initial implementation work only. This document is not evidence that any route has been replaced or validated.
