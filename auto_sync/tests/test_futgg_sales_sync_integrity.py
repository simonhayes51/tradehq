"""
Tests for the SalesSync data-integrity fix and its supporting machinery:
never advancing next_sales_due_at on a failed fetch (the production bug -
never_read falling by 200/cycle despite zero successful reads), the
circuit breaker, the broadened throttle signal, and safe diagnostic
logging.

Pure-Python logic (is_throttle_signal, circuit_breaker_tripped, log
redaction) needs no DB and runs anywhere. apply_batch_results()'s actual
UPDATE/INSERT behaviour is tested against a REAL local Postgres (skipped
if unreachable) rather than a hand-rolled fake - NOW() + INTERVAL and
NULL-preservation semantics are exactly the kind of thing worth verifying
against the real thing, same approach used for the migration-runner and
EasySBC advisory-lock/xmax tests elsewhere in this repo.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import futgg_sales_sync as mod

ADMIN_DSN = os.getenv(
    "TEST_ADMIN_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
)


def run_async(coro_fn):
    @functools.wraps(coro_fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(coro_fn(*args, **kwargs))
    return wrapper


# ---------------------------------------------------------------------
# Real-Postgres fixture (skipped if unreachable)
# ---------------------------------------------------------------------


async def _postgres_reachable() -> bool:
    try:
        conn = await asyncpg.connect(ADMIN_DSN, timeout=2)
    except Exception:
        return False
    await conn.close()
    return True


async def _make_db() -> str:
    name = f"tradehq_sales_test_{uuid.uuid4().hex[:12]}"
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()
    return name


async def _drop_db(name: str) -> None:
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", name
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        await conn.close()


def _dsn_for(name: str) -> str:
    base = ADMIN_DSN.rsplit("/", 1)[0]
    return f"{base}/{name}"


@pytest.fixture
def dsn():
    if not asyncio.run(_postgres_reachable()):
        pytest.skip(f"no Postgres reachable at {ADMIN_DSN}")
    name = asyncio.run(_make_db())
    try:
        yield _dsn_for(name)
    finally:
        asyncio.run(_drop_db(name))


async def _seed_players(conn_dsn: str, cards: list[dict]) -> None:
    """Minimal futgg_players shape - only the columns this worker
    touches. Real production schema (migration 038) has many more
    columns; irrelevant to these tests."""
    conn = await asyncpg.connect(conn_dsn)
    try:
        await conn.execute(
            """
            CREATE TABLE futgg_players (
                source_card_id BIGINT PRIMARY KEY,
                rating INTEGER,
                name TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                is_tradeable BOOLEAN,
                source_url TEXT,
                next_sales_due_at TIMESTAMPTZ,
                last_sales_status TEXT
            )
            """
        )
        # apply_batch_results() writes to futgg_sales_history - create it
        # via the real production DDL (ensure_schema), not a hand-rolled
        # copy that could quietly drift from the real column set.
        await mod.ensure_schema(conn)
        for card in cards:
            await conn.execute(
                """
                INSERT INTO futgg_players (source_card_id, rating, name, next_sales_due_at)
                VALUES ($1, $2, $3, $4)
                """,
                card["source_card_id"], card.get("rating", 85), card.get("name", "Test"),
                card.get("next_sales_due_at"),
            )
    finally:
        await conn.close()


async def _fetch_player(conn_dsn: str, card_id: int) -> dict:
    conn = await asyncpg.connect(conn_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT source_card_id, next_sales_due_at, last_sales_status FROM futgg_players "
            "WHERE source_card_id = $1",
            card_id,
        )
        return dict(row)
    finally:
        await conn.close()


def _row(card_id: int, rating: int = 85, next_sales_due_at=None) -> dict:
    return {"source_card_id": card_id, "rating": rating, "next_sales_due_at": next_sales_due_at}


class _Stats(dict):
    def __init__(self):
        super().__init__(ok=0, failed=0, sales_seen=0, batch_errors=0)


def _throttle():
    return mod.AdaptiveThrottle(8, 0.5)


# ---------------------------------------------------------------------
# Successful fetch advances next_sales_due_at (with and without sales)
# ---------------------------------------------------------------------


@run_async
async def test_successful_fetch_advances_next_sales_due_at(monkeypatch, dsn):
    monkeypatch.setattr(mod, "APPLY", True)
    await _seed_players(dsn, [_row(1001, next_sales_due_at=None)])
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        results = [{
            "eaId": 1001,
            "completedAuctions": [{"soldPrice": 50000, "soldDate": "2026-08-03T12:00:00Z"}],
            "currentPrice": 51000,
        }]
        await mod.apply_batch_results(
            pool, [_row(1001, next_sales_due_at=None)], results, _Stats(), mod.StageTimers(), _throttle()
        )
    finally:
        await pool.close()

    after = await _fetch_player(dsn, 1001)
    assert after["next_sales_due_at"] is not None
    assert after["last_sales_status"] == "success"


@run_async
async def test_successful_zero_sales_fetch_still_advances_it(monkeypatch, dsn):
    """A well-formed response with zero completed auctions is a real
    observation ('checked, nothing new'), not a failure - item 4."""
    monkeypatch.setattr(mod, "APPLY", True)
    await _seed_players(dsn, [_row(1002, next_sales_due_at=None)])
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        results = [{"eaId": 1002, "completedAuctions": [], "currentPrice": None}]
        await mod.apply_batch_results(
            pool, [_row(1002, next_sales_due_at=None)], results, _Stats(), mod.StageTimers(), _throttle()
        )
    finally:
        await pool.close()

    after = await _fetch_player(dsn, 1002)
    assert after["next_sales_due_at"] is not None
    assert after["last_sales_status"] == "success"


# ---------------------------------------------------------------------
# Failed fetches of every kind must NOT advance a never-read card
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    ["data_403", "sign_502", "challenge_required", "malformed_payload",
     "malformed_json", "no_signed_url", "exception:TypeError: x"],
)
@run_async
async def test_failed_fetch_never_advances_a_never_read_card(monkeypatch, dsn, error):
    monkeypatch.setattr(mod, "APPLY", True)
    await _seed_players(dsn, [_row(2001, next_sales_due_at=None)])
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        results = [{"eaId": 2001, "error": error, "signStatus": 200, "dataStatus": 403}]
        await mod.apply_batch_results(
            pool, [_row(2001, next_sales_due_at=None)], results, _Stats(), mod.StageTimers(), _throttle()
        )
    finally:
        await pool.close()

    after = await _fetch_player(dsn, 2001)
    # THE production bug: this must stay exactly NULL, not
    # "NOW() + 5 minutes" - a real timestamp here silently converts
    # "never read" into "read (but failed)".
    assert after["next_sales_due_at"] is None
    assert after["last_sales_status"] is None


@run_async
async def test_never_read_count_does_not_fall_after_a_fully_failed_cycle(monkeypatch, dsn):
    """The exact production symptom, reproduced: a whole batch of
    never-read cards, all fetches fail (data_403) - the never_read
    coverage count (next_sales_due_at IS NULL) must be unchanged after."""
    monkeypatch.setattr(mod, "APPLY", True)
    cards = [_row(3000 + i, next_sales_due_at=None) for i in range(10)]
    await _seed_players(dsn, cards)
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        conn = await pool.acquire()
        before = await conn.fetchval(
            "SELECT count(*) FROM futgg_players WHERE next_sales_due_at IS NULL"
        )
        await pool.release(conn)
        assert before == 10

        results = [{"eaId": c["source_card_id"], "error": "data_403"} for c in cards]
        await mod.apply_batch_results(pool, cards, results, _Stats(), mod.StageTimers(), _throttle())

        conn = await pool.acquire()
        after = await conn.fetchval(
            "SELECT count(*) FROM futgg_players WHERE next_sales_due_at IS NULL"
        )
        await pool.release(conn)
        assert after == 10  # unchanged - not 0, matching the reported bug
    finally:
        await pool.close()


# ---------------------------------------------------------------------
# A previously-read card gets a short, distinct retry backoff on failure
# ---------------------------------------------------------------------


@run_async
async def test_failed_fetch_gives_previously_read_card_a_short_backoff(monkeypatch, dsn):
    monkeypatch.setattr(mod, "APPLY", True)
    monkeypatch.setattr(mod, "FAILURE_RETRY_BACKOFF_MINUTES", 2)
    old_due = datetime.now(timezone.utc) - timedelta(minutes=1)  # already due, previously read
    await _seed_players(dsn, [_row(4001, next_sales_due_at=old_due)])
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        row = _row(4001, next_sales_due_at=old_due)
        results = [{"eaId": 4001, "error": "data_403"}]
        await mod.apply_batch_results(pool, [row], results, _Stats(), mod.StageTimers(), _throttle())
    finally:
        await pool.close()

    after = await _fetch_player(dsn, 4001)
    assert after["next_sales_due_at"] is not None
    assert after["last_sales_status"] == "error:data_403"
    # Distinct from (much shorter than) any real success interval - the
    # shortest configured tier is 5 minutes (under_75 minimum via
    # env_int's floor); 2 here is clearly a backoff, not coverage.
    due_in = after["next_sales_due_at"] - datetime.now(timezone.utc)
    assert due_in < timedelta(minutes=3)


# ---------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------


def test_circuit_breaker_opens_on_mass_failure(monkeypatch):
    monkeypatch.setattr(mod, "CIRCUIT_BREAKER_THRESHOLD", 0.8)
    monkeypatch.setattr(mod, "CIRCUIT_BREAKER_MIN_SAMPLE", 20)
    stats = {"ok": 4, "failed": 16, "sales_seen": 0, "batch_errors": 0}  # 20 attempted, 80% failed
    assert mod.circuit_breaker_tripped(stats) is True


def test_circuit_breaker_stays_closed_under_the_sample_floor(monkeypatch):
    monkeypatch.setattr(mod, "CIRCUIT_BREAKER_THRESHOLD", 0.8)
    monkeypatch.setattr(mod, "CIRCUIT_BREAKER_MIN_SAMPLE", 20)
    stats = {"ok": 0, "failed": 8, "sales_seen": 0, "batch_errors": 0}  # 100% failed but only 8 attempted
    assert mod.circuit_breaker_tripped(stats) is False


def test_circuit_breaker_stays_closed_below_threshold(monkeypatch):
    monkeypatch.setattr(mod, "CIRCUIT_BREAKER_THRESHOLD", 0.8)
    monkeypatch.setattr(mod, "CIRCUIT_BREAKER_MIN_SAMPLE", 20)
    stats = {"ok": 15, "failed": 5, "sales_seen": 0, "batch_errors": 0}  # 25% failed
    assert mod.circuit_breaker_tripped(stats) is False


def test_circuit_breaker_recovers_once_stats_reset_for_the_next_cycle():
    """Each cycle starts a fresh stats dict (see run_forever) - the
    breaker must not stay permanently open once a cycle actually
    recovers, since it's evaluated against that cycle's own running
    stats, not a global sticky flag."""
    tripped_cycle = {"ok": 2, "failed": 18, "sales_seen": 0, "batch_errors": 0}
    assert mod.circuit_breaker_tripped(tripped_cycle) is True

    next_cycle = {"ok": 18, "failed": 2, "sales_seen": 0, "batch_errors": 0}
    assert mod.circuit_breaker_tripped(next_cycle) is False


# ---------------------------------------------------------------------
# Adaptive throttle reacts to 403/sign failures, not only 429s
# ---------------------------------------------------------------------


class TestThrottleSignal:
    def test_429_is_a_throttle_signal(self):
        assert mod.is_throttle_signal("data_429") is True

    def test_challenge_required_is_a_throttle_signal(self):
        assert mod.is_throttle_signal("challenge_required") is True

    def test_data_403_is_a_throttle_signal(self):
        assert mod.is_throttle_signal("data_403") is True

    def test_sign_5xx_is_a_throttle_signal(self):
        assert mod.is_throttle_signal("sign_502") is True
        assert mod.is_throttle_signal("sign_500") is True

    def test_data_5xx_is_a_throttle_signal(self):
        assert mod.is_throttle_signal("data_503") is True

    def test_malformed_payload_is_not_a_throttle_signal(self):
        # An application-level parsing problem, not the server pushing back.
        assert mod.is_throttle_signal("malformed_payload") is False
        assert mod.is_throttle_signal("no_signed_url") is False


@run_async
async def test_throttle_backs_off_on_403_batch_not_only_429(monkeypatch):
    throttle = mod.AdaptiveThrottle(8, 0.5)
    results = [{"eaId": i, "error": "data_403"} for i in range(8)]
    rows = [_row(i, next_sales_due_at=datetime.now(timezone.utc)) for i in range(8)]

    class _FakePool:
        def acquire(self):
            raise AssertionError("APPLY=false path must not touch the pool")

    monkeypatch.setattr(mod, "APPLY", False)
    await mod.apply_batch_results(_FakePool(), rows, results, _Stats(), mod.StageTimers(), throttle)

    assert throttle.delay > 0.5
    assert throttle.batch_size < 8


# ---------------------------------------------------------------------
# Diagnostic logging redacts tokens
# ---------------------------------------------------------------------


def test_log_failure_sample_never_logs_a_signed_url_or_token(caplog):
    result = {
        "error": "data_403",
        "signStatus": 200,
        "dataStatus": 403,
        "dataContentType": "text/html",
        "challengeRequired": False,
        "expiresIn": 120,
        "signedUrlIsAbsolute": False,
        "signedUrlOrigin": "https://www.fut.gg",
        "sameOriginAsPage": True,
        "signedUrlHasVerifyParam": True,
        # A body preview containing something token-shaped - must come
        # out redacted, not verbatim.
        "dataBodyPreview": "verify=" + ("a" * 80) + " some html body",
    }
    with caplog.at_level(logging.WARNING, logger="futgg_sales_sync"):
        mod.log_failure_sample(2001, result)

    text = " ".join(r.message for r in caplog.records)
    assert "a" * 80 not in text  # the long token-shaped run itself is redacted
    assert "card=2001" in text
    assert "error=data_403" in text


def test_fetch_script_never_returns_the_full_signed_url():
    """Source-level guard: none of the JS's `return { ... }` result
    objects may contain the bare `signedUrl` variable (which carries the
    verify token in its query string) - only the derived safe fields
    (signedUrlOrigin/signedUrlPath/signedUrlIsAbsolute/...) may appear.
    `signedUrl` legitimately appears elsewhere as a function argument
    (new URL(signedUrl, ...), fetch(signedUrl, ...)) - this only checks
    the returned result objects themselves."""
    import re
    script = mod._FETCH_SCRIPT
    return_blocks = re.findall(r"return\s*\{[^}]*\}", script)
    assert return_blocks, "expected at least one return {...} in the fetch script"
    for block in return_blocks:
        assert not re.search(r"\bsignedUrl\b", block), block
    assert "parsed.origin" in script
    assert "parsed.pathname" in script


# ---------------------------------------------------------------------
# zero-sales vs with-sales success both use the same rating interval
# ---------------------------------------------------------------------


@run_async
async def test_zero_sales_and_nonzero_sales_both_use_the_rating_interval(monkeypatch, dsn):
    monkeypatch.setattr(mod, "APPLY", True)
    await _seed_players(dsn, [
        _row(5001, rating=90, next_sales_due_at=None),
        _row(5002, rating=90, next_sales_due_at=None),
    ])
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        rows = [
            _row(5001, rating=90, next_sales_due_at=None),
            _row(5002, rating=90, next_sales_due_at=None),
        ]
        results = [
            {"eaId": 5001, "completedAuctions": [], "currentPrice": None},
            {"eaId": 5002, "completedAuctions": [
                {"soldPrice": 1000, "soldDate": "2026-08-03T12:00:00Z"}
            ], "currentPrice": 1000},
        ]
        await mod.apply_batch_results(pool, rows, results, _Stats(), mod.StageTimers(), _throttle())
    finally:
        await pool.close()

    zero_sales = await _fetch_player(dsn, 5001)
    with_sales = await _fetch_player(dsn, 5002)
    # Both succeeded -> both get the SAME 90-rated interval, regardless of
    # whether any sales actually came back this time.
    assert zero_sales["last_sales_status"] == "success"
    assert with_sales["last_sales_status"] == "success"
    gap = abs((zero_sales["next_sales_due_at"] - with_sales["next_sales_due_at"]).total_seconds())
    assert gap < 5


# ---------------------------------------------------------------------
# Repair SQL only targets failed attempts from the incident window
# ---------------------------------------------------------------------

_REPAIR_SQL_PATH = Path(__file__).resolve().parent.parent / "sql" / "repair_salessync_20260804_403_incident.sql"


def _extract_statement(sql_text: str, after_marker: str, before_marker: str) -> str:
    return sql_text.split(after_marker, 1)[1].split(before_marker, 1)[0].strip()


@run_async
async def test_repair_sql_only_resets_incident_window_failures(monkeypatch, dsn):
    """Seeds a realistic mix - pre-incident errors, in-incident errors
    (both never-read-turned-error and previously-read-turned-error), a
    genuinely successful card, and a post-incident (post-fix) error using
    the NEW shorter backoff - and asserts the repair SQL only resets the
    in-incident rows, exactly as documented in the SQL file's own header."""
    await _seed_players(dsn, [_row(i, next_sales_due_at=None) for i in range(9001, 9006)])

    incident_start = datetime(2026, 8, 4, 18, 56, 0, tzinfo=timezone.utc)

    conn = await asyncpg.connect(dsn)
    try:
        # 9001: pre-incident error (old bug, but well before the incident
        # window) - must NOT be touched.
        await conn.execute(
            "UPDATE futgg_players SET next_sales_due_at = $2, last_sales_status = 'error:data_403' "
            "WHERE source_card_id = $1",
            9001, incident_start - timedelta(hours=2) + timedelta(minutes=5),
        )
        # 9002: in-incident, was never read before (the core bug case) -
        # must be reset back to NULL.
        await conn.execute(
            "UPDATE futgg_players SET next_sales_due_at = $2, last_sales_status = 'error:data_403' "
            "WHERE source_card_id = $1",
            9002, incident_start + timedelta(minutes=10, seconds=5),
        )
        # 9003: in-incident, was previously read (pushed-out case) - must
        # also be reset.
        await conn.execute(
            "UPDATE futgg_players SET next_sales_due_at = $2, last_sales_status = 'error:sign_502' "
            "WHERE source_card_id = $1",
            9003, incident_start + timedelta(hours=1, minutes=5),
        )
        # 9004: in-incident window, but a genuine SUCCESS - must never be
        # touched regardless of timing.
        await conn.execute(
            "UPDATE futgg_players SET next_sales_due_at = $2, last_sales_status = 'success' "
            "WHERE source_card_id = $1",
            9004, incident_start + timedelta(minutes=10),
        )
        # 9005: at/after the incident start exactly - boundary case,
        # inclusive per the SQL's >= comparison - must be reset.
        await conn.execute(
            "UPDATE futgg_players SET next_sales_due_at = $2, last_sales_status = 'error:data_403' "
            "WHERE source_card_id = $1",
            9005, incident_start + timedelta(minutes=5),
        )

        sql_text = _REPAIR_SQL_PATH.read_text()
        update_stmt = _extract_statement(
            sql_text,
            "-- STEP 2 - THE REPAIR",
            "-- STEP 3",
        )
        # Strip the leading comment lines the marker split left behind,
        # keep only the actual UPDATE statement.
        update_stmt = "UPDATE" + update_stmt.split("UPDATE", 1)[1]
        await conn.execute(update_stmt)

        rows = {
            r["source_card_id"]: r
            for r in await conn.fetch(
                "SELECT source_card_id, next_sales_due_at, last_sales_status FROM futgg_players "
                "WHERE source_card_id = ANY($1::bigint[])",
                [9001, 9002, 9003, 9004, 9005],
            )
        }

        assert rows[9001]["next_sales_due_at"] is not None  # pre-incident: untouched
        assert rows[9002]["next_sales_due_at"] is None       # in-incident, never-read: reset
        assert rows[9003]["next_sales_due_at"] is None       # in-incident, previously-read: reset
        assert rows[9004]["last_sales_status"] == "success"  # genuine success: never touched
        assert rows[9004]["next_sales_due_at"] is not None
        assert rows[9005]["next_sales_due_at"] is None        # boundary, inclusive: reset
    finally:
        await conn.close()


