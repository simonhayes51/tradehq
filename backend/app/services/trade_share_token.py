# app/services/trade_share_token.py
#
# Short-lived, HMAC-signed tokens that gate the internal trade-share
# render-data endpoint (GET /api/internal/render/trade-share/{trade_id}),
# mirroring player_card_token.py's pattern exactly - the caller there is
# headless Chromium, driven by our own backend, with no user session.
#
# Unlike a player card (public data, keyed only by card_id), a trade
# belongs to exactly one user, and `trades` has no globally-unique id -
# only a (user_id, trade_id) composite key. So the token signs BOTH
# fields together, and the render-data endpoint queries by that exact
# pair - a forged or reused trade_id alone can never surface a different
# user's trade, because the ownership check is baked into what the token
# can even decode to, not a separate lookup that could be gotten wrong.
from __future__ import annotations

import os

from itsdangerous import URLSafeTimedSerializer

_SALT = "trade-share-render-v1"

_SECRET = os.getenv("PLAYER_CARD_RENDER_SECRET") or os.getenv("SECRET_KEY") or "dev-insecure-trade-share-render-secret"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_SECRET, salt=_SALT)


def make_render_token(user_id: str, trade_id: int) -> str:
    return _serializer().dumps({"user_id": str(user_id), "trade_id": int(trade_id)})


def verify_render_token(trade_id: int, token: str, max_age_seconds: int = 120) -> str | None:
    """Returns the signed user_id if the token is valid, unexpired, and its
    signed trade_id matches the one being requested - otherwise None."""
    if not token:
        return None
    try:
        payload = _serializer().loads(token, max_age=max_age_seconds)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("trade_id") != int(trade_id):
        return None
    user_id = payload.get("user_id")
    return str(user_id) if user_id is not None else None
