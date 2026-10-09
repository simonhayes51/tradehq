"""Keep SalesSync due prioritisation and reporting scoped to FC27."""
import ast
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "futgg_sales_sync.py").read_text()


def test_due_queue_favours_actionable_tiers_and_respects_due_dates():
    ast.parse(SOURCE)
    block = SOURCE[SOURCE.index("async def select_due("):SOURCE.index("async def report_coverage(")]
    assert block.count("next_sales_due_at IS NULL OR next_sales_due_at <= NOW()") == 2
    assert "WHEN 'CRITICAL' THEN 0" in block
    assert "WHEN 'HIGH' THEN 1" in block
    assert "sales_first_read_at IS NOT NULL" in block


def test_coverage_only_counts_current_game():
    block = SOURCE[SOURCE.index("async def report_coverage("):SOURCE.index("#: Worker name", SOURCE.index("async def report_coverage("))]
    assert "AND p.game_year = $1" in block
    assert "int(GAME_YEAR)" in block