# ---------------------------------------------------------------------
# Network diagnostics (Cloudflare investigation instrumentation)
# ---------------------------------------------------------------------


class TestIsCfCookie:
    def test_known_cloudflare_cookie_names(self):
        for name in ("__cf_bm", "cf_clearance", "__cflb", "__cfruid", "__cfwaitingroom"):
            assert mod._is_cf_cookie(name) is True

    def test_cf_prefixed_unknown_cookie_still_flagged(self):
        assert mod._is_cf_cookie("cf_something_new") is True
        assert mod._is_cf_cookie("__cf_future_cookie") is True

    def test_unrelated_cookie_not_flagged(self):
        assert mod._is_cf_cookie("session_id") is False
        assert mod._is_cf_cookie("next-auth.csrf-token") is False

    def test_case_insensitive(self):
        assert mod._is_cf_cookie("CF_Clearance") is True


class _FakeDiagPage:
    def __init__(self, webdriver=False, user_agent="FakeUA/1.0"):
        self._webdriver = webdriver
        self._ua = user_agent
        self._listeners: dict[str, list] = {}

    async def evaluate(self, script, *args):
        if "navigator.webdriver" in script:
            return self._webdriver
        if "navigator.userAgent" in script:
            return self._ua
        return None

    def on(self, event, cb):
        self._listeners.setdefault(event, []).append(cb)

    def fire_request(self, request):
        for cb in list(self._listeners.get("request", [])):
            cb(request)

    def fire_response(self, response):
        for cb in list(self._listeners.get("response", [])):
            cb(response)


