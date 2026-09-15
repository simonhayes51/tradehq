"""
Regression tests for the Coolify Dockerfile-based deploy split
(Dockerfile.player / Dockerfile.price / Dockerfile.sbc + docker-compose.yml
+ COOLIFY.md). No Docker/Compose binary involved - pure text/YAML checks
against files in this repo, so they run anywhere with no network or DB.

What these guard against, concretely:
  - The reported bug: a one-shot script (futgg_player_sync.py /
    easysbc_sbc_sync.py) getting deployed as a normal restart-looping
    service instead of a Coolify Scheduled Task.
  - Silently picking the wrong script (the obsolete per-card
    futgg_price_sync.py instead of futgg_bulk_price_sync.py; the
    Cloudflare-blocked futbin_sbc_sync.py instead of easysbc_sbc_sync.py).
  - A worker Dockerfile/compose service growing a port/EXPOSE it doesn't
    need, or docker-compose.yml growing a second Postgres service.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

AUTO_SYNC_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AUTO_SYNC_DIR.parent


def _read(path: Path) -> str:
    return path.read_text()


# ---------------------------------------------------------------------
# Dockerfile.player
# ---------------------------------------------------------------------


def test_dockerfile_player_runs_the_one_shot_player_sync():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.player")
    assert re.search(r'CMD\s*\[\s*"python"\s*,\s*"futgg_player_sync\.py"\s*\]', text)


def test_dockerfile_player_uses_playwright_base():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.player")
    assert "playwright/python" in text


def test_dockerfile_player_defaults_to_fc27():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.player")
    assert "FUTGG_GAME_YEAR=27" in text


def test_dockerfile_player_exposes_no_port():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.player")
    assert "EXPOSE" not in text


def test_futgg_player_sync_is_actually_one_shot():
    """Guards the Scheduled Task deployment choice: if this script's
    __main__ block ever stops being a single crawl_once() call (e.g. grows
    an outer sleep/repeat loop around it), Dockerfile.player/COOLIFY.md's
    "one-shot, deploy as a Scheduled Task" guidance silently goes stale.
    (crawl_once() itself legitimately contains "while True" internally,
    for retrying a single listing page's pagination - that's not a
    process-level loop, so this only inspects the __main__ block.)"""
    text = _read(AUTO_SYNC_DIR / "futgg_player_sync.py")
    main_block = text.rsplit('if __name__ == "__main__":', 1)[-1]
    assert "asyncio.run(crawl_once())" in main_block
    assert "while True" not in main_block
    assert "asyncio.sleep" not in main_block


def test_futgg_player_sync_uses_game_year_card_identity():
    text = _read(AUTO_SYNC_DIR / "futgg_player_sync.py")
    assert "PRIMARY KEY (game_year, source_card_id)" in text
    assert "ON CONFLICT (game_year, source_card_id)" in text


# ---------------------------------------------------------------------
# Dockerfile.price
# ---------------------------------------------------------------------


def test_dockerfile_price_runs_bulk_price_sync_not_the_obsolete_per_card_scraper():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.price")
    assert re.search(r'CMD\s*\[\s*"python"\s*,\s*"futgg_bulk_price_sync\.py"\s*\]', text)
    assert "futgg_price_sync.py" not in text


def test_dockerfile_price_sets_futgg_bulk_apply():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.price")
    assert "FUTGG_BULK_APPLY=true" in text


def test_dockerfile_price_defaults_to_fc27():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.price")
    assert "FUTGG_GAME_YEAR=27" in text


def test_dockerfile_price_uses_playwright_base():
    # futgg_bulk_price_sync.py loads a real fut.gg page to listen for the
    # bulk feed requests (r2.fut.gg 403s direct/CORS requests) - needs a
    # real browser, unlike SBCSync.
    text = _read(AUTO_SYNC_DIR / "Dockerfile.price")
    assert "playwright/python" in text


def test_dockerfile_price_exposes_no_port():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.price")
    assert "EXPOSE" not in text


def test_futgg_bulk_price_sync_is_actually_long_running():
    """Guards the "use it directly, no loop wrapper" deployment choice: if
    this script ever loses its internal loop, Dockerfile.price/COOLIFY.md's
    "continuous service" guidance silently goes stale and it needs a
    wrapper + Scheduled Task instead. The loop condition is
    shutdown_event-aware (graceful SIGTERM/SIGINT), not a bare `while
    True`, but it is still an unbounded cycle -> sleep -> repeat loop."""
    text = _read(AUTO_SYNC_DIR / "futgg_bulk_price_sync.py")
    assert "asyncio.run(run_forever())" in text
    assert re.search(r"while not shutdown_event\.is_set\(\):", text)
    assert re.search(r"await sleep_or_shutdown\(", text)


# ---------------------------------------------------------------------
# Dockerfile.sbc
# ---------------------------------------------------------------------


def test_dockerfile_sbc_runs_easysbc_not_the_cloudflare_blocked_futbin_scraper():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.sbc")
    assert re.search(r'CMD\s*\[\s*"python"\s*,\s*"easysbc_sbc_sync\.py"\s*\]', text)
    # futbin_sbc_sync.py may legitimately be mentioned in comments
    # explaining why it was replaced - it must not appear in a CMD/RUN
    # instruction actually invoking it.
    cmd_lines = [line for line in text.splitlines() if line.strip().upper().startswith(("CMD", "RUN"))]
    assert not any("futbin_sbc_sync.py" in line for line in cmd_lines)


def test_dockerfile_sbc_does_not_need_playwright():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.sbc")
    assert "FROM python:3.11-slim" in text
    assert "playwright/python" not in text
    # Must not invoke the old Xvfb + headed-Chromium entrypoint.
    assert 'CMD ["/app/docker-entrypoint-sbc.sh"]' not in text


def test_dockerfile_sbc_exposes_no_port():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.sbc")
    assert "EXPOSE" not in text


def test_dockerfile_sbc_installs_only_its_own_minimal_requirements():
    """SBCSync must not install the shared requirements.txt (which also
    pulls in beautifulsoup4/requests/python-dotenv/playwright/tzdata for
    the Playwright-based workers) - only requirements-sbc.txt
    (aiohttp + asyncpg), which easysbc_sbc_sync.py + monitoring.py
    actually import."""
    text = _read(AUTO_SYNC_DIR / "Dockerfile.sbc")
    assert "requirements-sbc.txt" in text
    assert "COPY requirements.txt" not in text
    reqs = _read(AUTO_SYNC_DIR / "requirements-sbc.txt")
    pinned = [
        line.strip() for line in reqs.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert any(p.lower().startswith("aiohttp") for p in pinned)
    assert any(p.lower().startswith("asyncpg") for p in pinned)
    assert not any("playwright" in p.lower() for p in pinned)
    assert not any("beautifulsoup4" in p.lower() for p in pinned)


def test_easysbc_sbc_sync_exits_nonzero_on_failure_zero_on_success():
    """Dockerfile.sbc requires: exit 0 on success, non-zero on a real
    failure - so Coolify's Scheduled Task run history reflects real
    outcomes, not just "the container didn't crash"."""
    text = _read(AUTO_SYNC_DIR / "easysbc_sbc_sync.py")
    main_block = text.rsplit('if __name__ == "__main__":', 1)[-1]
    assert "ok = asyncio.run(crawl_once())" in main_block
    assert "sys.exit(0 if ok else 1)" in main_block
    assert "async def crawl_once() -> bool:" in text


def test_easysbc_sbc_sync_guards_against_overlapping_runs():
    """A Coolify Scheduled Task run that overlaps the previous one (a
    slow run still going, or a manual trigger during a scheduled one)
    must not race writes - guarded by a Postgres advisory lock, same
    idiom futgg_player_sync.py uses."""
    text = _read(AUTO_SYNC_DIR / "easysbc_sbc_sync.py")
    assert "pg_try_advisory_lock" in text
    assert "pg_advisory_unlock" in text
    assert "EASYSBC_LOCK_KEY" in text


def test_easysbc_sbc_sync_is_actually_one_shot():
    """Guards the Scheduled Task deployment choice for SBCSync, same
    reasoning as the PlayerSync check above."""
    text = _read(AUTO_SYNC_DIR / "easysbc_sbc_sync.py")
    assert "asyncio.run(crawl_once())" in text
    body = text.split('if __name__ == "__main__":')[0]
    assert "while True" not in body


# ---------------------------------------------------------------------
# Dockerfile.sales
# ---------------------------------------------------------------------


def test_dockerfile_sales_runs_fc27_policy_then_xvfb_entrypoint_then_sales_sync():
    """CMD now bootstraps Xvfb (headed Chromium - 2026-08-04
    headless-detection finding) via docker-entrypoint-sales.sh rather than
    calling python directly. That script must still end by exec'ing
    futgg_sales_sync.py directly (not `sh -c`, not a second sleep/loop
    wrapper around an already-looping script) - exec replaces the shell
    process so SIGTERM/SIGINT still reach the worker directly for its own
    graceful-shutdown handling, and no wrapper double-loops run_forever()."""
    text = _read(AUTO_SYNC_DIR / "Dockerfile.sales")
    assert re.search(r'CMD\s*\[\s*"bash"\s*,\s*"/app/fc27-sales-policy\.sh"\s*\]', text)
    cmd_lines = [line for line in text.splitlines() if line.strip().upper().startswith("CMD")]
    assert len(cmd_lines) == 1
    assert "sh -c" not in cmd_lines[0]

    policy = _read(AUTO_SYNC_DIR / "fc27-sales-policy.sh")
    assert 'GAME_YEAR="${FUTGG_GAME_YEAR:-27}"' in policy
    assert re.search(r'^exec /app/docker-entrypoint-sales\.sh\s*$', policy, re.MULTILINE)

    entrypoint = _read(AUTO_SYNC_DIR / "docker-entrypoint-sales.sh")
    assert re.search(r'^exec python futgg_sales_sync\.py\s*$', entrypoint, re.MULTILINE)
    # No sleep/loop wrapper around the already-looping script - a real
    # shell `while` loop keyword (not just the word appearing in a
    # comment) would double-loop run_forever().
    code_lines = [
        line for line in entrypoint.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any(re.search(r'\bwhile\b', line) for line in code_lines)


def test_dockerfile_sales_defaults_to_headed_chromium_via_xvfb():
    """PLAYWRIGHT_HEADLESS=false + a real DISPLAY is the fix for the
    confirmed root cause (Cloudflare challenging based on the
    HeadlessChrome signal in navigator.userAgentData, independent of
    transport) - see docker-entrypoint-sales.sh's own comment."""
    text = _read(AUTO_SYNC_DIR / "Dockerfile.sales")
    assert "ENV PLAYWRIGHT_HEADLESS=false" in text
    assert "ENV DISPLAY=:99" in text
    assert "Xvfb" in text


