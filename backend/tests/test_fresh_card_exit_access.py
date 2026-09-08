from app.services.fresh_card_routes import exit_quality_is_unlocked


class _Viewer:
    def __init__(self, *, opportunity_feed: bool, is_free: bool):
        self._opportunity_feed = opportunity_feed
        self.is_free = is_free

    def has(self, feature: str) -> bool:
        assert feature == "opportunity_feed"
        return self._opportunity_feed


def test_elite_receives_exit_evidence_for_any_card():
    viewer = _Viewer(opportunity_feed=True, is_free=False)
    assert exit_quality_is_unlocked(viewer, 123, []) is True


def test_free_receives_exit_evidence_only_for_persisted_daily_pick():
    viewer = _Viewer(opportunity_feed=False, is_free=True)
    assert exit_quality_is_unlocked(viewer, 123, [99, 123]) is True
    assert exit_quality_is_unlocked(viewer, 124, [99, 123]) is False


def test_signed_out_or_non_free_locked_viewer_never_receives_exit_evidence():
    viewer = _Viewer(opportunity_feed=False, is_free=False)
    assert exit_quality_is_unlocked(viewer, 123, [123]) is False
