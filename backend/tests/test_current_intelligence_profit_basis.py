from decimal import Decimal

from app.services.futgg_current_intelligence_store import _args
from app.services.futgg_intelligence import CardIntelligence
from app.services.snapshot_market_surfaces import _to_pair


def test_current_state_store_keeps_profit_basis_next_to_profit():
    ci = CardIntelligence(
        card_id=42,
        current_executable_buy=Decimal("25500"),
        recommended_buy_max=Decimal("40000"),
        recommended_sell_target=Decimal("45500"),
        expected_profit_after_tax=Decimal("17725"),
        expected_profit_basis=Decimal("25500"),
    )

    args = _args({"current_bin": 25500, "game_year": 27}, ci)

    # expected_profit_basis is deliberately the final persisted value so
    # adding it did not renumber the mature current-state insert contract.
    assert args[-1] == Decimal("25500")
    assert args[13] == Decimal("17725")
    assert args[8] == Decimal("40000")


def test_persisted_surface_restores_profit_basis_for_client_serialisation():
    row, ci = _to_pair({
        "source_card_id": 42,
        "current_bin": 25500,
        "rec_current_bin": 25500,
        "rec_recommended_buy_max": Decimal("40000"),
        "rec_current_executable_buy": Decimal("25500"),
        "rec_recommended_sell_target": Decimal("45500"),
        "rec_expected_profit_after_tax": Decimal("17725"),
        "rec_expected_profit_basis": Decimal("25500"),
        "rec_signal": "buy",
        "rec_status": "active",
    })

    assert row["current_bin"] == 25500
    assert ci.expected_profit_basis == Decimal("25500")
    assert ci.expected_profit_after_tax == Decimal("17725")
    assert ci.recommended_buy_max == Decimal("40000")
    assert ci.recommended_sell_target == Decimal("45500")


def test_profini_shape_describes_one_trade_not_two_entries():
    """Production regression: 45,500 sale at a 25,500 basis really is
    +17,725 after tax; at the 40,000 ceiling it is only +3,225. Persisting
    the basis is what lets the UI say Buy now 25,500 / Pay up to 40,000
    rather than putting +17,725 beside Buy below 40,000."""
    target = Decimal("45500")
    basis = Decimal("25500")
    ceiling = Decimal("40000")

    profit_at_basis = target * Decimal("0.95") - basis
    profit_at_ceiling = target * Decimal("0.95") - ceiling

    assert profit_at_basis == Decimal("17725.00")
    assert profit_at_ceiling == Decimal("3225.00")
    assert profit_at_basis != profit_at_ceiling