def test_dockerfile_sales_defaults_to_fc27():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.sales")
    assert "FUTGG_GAME_YEAR=27" in text


def test_docker_entrypoint_sales_is_executable():
    path = AUTO_SYNC_DIR / "docker-entrypoint-sales.sh"
    assert path.exists()
    import os
    assert os.access(path, os.X_OK), "docker-entrypoint-sales.sh must be executable (git mode bit)"


def test_dockerfile_sales_uses_playwright_base():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.sales")
    assert "playwright/python" in text


def test_dockerfile_sales_exposes_no_port():
    text = _read(AUTO_SYNC_DIR / "Dockerfile.sales")
    assert "EXPOSE" not in text


def test_futgg_sales_sync_is_actually_long_running():
    """Guards the "use it directly, no loop wrapper" deployment choice,
    same reasoning as the PriceSync check above - if this script's
    main()/__main__ ever stops calling run_forever() for the normal
    (non-diagnostic) path, Dockerfile.sales/COOLIFY.md's "continuous
    service" guidance silently goes stale. main() also supports a
    one-card diagnostic short-circuit (FUTGG_SALES_DIAGNOSTIC_CARD_ID) -
    that's a distinct, explicitly opt-in one-shot mode, not the normal
    deployment path this check guards."""
    text = _read(AUTO_SYNC_DIR / "futgg_sales_sync.py")
    assert "async def run_forever() -> None:" in text
    assert "asyncio.run(main())" in text
    main_fn_block = text.split("async def main() -> bool | None:", 1)[-1].split("\n\n\n", 1)[0]
    assert "await run_forever()" in main_fn_block


