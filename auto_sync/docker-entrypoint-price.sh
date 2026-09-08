#!/usr/bin/env bash
set -euo pipefail

# FUT.GG now serves Cloudflare's "Attention Required" page to the
# headless PriceSync browser. SalesSync already avoids the same headless
# signal by running Chromium headed against Xvfb, so PriceSync must use
# the same browser shape instead of advertising HeadlessChrome.

DISPLAY_NUM="${DISPLAY#:}"
SOCKET="/tmp/.X11-unix/X${DISPLAY_NUM}"
LOCK="/tmp/.X${DISPLAY_NUM}-lock"

if [ -e "$LOCK" ]; then
    stale_pid="$(tr -d '[:space:]' < "$LOCK" 2>/dev/null || true)"
    if [ -n "$stale_pid" ] && kill -0 "$stale_pid" 2>/dev/null; then
        echo "FATAL: display ${DISPLAY:-:99} lock ${LOCK} names live PID ${stale_pid}"
        exit 1
    fi
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
        echo "FATAL: Xvfb exited before creating ${SOCKET}"
        exit 1
    fi
    sleep 0.1
done

if [ "$ready" != "1" ]; then
    echo "WARNING: Xvfb socket ${SOCKET} not seen after 2s"
fi

cleanup() {
    if kill -0 "$XVFB_PID" 2>/dev/null; then
        kill "$XVFB_PID" 2>/dev/null || true
        wait "$XVFB_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

export PLAYWRIGHT_HEADLESS=false
exec python futgg_bulk_price_sync.py
