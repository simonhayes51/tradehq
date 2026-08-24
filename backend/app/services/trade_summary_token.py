# app/services/trade_summary_token.py
#
# Signed tokens for the portfolio-summary share card's internal
# render-data endpoint - mirrors trade_share_token.py, but signs
# (user_id, period) instead of (user_id, trade_id), since a summary has
# no single trade to scope by.
from __future__ import annotations

import os

from itsdangerous import URLSafeTimedSerializer

_SALT = "trade-summary-render-v1"

_SECRET = os.getenv("PLAYER_CARD_RENDER_SECRET") or os.getenv("SECRET_KEY") or "dev-insecure-trade-share-render-secret"

VALID_PERIODS = frozenset({"today", "week", "all"})


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_SECRET, salt=_SALT)


def make_render_token(user_id: str, period: str) -> str:
    return _serializer().dumps({"user_id": str(user_id), "period": period})


def verify_render_token(period: str, token: str, max_age_seconds: int = 120) -> str | None:
    if not token:
        return None
    try:
        payload = _serializer().loads(token, max_age=max_age_seconds)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("period") != period:
        return None
    user_id = payload.get("user_id")
    return str(user_id) if user_id is not None else None