# ---------------------------------------------------------------------
# docker-compose.yml
# ---------------------------------------------------------------------


def _compose() -> dict:
    return yaml.safe_load(_read(REPO_ROOT / "docker-compose.yml"))


def test_compose_has_no_second_postgres_service():
    services = _compose()["services"]
    assert "postgres" not in services


def test_compose_drops_the_obsolete_per_card_price_worker():
    services = _compose()["services"]
    assert "futgg_price_worker" not in services


def test_compose_workers_use_the_dedicated_dockerfiles():
    services = _compose()["services"]
    assert services["futgg_player_worker"]["build"]["dockerfile"] == "Dockerfile.player"
    assert services["futgg_bulk_price_worker"]["build"]["dockerfile"] == "Dockerfile.price"
    assert services["futgg_sbc_worker"]["build"]["dockerfile"] == "Dockerfile.sbc"
    assert services["futgg_sales_worker"]["build"]["dockerfile"] == "Dockerfile.sales"


def test_compose_sales_worker_has_no_command_override():
    """SalesSync must not override the container command in compose
    either - Dockerfile.sales already bakes in the correct default CMD,
    matching the "no command overrides in Coolify" requirement."""
    services = _compose()["services"]
    assert "command" not in services["futgg_sales_worker"]


def test_compose_workers_expose_no_ports():
    services = _compose()["services"]
    for name in (
        "futgg_player_worker",
        "futgg_bulk_price_worker",
        "futgg_sbc_worker",
        "futgg_sales_worker",
    ):
        assert "ports" not in services[name], f"{name} must not expose a port"


