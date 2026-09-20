from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from app.fc_cards.cards import Card, STAT_KEYS, active_game_year, normalize_stat, pull_weighted_card, search_card, search_cards, stat_label
from app.fc_cards.db import get_card_pool, get_game_pool
from app.fc_cards.game import (
    accept_battle,
    accept_trade,
    create_battle,
    create_trade,
    get_user,
    open_pack,
    open_pack_many,
    open_weekly_pack,
    open_weekly_pack_many,
    owned_copies,
)
from app.fc_cards.schema import ensure_schema

log = logging.getLogger("fc-card-clash")


def _token() -> str:
    token = os.getenv("FC_CARDS_BOT_TOKEN") or os.getenv("DISCORD_GAME_BOT_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set FC_CARDS_BOT_TOKEN, DISCORD_GAME_BOT_TOKEN or DISCORD_BOT_TOKEN")
    return token


def _guild_object() -> Optional[discord.Object]:
    guild_id = os.getenv("FC_CARDS_DISCORD_GUILD_ID") or os.getenv("DISCORD_SERVER_ID")
    return discord.Object(id=int(guild_id)) if guild_id else None


def _int_env(name: str, default: int, *, minimum: int = 1, maximum: int = 10) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _short_id(value: object) -> str:
    return str(value).split("-")[0]


def _card_title(card: Card) -> str:
    return f"{card.rating} {card.name} ({card.position})"


def _card_line(card: Card) -> str:
    return f"**{_card_title(card)}** - {card.version} - `{card.card_id}`"


def _card_embed(title: str, card: Card, *, description: Optional[str] = None, color: int = 0xEABD45) -> discord.Embed:
    embed = discord.Embed(title=title, description=description or _card_line(card), color=color)
    embed.add_field(name="Card", value=_card_line(card), inline=False)
    embed.add_field(name="Stats", value="  ".join(f"**{stat_label(s)}** {card.stat(s)}" for s in STAT_KEYS), inline=False)
    if card.club or card.league or card.nation:
        embed.add_field(
            name="Details",
            value=" / ".join(v for v in (card.club, card.league, card.nation) if v)[:1024],
            inline=False,
        )
    if card.image_url:
        embed.set_image(url=card.image_url)
    embed.set_footer(text="FC Card Clash")
    return embed


def _pack_color(card: Card) -> int:
    if card.rating >= 90:
        return 0x9B59B6
    if card.rating >= 88:
        return 0xF1C40F
    if card.rating >= 86:
        return 0xE67E22
    if card.rating >= 84:
        return 0x3498DB
    return 0x2ECC71


def _pack_tier(card: Card) -> str:
    if card.rating >= 90:
        return "WALKOUT"
    if card.rating >= 88:
        return "BIG PULL"
    if card.rating >= 86:
        return "BOARD"
    return "PACK OPENED"


