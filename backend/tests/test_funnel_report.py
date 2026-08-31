# tests/test_funnel_report.py
#
# The conversion report.
#
# Collecting events without making them usable is most of the cost of
# analytics and none of the value. What these tests protect is the part
# that is easy to get quietly wrong: counting page views as people,
# counting a browser event as revenue, and counting one enthusiastic user
# as eleven.
from __future__ import annotations

import asyncio
import inspect
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import funnel


def run(coro):
    return asyncio.run(coro)


class FakeConn:
    """Answers each query with a canned result, keyed by a marker in the SQL."""

    def __init__(self, counts=None, activation=None, medians=None, actives=None, trend=None):
        self.counts = counts or []
        self.activation = activation or {"signups": 0, "activated": 0}
        self.medians = medians or {"hours_to_action": None, "hours_to_checkout": None}
        self.actives = actives or []
        self.trend = trend or []
        self.queries = []

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        if "GROUP BY event, source" in sql:
            return self.counts
        if "props->>'tier'" in sql:
            return self.actives
        if "::date AS day" in sql:
            return self.trend
        return []

    async def fetchrow(self, sql, *args):
        self.queries.append(sql)
        if "cohort" in sql:
            return self.activation
        if "PERCENTILE_CONT" in sql:
            return self.medians
        return None


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class Ctx:
            async def __aenter__(self): return conn
            async def __aexit__(self, *a): return False

        return Ctx()


def count_row(event, source="client", events=0, users=0):
    return {"event": event, "source": source, "events": events, "users": users}


def report(**kwargs):
    conn = FakeConn(**kwargs)
    result = run(funnel.conversion_report("30d", pool=FakePool(conn)))
    return result, conn


class TestWindows:
    @pytest.mark.parametrize("window,days", [("7d", 7), ("30d", 30), ("90d", 90)])
    def test_the_presets_resolve(self, window, days):
        frm, to, label = funnel.resolve_window(window)
        assert label == window
        assert (to - frm).days in (days - 1, days)

    def test_today_is_the_utc_day_not_a_sliding_24_hours(self):
        # A daily report that slides is impossible to compare with
        # yesterday's.
        frm, to, label = funnel.resolve_window("today")
        assert label == "today"
        assert (frm.hour, frm.minute, frm.second) == (0, 0, 0)

    def test_a_custom_range_wins_over_the_preset(self):
        frm, to, label = funnel.resolve_window("7d", date(2026, 1, 1), date(2026, 1, 31))
        assert label == "2026-01-01..2026-01-31"
        assert frm.date() == date(2026, 1, 1)

    def test_a_backwards_range_is_corrected_rather_than_returning_nothing(self):
        frm, to, _ = funnel.resolve_window("7d", date(2026, 1, 31), date(2026, 1, 1))
        assert frm.date() == date(2026, 1, 1)
        assert to.date() == date(2026, 1, 31)

    def test_an_enormous_range_is_clamped(self):
        # One request must not be able to scan the whole table.
        frm, to, _ = funnel.resolve_window("7d", date(2000, 1, 1), date(2026, 1, 1))
        assert (to - frm).days <= funnel.MAX_WINDOW_DAYS


class TestRatesAreHonestAboutEmptiness:
    def test_no_data_gives_none_not_zero_percent(self):
        # "No signups yet" and "signups that all failed to convert" are
        # different facts; 0% for both is lying about one of them.
        assert funnel._rate(0, 0) is None

    def test_a_real_rate_is_a_percentage(self):
        assert funnel._rate(1, 4) == 25.0


class TestSignedOutEventsAreNotPeople:
    def test_they_are_reported_as_event_counts(self):
        result, _ = report(counts=[
            count_row("signup_viewed", events=900, users=0),
            count_row("signup_started", events=120, users=0),
        ])
        assert result["signed_out"]["signup_page_views"] == 900
        assert result["signed_out"]["signup_starts"] == 120

    def test_and_labelled_so_nobody_reads_them_as_visitors(self):
        result, _ = report()
        note = result["signed_out"]["note"]
        assert "not unique people" in note

    def test_nothing_attempts_to_identify_an_anonymous_visitor(self):
        # No fingerprinting, and no invented session id.
        #
        # Executable lines only: this module's own docstring says "no
        # fingerprinting", and a naive substring search over the raw
        # source matches the promise rather than a violation of it.
        code = "\n".join(
            line for line in inspect.getsource(funnel).splitlines()
            if not line.strip().startswith("#")
        )
        for forbidden in ("fingerprint", "ip_address", "user_agent", "device_id"):
            assert forbidden not in code.lower(), forbidden


