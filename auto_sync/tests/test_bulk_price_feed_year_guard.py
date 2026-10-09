"""Bulk price feeds must match the configured FUT game year and R2 asset."""
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "futgg_bulk_price_sync.py"


def _validator():
    tree = ast.parse(SOURCE.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_is_expected_price_feed_url")
    module = ast.Module(body=[fn], type_ignores=[])
    namespace = {"urlparse": __import__("urllib.parse", fromlist=["urlparse"]).urlparse, "GAME_YEAR": 27}
    exec(compile(ast.fix_missing_locations(module), "<validator>", "exec"), namespace)
    return namespace["_is_expected_price_feed_url"]


def test_only_fc27_r2_json_price_feed_urls_are_accepted():
    valid = _validator()
    assert valid("https://r2.fut.gg/27/player-prices-index.v1.abc123.json", "player-prices-index")
    assert valid("https://r2.fut.gg/27/player-prices-ps5-dyn.v1.abc123.json", "player-prices-ps5-dyn")
    assert not valid("https://r2.fut.gg/26/player-prices-index.v1.abc123.json", "player-prices-index")
    assert not valid("https://r2.fut.gg/26/player-prices-ps5-dyn.v1.abc123.json", "player-prices-ps5-dyn")
    assert not valid("https://evil.example/27/player-prices-index.v1.abc123.json", "player-prices-index")
    assert not valid("https://r2.fut.gg/27/player-prices-index.v1.abc123.json/extra", "player-prices-index")


def test_ingestion_checks_urls_before_seeding():
    source = SOURCE.read_text()
    assert source.index('if not (\n        _is_expected_price_feed_url(index_url') < source.index('seeded_from_feed = 0')
    assert 'REJECTED cross-year or unexpected bulk price asset' in source
