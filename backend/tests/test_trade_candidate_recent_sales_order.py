"""The trade scan must not prefer old high-volume sales over current sales."""
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "app"
ROUTER = (BACKEND / "routers/v2/futgg_market.py").read_text()
DIAGNOSTIC = (BACKEND / "why_no_trades.py").read_text()


def test_dashboard_and_diagnostic_use_identical_recent_sales_candidate_order():
    expected = "latest_sale_at DESC NULLS LAST, sales_count DESC NULLS LAST"
    assert f'CANDIDATE_SCAN_ORDER_BY = "{expected}"' in ROUTER
    assert f'DASHBOARD_ORDER_BY = "{expected}"' in DIAGNOSTIC


def test_trade_scan_still_has_bounded_candidate_pool():
    assert "CANDIDATE_SCAN_LIMIT = 500" in ROUTER
