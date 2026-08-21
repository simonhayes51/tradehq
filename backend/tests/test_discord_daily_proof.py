import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import app.services.discord_integration as discord_integration
from app.services.discord_integration import DiscordClient, DiscordConfig
from app.services.discord_daily_proof import (
    DailyProofSchedule,
    ReportingPeriod,
    build_daily_proof_embed,
    build_daily_proof_report,
    latest_completed_reporting_period,
    publish_daily_proof_review,
)


PERIOD = ReportingPeriod(
    report_date=datetime(2026, 8, 21).date(),
    start=datetime(2026, 8, 20, 20, tzinfo=timezone.utc),
    end=datetime(2026, 8, 21, 20, tzinfo=timezone.utc),
    timezone_name="Europe/London",
)


def _proof_row(
    status="target_hit",
    *,
    snapshot_id=1,
    name="Example Card",
    entry=1_000,
    outcome_price=None,
    graded_at=None,
):
    detected = PERIOD.start - timedelta(hours=30)
    entry_at = detected + timedelta(minutes=5)
    outcome_at = entry_at + timedelta(minutes=20)
    prices = {
        "target_hit": 1_500,
        "profitable_unrealised": 1_200,
        "flat": 1_053,
        "loss_unrealised": 900,
        "downside_hit": 800,
    }
    outcome_price = outcome_price or prices.get(status, 900)
    result = Decimal(outcome_price) * Decimal("0.95") - Decimal(entry)
    roi = result / Decimal(entry)
    return {
        "snapshot_id": snapshot_id,
        "outcome_source_card_id": 12_000 + snapshot_id,
        "snapshot_source_card_id": 12_000 + snapshot_id,
        "player_source_card_id": 12_000 + snapshot_id,
        "name": name,
        "evaluated_at": detected,
        "graded_at": graded_at or PERIOD.start + timedelta(hours=2),
        "window_end": detected + timedelta(hours=24),
        "entry_achieved": True,
        "entry_at": entry_at,
        "entry_price": entry,
        "entry_observation_verified": True,
        "exit_achieved": status == "target_hit",
        "exit_at": outcome_at if status == "target_hit" else None,
        "realised_sell_price": outcome_price if status == "target_hit" else None,
        "outcome_price": outcome_price,
        "outcome_observed_at": outcome_at,
        "outcome_observation_verified": True,
        "outcome_price_type": "target_exit" if status == "target_hit" else "window_mark",
        "net_profit_after_tax": result,
        "realised_roi": roi,
        "target_hit": status == "target_hit",
        "downside_hit": status == "downside_hit",
        "max_adverse_excursion": Decimal("-0.15")
        if status == "downside_hit"
        else Decimal("-0.02"),
        "minutes_to_target": 20 if status == "target_hit" else None,
        "recommended_sell_target": 1_400,
        "observation_count": 3,
        "outcome_status": status,
        "snapshot_closed": True,
        "currently_live": False,
    }


def test_daily_report_counts_and_aggregates_all_outcomes():
    rows = [
        _proof_row("target_hit", snapshot_id=1),
        _proof_row("profitable_unrealised", snapshot_id=2),
        _proof_row("loss_unrealised", snapshot_id=3),
        _proof_row("downside_hit", snapshot_id=4),
        _proof_row("flat", snapshot_id=5),
    ]
    report = build_daily_proof_report(rows, PERIOD)

    expected = sum((row["net_profit_after_tax"] for row in rows), Decimal("0"))
    assert report["total"] == 5
    assert report["wins"] == 2
    assert report["losses"] == 2
    assert report["flats"] == 1
    assert Decimal(report["win_rate"]) == Decimal("0.4")
    assert Decimal(report["combined_result"]) == expected
    assert Decimal(report["average_result"]) == expected / Decimal("5")
    assert Decimal(report["median_result"]) == rows[4]["net_profit_after_tax"]


def test_insufficient_and_currently_live_outcomes_are_excluded():
    insufficient = _proof_row("loss_unrealised", snapshot_id=1)
    insufficient["outcome_status"] = "insufficient_observations"
    live = _proof_row("target_hit", snapshot_id=2)
    live["currently_live"] = True

    assert build_daily_proof_report([insufficient, live], PERIOD) is None


def test_top_three_wins_are_coin_ranked_and_deterministic():
    rows = [
        _proof_row("target_hit", snapshot_id=1, name="Fourth", outcome_price=1_400),
        _proof_row("target_hit", snapshot_id=2, name="First", outcome_price=2_000),
        _proof_row("target_hit", snapshot_id=3, name="Third", outcome_price=1_600),
        _proof_row("target_hit", snapshot_id=4, name="Second", outcome_price=1_800),
    ]
    forward = build_daily_proof_report(rows, PERIOD)
    reversed_report = build_daily_proof_report(list(reversed(rows)), PERIOD)

    assert [item["name"] for item in forward["top_wins"]] == ["First", "Second", "Third"]
    assert forward["top_wins"] == reversed_report["top_wins"]


