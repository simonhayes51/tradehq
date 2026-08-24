import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.trade_summary_data as trade_summary_data
from app.services.trade_summary_token import make_render_token, verify_render_token


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Token: signs (user_id, period) together, same pattern as trade_share_token.
# ---------------------------------------------------------------------------


def test_token_round_trips():
    token = make_render_token("42", "week")
    assert verify_render_token("week", token) == "42"


def test_token_rejects_mismatched_period():
    token = make_render_token("42", "week")
    assert verify_render_token("today", token) is None


def test_token_rejects_garbage():
    assert verify_render_token("week", "not-a-real-token") is None


def test_token_rejects_empty():
    assert verify_render_token("week", "") is None


# ---------------------------------------------------------------------------
# Summary aggregation - scoped by user_id from the query itself, never a
# caller-supplied filter.
# ---------------------------------------------------------------------------


class _FakePool:
    def __init__(self, totals_row, best_sale_row):
        self._totals_row = totals_row
        self._best_sale_row = best_sale_row
        self.queries = []

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        if "COUNT(*) FILTER" in query:
            return self._totals_row
        return self._best_sale_row


def test_get_owned_portfolio_summary_rejects_unknown_period():
    with pytest.raises(ValueError):
        run(trade_summary_data.get_owned_portfolio_summary("42", "yesterday"))


def test_get_owned_portfolio_summary_computes_win_rate(monkeypatch):
    pool = _FakePool(
        totals_row={"wins": 3, "losses": 1, "closed_trades": 4, "total_profit": 15000, "total_ea_tax": 750},
        best_sale_row={"player": "Mbappe", "version": "TOTS", "buy": 100000, "sell": 150000, "profit": 45000, "card_id": 1},
    )
    monkeypatch.setattr(trade_summary_data, "get_core_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(trade_summary_data, "card_art_for", AsyncMock(return_value=None))

    summary = run(trade_summary_data.get_owned_portfolio_summary("42", "week"))
    assert summary["win_rate"] == 75.0
    assert summary["closed_trades"] == 4
    assert summary["best_sale"]["player"] == "Mbappe"
    # Every query is scoped by user_id=$1 - never trusts an unfiltered read.
    assert all(args[0] == "42" for _, args in pool.queries)


def test_get_owned_portfolio_summary_handles_zero_trades(monkeypatch):
    pool = _FakePool(
        totals_row={"wins": 0, "losses": 0, "closed_trades": 0, "total_profit": 0, "total_ea_tax": 0},
        best_sale_row=None,
    )
    monkeypatch.setattr(trade_summary_data, "get_core_pool", AsyncMock(return_value=pool))

    summary = run(trade_summary_data.get_owned_portfolio_summary("42", "today"))
    assert summary["win_rate"] == 0.0
    assert summary["best_sale"] is None


def test_get_owned_portfolio_summary_all_time_has_no_date_filter(monkeypatch):
    pool = _FakePool(
        totals_row={"wins": 1, "losses": 0, "closed_trades": 1, "total_profit": 500, "total_ea_tax": 25},
        best_sale_row=None,
    )
    monkeypatch.setattr(trade_summary_data, "get_core_pool", AsyncMock(return_value=pool))

    run(trade_summary_data.get_owned_portfolio_summary("42", "all"))
    assert all("sold_at >=" not in query for query, _ in pool.queries)


# ---------------------------------------------------------------------------
# Caption text
# ---------------------------------------------------------------------------


def test_summary_caption_mentions_wins_losses_and_profit():
    summary = {
        "period_label": "This Week", "wins": 5, "losses": 2, "total_profit": 42000,
        "best_sale": {"player": "Haaland", "profit": 12000},
    }
    caption = trade_summary_data.build_summary_share_caption(summary)
    assert "5W-2L" in caption
    assert "42,000" in caption
    assert "Haaland" in caption


def test_summary_caption_handles_no_best_sale():
    summary = {"period_label": "Today", "wins": 0, "losses": 0, "total_profit": 0, "best_sale": None}
    caption = trade_summary_data.build_summary_share_caption(summary)
    assert "Best sale" not in caption
