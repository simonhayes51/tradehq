#!/usr/bin/env bash
set -euo pipefail

# Headed Chromium via Xvfb (item: 2026-08-04 headless-detection finding).
#
# Production diagnostics confirmed Cloudflare is challenging
# /api/fut/player-prices/26/<id>/?verify=... based on the browser being
# HEADLESS, not on which transport requests it - the browser's own
# User-Agent Client Hints (navigator.userAgentData, visible in this
# worker's own outbound Google Analytics traffic as
# uafvl=HeadlessChrome;...) leak "HeadlessChrome" regardless of the
# spoofed User-Agent HEADER, and both signed_fetch's injected fetch() and
# page_capture's fully organic page navigation were challenged
# identically while headless. Running headed Chromium against a real
# (virtual) X11 display removes that specific signal, matching the same
# headed-vs-headless behavioural difference already confirmed for
# FUTBIN's Cloudflare in this repo's retired futbin_sbc_sync.py (headless
# got 403, headed got 200 - see Dockerfile.sbc's history and README.md's
# "SBC collector" section). FUTBIN ultimately still blocked headed
# traffic for an UNRELATED reason (a network/IP-level WAF block, not a
# solvable JS challenge) - that is not evidence this won't work for
# FUT.GG's Cloudflare, which is a different site with a different
# configuration; it only means headed mode alone isn't a universal fix
# for every Cloudflare deployment.
#
# Started Xvfb directly instead of using `xvfb-run -a`, same reasoning as
# docker-entrypoint-sbc.sh: `xvfb-run -a` auto-picks a display number by
# scanning /tmp/.X*-lock files, a mechanism known to hang on restricted/
# ephemeral container runtimes. Starting Xvfb ourselves on the fixed
# display already set via ENV DISPLAY=:99, with an explicit (bounded)
# readiness check instead of an unbounded implicit one, avoids that
# failure mode entirely - and fails loudly within ~2s instead of hanging
# forever if Xvfb itself is the thing that's broken in this environment.
DISPLAY_NUM="${DISPLAY#:}"
SOCKET="/tmp/.X11-unix/X${DISPLAY_NUM}"
LOCK="/tmp/.X${DISPLAY_NUM}-lock"

# Clear a stale lock/socket before starting Xvfb.
#
# Production evidence (31 August): "Fatal server error: (EE) Server is
# already active for display 99", Xvfb refusing to start at all, on a
# container that had just been (re)started - meaning the LOCK file
# survived even though nothing from the previous Xvfb process could
# still be running in a fresh container. Xvfb writes /tmp/.XNN-lock
# (containing its own PID) and the socket at $SOCKET, and neither is
# guaranteed to be cleaned up on a SIGKILL or an unclean container exit -
# an ephemeral filesystem does not imply the file is gone before this
# script's next cold start reads it.
#
# This entrypoint is the sole owner of this display in this container, so
# it is always safe to clear both before starting: if a PID the lock
# names is genuinely still alive (kill -0 succeeds), something unexpected
# is running and this fails loudly rather than fighting it; otherwise the
# lock is stale by definition and removing it is exactly what a clean
# shutdown would have done.
if [ -e "$LOCK" ]; then
    stale_pid="$(tr -d '[:space:]' < "$LOCK" 2>/dev/null || true)"
    if [ -n "$stale_pid" ] && kill -0 "$stale_pid" 2>/dev/null; then
        echo "FATAL: display ${DISPLAY:-:99} lock ${LOCK} names PID ${stale_pid}, which is still running - refusing to remove a live lock"
        exit 1
    fi
    echo "Removing stale Xvfb lock ${LOCK} (PID ${stale_pid:-unknown} not running)"
    rm -f "$LOCK"
fi
rm -f "$SOCKET"

echo "Starting Xvfb on display ${DISPLAY:-:99}..."
Xvfb "${DISPLAY:-:99}" -screen 0 1920x1080x24 -nolisten tcp &
XVFB_PID=$!

ready=0
for _ in $(seq 1 20); do
    if [ -e "$SOCKET" ]; then
        ready=1
        break
    fi
    if ! kill -0 "$XVFB_PID" 2>/dev/null; then
        echo "Xvfb process exited before creating ${SOCKET} - see any Xvfb output above for why"
        exit 1
    fi
    sleep 0.1
done