def test_compose_one_shot_workers_do_not_auto_restart():
    """restart: "no" for the one-shot workers in local-compose mode -
    letting Compose's default restart policy apply here would reproduce
    the exact restart-loop bug this whole change fixes."""
    services = _compose()["services"]
    assert services["futgg_player_worker"]["restart"] == "no"
    assert services["futgg_sbc_worker"]["restart"] == "no"


def test_compose_continuous_workers_do_restart():
    services = _compose()["services"]
    assert services["futgg_bulk_price_worker"]["restart"] == "unless-stopped"
    assert services["futgg_sales_worker"]["restart"] == "unless-stopped"


def test_compose_workers_use_external_database_url():
    services = _compose()["services"]
    for name in (
        "futgg_player_worker",
        "futgg_bulk_price_worker",
        "futgg_sbc_worker",
        "futgg_sales_worker",
    ):
        db_url = services[name]["environment"]["DATABASE_URL"]
        assert db_url == "${DATABASE_URL}"


# ---------------------------------------------------------------------
# COOLIFY.md exists and names all three resources
# ---------------------------------------------------------------------


def test_coolify_doc_covers_all_four_resources():
    text = _read(AUTO_SYNC_DIR / "COOLIFY.md")
    for name in ("PlayerSync", "PriceSync", "SBCSync", "SalesSync"):
        assert name in text
    for dockerfile in ("Dockerfile.player", "Dockerfile.price", "Dockerfile.sbc", "Dockerfile.sales"):
        assert dockerfile in text


def test_coolify_doc_documents_self_healing_price_env_vars():
    text = _read(AUTO_SYNC_DIR / "COOLIFY.md")
    for var in (
        "FUTGG_DISCOVER_URL",
        "FUTGG_DISCOVERY_ENABLED",
        "FUTGG_DISCOVERY_RETRY_COUNT",
        "FUTGG_DISCOVERY_FAILURE_BACKOFF_SECONDS",
    ):
        assert var in text


def test_coolify_doc_documents_sales_setup_exactly():
    text = _read(AUTO_SYNC_DIR / "COOLIFY.md")
    sales_section = text.split("## SalesSync", 1)[1].split("## SBCSync", 1)[0]
    assert "simonhayes51/tradehq" in sales_section
    assert "**Branch:** `main`" in sales_section
    assert "Continuous Application" in sales_section
    assert "Build Pack:** Dockerfile" in sales_section
    assert "Base Directory:** `/auto_sync`" in sales_section
    assert "**Dockerfile:** `/Dockerfile.sales`" in sales_section
    assert "**Ports:** None" in sales_section
    assert "**Domain:** None" in sales_section
    assert "DATABASE_URL" in sales_section
    assert "no command override" in sales_section.lower()


