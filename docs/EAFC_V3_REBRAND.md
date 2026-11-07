# EAFC.Live 3.0 — Sports Editorial Rebrand

Status: **development branch only**. Do not deploy without review and verification.

## Implemented in this branch
- White/black/electric-orange public marketing theme, scoped to `.v3-public`.
- Dark/orange member theme, scoped to `.v3-root`.
- Responsive member/dashboard polish and billing theme.
- Discord command embed primary brand colour switched to orange. Warning, danger and neutral colours remain semantic.
- New Stripe checkout trial default set to zero; `.env.example` updated. **Production environment variables must be reviewed separately.** Existing subscriptions are not migrated.

## Files
- `frontend/src/v3/design/tokens.css`
- `frontend/src/v3/design/public.css`
- `frontend/src/v3/design/member.css`
- `frontend/src/v3/design/billing.css`
- `frontend/src/PublicSite.jsx`
- `frontend/src/v2/App.v2.jsx`
- `backend/app/services/discord_embeds.py`
- `backend/main.py`
- `.env.example`

## Verification before merging
1. `cd frontend && npm ci && npm run build`.
2. Run existing frontend tests and manually verify public homepage, guides, pricing, login, billing, dashboard, opportunities, watchlist, portfolio and mobile navigation.
3. `cd backend && pytest -q tests/test_billing_hardening.py tests/test_billing_plans_cache.py tests/test_subscription_lifecycle.py`.
4. Test Stripe monthly and season checkout in test mode, including zero-day trial, failed payments, webhook retries and existing subscriber renewals.
5. Verify Discord embed JSON with current command/feed tests and ensure no semantic status colour regressions.
6. Review accessibility, contrast, keyboard focus, narrow-screen overflow and reduced-motion settings.
7. Audit hard-coded green styling across older pages and Discord feed builders. This branch is a **first integration pass**, not an exhaustive visual migration.

## Rollback
The branch has not changed production. If later deployed, revert the merge or deploy the previous frontend/backend image. Stripe trial behaviour is additionally controlled by the production `STRIPE_TRIAL_DAYS` environment variable. Never attempt to roll back Stripe charges or subscription history to undo a visual release.

## Next implementation passes
- Replace legacy brand icon assets with approved, rights-cleared V3 logo artwork.
- Refactor large dashboard components into modular V3 components and update all trade-tool page styles.
- Audit and align Discord automatic feed embeds, notifications and promotional assets.
- Add visual regression screenshots and automated V3 component tests.
- Stage the full rebrand behind an explicit production rollout flag before enabling it on live hosts.
