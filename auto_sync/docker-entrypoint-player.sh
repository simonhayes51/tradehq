#!/usr/bin/env bash
set -euo pipefail

# PlayerSync's cron task executes inside a persistent Coolify container.
# A previous run may have left Xvfb alive: the old "exec python" replaced
# the shell so its EXIT trap could never clean up the child Xvfb.
# Never remove an active display socket or kill an Xvfb process we did
# not start. Reuse a functioning pre-existing display instead.
DISPLAY="${DISPLAY:-:99}"
export DISPLAY
DISPLAY_NUM="${DISPLAY#:}"
SOCKET="/tmp/.X11-unix/X${DISPLAY_NUM}"
LOCK="/tmp/.X${DISPLAY_NUM}-lock"
XVFB_PID=""

cleanup() {
    if [ -n "$XVFB_PID" ] && kill -0 "$XVFB_PID" 2>/dev/null; then
        kill "$XVFB_PID" 2>/dev/null || true
        wait "$XVFB_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

if [ -e "$LOCK" ]; then
    stale_pid="$(tr -d '[:space:]' < "$LOCK" 2>/dev/null || true)"
    if [ -n "$stale_pid" ] && kill -0 "$stale_pid" 2>/dev/null; then
        if [ -S "$SOCKET" ]; then
            echo "Reusing running Xvfb on display $DISPLAY (PID $stale_pid)"
        else
            echo "FATAL: display $DISPLAY lock belongs to running PID $stale_pid but socket is missing"
            exit 1
        fi
    else
        echo "Removing stale Xvfb lock $LOCK (PID ${stale_pid:-unknown} not running)"
        rm -f "$LOCK"
        rm -f "$SOCKET"
    fi
elif [ -e "$SOCKET" ]; then
    echo "FATAL: display $DISPLAY socket exists without a lock; refusing to remove a potentially active socket"
    exit 1
fi

if [ ! -S "$SOCKET" ]; then
    echo "Starting Xvfb on display $DISPLAY..."
    Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp &
    XVFB_PID=$!
    ready=0
    for _ in $(seq 1 20); do
        if [ -S "$SOCKET" ]; then
            ready=1
            break
        fi
        if ! kill -0 "$XVFB_PID" 2>/dev/null; then
            echo "Xvfb process exited before creating $SOCKET"
            exit 1
        fi
        sleep 0.1
    done
    if [ "$ready" != "1" ]; then
        echo "FATAL: Xvfb socket $SOCKET not ready after 2 seconds"
        exit 1
    fi
    echo "Xvfb ready (socket $SOCKET present)"
fi

# Do not exec: the shell must remain to run the EXIT trap and reap the
# Xvfb process it started when the one-shot Python sync completes.
python futgg_player_sync.py
