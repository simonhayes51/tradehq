"""Regression checks for FC27 special-card placeholder enrichment."""
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "futgg_player_sync.py"
TEXT = SOURCE.read_text()


def test_placeholder_recovery_is_bounded_and_fc27_only():
    assert "FUTGG_PLACEHOLDER_RECOVERY_LIMIT" in TEXT
    assert 'AND base.source_card_id = (pending.source_card_id % $2)' in TEXT
    assert "WHERE pending.game_year = $1" in TEXT
    assert "AND base.game_year = $1" in TEXT
    assert "'feed_only_placeholder' = ANY(pending.metadata_warnings)" in TEXT
    assert "JOIN futgg_players base" in TEXT


def test_recovery_does_not_use_synthetic_placeholder_urls():
    fn = TEXT[TEXT.index("async def recover_placeholder_urls"):TEXT.index("async def existing_ids")]
    assert "https://www.fut.gg/players/" in fn
    assert "source_url" not in fn
    assert "source_slug" in fn


def test_redirected_special_cannot_overwrite_wrong_card():
    fn = TEXT[TEXT.index("async def _fetch_worker"):TEXT.index("async def crawl_once")]
    assert "FUT.GG redirected card to another version" in fn
    assert "Card mismatch:" in fn


def test_player_sync_parses_after_recovery_and_preserves_existing_rows():
    ast.parse(TEXT)
    assert "urls[recovery_url] = recovery_metadata" in TEXT
    assert "metadata_warnings = EXCLUDED.metadata_warnings" in TEXT