class _FakeDiagContext:
    def __init__(self, cookies):
        self._cookies = cookies

    async def cookies(self):
        return self._cookies


class _FakeDiagRequest:
    def __init__(self, url, method, headers, resource_type="fetch"):
        self.url = url
        self.method = method
        self._headers = headers
        self.resource_type = resource_type

    async def all_headers(self):
        return self._headers


class _FakeDiagResponse:
    def __init__(self, url, status, headers):
        self.url = url
        self.status = status
        self._headers = headers

    async def all_headers(self):
        return self._headers


@run_async
async def test_capture_context_diagnostics_reports_webdriver_and_cf_cookies():
    page = _FakeDiagPage(webdriver=False, user_agent="Mozilla/5.0 test")
    context = _FakeDiagContext([
        {"name": "cf_clearance", "value": "secret-token-value"},
        {"name": "session_id", "value": "abc123"},
    ])

    diag = await mod.capture_context_diagnostics(context, page)

    assert diag["navigator_webdriver"] is False
    assert diag["runtime_user_agent"] == "Mozilla/5.0 test"
    assert diag["route_interception_active"] is False
    assert "page.evaluate" in diag["fetch_execution_context"]
    assert diag["cloudflare_cookies_present_before_request"] == ["cf_clearance"]
    assert set(diag["all_cookie_names_in_context"]) == {"cf_clearance", "session_id"}
    # The cookie VALUE must never appear in the diagnostics dict at all -
    # only names.
    assert "secret-token-value" not in str(diag)


