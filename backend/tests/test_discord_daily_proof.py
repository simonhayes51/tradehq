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
    source_card_id=None,
    evaluated_at=None,
    card_rating=90,
    rarity="Special",
):
    detected = evaluated_at or PERIOD.start - timedelta(hours=30)
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
        "outcome_source_card_id": source_card_id or 12_000 + snapshot_id,
        "snapshot_source_card_id": source_card_id or 12_000 + snapshot_id,
        "player_source_card_id": source_card_id or 12_000 + snapshot_id,
        "name": name,
        "card_rating": card_rating,
        "rarity": rarity,
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


def test_repeated_snapshots_for_same_card_are_consolidated_everywhere():
    earliest = _proof_row(
        "target_hit",
        snapshot_id=1,
        source_card_id=99_001,
        name="Repeated Card",
        entry=1_000,
        outcome_price=1_500,
    )
    later = _proof_row(
        "target_hit",
        snapshot_id=2,
        source_card_id=99_001,
        name="Repeated Card",
        entry=900,
        outcome_price=2_000,
        evaluated_at=earliest["evaluated_at"] + timedelta(hours=1),
    )

    report = build_daily_proof_report([later, earliest], PERIOD)

    assert report["total"] == 1
    assert report["wins"] == 1
    assert report["repeat_snapshots_consolidated"] == 1
    assert report["combined_result"] == str(earliest["net_profit_after_tax"])
    assert [item["snapshot_id"] for item in report["top_wins"]] == [1]


def test_same_player_name_with_distinct_card_versions_remains_distinct():
    rows = [
        _proof_row(
            "target_hit",
            snapshot_id=1,
            source_card_id=99_001,
            name="Same Player",
            card_rating=90,
            rarity="Gold",
        ),
        _proof_row(
            "target_hit",
            snapshot_id=2,
            source_card_id=99_002,
            name="Same Player",
            card_rating=95,
            rarity="Special",
        ),
    ]

    report = build_daily_proof_report(rows, PERIOD)
    embed = build_daily_proof_embed(report, "https://app.eafc.live")
    best_results = next(field for field in embed["fields"] if field["name"] == "BEST RESULTS")

    assert report["total"] == 2
    assert report["repeat_snapshots_consolidated"] == 0
    assert "Same Player** (90 Gold)" in best_results["value"]
    assert "Same Player** (95 Special)" in best_results["value"]


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


class TestAggregatePublishedOutcomes:
    """Pure aggregation tests for the Published Opportunity Track Record
    (app/services/discord_daily_proof.py's aggregate_published_outcomes(),
    called by published_track_record() after fetching + defensibility-
    filtering real rows). This is the hotfix for the Track Record panel
    conflating high-volume internal signal validation with genuine
    published-to-users outcomes."""

    def test_below_minimum_sample_suppresses_percentages(self):
        from app.services.discord_daily_proof import aggregate_published_outcomes

        rows = [_proof_row("target_hit", snapshot_id=i) for i in range(5)]
        result = aggregate_published_outcomes(rows)
        assert result["published_opportunities"] == 5
        assert result["has_enough_data"] is False
        assert result["win_rate_pct"] is None
        assert result["total_profit_after_tax"] is None

    def test_win_rate_and_profit_computed_from_deduplicated_rows(self):
        from app.services.discord_daily_proof import aggregate_published_outcomes

        rows = (
            [_proof_row("target_hit", snapshot_id=i) for i in range(15)]
            + [_proof_row("downside_hit", snapshot_id=100 + i) for i in range(5)]
        )
        result = aggregate_published_outcomes(rows)
        assert result["published_opportunities"] == 20
        assert result["has_enough_data"] is True
        assert result["wins"] == 15
        assert result["losses"] == 5
        assert result["win_rate_pct"] == 75.0
        # Every published row is, by construction, entered - the publish
        # pipeline has never tracked "recommended but never entered".
        assert result["entry_reached"] == 20
        assert result["entry_reached_not_tracked"] is True

    def test_repeated_snapshots_for_the_same_card_count_once(self):
        # A card recommended across several different sent days must not
        # be counted once per day - same "one card, one call" rule the
        # single-day report already applies, extended across the window.
        from app.services.discord_daily_proof import aggregate_published_outcomes

        rows = (
            [_proof_row("target_hit", snapshot_id=1, source_card_id=999)]
            + [_proof_row("target_hit", snapshot_id=2, source_card_id=999)]
            + [_proof_row("target_hit", snapshot_id=i) for i in range(3, 22)]
        )
        result = aggregate_published_outcomes(rows)
        # 21 raw rows, 2 of them the same card -> 20 unique published calls.
        assert result["published_opportunities"] == 20

    def test_median_profit_and_roi_are_real_medians_not_averages(self):
        from app.services.discord_daily_proof import aggregate_published_outcomes

        # 20 modest wins plus one wildly large one - the median must stay
        # near the modest cluster, unlike an unweighted average which the
        # ROI hotfix specifically exists to stop trusting on its own.
        rows = [_proof_row("target_hit", snapshot_id=i, entry=50_000, outcome_price=55_000) for i in range(20)]
        rows.append(_proof_row("target_hit", snapshot_id=999, entry=100, outcome_price=100_000))
        result = aggregate_published_outcomes(rows)
        assert result["published_opportunities"] == 21
        # (55000*0.95 - 50000) / 50000 = 0.045 -> 4.5%
        assert result["median_roi_pct"] == pytest.approx(4.5, abs=0.5)

    def test_median_time_to_target_only_uses_target_hit_rows(self):
        from app.services.discord_daily_proof import aggregate_published_outcomes

        rows = [_proof_row("target_hit", snapshot_id=i) for i in range(10)]
        rows += [_proof_row("flat", snapshot_id=100 + i) for i in range(10)]
        result = aggregate_published_outcomes(rows)
        # _proof_row's target_hit rows are all 20 minutes to target.
        assert result["median_time_to_target_minutes"] == 20


