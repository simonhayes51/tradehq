# What the assistant costs, and why the limits are what they are

Phase 2 shipped a 200-messages-per-day ceiling for Elite members. It was
chosen as "generous enough not to be a daily allowance" — a UX judgement,
never a costed one. This document is the costing, and the reasoning behind
the numbers that replaced it.

## What was measured

Sized against the real prompt in `app/routers/v2/ai_chat.py`: the actual
`SYSTEM_PROMPT`, the actual `TOOLS` schema, and tool results in the exact
shape `_chat_safe_card()` produces.

| | tokens |
|---|---|
| `SYSTEM_PROMPT` | ~306 |
| `TOOLS` schema | ~438 — **resent on every model call** |
| fixed floor per call | ~744 |
| card lookup result | ~171 |
| budget search result (5 cards) | ~848 |
| track-record result | ~95 |

**One customer message is up to three model calls.** The loop calls the
model, runs the tool it asks for, and calls again with the result in
context. Every round resends the whole conversation plus the tool schema.

| turn | history | in | out |
|---|---|---|---|
| card lookup | 0 turns | ~1,750 | ~280 |
| card lookup | 5 turns | ~4,105 | ~280 |
| card lookup | 20 turns | ~11,170 | ~280 |
| budget search | 20 turns | ~11,848 | ~280 |

### These are estimates, and are labelled as such

`tiktoken`'s BPE table could not be downloaded in the build environment
(the egress proxy denies `openaipublic.blob.core.windows.net`) and there
is no API key available here, so the table above is a **character-based
estimate** using ~4.0 chars/token for prose and ~3.2 for dense JSON, with
costs reported at the pessimistic end.

This is a planning input, not a measurement. The shipped accounting
records **provider-reported** token usage (`response.usage`), so the first
day of real traffic replaces every number here with a measured one. Read
them from the admin summary, not from this file.

## Cost

**No pricing existed anywhere in this repository.** The defaults in
`app/services/ai_usage.py` are the published list rates for
`gpt-4o-mini` — **$0.15 / 1M input, $0.60 / 1M output** — the model this
deployment used *before* switching to `gpt-5.6-luna`. They were never
verified against this account's invoices even for `gpt-4o-mini`, and are
now the wrong model's rates outright: nobody has supplied real per-token
pricing for `gpt-5.6-luna`, so every dollar figure below is computed
against a different model's cost. They remain the configured defaults
only because there is nothing else to put there yet, and are
environment-overridable for exactly that reason:

```
AI_CHAT_PRICE_PER_1M_INPUT=0.15
AI_CHAT_PRICE_PER_1M_OUTPUT=0.60
AI_CHAT_PRICE_CURRENCY=USD
```

**Action required before launch:** get `gpt-5.6-luna`'s real published
per-token price into `AI_CHAT_PRICE_PER_1M_INPUT`/`_OUTPUT`, re-run the
tables below with that number, then check one real invoice against
`GET /api/v2/admin/ai-usage` and correct again if it differs. Until then
every £ figure in this document is priced as `gpt-4o-mini`, not the model
actually being called.

At those rates, converting at £1 = $1.27:

| | per message |
|---|---|
| typical (card lookup, 5 turns of history) | £0.00062 |
| worst (budget search, 20 turns) | £0.00154 |

### Monthly cost per Elite member, against the £3 subscription

| msgs/day | typical | % of £3 | worst | % of £3 |
|---|---|---|---|---|
| 10 | £0.19 | 6% | £0.46 | 15% |
| 20 | £0.37 | 12% | £0.92 | 31% |
| 30 | £0.56 | 19% | £1.38 | 46% |
| 50 | £0.93 | 31% | £2.31 | 77% |
| **200** | **£3.72** | **124%** | **£9.22** | **307%** |

**200/day loses money on any member who uses it as permitted.**

## The hole that mattered more than the daily count

