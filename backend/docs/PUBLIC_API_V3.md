# EAFC.Live Public Data API v3

v3 is the FC27-first external API. It reads the live FUT.GG-backed market
pipeline and keeps `game_year` explicit so FC26 and FC27 records cannot collide.

## Authentication

Send the customer's show-once key on every request:

```http
X-API-Key: eafclive_...
```

Keys are stored as SHA-256 hashes, can be revoked immediately, and are metered
per key. Existing legacy `futhub_` keys remain valid because authentication is
hash-based; only newly issued keys use the EAFC.Live prefix.

## Starter allowance

- 60 requests/minute
- 5,000 requests/month

Trader and Dev retain the existing larger quotas.

## FC27 endpoints

```text
GET /api/public/v3/players/search?q=aguero&game_year=27
GET /api/public/v3/players/153079?game_year=27
GET /api/public/v3/players/153079/price?game_year=27
GET /api/public/v3/players/153079/sales?game_year=27&limit=20
GET /api/public/v3/usage
```

The `/price` route is the preferred low-payload endpoint for bots. It returns
the current PS5 BIN, FUT.GG price range, collector timestamps, and latest
completed sale. `/players/{card_id}` adds card metadata and sales summary.

## Admin fulfilment

Admin > Users > API Keys can issue a key to an external customer without making
them create an EAFC.Live account. The plaintext secret is displayed once only.
The same screen shows monthly usage, allows tier changes, and can revoke a key.

For a low-volume integration such as ~100 lookups/day, Starter's 5,000 monthly
requests is sufficient.
