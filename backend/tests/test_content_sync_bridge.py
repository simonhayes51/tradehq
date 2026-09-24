from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.v2 import content_sync


def _client() -> TestClient:
    app = FastAPI()
    app.state.player_pool = object()
    app.include_router(content_sync.router, prefix="/api/v2")
    return TestClient(app)


def test_ingest_is_closed_when_token_is_not_configured(monkeypatch):
    monkeypatch.delenv("FUTGG_CONTENT_SYNC_TOKEN", raising=False)
    response = _client().post("/api/v2/internal/futgg-content", json={})
    assert response.status_code == 503


def test_ingest_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("FUTGG_CONTENT_SYNC_TOKEN", "correct-token")
    response = _client().post(
        "/api/v2/internal/futgg-content",
        headers={"X-Content-Sync-Token": "wrong-token"},
        json={},
    )
    assert response.status_code == 401


def test_ingest_accepts_authenticated_collector(monkeypatch):
    monkeypatch.setenv("FUTGG_CONTENT_SYNC_TOKEN", "correct-token")

    async def fake_ingest(pool, payload):
        assert pool is not None
        assert payload["game_year"] == 27
        return {"sbcs": 9, "solution_items": 22, "gallery_sets": 126, "gallery_tags": 21}

    monkeypatch.setattr(content_sync.futgg_content, "ingest_content_payload", fake_ingest)
    response = _client().post(
        "/api/v2/internal/futgg-content",
        headers={"X-Content-Sync-Token": "correct-token"},
        json={"game_year": 27},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "sbcs": 9,
        "solution_items": 22,
        "gallery_sets": 126,
        "gallery_tags": 21,
    }
