from app.auth.api_keys import generate_api_key, require_api_key
from app.routers.public_api_v3 import router


def test_new_api_keys_use_eafclive_brand():
    key = generate_api_key()
    assert key.startswith("eafclive_")
    assert len(key) > len("eafclive_") + 20


def test_v3_routes_are_api_key_gated():
    expected = {
        "/api/public/v3/players/search",
        "/api/public/v3/players/{card_id}",
        "/api/public/v3/players/{card_id}/price",
        "/api/public/v3/players/{card_id}/sales",
        "/api/public/v3/usage",
    }
    seen = set()
    for route in router.routes:
        if route.path not in expected:
            continue
        seen.add(route.path)
        dependencies = [dep.call for dep in route.dependant.dependencies]
        assert require_api_key in dependencies, f"{route.path} is not API-key gated"
    assert seen == expected
