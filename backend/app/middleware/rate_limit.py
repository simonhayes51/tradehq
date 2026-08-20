from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Pre-launch gap: no endpoint in this app had any request throttling, which
# left /api/auth/email/login and /register open to credential stuffing and
# left public search/price lookups trivially scrapable. This is a minimal,
# dependency-free, per-process in-memory limiter - good enough for a single
# uvicorn worker at launch. If the app ever runs multiple worker processes
# behind one host, replace the in-memory store with a shared one (e.g.
# Redis) since each worker would otherwise track its own counts.

# (path_prefix, requests_allowed, window_seconds)
RATE_LIMIT_RULES: Tuple[Tuple[str, int, int], ...] = (
    ("/api/auth/email/login", 10, 300),
    ("/api/auth/email/register", 5, 300),
    ("/api/players/search", 60, 60),
    ("/api/players/autocomplete", 120, 60),
)

_hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # ProxyHeadersMiddleware (trusted_hosts="*") already rewrites
    # request.client from X-Forwarded-For, so this is the real client IP
    # even behind a load balancer/CDN, not the proxy's own address.
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        rule = next((r for r in RATE_LIMIT_RULES if path.startswith(r[0])), None)
        if rule is not None:
            _, limit, window = rule
            key = (rule[0], _client_ip(request))
            now = time.monotonic()
            bucket = _hits[key]
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= limit:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests, please slow down."},
                    headers={"Retry-After": str(window)},
                )
            bucket.append(now)
        return await call_next(request)