class TestPeopleAreCountedOnce:
    def test_user_scoped_metrics_use_distinct_users_not_event_counts(self):
        # One person opening the pricing page eleven times is one person
        # considering an upgrade.
        result, _ = report(counts=[count_row("pricing_views", events=0, users=0),
                                   count_row("pricing_viewed", events=110, users=10)])
        assert result["upgrade"]["pricing_views"] == 10

    def test_the_counting_query_actually_says_distinct(self):
        _, conn = report()
        counts_sql = next(q for q in conn.queries if "GROUP BY event, source" in q)
        assert "COUNT(DISTINCT user_id)" in counts_sql

    def test_activation_is_a_cohort_not_a_window_intersection(self):
        # Counting activations in the window against signups in the window
        # would credit this month's signups with last month's members
        # finally doing something.
        _, conn = report()
        sql = next(q for q in conn.queries if "cohort" in q)
        assert "signup_completed" in sql
        assert "EXISTS" in sql


class TestRevenueComesFromStripe:
    def test_confirmed_subscriptions_count_only_server_rows(self):
        result, _ = report(counts=[
            # A browser claiming eight completions...
            count_row("checkout_completed", source="client", events=8, users=8),
            # ...against three Stripe confirmations.
            count_row("subscription_confirmed", source="server", events=3, users=3),
            count_row("checkout_started", events=10, users=10),
            count_row("signup_completed", events=40, users=40),
        ])
        assert result["upgrade"]["subscriptions_confirmed"] == 3
        assert result["upgrade"]["returned_from_checkout"] == 8
        # The rates use the confirmed number, not the browser's.
        assert result["upgrade"]["checkout_completion_rate"] == 30.0
        assert result["upgrade"]["signup_to_elite_rate"] == 7.5

    def test_a_client_row_for_a_server_event_is_not_counted_as_revenue(self):
        # Defence in depth: the endpoint refuses these, but if one ever
        # existed it must not become revenue.
        result, _ = report(counts=[
            count_row("subscription_confirmed", source="client", events=99, users=99),
            count_row("checkout_started", events=10, users=10),
        ])
        assert result["upgrade"]["subscriptions_confirmed"] == 0

    def test_the_payload_says_which_number_is_revenue(self):
        result, _ = report()
        note = result["upgrade"]["note"]
        assert "Stripe webhooks" in note
        assert "browser event" in note

    def test_the_daily_trend_uses_the_server_confirmation_too(self):
        _, conn = report()
        sql = next(q for q in conn.queries if "::date AS day" in q)
        assert "subscription_confirmed" in sql
        assert "source = 'server'" in sql


class TestActivation:
    def test_the_definition_is_in_code_and_in_the_payload(self):
        result, _ = report(activation={"signups": 100, "activated": 42})
        assert result["activation"]["definition"] == list(funnel.ACTIVATION_EVENTS)
        assert result["activation"]["activation_rate"] == 42.0

    def test_signing_up_is_not_itself_activation(self):
        assert "signup_completed" not in funnel.ACTIVATION_EVENTS
        assert "pricing_viewed" not in funnel.ACTIVATION_EVENTS

    def test_it_names_things_the_product_is_actually_for(self):
        assert set(funnel.ACTIVATION_EVENTS) == {
            "free_opportunity_viewed",
            "first_watchlist_card_added",
            "first_alert_created",
            "first_portfolio_trade_added",
        }

    def test_an_empty_cohort_reports_no_rate_rather_than_zero(self):
        result, _ = report(activation={"signups": 0, "activated": 0})
        assert result["activation"]["activation_rate"] is None


class TestTiming:
    def test_medians_are_reported_in_hours(self):
        result, _ = report(medians={"hours_to_action": 3.5, "hours_to_checkout": 71.25})
        assert result["timing"]["median_hours_to_first_action"] == 3.5
        assert result["timing"]["median_hours_to_checkout"] == 71.25

    def test_no_data_is_none_not_zero(self):
        result, _ = report()
        assert result["timing"]["median_hours_to_first_action"] is None

    def test_the_query_uses_a_median_not_an_average(self):
        # One member converting four months later would drag a mean into
        # meaninglessness.
        _, conn = report()
        sql = next(q for q in conn.queries if "PERCENTILE_CONT" in q)
        assert "AVG(" not in sql
        assert "PERCENTILE_CONT(0.5)" in sql

    def test_actions_before_signup_are_excluded(self):
        _, conn = report()
        sql = next(q for q in conn.queries if "PERCENTILE_CONT" in q)
        assert "f.occurred_at >= s.signed_up_at" in sql


