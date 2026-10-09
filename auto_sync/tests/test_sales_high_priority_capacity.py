"""Check the sales collector reserves enough slots for high-priority cards."""
import ast
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "futgg_sales_sync.py").read_text()


def test_high_priority_floor_and_safe_cap():
    ast.parse(SOURCE)
    assert '"HIGH": 0.55' in SOURCE
    assert '"CRITICAL": 0.25' in SOURCE
    assert '"DORMANT": 0.025' in SOURCE
    assert 'SELECT_LIMIT_HARD_CAP = env_int("FUTGG_SALES_SELECT_LIMIT_HARD_CAP", 90' in SOURCE


def test_due_and_backfill_guards_remain():
    assert "(next_sales_due_at IS NULL OR next_sales_due_at <= NOW())" in SOURCE
    assert "(sales_first_read_at IS NOT NULL)" in SOURCE
