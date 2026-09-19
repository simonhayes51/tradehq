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
- `/profile [user]` - record, streaks, wallet, and collection counts.
- `/collection [view] [user]` - best cards or duplicate trade stock.
- `/card <card>` - card inspection and owned copies.
- `/trade <user> <your_card> [requested_card]` - duplicate-only trade offer.
- `/trade_accept <trade_id>` - accept pending trade.
- `/clash <user> <card> <stat>` - stake one owned card. Your only copy can be lost.
- `/clash_accept <battle_id> <card> <stat>` - accept and resolve the clash.
- `/leaderboard [scope]` - global or server rankings.

## Battle Rules

Card Clash is best of 3:

1. Challenger's chosen stat
2. Defender's chosen stat
3. Random decider from the remaining stat pool

Winner keeps both staked cards. Trades protect first copies; battles do not.

