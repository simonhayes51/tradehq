from __future__ import annotations

import inspect
import os

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder/placeholder")
os.environ.setdefault("SECRET_KEY", "test-only-placeholder-not-a-real-key")
os.environ.setdefault("ENV", "test")


def test_email_auth_is_not_mounted_by_default_in_production():
    import main

    source = inspect.getsource(main)
    assert 'os.getenv("ENABLE_EMAIL_AUTH", "0" if IS_PROD else "1") == "1"' in source
    assert "app.include_router(auth_email_router)" in source


def test_proxy_headers_are_not_trusted_from_every_host():
    import main

    source = inspect.getsource(main)
    assert 'trusted_hosts="*"' not in source
    assert "TRUSTED_PROXY_HOSTS" in source


def test_origin_guard_blocks_bad_cross_origin_cookie_mutations():
    import main

    source = inspect.getsource(main.OriginGuardMiddleware)
    assert "POST" in source
    assert "DELETE" in source
    assert '"session" in request.cookies' in source
    assert "Invalid request origin" in source


def test_launch_health_derives_opportunity_status_from_freshness():
    from app.routers import launch_health

    assert launch_health._opportunity_status(1, 60, 60, 60) == launch_health.STATUS_HEALTHY
    assert launch_health._opportunity_status(0, 60, 60, 60) == launch_health.STATUS_DEGRADED
    assert launch_health._opportunity_status(1, 7200, 60, 60) == launch_health.STATUS_DOWN
    source = inspect.getsource(launch_health.launch_health)
    assert '"status": "ACTIVE"' not in source
    assert "_opportunity_status" in source
