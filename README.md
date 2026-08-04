# FutHub — monorepo

Combines the three previously separate repositories into one, as folders,
for browsing and review purposes. Content is `main` from each source repo
as of 2026-08-04.

| Folder      | Was                              | Runtime                          | Deploy target |
|-------------|-----------------------------------|-----------------------------------|---------------|
| `backend/`  | `simonhayes51/backend`            | FastAPI, Python                   | Railway (API service) |
| `frontend/` | `simonhayes51/frontend_new`       | React + Vite                      | Railway/static (built assets) |
| `auto_sync/`| `simonhayes51/auto_sync`          | Playwright/Chromium, Python       | Railway (multiple worker processes, see `auto_sync/Procfile`) |

**This is not a runtime merge.** The three services still deploy and scale
independently, and should — `auto_sync` runs headless Chromium workers,
`backend` is a request-serving API, `frontend` is a static build. Bundling
those into one process would mean shipping a browser automation stack
inside the API server. See `REVIEW.md` for the reasoning.

**This is not pushed anywhere.** It's a local snapshot for review. The
three GitHub repos are still the source of truth; nothing here should be
treated as authoritative until you decide to actually consolidate the
git history (which this zip deliberately does not attempt — flattening
three independent histories into one is a real decision with real
tradeoffs, not something to do silently inside a review task).

See `REVIEW.md` for the code review: what's dead, what's been removed
already, and what's still live but worth a decision.

See `DEPLOY.md` for standing this up on Coolify as one repo against one
Postgres database, including a real ordering hazard in the migration
runner that matters specifically because this is a fresh database
rather than the existing one.
