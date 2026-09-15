from app.routers.v2.release_price_rises import _build_playbook, _build_trends, _sort_key


def _row(**overrides):
    base = {
        "card_id": 1,
        "name": "Launch Card",
        "position": "ST",
        "rarity": "gold_rare",
        "pace": 90,
        "nation": "England",
        "nation_image_url": None,
        "league": "Premier League",
        "league_image_url": None,
        "early_price": 1000,
        "full_price": 2500,
        "abs_change": 1500,
        "pct_change": 150.0,
        "net_profit_after_tax": 1375,
        "net_roi_after_tax": 137.5,
        "price_band": {"key": "1k_5k", "label": "1k-5k"},
    }
    base.update(overrides)
    return base


def test_build_trends_includes_price_bands_and_after_tax_profit():
    rows = [
        _row(card_id=1, name="Fast ST 1", position="ST", pct_change=100.0, net_profit_after_tax=900),
        _row(card_id=2, name="Fast ST 2", position="ST", pct_change=120.0, net_profit_after_tax=1100),
        _row(card_id=3, name="Fast ST 3", position="ST", pct_change=140.0, net_profit_after_tax=1300),
        _row(card_id=4, name="Slow CB 1", position="CB", pace=60, pct_change=20.0, net_profit_after_tax=100, price_band={"key": "5k_25k", "label": "5k-25k"}),
        _row(card_id=5, name="Slow CB 2", position="CB", pace=61, pct_change=30.0, net_profit_after_tax=150, price_band={"key": "5k_25k", "label": "5k-25k"}),
        _row(card_id=6, name="Slow CB 3", position="CB", pace=62, pct_change=40.0, net_profit_after_tax=200, price_band={"key": "5k_25k", "label": "5k-25k"}),
    ]

    trends = _build_trends(rows)

    assert trends["by_position"][0]["position"] == "ST"
    assert trends["by_position"][0]["median_profit_after_tax"] == 1100
    assert trends["by_price_band"][0]["price_band"] == "1k-5k"
    assert trends["by_price_band"][0]["median_pct_change"] == 120.0


def test_build_playbook_turns_history_into_actionable_profile_copy():
    rows = [
        _row(card_id=1, name="Fast ST 1", position="ST", pct_change=100.0, net_profit_after_tax=900),
        _row(card_id=2, name="Fast ST 2", position="ST", pct_change=120.0, net_profit_after_tax=1100),
        _row(card_id=3, name="Fast ST 3", position="ST", pct_change=140.0, net_profit_after_tax=1300),
    ]
    trends = _build_trends(rows)

    playbook = _build_playbook(rows, trends)

    assert playbook["headline"].startswith("Best FC27 watch profile")
    assert "guaranteed buy signal" in playbook["summary"]
    assert any(card["label"] == "Best coin return" for card in playbook["cards"])


def test_sort_key_keeps_missing_values_last_in_both_directions():
    rows = [{"pct_change": None}, {"pct_change": 5}, {"pct_change": 12}]

    assert [r["pct_change"] for r in sorted(rows, key=lambda r: _sort_key(r, "pct_change", "desc"))] == [12, 5, None]
    assert [r["pct_change"] for r in sorted(rows, key=lambda r: _sort_key(r, "pct_change", "asc"))] == [5, 12, None]
