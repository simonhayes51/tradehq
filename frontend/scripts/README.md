# Frontend build helpers

`prepare-companion.mjs` runs before Vite builds the production frontend.

The deploy Docker context is `frontend/`, while the maintainable Companion
source lives in the sibling `eafc-live-userscript/` package. Because Docker
cannot read outside its build context, this helper applies the small,
validated FC27 compatibility migration to the checked-in userscript artifact
before Vite copies `public/` into `dist/`.

It is intentionally fail-closed: if the checked-in artifact no longer contains
the exact version/path fragments this migration expects, the frontend build
stops instead of silently shipping an old or partially patched Companion.

For normal Companion development, `eafc-live-userscript/build.mjs` remains the
source-of-truth build. Rebuild and copy its output back into
`frontend/public/companion/eafc-live.user.js` when working on that package
locally; once the checked-in artifact is natively v1.10.0+, this one-time
preparation helper can be retired.
