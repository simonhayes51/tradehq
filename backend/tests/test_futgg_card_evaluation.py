from datetime import datetime, timedelta, timezone

import pytest

from app.services.futgg_card_evaluation import evaluate_fresh_card


AS_OF = datetime.now(timezone.utc)


class _Provider:
    async def get_player(self, card_id: int):
        return {
            "source_card_id": card_id,
            "source_player_id": 100,
            "source_slug": "test-card",
            "game_year": 27,
            "name": "Test Card",
            "rating": 88,
            "primary_position": "ST",
            "is_tradeable": True,
            "last_price_status": "success",
            "current_bin": 50000,
            "bin_captured_at": AS_OF - timedelta(minutes=5),
            "price_tier": "gold_rare",
            "sales_count": 30,
            "sales_median": 52000,
            "sales_trimmed_mean": 52000,
            "latest_sale_price": 52000,
            "latest_sale_at": AS_OF - timedelta(minutes=3),
            "sales_dispersion_ratio": 0.02,
        }

    async def get_recent_sales(self, card_id: int):
        return [
            {
                "sold_price": 52000,
                "approximate_sold_at": AS_OF - timedelta(minutes=3),
            }
        ]

    async def get_bin_observations_by_ids(self, card_ids):
        return {}


@pytest.mark.asyncio
async def test_evaluate_fresh_card_fails_soft_when_engine_raises(monkeypatch):
    import app.services.futgg_card_evaluation as module

    def _broken_engine(*args, **kwargs):
        raise RuntimeError("unexpected production data shape")

    monkeypatch.setattr(module, "evaluate_card", _broken_engine)

    result = await evaluate_fresh_card(_Provider(), 123, requested_by="player_detail")

    assert result is not None
    assert result.row["source_card_id"] == 123
    assert result.intelligence.signal == "insufficient_data"
    assert result.intelligence.reason_codes == ["evaluation_failed"]