class TestActiveMembers:
    def test_free_and_elite_are_reported_separately(self):
        result, _ = report(actives=[
            {"tier": "free", "users": 220},
            {"tier": "elite", "users": 37},
        ])
        assert result["active_members"] == {"free": 220, "elite": 37}

    def test_tier_comes_from_the_event_not_a_second_entitlement_query(self):
        _, conn = report()
        sql = next(q for q in conn.queries if "props->>'tier'" in q)
        assert "users" not in sql.lower().split("from")[1].split("where")[0]


class TestTheReportIsAggregated:
    def test_no_query_selects_raw_event_rows(self):
        _, conn = report()
        for sql in conn.queries:
            assert "SELECT *" not in sql
            # Every query is a GROUP BY, an aggregate or a percentile.
            assert re.search(r"COUNT\(|PERCENTILE_CONT|GROUP BY", sql), sql

    def test_the_whole_page_is_five_queries_not_twenty(self):
        # Twenty round trips would make the dashboard the slowest thing in
        # the product.
        _, conn = report()
        assert len(conn.queries) <= 5

    def test_the_report_carries_its_window(self):
        result, _ = report()
        assert result["window"] == "30d"
        assert result["from"] and result["to"]


# ---------------------------------------------------------------------------
# Who can read it
# ---------------------------------------------------------------------------

import os as _os  # noqa: E402

_os.environ.setdefault("DATABASE_URL", "postgresql://placeholder/placeholder")
_os.environ.setdefault("SECRET_KEY", "test-only-placeholder-not-a-real-key")
_os.environ.setdefault("ENV", "test")


def _reporting_client(viewer_ent):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers.admin import require_admin
    from app.routers.admin_reporting import router as reporting_router

    app = FastAPI()
    app.include_router(reporting_router)

    async def _admin():
        from fastapi import HTTPException

        if not viewer_ent.get("user_id"):
            raise HTTPException(401, "Not authenticated")
        if not viewer_ent.get("is_admin"):
            raise HTTPException(403, "Admin only")
        return viewer_ent

    app.dependency_overrides[require_admin] = _admin
    return TestClient(app, raise_server_exceptions=False)


class TestAdminOnly:
    def test_an_anonymous_caller_is_refused(self):
        client = _reporting_client({"user_id": None, "is_admin": False})
        assert client.get("/api/admin/funnel").status_code == 401
        assert client.get("/api/admin/ai-usage").status_code == 401

    def test_an_ordinary_member_is_refused(self):
        client = _reporting_client({"user_id": "u1", "is_admin": False})
        assert client.get("/api/admin/funnel").status_code == 403

    def test_an_elite_member_is_still_refused(self):
        # Paying is not administrating.
        client = _reporting_client({"user_id": "u1", "is_admin": False, "tier": "elite"})
        assert client.get("/api/admin/funnel").status_code == 403

    def test_the_reporting_routes_all_depend_on_require_admin(self):
        from app.routers.admin import require_admin
        from app.routers.admin_reporting import router as reporting_router

        for route in reporting_router.routes:
            dependant = getattr(route, "dependant", None)
            deps = [d.call for d in dependant.dependencies] if dependant else []
            assert require_admin in deps, (
                f"{route.path} does not depend on require_admin - an admin "
                "report that checks admin-ness its own way is a second "
                "answer to a question the entitlement system already owns"
            )

    def test_the_dashboard_is_not_in_customer_navigation(self):
        # It is reachable by an admin who knows the URL, and not offered
        # to anyone else.
        import pathlib

        sidebar = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "v2" / "components" / "Sidebar.jsx"
        if not sidebar.exists():
            pytest.skip("frontend not present in this checkout")
        assert "/admin/funnel" not in sidebar.read_text()


class TestDegradedReporting:
    def test_a_missing_table_reports_a_clear_state_not_a_stack_trace(self, monkeypatch):
        from app.services import funnel as funnel_module

        async def broken(*a, **k):
            raise RuntimeError('relation "funnel_events" does not exist')

        monkeypatch.setattr(funnel_module, "conversion_report", broken)
        client = _reporting_client({"user_id": "admin", "is_admin": True})
        resp = client.get("/api/admin/funnel")
        assert resp.status_code == 503
        body = resp.json()["detail"]
        assert body["error"] == "funnel_unavailable"
        # Names the migration, not the exception.
        assert "075" in body["message"]
        assert "relation" not in body["message"]

    def test_a_bad_window_is_rejected_before_any_query(self):
        client = _reporting_client({"user_id": "admin", "is_admin": True})
        resp = client.get("/api/admin/funnel", params={"window": "all-time"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "bad_window"
