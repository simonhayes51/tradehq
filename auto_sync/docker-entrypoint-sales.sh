#!/usr/bin/env bash
set -euo pipefail

# SalesSync process supervisor.
#
# This entrypoint keeps a headed Chromium environment alive under Xvfb and
# supervises both the detailed-sales worker and the optional bundled bulk-price
# worker. The sales worker now starts through futgg_sales_shadow_worker.py,
# which wraps select_due() only to record read-only information-value shadow
# observations. It returns the original live due rows unchanged and makes zero
# extra FUT.GG sales requests.

XVFB_DISPLAY="${DISPLAY:-:99}"
XVFB_SCREEN="${XVFB_SCREEN:-1920x1080x24}"
RESTART_DELAY_SECONDS="${FUTGG_WORKER_RESTART_DELAY_SECONDS:-10}"
RUN_BULK_WITH_SALES="${FUTGG_RUN_BULK_WITH_SALES:-true}"

XVFB_PID=""
SALES_PID=""
BULK_PID=""
STOP_REQUESTED=0

start_xvfb() {
    if [ "${PLAYWRIGHT_HEADLESS:-false}" = "true" ]; then
        echo "PLAYWRIGHT_HEADLESS=true; Xvfb not required"
        return
    fi
    echo "Starting Xvfb on ${XVFB_DISPLAY} (${XVFB_SCREEN})..."
    Xvfb "${XVFB_DISPLAY}" -screen 0 "${XVFB_SCREEN}" -ac +extension RANDR >/tmp/xvfb.log 2>&1 &
    XVFB_PID=$!
    sleep 1
    if ! kill -0 "$XVFB_PID" 2>/dev/null; then
        echo "FATAL: Xvfb failed to start"
        cat /tmp/xvfb.log || true
        exit 1
    fi
}

start_bulk() {
    if [ "${RUN_BULK_WITH_SALES,,}" = "false" ] || [ "$RUN_BULK_WITH_SALES" = "0" ]; then
        echo "Bundled bulk price sync disabled"
        return
    fi
    echo "Starting bundled FUT.GG bulk price sync..."
    python futgg_bulk_price_sync.py &
    BULK_PID=$!
}

cleanup() {
    if [ -n "$SALES_PID" ] && kill -0 "$SALES_PID" 2>/dev/null; then
        kill "$SALES_PID" 2>/dev/null || true
        wait "$SALES_PID" 2>/dev/null || true
    fi
    if [ -n "$BULK_PID" ] && kill -0 "$BULK_PID" 2>/dev/null; then
        kill "$BULK_PID" 2>/dev/null || true
        wait "$BULK_PID" 2>/dev/null || true
    fi
    if [ -n "$XVFB_PID" ] && kill -0 "$XVFB_PID" 2>/dev/null; then
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

start_xvfb
start_bulk

while [ "$STOP_REQUESTED" = "0" ]; do
    echo "Starting FUT.GG sales sync with read-only information shadow..."
    python futgg_sales_shadow_worker.py &
    SALES_PID=$!

    while kill -0 "$SALES_PID" 2>/dev/null; do
        if [ -n "$XVFB_PID" ] && ! kill -0 "$XVFB_PID" 2>/dev/null; then
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
