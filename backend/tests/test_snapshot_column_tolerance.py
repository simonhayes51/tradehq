"""
The provider must not name a column the view might not have.

WHY THIS EXISTS
---------------
Adding bin_checked_at / sales_p10 / sales_p90 / sales_interdecile_ratio
to _SNAPSHOT_COLUMNS fixed a real bug - the engine had been reading None
for all of them - and introduced a worse one.

Every futgg_market_snapshot migration carries `requires-column` guards
and SKIPS itself when auto_sync has not yet created the columns it reads
(see 050's header, and the run_migrations tests that exercise exactly
that path). So "the migration has not applied" is a supported, expected
state, and migration 050 promised it explicitly: "a database still on
047's view shape keeps working exactly as it did, just without the new
gate."

Naming a missing column in the SELECT breaks that promise in the loudest
possible way. It is not a degraded read - it is UndefinedColumnError on
EVERY provider query, so the players list, opportunities, trade finder
and dashboard all fail together. Deploying the backend ahead of its
migration, or onto a database where the migration deferred, would take
the whole market layer down.

The columns are optional to the engine by design - it treats each as
"gate not applicable" when absent - so narrowing the SELECT to what the
view actually has degrades exactly as intended.
"""
from __future__ import annotations

import asyncio
import functools
import re

import pytest


def sync(fn):
    """This repo configures no async pytest plugin - see auto_sync/tests
    for the same wrapper."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.new_event_loop().run_until_complete(fn(*args, **kwargs))
    return wrapper

from app.services import market_data_provider as mdp


class _FakeConn:
    """Reports a fixed set of view columns, like pg_attribute would."""

    def __init__(self, columns):
        self._columns = columns
        self.queries = []

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        return [{"attname": c} for c in self._columns]


@pytest.fixture(autouse=True)
def _reset_cache():
    mdp._snapshot_columns_sql = None
    mdp._snapshot_has_checked_at = None
    yield
    mdp._snapshot_columns_sql = None
    mdp._snapshot_has_checked_at = None


def _all_wanted():
    return [c.strip() for c in mdp._SNAPSHOT_COLUMNS.replace("\n", " ").split(",") if c.strip()]


class TestColumnResolution:
    @sync
    async def test_a_fully_migrated_view_selects_everything(self):
        conn = _FakeConn(_all_wanted())
        resolved = await mdp.resolve_snapshot_columns(conn)
        assert set(re.findall(r"[a-z0-9_]+", resolved)) == set(_all_wanted())

    @sync
    async def test_a_pre_052_view_drops_only_the_missing_optional_column(self):
        """The exact deploy hazard: backend ahead of its migration."""
        wanted = [c for c in _all_wanted() if c != "bin_checked_at"]
        resolved = await mdp.resolve_snapshot_columns(_FakeConn(wanted))
        assert "bin_checked_at" not in resolved
        assert "current_bin" in resolved
        assert "sales_interdecile_ratio" in resolved

    @sync
    async def test_a_pre_050_view_drops_that_migrations_columns_too(self):
        wanted = [
            c for c in _all_wanted()
            if c not in {"sales_p10", "sales_p90", "sales_interdecile_ratio", "bin_checked_at"}
        ]
        resolved = await mdp.resolve_snapshot_columns(_FakeConn(wanted))
        for dropped in ("sales_p10", "sales_p90", "sales_interdecile_ratio", "bin_checked_at"):
            assert dropped not in resolved
        assert "sales_median" in resolved

    @sync
    async def test_it_warns_rather_than_dropping_columns_silently(self, caplog):
        wanted = [c for c in _all_wanted() if c != "bin_checked_at"]
        with caplog.at_level("WARNING"):
            await mdp.resolve_snapshot_columns(_FakeConn(wanted))
        assert "bin_checked_at" in caplog.text

    @sync
    async def test_a_missing_REQUIRED_column_is_an_error_not_a_shrug(self, caplog):
        """Dropping an optional column is a supported degradation.
        Dropping current_bin is a broken view, and must not be quietly
        absorbed into a narrower SELECT."""
        wanted = [c for c in _all_wanted() if c != "current_bin"]
        with caplog.at_level("ERROR"):
            await mdp.resolve_snapshot_columns(_FakeConn(wanted))
        assert "current_bin" in caplog.text

    @sync
    async def test_display_name_and_full_name_are_selected_on_a_migrated_view(self):
        # Regression: migration 065 added display_name/full_name to
        # futgg_market_snapshot so user-facing surfaces could prefer
        # FUT.GG's short name ("Marquinhos") over the Player Information
        # full legal name ("Marcos Aoás Corrêa") - but _SNAPSHOT_COLUMNS
        # was never updated to actually request them, so
        # get_player()/get_players_by_ids() silently never selected
        # either column. _serialize_player()'s COALESCE(display_name,
        # name) always saw display_name=None as a result and fell back
        # to the full name for every single player, regardless of how
        # well-populated the database's own display_name column was.
        resolved = await mdp.resolve_snapshot_columns(_FakeConn(_all_wanted()))
        assert "display_name" in resolved
        assert "full_name" in resolved

    @sync
    async def test_a_pre_065_view_drops_display_name_and_full_name_only(self):
        wanted = [c for c in _all_wanted() if c not in {"display_name", "full_name"}]
        resolved = await mdp.resolve_snapshot_columns(_FakeConn(wanted))
        assert "display_name" not in resolved
        assert "full_name" not in resolved
        assert "current_bin" in resolved

    @sync
    async def test_the_view_is_inspected_once_per_process(self):
        conn = _FakeConn(_all_wanted())
        await mdp.resolve_snapshot_columns(conn)
        await mdp.resolve_snapshot_columns(conn)
        await mdp.resolve_snapshot_columns(conn)
        assert len(conn.queries) == 1


class TestPriceAgeSql:
    @sync
    async def test_it_uses_both_clocks_when_the_view_has_both(self):
        await mdp.resolve_snapshot_columns(_FakeConn(_all_wanted()))
        assert mdp.price_age_sql() == "coalesce(bin_checked_at, bin_captured_at)"

    @sync
    async def test_it_never_names_bin_checked_at_on_a_pre_052_view(self):
        """This expression goes into WHERE clauses and the freshness
        summary. Naming the column there fails just as hard as naming it
        in the SELECT."""
        wanted = [c for c in _all_wanted() if c != "bin_checked_at"]
        await mdp.resolve_snapshot_columns(_FakeConn(wanted))
        assert mdp.price_age_sql() == "bin_captured_at"