def _next_daily_unlock() -> str:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).date()
    unlock = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)
    remaining = unlock - now
    hours, rem = divmod(int(remaining.total_seconds()), 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m"


def _next_weekly_unlock() -> str:
    now = datetime.now(timezone.utc)
    days_until_monday = (7 - now.weekday()) % 7 or 7
    next_monday = (now + timedelta(days=days_until_monday)).date()
    unlock = datetime(next_monday.year, next_monday.month, next_monday.day, tzinfo=timezone.utc)
    remaining = unlock - now
    days = remaining.days
    hours = remaining.seconds // 3600
    return f"{days}d {hours}h"


def _pack_embed(pack_name: str, result, user_mention: str, *, boosted: bool = False) -> discord.Embed:
    card = result.card
    duplicate = "Duplicate" if result.is_duplicate else "New card"
    tier = _pack_tier(card)
    title = f"{tier}: {pack_name}" if tier != "PACK OPENED" else f"{pack_name} Opened"
    embed = _card_embed(title, card, color=_pack_color(card))
    embed.description = (
        f"{user_mention} pulled {_card_line(card)}\n"
        f"**{duplicate}** - now owned: **x{result.copies}**"
    )
    if result.streak:
        embed.add_field(name="Daily streak", value=f"**{result.streak}**", inline=True)
        if result.streak in (3, 7, 30):
            embed.add_field(name="Streak bonus", value="Your odds are boosted while the streak keeps going.", inline=False)
    if boosted:
        embed.add_field(name="Weekly boost", value="Higher chance of 86+ cards.", inline=True)
    return embed


def _pack_results_embed(pack_name: str, results, user_mention: str, *, boosted: bool = False) -> discord.Embed:
    best = max((r.card for r in results), key=lambda card: (card.rating, card.card_id))
    embed = discord.Embed(title=f"{pack_name} Opened", color=_pack_color(best))
    embed.description = f"{user_mention} opened a **{len(results)} card** pack."
    lines = []
    for result in sorted(results, key=lambda r: (r.card.rating, r.card.card_id), reverse=True):
        status = "dupe" if result.is_duplicate else "new"
        lines.append(f"`x{result.copies}` {_card_line(result.card)} - **{status}**")
    embed.add_field(name="Pulls", value="\n".join(lines)[:1024], inline=False)
    streak = next((int(r.streak or 0) for r in results if int(r.streak or 0)), 0)
    if streak:
        embed.add_field(name="Daily streak", value=f"**{streak}**", inline=True)
    if boosted:
        embed.add_field(name="Weekly boost", value="Higher chance of 86+ cards.", inline=True)
    if best.image_url:
        embed.set_image(url=best.image_url)
    embed.set_footer(text="FC Card Clash")
    return embed


async def _pool_total() -> int:
    assert bot.card_pool is not None
    async with bot.card_pool.acquire() as conn:
        value = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM futgg_market_snapshot
            WHERE rating >= 75
              AND game_year = $1
              AND pace IS NOT NULL
              AND shooting IS NOT NULL
              AND passing IS NOT NULL
              AND dribbling IS NOT NULL
              AND defending IS NOT NULL
              AND physicality IS NOT NULL
              AND COALESCE(card_image_stored_url, card_image_url, player_image_url) IS NOT NULL
              AND COALESCE(is_active, TRUE) = TRUE
            """,
            active_game_year(),
        )
    return int(value or 0)


class CardClashBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.game_pool: asyncpg.Pool | None = None
        self.card_pool: asyncpg.Pool | None = None

    async def setup_hook(self) -> None:
        self.game_pool = await get_game_pool()
        self.card_pool = await get_card_pool()
        await ensure_schema(self.game_pool)
        guild = _guild_object()
        if guild:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced FC Card Clash commands to guild %s", guild.id)
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            log.info("Cleared FC Card Clash global commands while guild sync is enabled")
        else:
            await self.tree.sync()
            log.info("Synced FC Card Clash global commands")


bot = CardClashBot()


@bot.tree.interaction_check
async def channel_check(interaction: discord.Interaction) -> bool:
    if interaction.command and interaction.command.name == "clash_channel":
        return True
    if not interaction.guild_id or not bot.game_pool:
        return True

    async with bot.game_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT channel_id
            FROM fc_card_guild_channels
            WHERE guild_id = $1
            ORDER BY created_at ASC
            """,
            str(interaction.guild_id),
        )
    if not rows:
        return True

    allowed = {str(row["channel_id"]) for row in rows}
    if str(interaction.channel_id) in allowed:
        return True

    channel_list = " or ".join(f"<#{channel_id}>" for channel_id in allowed)
    await interaction.response.send_message(
        f"Use FC Card Clash commands in {channel_list}.",
        ephemeral=True,
    )
    return False


async def _find_card(query: str) -> Card:
    assert bot.card_pool is not None
    async with bot.card_pool.acquire() as conn:
        card = await search_card(conn, query)
    if not card:
        raise ValueError("I could not find that Gold+ card.")
    return card


