from app.fc_cards.cards import Card, normalize_stat, stat_label
from app.fc_cards.game import resolve_battle, shard_reward
import pytest


def make_card(card_id: int, rating: int, **stats: int) -> Card:
    defaults = {
        "pace": 70,
        "shooting": 70,
        "passing": 70,
        "dribbling": 70,
        "defending": 70,
        "physicality": 70,
    }
    defaults.update(stats)
    return Card(
        card_id=card_id,
        name=f"Card {card_id}",
        rating=rating,
        position="ST",
        version="Gold",
        rarity="Gold",
        image_url=None,
        **defaults,
    )


def test_normalize_stat_accepts_short_and_long_names():
    assert normalize_stat("PAC") == "pace"
    assert normalize_stat("shooting") == "shooting"
    assert normalize_stat("DEF") == "defending"
    assert stat_label("physicality") == "PHY"


def test_resolve_battle_uses_player_picks_and_random_decider(monkeypatch):
    challenger = make_card(1, 84, pace=91, physicality=65, dribbling=88)
    opponent = make_card(2, 83, pace=77, physicality=86, dribbling=80)

    monkeypatch.setattr("app.fc_cards.game.random_decider", lambda excluded: "dribbling")

    winner, rounds = resolve_battle(challenger, opponent, "pace", "physicality")

    assert winner == "challenger"
    assert [r["stat"] for r in rounds] == ["pace", "physicality", "dribbling"]
    assert [r["winner"] for r in rounds] == ["challenger", "opponent", "challenger"]


def test_resolve_battle_rating_breaks_drawn_rounds(monkeypatch):
    challenger = make_card(1, 82, pace=90, physicality=70, dribbling=70)
    opponent = make_card(2, 85, pace=80, physicality=80, dribbling=70)

    monkeypatch.setattr("app.fc_cards.game.random_decider", lambda excluded: "dribbling")

    winner, rounds = resolve_battle(challenger, opponent, "pace", "physicality")

    assert [r["winner"] for r in rounds] == ["challenger", "opponent", "draw"]
    assert winner == "opponent"


def test_resolve_battle_rejects_same_stat():
    challenger = make_card(1, 84, pace=91)
    opponent = make_card(2, 83, pace=77)

    with pytest.raises(ValueError, match="same stat"):
        resolve_battle(challenger, opponent, "pace", "pace")


def test_shard_reward_scales_with_rarity():
    assert shard_reward(91) == 40
    assert shard_reward(88) == 25
    assert shard_reward(86) == 15
    assert shard_reward(84) == 8
    assert shard_reward(82) == 4
    assert shard_reward(75) == 2
    assert shard_reward(91) > shard_reward(88) > shard_reward(86) > shard_reward(84) > shard_reward(82) > shard_reward(75)