if [ "$ready" = "1" ]; then
    echo "Xvfb ready (socket ${SOCKET} present)"
else
    echo "WARNING: Xvfb socket ${SOCKET} not seen after 2s - continuing anyway; the worker's own browser-launch timeout will fail loudly if this is fatal"
fi

# Railway currently deploys only Dockerfile.sales. Start the bulk price
# worker here as well so current BIN data comes from FUT.GG's captured
# catalogue feed instead of forcing the signed sales worker to carry
# pricing freshness one card at a time.
RUN_BULK_WITH_SALES="${FUTGG_RUN_BULK_WITH_SALES:-true}"
BULK_PID=""
if [ "${RUN_BULK_WITH_SALES,,}" != "false" ] && [ "${RUN_BULK_WITH_SALES}" != "0" ]; then
    echo "Starting bundled bulk price sync..."
    PLAYWRIGHT_HEADLESS=true FUTGG_BULK_APPLY="${FUTGG_BULK_APPLY:-true}" python futgg_bulk_price_sync.py &
    BULK_PID=$!
fi

# Old Railway deployments carried FUTGG_SALES_SELECT_LIMIT=400, which is
# above the live signed-endpoint ceiling and repeatedly trips FUT.GG's
# 429 limit. Keep an escape hatch, but cap the signed path by default now
# that bulk prices are supplied separately.
#
# 60 (and CYCLE_SLEEP_MIN=300 below) were sized only to stop tripping
# 429s, never checked against what the sales cadence actually needs.
# Root-cause review (2026-09-04) found that pairing - 60 cards every
# (~5s active work + 300s forced sleep) - creates a hard ceiling of
# ~709-720 cards/hour EVEN AT A FULLY HEALTHY THROTTLE, against a
# ~1,327/hour steady-state requirement derived from the effective-tier
# population and cadence (CRITICAL 3h/HIGH 12h/NORMAL 24h/LOW 48h/
# DORMANT ~66.7h - see futgg_sales_sync.py's SALES_INTERVALS). That
# shortfall is structural, not a symptom of throttling or recovery
# speed: once a cycle is healthy, the 300s static sleep is ~98% of its
# wall time, dwarfing AdaptiveThrottle's own per-batch pacing - which
# already reacts to real 429 feedback in real time and, after the
# rolling-window recovery fix (same date), no longer needs a large
# static safety margin bolted on top of it to stay stable. Keep the
# cycle small enough that a productive pass is less likely to end by
# exhausting FUT.GG's signed-endpoint rate window; AdaptiveThrottle remains
# the real governor of request RATE.
SALES_SELECT_LIMIT_MAX="${FUTGG_SALES_SELECT_LIMIT_MAX:-90}"
if [ -n "${FUTGG_SALES_SELECT_LIMIT:-}" ]; then
    if [ "${FUTGG_SALES_SELECT_LIMIT}" -gt "${SALES_SELECT_LIMIT_MAX}" ] 2>/dev/null; then
        echo "Capping FUTGG_SALES_SELECT_LIMIT from ${FUTGG_SALES_SELECT_LIMIT} to ${SALES_SELECT_LIMIT_MAX}"
        export FUTGG_SALES_SELECT_LIMIT="${SALES_SELECT_LIMIT_MAX}"
    fi
else
    export FUTGG_SALES_SELECT_LIMIT="${SALES_SELECT_LIMIT_MAX}"
fi

# With bulk prices running, the signed endpoint only needs to refresh
# completed-sales history. Force a real pause between signed batches so
# a clean cycle does not immediately drain FUT.GG's rate window again.
#
# The 2026-09-06 Railway trace showed 90s plus a 120-card cap repeatedly
# produced one useful cycle followed by a wasted 429-only cycle. 180s is
# deliberately slower on paper but should produce more useful reads by
# letting FUT.GG's rate window recover between cycles.
SALES_CYCLE_SLEEP_MIN="${FUTGG_SALES_CYCLE_SLEEP_MIN:-180}"
SALES_CYCLE_SLEEP_FORCE="${FUTGG_SALES_CYCLE_SLEEP_FORCE:-true}"
if [ "${SALES_CYCLE_SLEEP_FORCE,,}" != "false" ] && [ "${SALES_CYCLE_SLEEP_FORCE}" != "0" ]; then
    if [ "${FUTGG_SALES_CYCLE_SLEEP:-}" != "${SALES_CYCLE_SLEEP_MIN}" ]; then
        echo "Setting FUTGG_SALES_CYCLE_SLEEP to ${SALES_CYCLE_SLEEP_MIN} for signed-endpoint recovery"
        export FUTGG_SALES_CYCLE_SLEEP="${SALES_CYCLE_SLEEP_MIN}"
    fi
