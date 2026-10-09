"""Regression checks for FUT.GG full-card art backfill."""
import ast
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "futgg_player_sync.py").read_text()


def test_player_sync_backfills_only_missing_original_composites():
    ast.parse(SOURCE)
    start = SOURCE.index("async def backfill_listing_card_images(")
    end = SOURCE.index("def _build_targets(", start)
    snippet = SOURCE[start:end]
    assert '"player-item-card/" in metadata.image_url' in snippet
    assert "int(match.group(\"game_year\")) == game_year" in snippet
    assert "p.card_image_url IS NULL" in snippet
    assert "p.game_year = $1" in snippet
    assert "card_image_stored_status = NULL" in snippet


def test_backfill_runs_even_for_existing_players():
    assert "await backfill_listing_card_images(conn, urls, GAME_YEAR)" in SOURCE
    assert SOURCE.index("await backfill_listing_card_images(conn, urls, GAME_YEAR)") < SOURCE.index("targets, skipped_existing = _build_targets(")