async def _autocomplete_cards(_: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not current or not bot.card_pool:
        return []
    async with bot.card_pool.acquire() as conn:
        cards = await search_cards(conn, current, limit=10)
    return [
        app_commands.Choice(name=f"{card.rating} {card.name} - {card.version} [{card.card_id}]"[:100], value=str(card.card_id))
        for card in cards
    ]


def _choice_name(card: Card, copies: int) -> str:
    return f"x{copies} | {card.rating} {card.name} ({card.position}) - {card.version}"[:100]


async def _autocomplete_owned_cards(
    interaction: discord.Interaction,
    current: str,
    *,
    discord_user_id: Optional[str] = None,
    min_copies: int = 1,
) -> list[app_commands.Choice[str]]:
    if not bot.game_pool or not bot.card_pool:
        return []
    user_id = discord_user_id or str(interaction.user.id)
    q = (current or "").strip()
    async with bot.game_pool.acquire() as game_conn:
        inventory = await game_conn.fetch(
            """
            SELECT card_id, copies
            FROM fc_card_inventory
            WHERE discord_user_id = $1
              AND copies >= $2
            ORDER BY copies DESC, card_id DESC
            LIMIT 500
            """,
            user_id,
            min_copies,
        )
    if not inventory:
        return []

    copies = {int(r["card_id"]): int(r["copies"]) for r in inventory}
    card_ids = list(copies)
    async with bot.card_pool.acquire() as card_conn:
        rows = await card_conn.fetch(
            """
            SELECT source_card_id,
                   COALESCE(NULLIF(BTRIM(display_name), ''), NULLIF(BTRIM(name), ''), 'Card ' || source_card_id::text) AS name,
                   rating, primary_position, rarity,
                   card_image_stored_url, card_image_url, player_image_url,
                   pace, shooting, passing, dribbling, defending, physicality,
                   club, league, nation
            FROM futgg_market_snapshot
            WHERE source_card_id = ANY($1::bigint[])
              AND game_year = $2
              AND (
                $3 = ''
                OR display_name ILIKE '%' || $3 || '%'
                OR name ILIKE '%' || $3 || '%'
                OR source_card_id::text ILIKE '%' || $3 || '%'
              )
            ORDER BY rating DESC, source_card_id DESC
            LIMIT 25
            """,
            card_ids,
            active_game_year(),
            q,
        )
    cards = [Card.from_row(row) for row in rows]
    return [app_commands.Choice(name=_choice_name(card, copies[card.card_id]), value=str(card.card_id)) for card in cards]


async def _autocomplete_my_cards(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return await _autocomplete_owned_cards(interaction, current)


async def _autocomplete_my_duplicates(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return await _autocomplete_owned_cards(interaction, current, min_copies=2)


async def _autocomplete_trade_request_cards(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    target = getattr(interaction.namespace, "user", None)
    if not target:
        return []
    return await _autocomplete_owned_cards(interaction, current, discord_user_id=str(target.id), min_copies=2)


async def _autocomplete_pending_trades(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not bot.game_pool:
        return []
    q = (current or "").strip()
    async with bot.game_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, proposer_id, proposer_card_id, recipient_card_id
            FROM fc_card_trades
            WHERE recipient_id = $1
              AND status = 'pending'
              AND expires_at > NOW()
              AND ($2 = '' OR id::text ILIKE '%' || $2 || '%')
            ORDER BY created_at DESC
            LIMIT 10
            """,
            str(interaction.user.id),
            q,
        )
    return [
        app_commands.Choice(name=f"Trade {_short_id(row['id'])}", value=str(row["id"]))
        for row in rows
    ]


async def _autocomplete_pending_battles(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not bot.game_pool:
        return []
    q = (current or "").strip()
    async with bot.game_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, challenger_id
            FROM fc_card_battles
            WHERE opponent_id = $1
              AND status = 'pending'
              AND expires_at > NOW()
              AND ($2 = '' OR id::text ILIKE '%' || $2 || '%')
            ORDER BY created_at DESC
            LIMIT 10
            """,
            str(interaction.user.id),
            q,
        )
    return [
        app_commands.Choice(name=f"Battle {_short_id(row['id'])}", value=str(row["id"]))
        for row in rows
    ]


async def _send_error(interaction: discord.Interaction, message: str) -> None:
    embed = discord.Embed(title="Not quite", description=message, color=0xE74C3C)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_ready() -> None:
    log.info("FC Card Clash logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")


@bot.tree.command(name="help", description="How FC Card Clash works.")
async def help_command(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="FC Card Clash Help", color=0x5865F2)
    embed.description = (
        "**/pack** - open your daily card\n"
        "**/weekly** - open your boosted weekly pack\n"
        "**/lastpull** - show off your latest pull\n"
        "**/progress** - show collection completion\n"
        "**/collection** - view your best cards\n"
        "**/dupes** - show cards you can trade\n"
        "**/trade** - trade duplicate cards\n"
        "**/clash** - stake a card in winner-keeps-both battle\n"
        "**/profile** - view record and collection stats\n"
        "**/leaderboard** - global or server rankings\n\n"
        "**Warning:** clash stakes are real. If you lose, the card leaves your collection."
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clash_channel", description="Set which channels can use FC Card Clash commands.")
@app_commands.describe(
    mode="Add, remove, list or clear allowed channels.",
    channel="Channel to add/remove. Defaults to this channel.",
)
@app_commands.choices(mode=[
    app_commands.Choice(name="Add channel", value="add"),
    app_commands.Choice(name="Remove channel", value="remove"),
    app_commands.Choice(name="List channels", value="list"),
    app_commands.Choice(name="Clear all", value="clear"),
])
async def clash_channel(
    interaction: discord.Interaction,
    mode: str,
    channel: Optional[discord.TextChannel] = None,
) -> None:
    if not interaction.guild_id:
        await interaction.response.send_message("This can only be used inside a server.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need Manage Server permission to change FC Card Clash channels.", ephemeral=True)
        return

    assert bot.game_pool is not None
    guild_id = str(interaction.guild_id)
    target_channel = channel or interaction.channel
    async with bot.game_pool.acquire() as conn:
        if mode == "add":
            if not target_channel:
                await interaction.response.send_message("Pick a channel to add.", ephemeral=True)
                return
            await conn.execute(
                """
                INSERT INTO fc_card_guild_channels (guild_id, channel_id, created_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, channel_id) DO NOTHING
                """,
                guild_id,
                str(target_channel.id),
                str(interaction.user.id),
            )
            await interaction.response.send_message(
                f"FC Card Clash commands are now allowed in {target_channel.mention}.",
                ephemeral=True,
            )
            return

        if mode == "remove":
            if not target_channel:
                await interaction.response.send_message("Pick a channel to remove.", ephemeral=True)
                return
            await conn.execute(
                "DELETE FROM fc_card_guild_channels WHERE guild_id = $1 AND channel_id = $2",
                guild_id,
                str(target_channel.id),
            )
            await interaction.response.send_message(
                f"Removed {target_channel.mention} from FC Card Clash allowed channels.",
                ephemeral=True,
            )
            return

        if mode == "clear":
            await conn.execute("DELETE FROM fc_card_guild_channels WHERE guild_id = $1", guild_id)
            await interaction.response.send_message(
                "FC Card Clash commands can now be used in any channel in this server.",
                ephemeral=True,
            )
            return

        rows = await conn.fetch(
            """
            SELECT channel_id
            FROM fc_card_guild_channels
            WHERE guild_id = $1
            ORDER BY created_at ASC
            """,
            guild_id,
        )

    if not rows:
        message = "No channel limit is set. FC Card Clash commands can be used in any channel."
    else:
        message = "FC Card Clash commands are allowed in:\n" + "\n".join(f"<#{row['channel_id']}>" for row in rows)
    await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(name="pack", description="Open your daily FC Card Clash pack.")
async def pack(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    assert bot.game_pool is not None and bot.card_pool is not None
    try:
        pack_size = _int_env("FC_CARDS_DAILY_PACK_SIZE", 3, minimum=1, maximum=10)
        async with bot.game_pool.acquire() as game_conn:
            user = await get_user(game_conn, str(interaction.user.id))
            async with bot.card_pool.acquire() as card_conn:
                cards = [await pull_weighted_card(card_conn, int(user["pack_streak"] or 0)) for _ in range(pack_size)]
            results = await open_pack_many(game_conn, str(interaction.user.id), cards)
        embed = _pack_results_embed("Daily Pack", results, interaction.user.mention)
        await interaction.followup.send(embed=embed)
    except Exception as exc:
        if "Daily pack already opened" in str(exc):
            await _send_error(interaction, f"You've already opened today's pack.\nNext pack unlocks in **{_next_daily_unlock()}**.")
            return
        await _send_error(interaction, str(exc))


@bot.tree.command(name="weekly", description="Open your boosted weekly FC Card Clash pack.")
async def weekly(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    assert bot.game_pool is not None and bot.card_pool is not None
    try:
        pack_size = _int_env("FC_CARDS_WEEKLY_PACK_SIZE", 5, minimum=1, maximum=10)
        async with bot.game_pool.acquire() as game_conn:
            async with bot.card_pool.acquire() as card_conn:
                cards = [await pull_weighted_card(card_conn, 7, odds_multiplier=2.25) for _ in range(pack_size)]
            results = await open_weekly_pack_many(game_conn, str(interaction.user.id), cards)
        embed = _pack_results_embed("Weekly Pack", results, interaction.user.mention, boosted=True)
        await interaction.followup.send(embed=embed)
    except Exception as exc:
        if "Weekly pack already opened" in str(exc):
            await _send_error(interaction, f"You've already opened this week's pack.\nNext weekly pack unlocks in **{_next_weekly_unlock()}**.")
            return
        await _send_error(interaction, str(exc))


@bot.tree.command(name="profile", description="View an FC Card Clash profile.")
async def profile(interaction: discord.Interaction, user: Optional[discord.User] = None) -> None:
    await interaction.response.defer(ephemeral=True)
    target = user or interaction.user
    assert bot.game_pool is not None
    async with bot.game_pool.acquire() as conn:
        row = await get_user(conn, str(target.id))
        stats = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(copies), 0) AS total_cards,
                   COUNT(*) FILTER (WHERE copies > 0) AS unique_cards,
                   COUNT(*) FILTER (WHERE copies > 1) AS duplicate_cards
            FROM fc_card_inventory
            WHERE discord_user_id = $1
            """,
            str(target.id),
        )
        last_pull = await conn.fetchrow(
            """
            SELECT card_id, source, created_at
            FROM fc_card_pack_openings
            WHERE discord_user_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            str(target.id),
        )
    pool_total = await _pool_total()
    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    played = wins + losses
    win_rate = f"{(wins / played * 100):.1f}%" if played else "0.0%"
    unique_cards = int(stats["unique_cards"] or 0)
    completion = f"{(unique_cards / pool_total * 100):.1f}%" if pool_total else "0.0%"
    embed = discord.Embed(title=f"{target.display_name}'s FC Card Clash Profile", color=0x5865F2)
    embed.add_field(name="Record", value=f"**{wins}W - {losses}L**\nWin rate: **{win_rate}**", inline=True)
    embed.add_field(name="Streaks", value=f"Current: **{row['current_streak']}**\nBest: **{row['best_streak']}**", inline=True)
    embed.add_field(name="Wallet", value=f"Coins: **{row['coins']}**\nShards: **{row['shards']}**", inline=True)
    embed.add_field(
        name="Collection",
        value=(
            f"Total: **{stats['total_cards']}**\n"
            f"Unique: **{unique_cards}/{pool_total}**\n"
            f"Complete: **{completion}**\n"
            f"Dupes: **{stats['duplicate_cards']}**"
        ),
        inline=True,
    )
    embed.add_field(name="Packs", value=f"Daily streak: **{row['pack_streak']}**", inline=True)
    if last_pull:
        embed.add_field(name="Last pull", value=f"`{last_pull['card_id']}` from **{last_pull['source']}** pack", inline=True)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="progress", description="Show FC Card Clash collection progress.")
async def progress(interaction: discord.Interaction, user: Optional[discord.User] = None) -> None:
    await interaction.response.defer()
    target = user or interaction.user
    assert bot.game_pool is not None
    async with bot.game_pool.acquire() as conn:
        stats = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(copies), 0) AS total_cards,
                   COUNT(*) FILTER (WHERE copies > 0) AS unique_cards,
                   COUNT(*) FILTER (WHERE copies > 1) AS duplicate_cards
            FROM fc_card_inventory
            WHERE discord_user_id = $1
            """,
            str(target.id),
        )
    pool_total = await _pool_total()
    unique_cards = int(stats["unique_cards"] or 0)
    completion = f"{(unique_cards / pool_total * 100):.1f}%" if pool_total else "0.0%"
    embed = discord.Embed(title=f"{target.display_name}'s Collection Progress", color=0x2ECC71)
    embed.description = (
        f"Unique cards: **{unique_cards}/{pool_total}**\n"
        f"Completion: **{completion}**\n"
        f"Total cards owned: **{stats['total_cards']}**\n"
        f"Duplicate cards: **{stats['duplicate_cards']}**"
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="lastpull", description="Show off your latest FC Card Clash pull.")
async def lastpull(interaction: discord.Interaction, user: Optional[discord.User] = None) -> None:
    await interaction.response.defer()
    target = user or interaction.user
    assert bot.game_pool is not None and bot.card_pool is not None
    async with bot.game_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT card_id, source, created_at
            FROM fc_card_pack_openings
            WHERE discord_user_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            str(target.id),
        )
    if not row:
        await _send_error(interaction, "No pulls found yet.")
        return
    async with bot.card_pool.acquire() as conn:
        card = await search_card(conn, str(row["card_id"]))
    if not card:
        await _send_error(interaction, "That pull is no longer in the active card pool.")
        return
    embed = _card_embed(f"{target.display_name}'s Last Pull", card, color=_pack_color(card))
    embed.description = f"Latest pull from a **{row['source']}** pack:\n{_card_line(card)}"
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="dupes", description="Show duplicate cards you can trade.")
async def dupes(interaction: discord.Interaction, user: Optional[discord.User] = None) -> None:
    await collection(interaction, view="duplicates", user=user)


@bot.tree.command(name="collection", description="Show your best cards or duplicates.")
@app_commands.describe(view="Choose best cards or duplicate trade stock.")
@app_commands.choices(view=[
    app_commands.Choice(name="Best cards", value="best"),
    app_commands.Choice(name="Duplicates", value="duplicates"),
])
async def collection(interaction: discord.Interaction, view: str = "best", user: Optional[discord.User] = None) -> None:
    await interaction.response.defer(ephemeral=True)
    target = user or interaction.user
    assert bot.game_pool is not None and bot.card_pool is not None
    filter_sql = "AND i.copies > 1" if view == "duplicates" else ""
    async with bot.game_pool.acquire() as game_conn:
        rows = await game_conn.fetch(
            f"""
            SELECT i.card_id, i.copies
            FROM fc_card_inventory i
            WHERE i.discord_user_id = $1
              AND i.copies > 0
              {filter_sql}
            ORDER BY i.copies DESC, i.card_id DESC
            LIMIT 25
            """,
            str(target.id),
        )
    if not rows:
        await _send_error(interaction, "No cards found for that view yet.")
        return
    card_ids = [int(r["card_id"]) for r in rows]
    copies = {int(r["card_id"]): int(r["copies"]) for r in rows}
    async with bot.card_pool.acquire() as conn:
        card_rows = await conn.fetch(
            """
            SELECT source_card_id,
                   COALESCE(NULLIF(BTRIM(display_name), ''), NULLIF(BTRIM(name), ''), 'Card ' || source_card_id::text) AS name,
                   rating, primary_position, rarity,
                   card_image_stored_url, card_image_url, player_image_url,
                   pace, shooting, passing, dribbling, defending, physicality,
                   club, league, nation
            FROM futgg_market_snapshot
            WHERE source_card_id = ANY($1::bigint[])
              AND game_year = $2
            ORDER BY rating DESC, source_card_id DESC
            """,
            card_ids,
            active_game_year(),
        )
    cards = [Card.from_row(r) for r in card_rows]
    embed = discord.Embed(
        title=f"{target.display_name}'s {'Duplicate Stock' if view == 'duplicates' else 'Best Cards'}",
        color=0xEABD45,
    )
    embed.description = "\n".join(f"`x{copies[c.card_id]}` {_card_line(c)}" for c in cards[:15])
    if cards and cards[0].image_url:
        embed.set_thumbnail(url=cards[0].image_url)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="card", description="Inspect a card and see how many copies you own.")
@app_commands.autocomplete(card=_autocomplete_my_cards)
async def card(interaction: discord.Interaction, card: str) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        found = await _find_card(card)
        assert bot.game_pool is not None
        async with bot.game_pool.acquire() as conn:
            copies = await owned_copies(conn, str(interaction.user.id), found.card_id)
        embed = _card_embed("Card Inspector", found, color=0x9B59B6)
        embed.add_field(name="Your copies", value=f"**x{copies}**", inline=True)
        embed.add_field(name="Battle risk", value="Can be staked even if this is your only copy.", inline=True)
        embed.add_field(name="Trade rule", value="Only duplicate copies can be traded.", inline=True)
        await interaction.followup.send(embed=embed)
    except Exception as exc:
        await _send_error(interaction, str(exc))


@bot.tree.command(name="trade", description="Offer a duplicate card trade.")
@app_commands.autocomplete(your_card=_autocomplete_my_duplicates, requested_card=_autocomplete_trade_request_cards)
async def trade(
    interaction: discord.Interaction,
    user: discord.User,
    your_card: str,
    requested_card: Optional[str] = None,
) -> None:
    await interaction.response.defer()
    try:
        offered = await _find_card(your_card)
        requested = await _find_card(requested_card) if requested_card else None
        assert bot.game_pool is not None
        async with bot.game_pool.acquire() as conn:
            row = await create_trade(
                conn,
                str(interaction.guild_id) if interaction.guild_id else None,
                str(interaction.channel_id) if interaction.channel_id else None,
                str(interaction.user.id),
                str(user.id),
                offered.card_id,
                requested.card_id if requested else None,
            )
        want = _card_line(requested) if requested else "any duplicate/counter-offer agreed in chat"
        embed = discord.Embed(title="Trade Offer Created", color=0x3498DB)
        embed.description = (
            f"{interaction.user.mention} offers {user.mention}:\n"
            f"Give: {_card_line(offered)}\n"
            f"Request: {want}\n\n"
            f"{user.mention}, use `/trade_accept` and pick this trade from the list.\n"
            "Trades are duplicates-only for both sides."
        )
        if offered.image_url:
            embed.set_thumbnail(url=offered.image_url)
        embed.set_footer(text=f"Trade ID {_short_id(row['id'])} - expires in 15 minutes")
        await interaction.followup.send(embed=embed)
    except Exception as exc:
        await _send_error(interaction, str(exc))


@bot.tree.command(name="trade_accept", description="Accept a pending duplicate-only trade.")
@app_commands.autocomplete(trade_id=_autocomplete_pending_trades)
async def trade_accept(interaction: discord.Interaction, trade_id: str) -> None:
    await interaction.response.defer()
    try:
        assert bot.game_pool is not None
        async with bot.game_pool.acquire() as conn:
            row = await accept_trade(conn, trade_id, str(interaction.user.id))
        embed = discord.Embed(title="Trade Complete", description=f"Trade `{_short_id(row['id'])}` has been completed.", color=0x2ECC71)
        await interaction.followup.send(embed=embed)
    except Exception as exc:
        await _send_error(interaction, str(exc))


STAT_CHOICES = [
    app_commands.Choice(name="PAC", value="pace"),
    app_commands.Choice(name="SHO", value="shooting"),
    app_commands.Choice(name="PAS", value="passing"),
    app_commands.Choice(name="DRI", value="dribbling"),
    app_commands.Choice(name="DEF", value="defending"),
    app_commands.Choice(name="PHY", value="physicality"),
]


@bot.tree.command(name="clash", description="Stake one card in a winner-keeps-both Card Clash.")
@app_commands.autocomplete(card=_autocomplete_my_cards)
@app_commands.choices(stat=STAT_CHOICES)
async def clash(interaction: discord.Interaction, user: discord.User, card: str, stat: str) -> None:
    await interaction.response.defer()
    try:
        picked_stat = normalize_stat(stat)
        staked = await _find_card(card)
        assert bot.game_pool is not None
        async with bot.game_pool.acquire() as conn:
            copies = await owned_copies(conn, str(interaction.user.id), staked.card_id)
            row = await create_battle(
                conn,
                str(interaction.guild_id) if interaction.guild_id else None,
                str(interaction.channel_id) if interaction.channel_id else None,
                str(interaction.user.id),
                str(user.id),
                staked.card_id,
                picked_stat,
            )
        risk = "THIS IS YOUR ONLY COPY." if copies == 1 else f"You own x{copies}."
        embed = discord.Embed(title="Blind Card Clash Challenge", color=0xC0392B)
        embed.description = (
            f"{interaction.user.mention} has challenged {user.mention}.\n"
            "**Their card and stat are locked in, but hidden.**\n\n"
            f"Risk: **{risk} If they lose, their card is gone.**\n"
            "You pick your card and stat blind. Same stat is not allowed.\n\n"
            f"{user.mention}, use `/clash_accept` and pick this battle from the list."
        )
        embed.set_footer(text=f"Battle ID {_short_id(row['id'])} - expires in 10 minutes")
        await interaction.followup.send(embed=embed)
    except Exception as exc:
        await _send_error(interaction, str(exc))


@bot.tree.command(name="clash_accept", description="Accept a Card Clash and stake one of your cards.")
@app_commands.autocomplete(battle_id=_autocomplete_pending_battles, card=_autocomplete_my_cards)
@app_commands.choices(stat=STAT_CHOICES)
async def clash_accept(interaction: discord.Interaction, battle_id: str, card: str, stat: str) -> None:
    await interaction.response.defer()
    try:
        opponent_stat = normalize_stat(stat)
        opponent_card = await _find_card(card)
        assert bot.game_pool is not None and bot.card_pool is not None
        async with bot.game_pool.acquire() as game_conn:
            battle = await game_conn.fetchrow("SELECT * FROM fc_card_battles WHERE id = $1::uuid", battle_id)
            if not battle:
                raise ValueError("Clash not found.")
            async with bot.card_pool.acquire() as card_conn:
                challenger_card = await search_card(card_conn, str(battle["challenger_card_id"]))
            if not challenger_card:
                raise ValueError("Challenger card no longer exists in the card pool.")
            result = await accept_battle(game_conn, battle_id, str(interaction.user.id), opponent_card, opponent_stat, challenger_card)
        rounds = result["rounds"] or []
        if isinstance(rounds, str):
            rounds = json.loads(rounds)
        winner = f"<@{result['winner_id']}>"
        loser = f"<@{result['loser_id']}>"
        winner_card = challenger_card if str(result["winner_id"]) == str(battle["challenger_id"]) else opponent_card
        loser_card = opponent_card if str(result["winner_id"]) == str(battle["challenger_id"]) else challenger_card
        lines = []
        for idx, r in enumerate(rounds, start=1):
            source = {"challenger": "their pick", "opponent": "your pick", "random": "random"}[r["source"]]
            round_winner = "draw" if r["winner"] == "draw" else ("challenger" if r["winner"] == "challenger" else "defender")
            lines.append(
                f"**R{idx} {stat_label(r['stat'])}** ({source}) - "
                f"{challenger_card.name} {r['challenger']} vs {opponent_card.name} {r['opponent']} - **{round_winner}**"
            )
        embed = discord.Embed(title="Card Clash Result", color=0xF1C40F)
        embed.description = (
            f"{winner} beat {loser} and won **{loser_card.name}**.\n\n"
            f"**Winner now keeps**\n"
            f"{_card_line(winner_card)}\n"
            f"{_card_line(loser_card)}\n\n"
            "**Rounds**\n"
            + "\n".join(lines)
        )
        embed.add_field(name="Reward", value="Winner +50 coins. Loser -15 coins.", inline=False)
        if winner_card.image_url:
            embed.set_image(url=winner_card.image_url)
        await interaction.followup.send(embed=embed)
    except Exception as exc:
        await _send_error(interaction, str(exc))


@bot.tree.command(name="leaderboard", description="Show global or server FC Card Clash rankings.")
@app_commands.choices(scope=[
    app_commands.Choice(name="Global", value="global"),
    app_commands.Choice(name="Server", value="server"),
])
async def leaderboard(interaction: discord.Interaction, scope: str = "global") -> None:
    await interaction.response.defer(ephemeral=True)
    assert bot.game_pool is not None
    async with bot.game_pool.acquire() as conn:
        if scope == "server" and interaction.guild_id:
            rows = await conn.fetch(
                """
                WITH guild_records AS (
                    SELECT winner_id AS discord_user_id, COUNT(*)::int AS wins, 0::int AS losses
                    FROM fc_card_battles
                    WHERE status = 'completed' AND guild_id = $1
                    GROUP BY winner_id
                    UNION ALL
                    SELECT loser_id AS discord_user_id, 0::int AS wins, COUNT(*)::int AS losses
                    FROM fc_card_battles
                    WHERE status = 'completed' AND guild_id = $1
                    GROUP BY loser_id
                )
                SELECT discord_user_id, SUM(wins)::int AS wins, SUM(losses)::int AS losses,
                       MAX(u.best_streak)::int AS best_streak
                FROM guild_records gr
                JOIN fc_card_users u USING (discord_user_id)
                GROUP BY discord_user_id
                ORDER BY SUM(wins) DESC, MAX(u.best_streak) DESC, SUM(losses) ASC
                LIMIT 10
                """,
                str(interaction.guild_id),
            )
        else:
            rows = await conn.fetch(
                """
                SELECT discord_user_id, wins, losses, current_streak, best_streak
                FROM fc_card_users
                ORDER BY wins DESC, best_streak DESC, losses ASC
                LIMIT 10
                """
            )
    embed = discord.Embed(title=f"FC Card Clash Leaderboard ({scope})", color=0x5865F2)
    embed.description = "\n".join(
        f"**{idx}.** <@{r['discord_user_id']}> - **{r['wins']}W/{r['losses']}L** - best streak {r['best_streak']}"
        for idx, r in enumerate(rows, start=1)
    ) or "No battles yet."
    await interaction.followup.send(embed=embed)


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    bot.run(_token())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

