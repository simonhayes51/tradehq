"""Temporary full Elite access for signed-in users before FC27 launch.

This is deliberately an entitlement overlay, not a Stripe trial and not a
billing-row mutation. Existing paid/admin/manual grants remain real paid access;
otherwise a signed-in account receives Elite features until the configured
cutoff and automatically falls back to Free afterwards.

The overlay is installed before main.py imports the route tree (see
performance_bootstrap.py), so every consumer of resolve_user_tier(), including
Discord reconciliation, sees the same answer. New personal Stripe checkout is
blocked while the giveaway is active so we do not charge somebody for access
we are currently giving them for free. Existing subscriptions can still be
managed/cancelled normally.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from starlette.responses import JSONResponse

log = logging.getLogger("preseason_elite_access")

DEFAULT_PRESEASON_ELITE_UNTIL = "2026-09-18T00:00:00+00:00"
PROMO_NAME = "FC27 preseason Elite access"


def _parse_cutoff(raw: Optional[str] = None) -> Optional[datetime]:
    value = (raw if raw is not None else os.getenv(
        "PRESEASON_ELITE_UNTIL", DEFAULT_PRESEASON_ELITE_UNTIL
    )).strip()
    if not value or value.lower() in {"off", "false", "disabled", "0"}:
        return None
    try:
        # Accept the common trailing-Z spelling as well as explicit offsets.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        log.error("Invalid PRESEASON_ELITE_UNTIL=%r; preseason access disabled", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def preseason_elite_active(now: Optional[datetime] = None) -> bool:
    cutoff = _parse_cutoff()
    if cutoff is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) < cutoff


def preseason_payload() -> Dict[str, Any]:
    cutoff = _parse_cutoff()
    return {
        "name": PROMO_NAME,
        "active": bool(cutoff and preseason_elite_active()),
        "until": cutoff.isoformat() if cutoff else None,
        "requires_payment": False,
        "requires_stripe_trial": False,
    }


def install_preseason_elite_access() -> None:
    """Overlay the canonical entitlement resolver once per API process."""
    from app.auth import entitlements

    if getattr(entitlements, "_preseason_elite_overlay_installed", False):
        return

    original_resolve_tier = entitlements._resolve_tier
    original_builder = entitlements._entitlements_for_user
    original_compute = entitlements.compute_entitlements

    def effective_resolve_tier(state):
        base = original_resolve_tier(state)
        if base == "elite":
            return base
        return "elite" if preseason_elite_active() else base

    async def enriched_builder(
        pool,
        user_id: str,
        *,
        session_roles=(),
        discord_id: Optional[str] = None,
    ):
        # original_builder performs the real billing/admin resolution. Because
        # its global _resolve_tier lookup now points at effective_resolve_tier,
        # the returned features/limits are Elite during the promo without
        # writing fake premium_until/subscription rows.
        ent = await original_builder(
            pool,
            user_id,
            session_roles=session_roles,
            discord_id=discord_id,
        )

        # Determine WHY access is Elite separately from the effective tier, so
        # the UI never calls this a trial/subscription when it is the giveaway.
        state = await entitlements._load_billing_state(pool, str(user_id))
        is_admin = entitlements._is_admin(
            state,
            str(user_id),
            str(discord_id) if discord_id else None,
        )
        base_tier = original_resolve_tier(state)
        promo_active = preseason_elite_active()
        if is_admin:
            access_source = "admin"
        elif base_tier == "elite":
            access_source = "paid_or_granted"
        elif promo_active:
            access_source = "preseason"
        else:
            access_source = "free"

        ent["access_source"] = access_source
        ent["promo"] = preseason_payload()
        # original_builder cached before we added provenance; replace that cache
        # entry with the canonical enriched shape.
        entitlements._ENT_CACHE[str(user_id)] = {"at": time.time(), "ent": dict(ent)}
        return ent

    async def enriched_compute(req):
        ent = await original_compute(req)
        if "access_source" not in ent:
            # Anonymous visitors remain Free. Expose the promotion only so the
            # frontend can explain that signing in unlocks it; do not grant
            # account-only features to an unidentified request.
            ent["access_source"] = "anonymous" if not ent.get("user_id") else "free"
            ent["promo"] = preseason_payload()
        return ent

    entitlements._resolve_tier = effective_resolve_tier
    entitlements._entitlements_for_user = enriched_builder
    entitlements.compute_entitlements = enriched_compute
    entitlements._ENT_CACHE.clear()
    entitlements._preseason_elite_overlay_installed = True

    cutoff = _parse_cutoff()
    log.info(
        "installed preseason Elite entitlement overlay: active=%s until=%s",
        preseason_elite_active(),
        cutoff.isoformat() if cutoff else "disabled",
    )


def install_preseason_checkout_guard(app) -> None:
    """Prevent new personal Stripe checkout while Elite is free for everyone."""
    if getattr(app.state, "preseason_checkout_guard_installed", False):
        return

    @app.middleware("http")
    async def _guard_preseason_checkout(request, call_next):
        if (
            request.method.upper() == "POST"
            and request.url.path.rstrip("/") == "/api/billing/create-checkout-session"
            and preseason_elite_active()
        ):
            promo = preseason_payload()
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Elite is free until FC27 early access. No payment is needed right now.",
                    "error": "preseason_elite_free",
                    "promo_until": promo["until"],
                },
            )
        return await call_next(request)

    app.state.preseason_checkout_guard_installed = True
