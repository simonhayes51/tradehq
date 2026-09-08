from datetime import datetime, timezone

from app.routers import admin_reporting


def test_upcoming_event_routes_exist_and_are_admin_scoped():
    paths = {(route.path, tuple(sorted(route.methods or []))) for route in admin_reporting.router.routes}

    assert ("/api/admin/upcoming-events", ("GET",)) in paths
    assert ("/api/admin/upcoming-events", ("POST",)) in paths
    assert ("/api/admin/upcoming-events/{event_id}", ("PATCH",)) in paths
    assert ("/api/admin/upcoming-events/{event_id}", ("DELETE",)) in paths


def test_upcoming_event_accepts_game_release_kind():
    payload = admin_reporting.UpcomingEventCreate(
        name="FC27 Release",
        kind="release",
        start_at=datetime(2026, 9, 25, 0, 0, tzinfo=timezone.utc),
        confidence="confirmed",
    )
    assert payload.kind == "release"
    assert payload.name == "FC27 Release"
