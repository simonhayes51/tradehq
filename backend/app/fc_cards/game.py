from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from app.fc_cards.cards import Card, random_decider

if TYPE_CHECKING:
    import asyncpg


@dataclass(frozen=True)
class PackResult:
    card: Card
    streak: int
    copies: int
    is_duplicate: bool


async def ensure_user(conn: "asyncpg.Connection", discord_user_id: str) -> None:
    await conn.execute(
        """
        INSERT INTO fc_card_users (discord_user_id)
        VALUES ($1)
        ON CONFLICT (discord_user_id) DO UPDATE SET updated_at = NOW()
        """,
        str(discord_user_id),
    )


async def get_user(conn: "asyncpg.Connection", discord_user_id: str) -> "asyncpg.Record":
    await ensure_user(conn, discord_user_id)
    row = await conn.fetchrow(
        "SELECT * FROM fc_card_users WHERE discord_user_id = $1",
        str(discord_user_id),
    )
    assert row is not None
    return row


def _today_utc() -> datetime.date:
    return datetime.now(timezone.utc).date()


def current_week_key() -> tuple[int, int]:
    today = _today_utc()
    iso = today.isocalendar()
    return iso.year, iso.week


async def owned_copies(conn: "asyncpg.Connection", discord_user_id: str, card_id: int) -> int:
    value = await conn.fetchval(
        "SELECT copies FROM fc_card_inventory WHERE discord_user_id = $1 AND card_id = $2",
        str(discord_user_id),
        int(card_id),
    )
    return int(value or 0)


async def grant_card(conn: "asyncpg.Connection", discord_user_id: str, card_id: int, quantity: int = 1) -> int:
    await ensure_user(conn, discord_user_id)
    row = await conn.fetchrow(
        """
        INSERT INTO fc_card_inventory (discord_user_id, card_id, copies)
        VALUES ($1, $2, $3)
        ON CONFLICT (discord_user_id, card_id)
        DO UPDATE SET copies = fc_card_inventory.copies + EXCLUDED.copies,
                      updated_at = NOW()
        RETURNING copies
        """,
        str(discord_user_id),
        int(card_id),
        int(quantity),
    )
    return int(row["copies"])


async def remove_card(conn: "asyncpg.Connection", discord_user_id: str, card_id: int, quantity: int = 1) -> int:
    row = await conn.fetchrow(
        """
        UPDATE fc_card_inventory
        SET copies = copies - $3,
            updated_at = NOW()
        WHERE discord_user_id = $1
          AND card_id = $2
          AND copies >= $3
        RETURNING copies
        """,
        str(discord_user_id),
        int(card_id),
        int(quantity),
    )
    if row is None:
        raise ValueError("You do not own that card any more.")

    copies = int(row["copies"])
    if copies == 0:
        await conn.execute(
            "DELETE FROM fc_card_inventory WHERE discord_user_id = $1 AND card_id = $2 AND copies = 0",
            str(discord_user_id),
            int(card_id),
        )
    return copies


async def open_pack(conn: "asyncpg.Connection", discord_user_id: str, card: Card) -> PackResult:
    async with conn.transaction():
        user = await get_user(conn, discord_user_id)
        if user["last_pack_date"] == _today_utc():
            raise ValueError("Daily pack already opened.")

        last_date = user["last_pack_date"]
        current_streak = int(user["pack_streak"] or 0)
        streak = current_streak + 1 if last_date and last_date.toordinal() == _today_utc().toordinal() - 1 else 1
        before = await owned_copies(conn, discord_user_id, card.card_id)
        copies = await grant_card(conn, discord_user_id, card.card_id)

        await conn.execute(
            """
            UPDATE fc_card_users
            SET last_pack_at = NOW(),
                last_pack_date = CURRENT_DATE,
                pack_streak = $2,
                updated_at = NOW()
            WHERE discord_user_id = $1
            """,
            str(discord_user_id),
            streak,
        )
        await conn.execute(
            """
            INSERT INTO fc_card_pack_openings (discord_user_id, card_id, streak)
            VALUES ($1, $2, $3)
            """,
            str(discord_user_id),
            card.card_id,
            streak,
        )
        return PackResult(card=card, streak=streak, copies=copies, is_duplicate=before > 0)


