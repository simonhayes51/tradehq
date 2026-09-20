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