`ChatRequest.messages` had `min_length=1` and **no maximum**, and the
client sends the whole conversation on every request. History is the
dominant term in the cost of a message, and it was chosen by the caller:

| history sent | tokens | cost/message | at the 6/min limit |
|---|---|---|---|
| 100 KB | ~32,000 | £0.0080 | £2.88/hour |
| 500 KB | ~160,000 | £0.0383 | £13.80/hour |
| 1 MB | ~320,000 | £0.0763 | **£27.45/hour** |

A single account could spend more in an hour than nine annual
subscriptions. Capping the daily count without capping history would have
left that untouched.

## The limits, and why

With a server-side history cap of 10 messages, the worst case per message
falls to £0.00070:

| history cap | worst/msg | 20/day | 30/day |
|---|---|---|---|
| 6 messages | £0.00059 | £0.35/mo (12%) | £0.53/mo (18%) |
| **10 messages** | **£0.00070** | £0.42/mo (14%) | **£0.63/mo (21%)** |
| 16 messages | £0.00087 | £0.52/mo (17%) | £0.78/mo (26%) |

**Chosen defaults** (all overridable):

| variable | default | why |
|---|---|---|
| `AI_CHAT_RATE_LIMIT_PER_MINUTE` | 6 | unchanged; stops a burst |
| `AI_CHAT_RATE_LIMIT_PER_DAY` | **30** | ~21% of £3 at the measured worst case; far above real use |
| `AI_CHAT_RATE_LIMIT_PER_MONTH` | **300** | the member who maxes out daily every day still costs <£0.60/mo |
| `AI_CHAT_MAX_CONCURRENT` | 1 | a chat UI sends one message at a time; more is a script |
| `AI_CHAT_MAX_HISTORY_MESSAGES` | 10 | five exchanges — enough to follow one card or one budget |
| `AI_CHAT_MAX_MESSAGE_CHARS` | 2000 | so one huge message cannot replace a long history |
| `AI_CHAT_MAX_OUTPUT_TOKENS` | 600 | makes "keep replies short" enforceable, not just requested |
| `AI_CHAT_ENABLED` | 1 | kill switch, read per request — no deploy needed to switch off |

30/day is a **backstop against automation and runaway loops**, not a
rationing scheme: a member using this as a trading assistant sends a
handful of questions in a session.

## Questions the objective asked, answered

- **Which model?** `gpt-5.6-luna`, via `OPENAI_CHAT_MODEL` — the only
  model the endpoint calls. On a model-call failure (`except Exception`
  around the `client.chat.completions.create` loop) the request fails
  with a 503; there is no retry against a different model.
- **Model calls per customer message?** Up to 3 (`_MAX_TOOL_ROUNDS`).
- **Do failed requests consume quota?** The rate windows, yes — a request
  that reached the provider spent money whether or not it produced an
  answer. The concurrency slot is always released. Failures are recorded
  with `status='failed'` and the tokens they actually spent.
- **Do blocked requests consume quota?** No. They never reach the
  provider, are recorded as `status='blocked'` with zero tokens, and do
  not consume the concurrency slot.
- **Can streaming retries double-charge?** No — the endpoint does not
  stream. It is a single non-streaming call per round, so there is no
  partial-response retry path. If streaming is added later, the usage
  object arrives only in the final chunk and this accounting must be
  revisited.
- **Can concurrent requests bypass the limits?** Not any more.
  `AI_CHAT_MAX_CONCURRENT` refuses a second in-flight message per account,
  and the Redis counters are atomic `INCR`s.
- **Fleet-wide?** Yes when `REDIS_URL` is set. Without Redis it degrades
  to per-worker, which is a weaker ceiling but not an absent one.

## Where to look in production

`GET /api/v2/admin/ai-usage?days=30` (admin only) reports messages,
successes, failures, refusals, unique users, provider-reported tokens,
model calls, estimated cost, cost per message, cost per active user, and
**the single heaviest account** — which is the number that actually
decides whether the limit is right.