def test_coolify_doc_documents_sales_integrity_env_vars_and_diagnostic_mode():
    text = _read(AUTO_SYNC_DIR / "COOLIFY.md")
    sales_section = text.split("## SalesSync", 1)[1].split("## SBCSync", 1)[0]
    for var in (
        "FUTGG_SALES_APPLY",
        "FUTGG_SALES_FAILURE_RETRY_BACKOFF_MINUTES",
        "FUTGG_SALES_CIRCUIT_BREAKER_THRESHOLD",
        "FUTGG_SALES_CIRCUIT_BREAKER_MIN_SAMPLE",
        "FUTGG_SALES_CIRCUIT_BREAKER_BACKOFF_SECONDS",
        "FUTGG_SALES_DIAGNOSTIC_CARD_ID",
    ):
        assert var in sales_section
    assert "repair_salessync_20260804_403_incident.sql" in sales_section
    assert "CLOUDFLARE_403_INVESTIGATION.md" in sales_section


def test_coolify_doc_documents_sales_transport_env_vars_and_recommendation():
    text = _read(AUTO_SYNC_DIR / "COOLIFY.md")
    sales_section = text.split("## SalesSync", 1)[1].split("## SBCSync", 1)[0]
    for var in (
        "FUTGG_SALES_TRANSPORT",
        "FUTGG_SALES_PAGE_CONCURRENCY",
        "FUTGG_SALES_PAGE_TIMEOUT_MS",
        "FUTGG_SALES_PAGE_SETTLE_MS",
        "FUTGG_SALES_SIGNED_REPROBE_MINUTES",
    ):
        assert var in sales_section
    # Recommended production setting must be explicit, not left implicit.
    assert "`auto`" in sales_section
    assert "recommended" in sales_section.lower()
    # Both diagnostic commands (signed_fetch and page_capture) must be documented.
    assert "FUTGG_SALES_TRANSPORT=signed_fetch" in sales_section
    assert "FUTGG_SALES_TRANSPORT=page_capture" in sales_section
    # Throughput estimates must be clearly labelled as estimates, not a
    # claimed live measurement this sandbox has no network access to take.
    assert "estimate" in sales_section.lower()


def test_cloudflare_investigation_doc_exists_and_covers_both_scripts():
    doc = AUTO_SYNC_DIR / "CLOUDFLARE_403_INVESTIGATION.md"
    assert doc.exists()
    text = doc.read_text()
    assert "futgg_bulk_price_sync.py" in text
    assert "futgg_sales_sync.py" in text
    assert "Just a moment" in " ".join(text.split())
    # Must not overclaim a fix - this is a diagnostics/comparison doc.
    assert "page.evaluate" in text
    assert "page.route" in text


def test_network_diagnostics_helpers_exist_in_sales_sync():
    text = _read(AUTO_SYNC_DIR / "futgg_sales_sync.py")
    for name in (
        "def capture_context_diagnostics(",
        "def _attach_network_diagnostics(",
        "navigator.webdriver",
        "all_headers()",
        "cf-ray",
    ):
        assert name in text


def test_repair_sql_file_exists_and_is_not_auto_run():
    sql_path = AUTO_SYNC_DIR / "sql" / "repair_salessync_20260804_403_incident.sql"
    assert sql_path.exists()
    text = sql_path.read_text()
    assert "2026-08-04 18:56:00" in text
    sql_lines = [
        line for line in text.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    sql_only = " ".join(sql_lines)
    assert "last_sales_status LIKE 'error:%'" in sql_only
    # Every real SQL statement targets error rows only - never a
    # statement that would match/reset a genuinely successful row.
    assert "last_sales_status = 'success'" not in sql_only
    # Not wired into ensure_schema() or any other code path that would
    # run it automatically - it must only ever be run by hand.
    for py_file in AUTO_SYNC_DIR.glob("*.py"):
        assert "repair_salessync_20260804_403_incident" not in py_file.read_text()


def test_coolify_doc_documents_sbc_setup_exactly():
    text = _read(AUTO_SYNC_DIR / "COOLIFY.md")
    sbc_section = text.split("## SBCSync", 1)[1]
    assert "simonhayes51/tradehq" in sbc_section
    assert "**Branch:** `main`" in sbc_section
    assert "Build pack:** Dockerfile" in sbc_section
    assert "python easysbc_sbc_sync.py" in sbc_section
    assert "30 6 * * *" in sbc_section
    assert "EASYSBC_DIAGNOSTIC=true" in sbc_section