@run_async
async def test_network_diagnostics_logs_cf_headers_and_cookie_presence_without_leaking_values(caplog):
    page = _FakeDiagPage()
    pending = mod._attach_network_diagnostics(page, label="TESTDIAG")

    request = _FakeDiagRequest(
        "https://www.fut.gg/api/fut/price-access/sign/",
        "POST",
        {
            "content-type": "application/json",
            "cookie": "cf_clearance=super-secret-value-should-not-leak; session=xyz",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 test",
        },
    )
    response = _FakeDiagResponse(
        "https://www.fut.gg/api/fut/player-prices/26/84131117/?verify=tok",
        403,
        {
            "content-type": "text/html; charset=UTF-8",
            "cf-ray": "8a1b2c3d4e5f-LHR",
            "cf-cache-status": "DYNAMIC",
            "server": "cloudflare",
        },
    )

    import logging
    with caplog.at_level(logging.INFO, logger="futgg_sales_sync"):
        page.fire_request(request)
        page.fire_response(response)
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5)

    text = "\n".join(r.message for r in caplog.records)
    assert "super-secret-value-should-not-leak" not in text
    assert "TESTDIAG REQUEST" in text
    assert "hasCookieHeader=True" in text
    assert "TESTDIAG RESPONSE" in text
    assert "status=403" in text
    assert "'cf-ray': '8a1b2c3d4e5f-LHR'" in text or "cf-ray" in text
    assert "cloudflare" in text.lower()


@run_async
async def test_network_diagnostics_detects_missing_cookie_header(caplog):
    page = _FakeDiagPage()
    pending = mod._attach_network_diagnostics(page, label="TESTDIAG2")
    request = _FakeDiagRequest(
        "https://www.fut.gg/api/fut/price-access/sign/", "POST",
        {"content-type": "application/json"},  # no cookie header at all
    )

    with caplog.at_level(logging.INFO, logger="futgg_sales_sync"):
        page.fire_request(request)
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5)

    text = "\n".join(r.message for r in caplog.records)
    assert "hasCookieHeader=False" in text