def test_worst_loss_is_deterministic():
    rows = [
        _proof_row("loss_unrealised", snapshot_id=1, name="Smaller", outcome_price=900),
        _proof_row("downside_hit", snapshot_id=2, name="Largest", outcome_price=700),
    ]
    report = build_daily_proof_report(rows, PERIOD)
    assert report["worst_loss"]["name"] == "Largest"


def test_historical_backlog_outside_period_is_ignored():
    rows = [
        _proof_row(
            "target_hit",
            snapshot_id=index,
            graded_at=PERIOD.start - timedelta(days=1),
        )
        for index in range(1, 101)
    ]
    assert build_daily_proof_report(rows, PERIOD) is None


def test_daily_embed_uses_review_language_and_disclaimer():
    report = build_daily_proof_report(
        [_proof_row("target_hit"), _proof_row("loss_unrealised", snapshot_id=2)], PERIOD
    )
    embed = build_daily_proof_embed(report, "https://app.eafc.live")
    all_text = " ".join(field["name"] + " " + field["value"] for field in embed["fields"])
    assert embed["title"] == "EAFC LIVE - DAILY TRADING REVIEW"
    assert "COMBINED AFTER-TAX OPPORTUNITY RESULT" in all_text
    assert "not an actual user portfolio" in all_text
    assert "Total profit earned" not in all_text


def test_discord_embed_payload_enforces_nonce_and_disables_mentions():
    client = DiscordClient(
        DiscordConfig("token", None, None, None, "proof-channel", None)
    )
    captured = {}

    async def request(method, path, *, payload=None):
        captured.update({"method": method, "path": path, "payload": payload})
        return 200, {"id": "message-id"}

    client._request = request
    status, _ = asyncio.run(
        client.post_embed(
            "proof-channel", {"title": "Review"}, nonce="daily-nonce", enforce_nonce=True
        )
    )
    assert status == 200
    assert captured["payload"]["nonce"] == "daily-nonce"
    assert captured["payload"]["enforce_nonce"] is True
    assert captured["payload"]["allowed_mentions"] == {"parse": []}


@pytest.mark.parametrize(
    "now,expected_hours",
    [
        (datetime(2026, 3, 29, 20, 30, tzinfo=timezone.utc), 23),
        (datetime(2026, 10, 25, 21, 30, tzinfo=timezone.utc), 25),
    ],
)
def test_london_reporting_period_handles_dst(now, expected_hours):
    period = latest_completed_reporting_period(now, DailyProofSchedule())
    assert period.end.astimezone(DailyProofSchedule().zone).hour == 21
    assert period.start.astimezone(DailyProofSchedule().zone).hour == 21
    assert (period.end - period.start).total_seconds() == expected_hours * 3600


def test_blank_schedule_environment_uses_production_defaults(monkeypatch):
    monkeypatch.setenv("DISCORD_PROOF_REPORT_TIMEZONE", "")
    monkeypatch.setenv("DISCORD_PROOF_REPORT_HOUR", "")
    monkeypatch.setenv("DISCORD_PROOF_REPORT_MINUTE", "")
    assert DailyProofSchedule.from_env() == DailyProofSchedule()


class ProofState:
    def __init__(self, rows, *, enabled_at=None):
        self.rows = rows
        self.enabled_at = enabled_at or PERIOD.start - timedelta(seconds=1)
        self.publication = None
        self.claim_locked = False


