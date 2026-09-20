# FC Card Clash

Standalone Discord card-collecting and battling bot for the tradehq backend.

## Start Command

Use this as a separate Coolify worker/background service:

```bash
python -m scripts.fc_card_clash_bot
```

Do not expose a domain or port. The process connects to Discord and stays
running.

## Environment

Required:

- `DATABASE_URL` - tradehq database containing `futgg_market_snapshot`
- `FC_CARDS_BOT_TOKEN` - preferred Discord bot token

Optional:

- `FC_CARDS_DATABASE_URL` - separate database for game state. If omitted, game tables are created in `DATABASE_URL`.
- `FC_CARDS_CARD_DATABASE_URL` - separate source database for card reads. If omitted, card reads use `DATABASE_URL`.
- `FC_CARDS_DISCORD_GUILD_ID` - sync slash commands instantly to one guild.
- `LOG_LEVEL=INFO`

Token fallback order:

1. `FC_CARDS_BOT_TOKEN`
2. `DISCORD_GAME_BOT_TOKEN`
3. `DISCORD_BOT_TOKEN`

The bot creates its own `fc_card_*` tables on startup.

## Commands

- `/pack` - daily weighted pack from Gold+ cards with stats and images.
- `/weekly` - boosted weekly pack with a higher chance of strong pulls.
- `/help` - quick in-Discord rules and command list.
- `/profile [user]` - record, streaks, wallet (coins/shards/shields), and collection counts with a completion bar.
- `/progress [user]` - collection completion with a visual bar.
- `/collection [view] [user]` - best cards or duplicate trade stock, paginated with buttons.
- `/dupes [user]` - shortcut for tradeable duplicate cards.
- `/card <card>` - card inspection and owned copies.
- `/trade <user> <your_card> [requested_card]` - duplicate-only trade offer.
- `/trade_accept <trade_id>` - accept pending trade.
- `/clash <user> <card> <stat> [confirm]` - stake one owned card. Staking your
  only copy requires `confirm: true`.
- `/clash_accept <battle_id> <card> <stat> [confirm]` - accept and resolve the
  clash. Same only-copy confirmation applies.
- `/shop` - view what coins and shards can buy.
- `/shop_buy <item>` - spend coins/shards on a streak shield, an extra pack,
  or a shard-funded pull.
- `/leaderboard [scope]` - global or server rankings.

## Battle Rules

Card Clash is best of 3, staking exactly one card per side (kept quick and
low-friction on purpose - two slash commands total resolve a full battle):

1. Challenger's chosen stat
2. Defender's chosen stat
3. Random decider from the remaining stat pool

Winner keeps both staked cards. Trades protect first copies; battles do not,
though staking a sole copy now requires explicit confirmation on both the
challenge and accept side. The initial challenge hides the challenger's card
and chosen stat, so the defender picks their stake and stat blind. Both
players must choose different stats. `/clash_accept` reveals rounds one at a
time with a short delay between them for pacing before showing the final
result.

## Economy

- Coins: +50 to the battle winner, -15 (floored at 0) from the loser. Spend
  them in `/shop_buy` on a streak shield (150) or an extra pack (200).
- Shards: earned automatically from any duplicate pull beyond your 2nd copy
  of a card, scaled by rarity. Spend 120 shards on a bonus pull via
  `/shop_buy shard_pull`.
- Streak shield: consumed automatically the next time you miss exactly one
  daily pack day, keeping your `pack_streak` alive instead of resetting it.

