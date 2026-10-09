"""Protect signed sales fetch requests from navigation-only global headers.

Railway diagnostic 2026-10-09: Chromium aborted POST
/api/fut/price-access/sign/ with net::ERR_INVALID_ARGUMENT before HTTP
status, while its request inherited sec-fetch-mode=navigate and
sec-fetch-dest=document from context-level extra_http_headers.
"""
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "futgg_sales_sync.py"


def test_signed_sales_browser_leaves_fetch_metadata_to_chromium():
    code = SOURCE.read_text()
    launch = code.split("async def _launch_browser_resources(", 1)[1]
    launch = launch.split("async def run_forever(", 1)[0]
    assert "extra_http_headers=" not in launch
    assert "user_agent=" in launch
    assert "locale='en-GB'" in launch


def test_sales_stealth_context_is_preserved():
    code = SOURCE.read_text()
    assert "Stealth().use_async(async_playwright())" in code