class _EmptyProofLedgerConn:
    """Minimal fake conn for published_track_record()'s two lookup
    queries, standing in for a database where daily proof has never
    published anything - proves the honest-empty-state path (no
    fabricated numbers) without needing a real Postgres instance."""

    def __init__(self, earliest_sent_date=None):
        self.earliest_sent_date = earliest_sent_date

    async def fetch(self, query, *args):
        assert "discord_daily_proof_publications" in query
        assert "status = 'sent'" in query
        return []

    async def fetchval(self, query, *args):
        assert "min(report_date)" in query
        return self.earliest_sent_date


class _EmptyProofLedgerPool:
    def __init__(self, earliest_sent_date=None):
        self._conn = _EmptyProofLedgerConn(earliest_sent_date)

    def acquire(self):
        conn = self._conn

        class Lease:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return Lease()


def test_published_track_record_is_honest_when_nothing_has_ever_been_sent():
    from app.services.discord_daily_proof import published_track_record

    pool = _EmptyProofLedgerPool(earliest_sent_date=None)
    result = asyncio.run(published_track_record(pool, window_days=90))
    assert result == {
        "has_published_data": False,
        "tracking_since": None,
        "window_days": 90,
    }


def test_published_track_record_reports_tracking_since_outside_the_window():
    from app.services.discord_daily_proof import published_track_record

    earliest = datetime(2025, 1, 1, tzinfo=timezone.utc).date()
    pool = _EmptyProofLedgerPool(earliest_sent_date=earliest)
    result = asyncio.run(published_track_record(pool, window_days=7))
    assert result["has_published_data"] is False
    assert result["tracking_since"] == earliest.isoformat()


class _FakeProofLedgerConn:
    """Fake conn supporting published_track_record()/
    published_recent_wins_and_losses()'s two query shapes: the sent-
    publications list, and per-day _SELECT_DAILY_OUTCOMES fetches keyed
    by report_date -> rows."""

    def __init__(self, pubs, rows_by_date):
        self._pubs = pubs
        self._rows_by_date = rows_by_date

    async def fetch(self, query, *args):
        if "discord_daily_proof_publications" in query:
            return self._pubs
        assert "o.graded_at >= $1 AND o.graded_at < $2" in query
        period_start = args[0]
        for report_date, rows in self._rows_by_date.items():
            if report_date == period_start.date():
                return rows
        return []

    async def fetchval(self, query, *args):
        return None


class _FakeProofLedgerPool:
    def __init__(self, pubs, rows_by_date):
        self._conn = _FakeProofLedgerConn(pubs, rows_by_date)

    def acquire(self):
        conn = self._conn

        class Lease:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return Lease()


def _pub_row(report_date, start, end):
    return {
        "report_date": report_date, "period_start": start,
        "period_end": end, "timezone_name": "Europe/London",
    }


def test_published_recent_wins_and_losses_are_split_and_sorted_most_recent_first():
    from app.services.discord_daily_proof import published_recent_wins_and_losses

    day1 = datetime(2026, 8, 20, tzinfo=timezone.utc).date()
    day1_start = PERIOD.start
    day1_end = PERIOD.end
    pubs = [_pub_row(day1, day1_start, day1_end)]
    rows_by_date = {
        day1: [
            _proof_row("target_hit", snapshot_id=1, name="Older Win",
                       graded_at=day1_start + timedelta(hours=1)),
            _proof_row("target_hit", snapshot_id=2, name="Newer Win",
                       graded_at=day1_start + timedelta(hours=5)),
            _proof_row("downside_hit", snapshot_id=3, name="A Loss",
                       graded_at=day1_start + timedelta(hours=2)),
        ]
    }
    pool = _FakeProofLedgerPool(pubs, rows_by_date)
    result = asyncio.run(published_recent_wins_and_losses(pool, window_days=90, limit=5))

    assert result["has_published_data"] is True
    win_names = [w["player_name"] for w in result["recent_wins"]]
    assert win_names == ["Newer Win", "Older Win"]  # most recent first
    loss_names = [l["player_name"] for l in result["recent_losses"]]
    assert loss_names == ["A Loss"]


def test_published_recent_wins_and_losses_honest_when_nothing_sent():
    from app.services.discord_daily_proof import published_recent_wins_and_losses

    pool = _FakeProofLedgerPool([], {})
    result = asyncio.run(published_recent_wins_and_losses(pool, window_days=90, limit=5))
    assert result["has_published_data"] is False
    assert result["recent_wins"] == []
    assert result["recent_losses"] == []