async def open_weekly_pack(conn: "asyncpg.Connection", discord_user_id: str, card: Card) -> PackResult:
    async with conn.transaction():
        user = await get_user(conn, discord_user_id)
        year, week = current_week_key()
        if user["last_weekly_pack_year"] == year and user["last_weekly_pack_week"] == week:
            raise ValueError("Weekly pack already opened.")

        before = await owned_copies(conn, discord_user_id, card.card_id)
        copies = await grant_card(conn, discord_user_id, card.card_id)

        await conn.execute(
            """
            UPDATE fc_card_users
            SET last_weekly_pack_at = NOW(),
                last_weekly_pack_year = $2,
                last_weekly_pack_week = $3,
                updated_at = NOW()
            WHERE discord_user_id = $1
            """,
            str(discord_user_id),
            year,
            week,
        )
        await conn.execute(
            """
            INSERT INTO fc_card_pack_openings (discord_user_id, card_id, source)
            VALUES ($1, $2, 'weekly')
            """,
            str(discord_user_id),
            card.card_id,
        )
        return PackResult(card=card, streak=0, copies=copies, is_duplicate=before > 0)


async def create_trade(
    conn: "asyncpg.Connection",
    guild_id: Optional[str],
    channel_id: Optional[str],
    proposer_id: str,
    recipient_id: str,
    proposer_card_id: int,
    recipient_card_id: Optional[int],
) -> "asyncpg.Record":
    if proposer_id == recipient_id:
        raise ValueError("You cannot trade with yourself.")
    await ensure_user(conn, proposer_id)
    await ensure_user(conn, recipient_id)
    if await owned_copies(conn, proposer_id, proposer_card_id) < 2:
        raise ValueError("Trades are duplicates-only. You need at least 2 copies of the card you are offering.")
    if recipient_card_id is not None and await owned_copies(conn, recipient_id, recipient_card_id) < 2:
        raise ValueError("The other player needs a duplicate copy of the requested card.")

    return await conn.fetchrow(
        """
        INSERT INTO fc_card_trades (
            guild_id, channel_id, proposer_id, recipient_id, proposer_card_id, recipient_card_id
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        guild_id,
        channel_id,
        proposer_id,
        recipient_id,
        proposer_card_id,
        recipient_card_id,
    )


async def accept_trade(conn: "asyncpg.Connection", trade_id: str, recipient_id: str) -> "asyncpg.Record":
    async with conn.transaction():
        trade = await conn.fetchrow("SELECT * FROM fc_card_trades WHERE id = $1::uuid FOR UPDATE", trade_id)
        if not trade or trade["status"] != "pending" or str(trade["recipient_id"]) != str(recipient_id):
            raise ValueError("Trade not found for you, or it is no longer pending.")
        if trade["expires_at"] < datetime.now(timezone.utc):
            await conn.execute("UPDATE fc_card_trades SET status = 'expired' WHERE id = $1::uuid", trade_id)
            raise ValueError("That trade has expired.")
        if await owned_copies(conn, trade["proposer_id"], trade["proposer_card_id"]) < 2:
            raise ValueError("The proposer no longer has a duplicate copy to trade.")
        if trade["recipient_card_id"] is not None and await owned_copies(conn, recipient_id, trade["recipient_card_id"]) < 2:
            raise ValueError("You no longer have a duplicate copy of the requested card.")

        await remove_card(conn, trade["proposer_id"], trade["proposer_card_id"])
        await grant_card(conn, recipient_id, trade["proposer_card_id"])
        if trade["recipient_card_id"] is not None:
            await remove_card(conn, recipient_id, trade["recipient_card_id"])
            await grant_card(conn, trade["proposer_id"], trade["recipient_card_id"])

        return await conn.fetchrow(
            "UPDATE fc_card_trades SET status = 'accepted', completed_at = NOW() WHERE id = $1::uuid RETURNING *",
            trade_id,
        )


async def create_battle(
    conn: "asyncpg.Connection",
    guild_id: Optional[str],
    channel_id: Optional[str],
    challenger_id: str,
    opponent_id: str,
    challenger_card_id: int,
    challenger_stat: str,
) -> "asyncpg.Record":
    if challenger_id == opponent_id:
        raise ValueError("You cannot clash yourself.")
    await ensure_user(conn, challenger_id)
    await ensure_user(conn, opponent_id)
    if await owned_copies(conn, challenger_id, challenger_card_id) < 1:
        raise ValueError("You do not own that card.")

    return await conn.fetchrow(
        """
        INSERT INTO fc_card_battles (
            guild_id, channel_id, challenger_id, opponent_id, challenger_card_id, challenger_stat
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        guild_id,
        channel_id,
        challenger_id,
        opponent_id,
        challenger_card_id,
        challenger_stat,
    )


def resolve_battle(challenger: Card, opponent: Card, challenger_stat: str, opponent_stat: str) -> tuple[str, list[dict]]:
    if challenger_stat == opponent_stat:
        raise ValueError("Both players cannot pick the same stat.")

    decider = random_decider([challenger_stat, opponent_stat])
    rounds = []
    challenger_wins = 0
    opponent_wins = 0
    for source, stat in (("challenger", challenger_stat), ("opponent", opponent_stat), ("random", decider)):
        challenger_value = challenger.stat(stat)
        opponent_value = opponent.stat(stat)
        if challenger_value == opponent_value:
            winner = "draw"
        elif challenger_value > opponent_value:
            winner = "challenger"
            challenger_wins += 1
        else:
            winner = "opponent"
            opponent_wins += 1
        rounds.append(
            {
                "source": source,
                "stat": stat,
                "challenger": challenger_value,
                "opponent": opponent_value,
                "winner": winner,
            }
        )

    if challenger_wins == opponent_wins:
        if challenger.rating == opponent.rating:
            winner = "challenger" if challenger.card_id >= opponent.card_id else "opponent"
        else:
            winner = "challenger" if challenger.rating > opponent.rating else "opponent"
    else:
        winner = "challenger" if challenger_wins > opponent_wins else "opponent"
    return winner, rounds


async def accept_battle(
    conn: "asyncpg.Connection",
    battle_id: str,
    opponent_id: str,
    opponent_card: Card,
    opponent_stat: str,
    challenger_card: Card,
) -> "asyncpg.Record":
    async with conn.transaction():
        battle = await conn.fetchrow("SELECT * FROM fc_card_battles WHERE id = $1::uuid FOR UPDATE", battle_id)
        if not battle or battle["status"] != "pending" or str(battle["opponent_id"]) != str(opponent_id):
            raise ValueError("Clash not found for you, or it is no longer pending.")
        if battle["expires_at"] < datetime.now(timezone.utc):
            await conn.execute("UPDATE fc_card_battles SET status = 'expired' WHERE id = $1::uuid", battle_id)
            raise ValueError("That clash has expired.")
        if await owned_copies(conn, battle["challenger_id"], battle["challenger_card_id"]) < 1:
            raise ValueError("The challenger no longer owns their staked card.")
        if await owned_copies(conn, opponent_id, opponent_card.card_id) < 1:
            raise ValueError("You do not own that card.")
        if opponent_stat == battle["challenger_stat"]:
            raise ValueError("Pick a different stat. Both players cannot choose the same stat.")

        winner_side, rounds = resolve_battle(
            challenger_card,
            opponent_card,
            battle["challenger_stat"],
            opponent_stat,
        )
        winner_id = battle["challenger_id"] if winner_side == "challenger" else opponent_id
        loser_id = opponent_id if winner_side == "challenger" else battle["challenger_id"]
        losing_card_id = opponent_card.card_id if winner_side == "challenger" else battle["challenger_card_id"]

        await remove_card(conn, loser_id, losing_card_id)
        await grant_card(conn, winner_id, losing_card_id)

        await conn.execute(
            """
            UPDATE fc_card_users
            SET wins = wins + 1,
                current_streak = current_streak + 1,
                best_streak = GREATEST(best_streak, current_streak + 1),
                coins = coins + 50,
                updated_at = NOW()
            WHERE discord_user_id = $1
            """,
            winner_id,
        )
        await conn.execute(
            """
            UPDATE fc_card_users
            SET losses = losses + 1,
                current_streak = 0,
                coins = GREATEST(coins - 15, 0),
                updated_at = NOW()
            WHERE discord_user_id = $1
            """,
            loser_id,
        )

        return await conn.fetchrow(
            """
            UPDATE fc_card_battles
            SET status = 'completed',
                opponent_card_id = $2,
                opponent_stat = $3,
                random_stat = $4,
                rounds = $5::jsonb,
                winner_id = $6,
                loser_id = $7,
                completed_at = NOW()
            WHERE id = $1::uuid
            RETURNING *
            """,
            battle_id,
            opponent_card.card_id,
            opponent_stat,
            rounds[-1]["stat"],
            json.dumps(rounds),
            winner_id,
            loser_id,
        )