class ProofConn:
    def __init__(self, state):
        self.state = state

    def transaction(self):
        state = self.state

        class Transaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                state.claim_locked = False
                return False

        return Transaction()

    async def fetchval(self, query, *args):
        if "pg_try_advisory_xact_lock" in query:
            if self.state.claim_locked:
                return False
            self.state.claim_locked = True
            return True
        if "daily_proof_enabled_at" in query:
            return self.state.enabled_at
        raise AssertionError(f"Unexpected fetchval query: {query}")

    async def fetchrow(self, query, *args):
        if query.startswith("SELECT * FROM discord_daily_proof_publications"):
            return self.state.publication
        if "UPDATE discord_daily_proof_publications" in query and "RETURNING *" in query:
            if not self.state.publication or self.state.publication["status"] != "retryable":
                return None
            self.state.publication["status"] = "sending"
            self.state.publication["attempts"] += 1
            self.state.publication["last_error"] = None
            return self.state.publication
        if "INSERT INTO discord_daily_proof_publications" in query:
            if self.state.publication:
                return None
            report_date, start, end, timezone_name, nonce, payload = args
            self.state.publication = {
                "report_date": report_date,
                "period_start": start,
                "period_end": end,
                "timezone_name": timezone_name,
                "status": "sending",
                "discord_nonce": nonce,
                "payload": payload,
                "attempts": 1,
                "retry_until": datetime(2099, 1, 1, tzinfo=timezone.utc),
                "last_error": None,
            }
            return {"report_date": report_date}
        raise AssertionError(f"Unexpected fetchrow query: {query}")

    async def fetch(self, query, *args):
        assert "o.graded_at >= $1 AND o.graded_at < $2" in query
        assert "s.evaluated_at >= st.daily_proof_enabled_at" in query
        return self.state.rows

    async def execute(self, query, *args):
        if "SET status='abandoned'" in query:
            self.state.publication["status"] = "abandoned"
            return "UPDATE 1"
        report_date, status, message_id, error = args
        assert report_date == self.state.publication["report_date"]
        self.state.publication["status"] = status
        self.state.publication["discord_message_id"] = message_id
        self.state.publication["last_error"] = error
        return "UPDATE 1"


class ProofPool:
    def __init__(self, state):
        self.state = state

    def acquire(self):
        conn = ProofConn(self.state)

        class Lease:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return Lease()


class ProofClient:
    class Config:
        proof_channel_id = "proof-channel"

    config = Config()

    def __init__(self, statuses=(200,)):
        self.statuses = iter(statuses)
        self.posts = []

    async def post_embed(self, channel_id, embed, *, nonce, enforce_nonce):
        self.posts.append((channel_id, embed, nonce, enforce_nonce))
        await asyncio.sleep(0)
        status = next(self.statuses)
        return status, {"id": "discord-message-1"} if status in (200, 201) else {}


def _publish(state, client):
    return asyncio.run(
        publish_daily_proof_review(
            ProofPool(state),
            client,
            now=PERIOD.end + timedelta(minutes=1),
            schedule=DailyProofSchedule(),
        )
    )


def test_no_outcomes_produces_no_post_or_ledger_row():
    state = ProofState([])
    client = ProofClient()
    assert _publish(state, client) is False
    assert client.posts == []
    assert state.publication is None


def test_first_report_waits_for_a_complete_post_enable_period():
    state = ProofState([_proof_row()], enabled_at=PERIOD.start + timedelta(seconds=1))
    client = ProofClient()
    assert _publish(state, client) is False
    assert client.posts == []


def test_same_day_and_restart_cannot_replay_report():
    state = ProofState([_proof_row()])
    first = ProofClient()
    assert _publish(state, first) is True
    assert state.publication["status"] == "sent"
    assert state.publication["discord_message_id"] == "discord-message-1"

    restarted = ProofClient()
    assert _publish(state, restarted) is False
    assert restarted.posts == []


def test_restart_does_not_replay_uncertain_sending_report():
    state = ProofState([_proof_row()])
    state.publication = {
        "report_date": PERIOD.report_date,
        "status": "sending",
        "discord_nonce": "existing",
        "payload": json.dumps({}),
        "attempts": 1,
        "retry_until": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "last_error": None,
    }
    client = ProofClient()
    assert _publish(state, client) is False
    assert client.posts == []


def test_concurrent_workers_cannot_double_publish():
    async def run_workers():
        state = ProofState([_proof_row()])
        client = ProofClient()
        results = await asyncio.gather(
            publish_daily_proof_review(
                ProofPool(state), client, now=PERIOD.end + timedelta(minutes=1)
            ),
            publish_daily_proof_review(
                ProofPool(state), client, now=PERIOD.end + timedelta(minutes=1)
            ),
        )
        return state, client, results

    state, client, results = asyncio.run(run_workers())
    assert sorted(results) == [False, True]
    assert len(client.posts) == 1
    assert state.publication["status"] == "sent"


@pytest.mark.parametrize("failure", [429, 503])
def test_rate_limit_and_server_error_retry_with_same_enforced_nonce(failure):
    state = ProofState([_proof_row()])
    client = ProofClient(statuses=(failure, 200))
    assert _publish(state, client) is False
    assert state.publication["status"] == "retryable"
    assert _publish(state, client) is True
    assert len(client.posts) == 2
    assert client.posts[0][2:] == client.posts[1][2:]
    assert client.posts[0][3] is True


def test_old_individual_proof_publisher_is_removed():
    assert not hasattr(discord_integration, "publish_proof_digest")
    worker_source = inspect.getsource(discord_integration.discord_worker_loop)
    assert "publish_daily_proof_review" in worker_source
    assert "publish_proof_digest" not in worker_source
