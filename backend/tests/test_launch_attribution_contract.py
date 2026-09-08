from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_and_backend_allow_partner_attribution_without_personal_fields():
    frontend = (ROOT.parent / "frontend" / "src" / "lib" / "analytics.js").read_text(encoding="utf-8")
    backend = (ROOT / "app" / "routers" / "v2" / "events.py").read_text(encoding="utf-8")

    for key in ('"source"', '"campaign"'):
        assert key in frontend
        assert key in backend

    assert 'params.get("ref")' in frontend
    assert 'params.get("utm_source")' in frontend
    assert 'params.get("utm_campaign")' in frontend
    assert "sessionStorage" in frontend

    # Attribution codes are operator-defined labels only. The funnel must
    # not grow fields for personal identifiers or full referring URLs.
    for forbidden in ('"email"', '"discord_id"', '"user_agent"', '"referrer_url"'):
        assert forbidden not in backend.split("ALLOWED_PROPS =", 1)[1].split("})", 1)[0]