elif [ -z "${FUTGG_SALES_CYCLE_SLEEP:-}" ]; then
    export FUTGG_SALES_CYCLE_SLEEP="${SALES_CYCLE_SLEEP_MIN}"
fi

: "${FUTGG_SALES_CIRCUIT_BREAKER_BACKOFF_SECONDS:=900}"
: "${FUTGG_SALES_CIRCUIT_BREAKER_MAX_BACKOFF_SECONDS:=3600}"
export FUTGG_SALES_CIRCUIT_BREAKER_BACKOFF_SECONDS
export FUTGG_SALES_CIRCUIT_BREAKER_MAX_BACKOFF_SECONDS

SALES_PID=""
STOP_REQUESTED=0
RESTART_DELAY_SECONDS="${FUTGG_WORKER_RESTART_DELAY_SECONDS:-15}"

start_bulk() {
    echo "Starting bundled bulk price sync..."
    PLAYWRIGHT_HEADLESS=true FUTGG_BULK_APPLY="${FUTGG_BULK_APPLY:-true}" python futgg_bulk_price_sync.py &
    BULK_PID=$!
}

cleanup() {
    if [ -n "$SALES_PID" ] && kill -0 "$SALES_PID" 2>/dev/null; then
        echo "Stopping sales sync..."
        kill "$SALES_PID" 2>/dev/null || true
        wait "$SALES_PID" 2>/dev/null || true
    fi
    if [ -n "$BULK_PID" ] && kill -0 "$BULK_PID" 2>/dev/null; then
        echo "Stopping bundled bulk price sync..."
        kill "$BULK_PID" 2>/dev/null || true
        wait "$BULK_PID" 2>/dev/null || true
    fi
    if kill -0 "$XVFB_PID" 2>/dev/null; then
        kill "$XVFB_PID" 2>/dev/null || true
        wait "$XVFB_PID" 2>/dev/null || true
    fi
}

request_stop() {
    STOP_REQUESTED=1
    cleanup
}

trap request_stop INT TERM
trap cleanup EXIT

while [ "$STOP_REQUESTED" = "0" ]; do
    echo "Starting FUT.GG sales sync..."
    python futgg_sales_sync.py &
    SALES_PID=$!

    while kill -0 "$SALES_PID" 2>/dev/null; do
        if ! kill -0 "$XVFB_PID" 2>/dev/null; then
            echo "FATAL: Xvfb exited; stopping sales sync so the container can restart"
            kill "$SALES_PID" 2>/dev/null || true
            wait "$SALES_PID" 2>/dev/null || true
            SALES_PID=""
            exit 1
        fi
        if [ "${RUN_BULK_WITH_SALES,,}" != "false" ] && [ "$RUN_BULK_WITH_SALES" != "0" ] \
            && { [ -z "$BULK_PID" ] || ! kill -0 "$BULK_PID" 2>/dev/null; }; then
            if [ -n "$BULK_PID" ]; then
                wait "$BULK_PID" 2>/dev/null || true
            fi
            echo "Bundled bulk price sync exited; restarting in ${RESTART_DELAY_SECONDS}s"
            sleep "$RESTART_DELAY_SECONDS"
            start_bulk
        fi
        sleep 5
    done

    if wait "$SALES_PID"; then
        SALES_STATUS=0
    else
        SALES_STATUS=$?
    fi
    SALES_PID=""

    if [ "$STOP_REQUESTED" != "0" ]; then
        break
    fi
    if [ -n "${FUTGG_SALES_DIAGNOSTIC_CARD_ID:-}" ] \
        || [ -n "${FUTGG_SALES_MAX_RUNTIME_MINUTES:-}" ] \
        || [ -n "${FUTGG_SALES_MAX_CYCLES:-}" ]; then
        exit "$SALES_STATUS"
    fi

    echo "FUT.GG sales sync exited unexpectedly with status ${SALES_STATUS}; restarting in ${RESTART_DELAY_SECONDS}s"
    sleep "$RESTART_DELAY_SECONDS"
done

exit 0
