"""Regression checks for low-disk bulk-price worker startup."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "futgg_bulk_price_sync.py").read_text()


def test_seed_uses_per_card_index_probes_without_global_sort():
    start = SOURCE.index("async def seed_last_written(")
    end = SOURCE.index("def select_rows_to_write(", start)
    body = SOURCE[start:end]
    assert "JOIN LATERAL (" in body
    assert "ORDER BY h.captured_at DESC" in body
    assert "LIMIT 1" in body
    assert "h.game_year = $2" in body
    assert "h.platform = $1" in body
    assert "SELECT DISTINCT ON" not in body


def test_startup_handles_postgres_disk_errors_without_restart_loop():
    start = SOURCE.index("async def run_forever(")
    body = SOURCE[start:]
    assert "try:\n            seeded = await seed_last_written(pool)" in body
    assert "except asyncpg.PostgresError:" in body
